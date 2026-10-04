from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from eth_abi import encode
import pytest

from app.chains.pons import FEE_ESCROW, ZERO, selector
from app.services.pons_reward_view import HOOK, snapshot

TOKEN, CURVE, OWNER, SPLITTER = ["0x" + c * 40 for c in "abcd"]


def adapter(phase=0, recipient=SPLITTER):
    return NS(launched=AsyncMock(return_value={"token": TOKEN, "curve": CURVE, "recipient": recipient,
        "pair": ZERO, "pool_fee": 0, "tick_spacing": 60, "exists": True, "phase": phase}),
        _call=AsyncMock(return_value="0x"+encode(["uint256"], [17]).hex()),
        rpc=NS(get_balance=AsyncMock(return_value=8)))


async def test_curve_escrow_and_splitter_are_separate_without_double_counting():
    a = adapter()
    data = await snapshot(a, TOKEN, {"splitter": SPLITTER})
    assert len(data["rows"]) == 4 and data["routing_matches_splitter"]
    assert [r["amount_raw"] for r in data["rows"]] == ["17", "17", "17", "8"]
    assert "total" not in data  # gross upstream fees are not net creator proceeds
    assert a._call.call_args_list[0].args == (CURVE, selector("quoteFeeBalance()"))
    assert a._call.call_args_list[2].args[0] == FEE_ESCROW


async def test_post_graduation_reads_both_currencies_and_detects_redirect():
    a = adapter(phase=2, recipient=OWNER)
    data = await snapshot(a, TOKEN, {"splitter": SPLITTER})
    assert data["routing_matches_splitter"] is False
    assert [r["asset"] for r in data["rows"][:4]] == [ZERO, ZERO, TOKEN, TOKEN]
    assert all(c.args[0] == HOOK for c in a._call.call_args_list[:4])
    assert data["rows"][4]["scope"] == "launch reward address"


async def test_provider_failure_and_malformed_returns_are_not_zero():
    a = adapter()
    a._call.side_effect = [RuntimeError("private provider"), "0x", "0x"+"00"*32]
    data = await snapshot(a, TOKEN, None)
    assert [r["amount_raw"] for r in data["rows"]] == [None, None, "0"]
    assert data["rows"][2]["scope"] == "recipient across launches"
    assert "private provider" not in str(data)
    a.launched.return_value["exists"] = False
    with pytest.raises(ValueError): await snapshot(a, TOKEN, None)
