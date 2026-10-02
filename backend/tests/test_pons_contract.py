"""Execute real Solidity bytecode in a local EVM; no live chain, wallet keys or funds."""
import json
import random
from pathlib import Path

import pytest
from eth_abi import decode, encode
from eth_tester import EthereumTester, PyEVMBackend
from eth_utils import to_canonical_address

from app.chains.pons_rewards import ESCROW, FACTORY_ARTIFACT, calldata

HELPERS = json.loads((Path(__file__).parent / "fixtures/pons-reward-helpers.json").read_text())


@pytest.fixture
def chain():
    state = PyEVMBackend.generate_genesis_state()
    state[to_canonical_address(ESCROW)] = {"balance": 0, "nonce": 0, "storage": {},
        "code": bytes.fromhex(HELPERS["MockPonsEscrow"]["deployedBytecode"])}
    return EthereumTester(PyEVMBackend(genesis_state=state))


def send(chain, to=None, data=b"", value=0, sender=None, success=True):
    tx = {"from": sender or chain.get_accounts()[0], "gas": 6_000_000, "value": value, "data": "0x" + data.hex()}
    if to:
        tx["to"] = to
    receipt = chain.get_transaction_receipt(chain.send_transaction(tx))
    assert receipt["status"] == int(success), receipt
    return receipt


def read(chain, to, signature, types=(), values=(), out="uint256"):
    result = chain.call({"from": chain.get_accounts()[0], "to": to, "data": "0x" + calldata(signature, types, values).hex()})
    return decode([out], bytes.fromhex(result[2:]))[0]


def setup(chain, creator=None, treasury=None, key=b"1" * 32):
    factory = send(chain, data=bytes.fromhex(FACTORY_ARTIFACT["bytecode"]))["contract_address"]
    args = [creator or chain.get_accounts()[1], treasury or chain.get_accounts()[2], key]
    types = ["address", "address", "bytes32"]
    splitter = read(chain, factory, "predict(address,address,bytes32)", types, args, "address")
    return factory, args, types, splitter


def credit(chain, splitter, amount):
    send(chain, ESCROW, calldata("credit(address)", ["address"], [splitter]), amount)


def test_counterfactual_claims_are_isolated_and_permissionless(chain):
    f, args, types, s = setup(chain)
    creator, treasury, key = args
    # The recipient has no code yet, but fees can accrue there without going to Helm's wallet.
    assert chain.get_code(s) == "0x"
    credit(chain, s, 10**18)
    credit(chain, creator, 5 * 10**18)  # unrelated old launches remain untouched
    before = [chain.get_balance(a) for a in (creator, treasury)]
    send(chain, f, calldata("distribute(address,address,bytes32)", types, args), sender=chain.get_accounts()[3])
    assert [chain.get_balance(a) - b for a, b in zip((creator, treasury), before)] == [94 * 10**16, 6 * 10**16]
    assert read(chain, ESCROW, "balanceOf(address)", ["address"], [creator]) == 5 * 10**18
    # Front-running deployment/distribution or retrying cannot redirect or duplicate a payout.
    send(chain, f, calldata("deploy(address,address,bytes32)", types, args), sender=chain.get_accounts()[4])
    send(chain, f, calldata("distribute(address,address,bytes32)", types, args))
    assert read(chain, s, "totalReleased()") == 10**18
    alternate = [creator, chain.get_accounts()[4], key]
    assert read(chain, f, "predict(address,address,bytes32)", types, alternate, "address") != s
    alternate = [creator, treasury, b"2" * 32]
    assert read(chain, f, "predict(address,address,bytes32)", types, alternate, "address") != s
    send(chain, s, calldata("release(address)", ["address"], [chain.get_accounts()[4]]), success=False)


@pytest.mark.parametrize("reject_creator", [True, False])
def test_rejecting_recipient_does_not_block_other_beneficiary(chain, reject_creator):
    reject = send(chain, data=bytes.fromhex(HELPERS["RejectEther"]["bytecode"]))["contract_address"]
    f, args, types, s = setup(chain, creator=reject if reject_creator else None,
                              treasury=None if reject_creator else reject)
    credit(chain, s, 10000)
    send(chain, f, calldata("distribute(address,address,bytes32)", types, args))
    assert read(chain, s, "released(address)", ["address"], [args[0]]) == (0 if reject_creator else 9400)
    assert read(chain, s, "released(address)", ["address"], [args[1]]) == (600 if reject_creator else 0)
    assert chain.get_balance(s) == (9400 if reject_creator else 600)


def test_cumulative_rounding_and_donations_conserve_every_wei(chain):
    f, args, types, s = setup(chain)
    rng, total = random.Random(42), 0
    for i in range(40):
        amount = 1 if i < 20 else rng.randrange(1, 10000)
        total += amount
        if i % 2:
            send(chain, s, value=amount)  # donations also obey immutable split
        else:
            credit(chain, s, amount)
        send(chain, f, calldata("distribute(address,address,bytes32)", types, args))
        helm = read(chain, s, "released(address)", ["address"], [args[1]])
        creator = read(chain, s, "released(address)", ["address"], [args[0]])
        assert helm == total * 600 // 10000
        assert creator + helm == total and chain.get_balance(s) == 0


def test_reentrant_recipient_cannot_double_withdraw(chain):
    attacker = send(chain, data=bytes.fromhex(HELPERS["Reenter"]["bytecode"]))["contract_address"]
    f, args, types, s = setup(chain, creator=attacker)
    send(chain, attacker, calldata("setTarget(address)", ["address"], [s]))
    credit(chain, s, 10000)
    send(chain, f, calldata("distribute(address,address,bytes32)", types, args))
    assert chain.get_balance(attacker) == 9400 and chain.get_balance(s) == 0
    assert read(chain, s, "totalReleased()") == 10000


def test_zero_or_equal_recipients_rejected(chain):
    f, args, types, _ = setup(chain)
    for invalid in ([args[0], args[0], args[2]], [args[0], "0x" + "00" * 20, args[2]]):
        send(chain, f, calldata("deploy(address,address,bytes32)", types, invalid), success=False)
