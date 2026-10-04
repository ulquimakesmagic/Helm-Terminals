"""Audit regressions use synthetic objects: no keys, provider access or funds."""
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from app.chains.base import AdapterError
from app.services import execution_journal as journal
from app.services import quote_approval as quotes


def job(**kw):
    return NS(kind="trade_sell", params=kw)


def tx(kind="approve", signature="approval", wallet="w"):
    return NS(kind=kind, signature=signature, wallet_id=wallet, status="confirmed")


def test_legacy_approval_only_cannot_be_confirmed():
    assert not journal.can_resolve(job(), [tx()])


def test_crashed_dispatch_cannot_confirm_successful_prefix():
    j = job(**journal.initialize({}))
    assert not journal.can_resolve(j, [tx()])


async def test_missing_required_sale_blocks_recovery_even_if_dispatch_sealed():
    class DB:
        async def commit(self): pass
    j = job(**journal.initialize({}))
    await journal.require_steps(DB(), j, "w", ["approve", "sell"])
    journal.seal(j, [NS(signature="approval", status="confirmed")])
    assert not journal.can_resolve(j, [tx()])


def test_completed_dispatch_with_full_evidence_can_reconcile():
    j = job(**journal.initialize({}))
    journal.seal(j, [NS(signature="sale", status="timeout")])
    assert journal.can_resolve(j, [tx("sell", "sale")])


def test_origin_receipt_is_not_bridge_completion():
    j = NS(kind="bridge", params=journal.initialize({"route_status": "pending"}))
    journal.seal(j, [NS(signature="origin", status="timeout")])
    assert not journal.can_resolve(j, [tx("bridge", "origin")])
    j.params["route_status"] = "success"
    assert journal.can_resolve(j, [tx("bridge", "origin")])


def test_quote_is_user_and_request_bound_and_expires(monkeypatch):
    monkeypatch.setattr(quotes.time, "time", lambda: 1000)
    token = quotes.issue("u", {"amount_raw": "100"}, {"minimum_raw": "90"})
    assert quotes.verify(token, "u", {"amount_raw": "100"})["minimum_raw"] == "90"
    for user, scope in [("attacker", {"amount_raw": "100"}), ("u", {"amount_raw": "101"})]:
        with pytest.raises(HTTPException): quotes.verify(token, user, scope)
    with pytest.raises(HTTPException): quotes.verify(token[:-1] + "z", "u", {"amount_raw": "100"})
    monkeypatch.setattr(quotes.time, "time", lambda: 1120)
    with pytest.raises(HTTPException): quotes.verify(token, "u", {"amount_raw": "100"})


def test_deteriorating_or_missing_quote_floor_rejected(monkeypatch):
    monkeypatch.setattr(quotes.time, "time", lambda: 1000)
    bounds = {"minimum_raw": "90", "asset": "token", "network": "base", "expires": 1100}
    for value in [None, "0", "89"]:
        with pytest.raises(AdapterError):
            quotes.check_relay_bounds({"details": {"currencyOut": {"minimumAmount": value}}}, bounds, "token", "base")
    quotes.check_relay_bounds({"details": {"currencyOut": {"minimumAmount": "91", "currency": {"chainId": 8453, "address": "token"}}}}, bounds, "token", "base")


def test_native_revenue_trace_excludes_reverted_and_delegate_effects():
    from app.services.receipt_evidence import native_net_received
    trace = {"type": "CALL", "to": "router", "value": "0x64", "calls": [
        {"type": "CALL", "to": "treasury", "value": "0x14"},
        {"type": "CALL", "to": "treasury", "value": "0xff", "error": "reverted"},
        {"type": "DELEGATECALL", "to": "treasury", "value": "0xff"},
        {"type": "CALL", "from": "treasury", "to": "refund", "value": "0x5"},
        {"type": "CALL", "error": "reverted", "calls": [{"type":"CALL", "to":"treasury", "value":"0xff"}]},
    ]}
    assert native_net_received(trace, "treasury") == 15


async def test_revenue_requires_finalized_canonical_proof(monkeypatch):
    from app.services.revenue import dex_fee_event, verify_dex_fee
    from app.services import receipt_evidence
    from unittest.mock import AsyncMock
    event = dex_fee_event("base", 100, False, "treasury", "job", None, "user", "tx", "buy")
    assert event.status == "pending" and event.estimated
    monkeypatch.setattr(receipt_evidence, "native_fee_evidence", AsyncMock(return_value=None))
    assert await verify_dex_fee(NS(network="base"), event) == "unavailable"
    assert event.status == "pending"
    monkeypatch.setattr(receipt_evidence, "native_fee_evidence", AsyncMock(return_value={"amount_raw":"80", "reference":"tx"}))
    assert await verify_dex_fee(NS(network="base"), event) == "verified"
    assert event.amount_raw == 80 and event.status == "received" and not event.estimated
    assert await verify_dex_fee(NS(network="base"), event) == "already_verified"


async def test_lost_execution_lease_fails_closed(monkeypatch):
    from app.services import execution_lease as lease
    from app.signing.base import SigningError
    monkeypatch.setattr(lease, "get_settings", lambda: NS(env="production"))
    monkeypatch.setattr(lease, "_connection", NS(closed=True, invalidated=True))
    import asyncio
    monkeypatch.setattr(lease, "_lock", asyncio.Lock())
    monkeypatch.setattr(lease, "_lost", False)
    with pytest.raises(SigningError): await lease.assert_owned()
    assert lease._lost


async def test_required_swap_cannot_disappear_after_successful_approval(monkeypatch):
    from unittest.mock import AsyncMock
    from app.chains.base import EvmPlan, EvmStep
    from app.services import executor
    sender = NS(send=AsyncMock(return_value=executor.SubmitResult("approval", "confirmed")))
    ex = NS(evm=lambda _: sender)
    j = NS(params=journal.initialize({}))
    db = NS(commit=AsyncMock())
    wallet = NS(id="w", network="base", address="wallet", signer_ref="test")
    monkeypatch.setattr(executor, "_record", AsyncMock())
    plan = EvmPlan([EvmStep("approve", "token", b"approval"),
                    EvmStep("swap", "router", b"", build=AsyncMock(return_value=None))])
    result = await executor._run_evm_steps(db, ex, j, wallet, "sell", plan)
    assert result.status == "failed" and "Required execution step" in result.error
    assert sender.send.await_count == 1
    assert [r["kind"] for r in j.params["execution"]["required"]] == ["sell_approve", "sell"]
