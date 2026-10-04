import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from eth_abi import encode
from eth_utils import keccak

from app.chains.base import AdapterError
from app.services.execution_intent import ExecutionIntent, AmountBound
from app.services.kyber_intent import EXECUTION, NATIVE, ROUTER, validate_calldata

W, TOKEN, OTHER = ["0x" + c * 40 for c in "abc"]


def transaction(*, recipient=W, amount=100, minimum=90, flags=0, permit=b"", fees=(), dst=TOKEN):
    desc = (NATIVE, dst, [], [], list(fees), [10] * len(fees), recipient, amount, minimum, flags, permit)
    return keccak(text=f"swapGeneric({EXECUTION})")[:4] + encode([EXECUTION], [(ROUTER, "0x"+"00"*20, b"route", desc, b"")])


async def validate(data):
    rpc = SimpleNamespace(network="base", call=AsyncMock(return_value="0x"+"00"*31+"01"))
    intent = ExecutionIntent("base", W, frozenset({W}), frozenset({ROUTER}),
        (AmountBound(NATIVE, 100),), (AmountBound(TOKEN, 90),), (), (), int(time.time())+60)
    await validate_calldata(rpc, ROUTER, data, 100, intent, fee_bps=0, fee_receiver="")


async def test_reviewed_router_calldata_preserves_output_floor():
    await validate(transaction())


@pytest.mark.parametrize("mutation", [dict(recipient=OTHER), dict(amount=101), dict(minimum=89),
    dict(flags=1), dict(flags=2), dict(flags=8), dict(flags=16), dict(flags=32), dict(permit=b"permit"),
    dict(fees=(OTHER,)), dict(dst=OTHER)])
async def test_malicious_provider_calldata_is_refused(mutation):
    with pytest.raises(AdapterError): await validate(transaction(**mutation))


async def test_unknown_selector_and_trailing_bytes_refused():
    for data in [b"\xde\xad\xbe\xef", transaction()+b"extra"]:
        with pytest.raises(AdapterError): await validate(data)
