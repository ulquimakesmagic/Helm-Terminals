"""Decode the reviewed MetaAggregationRouterV2 interface before signing.

Source: Sourcify verified contract, chain 1, 0x6131B5fae19EA4f9D964eAc0408E4408b66337b5,
contracts/MetaAggregationRouterV2.sol. Never accept permit, burn, partial-fill or extra-ETH flags.
"""
from eth_abi import decode, encode
from eth_utils import keccak

from ..chains.base import AdapterError
from .execution_intent import AmountBound, Effects, validate_effects

ROUTER = "0x6131b5fae19ea4f9d964eac0408e4408b66337b5"
NATIVE = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
DESC = "(address,address,address[],uint256[],address[],uint256[],address,uint256,uint256,uint256,bytes)"
EXECUTION = f"(address,address,bytes,{DESC},bytes)"
SELECTORS = {keccak(text=f"{method}({EXECUTION})")[:4] for method in ("swap", "swapGeneric")}
# Claim input into router, output-side fee, basis-point fees and internal approval are understood.
ALLOWED_FLAGS = 0x04 | 0x40 | 0x80 | 0x100


async def validate_calldata(rpc, router: str, data: bytes, value: int, intent, *, fee_bps: int, fee_receiver: str):
    if router.lower() != ROUTER or data[:4] not in SELECTORS:
        raise AdapterError("Kyber router or transaction form is not supported by Helm's signing policy")
    try:
        execution = decode([EXECUTION], data[4:])[0]
        if encode([EXECUTION], [execution]) != data[4:]:
            raise ValueError()
        call_target, approve_target, target_data, desc, _ = execution
        src, dst, receivers, amounts, fee_receivers, fee_amounts, recipient, amount, minimum, flags, permit = desc
    except Exception:
        raise AdapterError("Kyber returned malformed or noncanonical calldata") from None
    if flags & ~ALLOWED_FLAGS or permit or not target_data or minimum <= 0:
        raise AdapterError("Kyber transaction requests unsupported permissions or execution flags")
    if src == dst or len(receivers) != len(amounts) or sum(amounts) > amount:
        raise AdapterError("Kyber input transfers exceed the approved amount")
    if value != (amount if src == NATIVE else 0):
        raise AdapterError("Kyber native value does not match the approved input")
    if fee_bps:
        if (tuple(fee_receivers) != (fee_receiver.lower(),) or tuple(fee_amounts) != (fee_bps,)
                or not flags & 0x80 or bool(flags & 0x40) != (dst == NATIVE)):
            raise AdapterError("Kyber changed the approved fee recipient, amount or asset")
    elif fee_receivers or fee_amounts:
        raise AdapterError("Kyber added an unapproved fee")
    # Protocol ownership controls this registry; code presence or provider-supplied addresses do not establish trust.
    for target in {call_target, approve_target} - {"0x" + "00" * 20}:
        result = await rpc.call("eth_call", [{"to": ROUTER, "data": "0x" + (keccak(text="isWhitelist(address)")[:4]
                                + encode(["address"], [target])).hex()}, "latest"])
        if not isinstance(result, str) or int(result, 16) != 1:
            raise AdapterError("Kyber executor is not approved by the pinned router")
    validate_effects(intent, [Effects(ROUTER, (AmountBound(src, amount),), (AmountBound(dst, minimum),),
                      frozenset({recipient if int(recipient, 16) else intent.wallet}))],
                     network=rpc.network, wallet=intent.wallet)
