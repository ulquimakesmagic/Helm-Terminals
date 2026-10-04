import time

import pytest
from eth_abi import encode

from app.chains.base import AdapterError
from app.services.evm_exec import EvmCall
from app.services.execution_intent import AmountBound, ApprovalBound, Effects, ExecutionIntent, evm_transfer_effect, validate_effects
from app.services.tx_guard import APPROVE, TRANSFER

TOKEN, OWNER, RECIPIENT, SPENDER = ["0x" + c * 40 for c in "abcd"]


def intent(**overrides):
    return ExecutionIntent(**{**dict(network="base", wallet=OWNER, recipients=frozenset({RECIPIENT}),
        programs=frozenset({TOKEN, "native"}), maximum_debits=(AmountBound(TOKEN, 100), AmountBound("native", 10)),
        minimum_outputs=(), fee_limits=(), approvals=(ApprovalBound(TOKEN, SPENDER, 100),),
        expires=int(time.time())+60), **overrides})


def validate(calls, approved=None):
    validate_effects(approved or intent(), [evm_transfer_effect(c) for c in calls], network="base", wallet=OWNER)


def test_valid_native_and_token_transfers_and_exact_approval():
    validate([EvmCall(RECIPIENT, value=10), EvmCall(TOKEN, TRANSFER+encode(["address", "uint256"], [RECIPIENT, 100])),
              EvmCall(TOKEN, APPROVE+encode(["address", "uint256"], [SPENDER, 100]))])


@pytest.mark.parametrize("calls", [
    [EvmCall(OWNER, value=1)],
    [EvmCall(RECIPIENT, value=6), EvmCall(RECIPIENT, value=5)],
    [EvmCall(TOKEN, TRANSFER+encode(["address", "uint256"], [OWNER, 10]))],
    [EvmCall(TOKEN, APPROVE+encode(["address", "uint256"], [RECIPIENT, 10]))],
    [EvmCall(TOKEN, APPROVE+encode(["address", "uint256"], [SPENDER, 101]))],
    [EvmCall(TOKEN, b"\xde\xad\xbe\xef")],
    [EvmCall(None, b"creation")],
])
def test_provider_mutations_are_rejected(calls):
    with pytest.raises(AdapterError): validate(calls)


def test_authority_changes_chain_expiry_and_missing_output_are_rejected():
    with pytest.raises(AdapterError): validate([], intent(expires=1))
    with pytest.raises(AdapterError): validate([], intent(network="bsc"))
    with pytest.raises(AdapterError): validate([], intent(minimum_outputs=(AmountBound(TOKEN, 1),)))
    with pytest.raises(AdapterError):
        validate_effects(intent(), [Effects(TOKEN, authority_changes=frozenset({(OWNER, SPENDER)}))], network="base", wallet=OWNER)


def test_aggregate_fee_budget_is_enforced():
    approved = intent(fee_limits=(AmountBound("native", 5),))
    validate_effects(approved, [Effects("native", fees=(AmountBound("native", 5),))], network="base", wallet=OWNER)
    with pytest.raises(AdapterError, match="Aggregate fees"):
        validate_effects(approved, [Effects("native", fees=(AmountBound("native", 3),))] * 2,
                         network="base", wallet=OWNER)
