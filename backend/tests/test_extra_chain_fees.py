"""Helm's launch and trading fees on SunPump (TRON) and Tolly (Arc); none on Meme.Cooking (NEAR)."""

import asyncio

from sqlalchemy import select

from app.chains import extra_pads as ep
from app.chains import tron
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import RevenueEvent, TreasuryAddress
from app.services import extra_chains as xc
from app.services import platform_config as pc

from .conftest import signup
from .test_launchpads_extra import FakeArc, FakeSunPump, _go, _launch, pads  # noqa: F401 - fixture

TREASURY_T = "TLa2f6VPqDgRE67v1736s7bJ8Ray5wYjU7"
TREASURY_A = "0x" + "7a" * 20


async def fees_on(monkeypatch, trx_price=0.25, trade_bps=100):
    s = get_settings()
    monkeypatch.setattr(s, "launch_fee_usd", 10.0)
    monkeypatch.setattr(s, "platform_fee_bps", trade_bps)

    async def prices(market=None):
        return {"TRX": trx_price} if trx_price else {}
    monkeypatch.setattr("app.services.portfolio.coin_prices", prices)
    async with get_sessionmaker()() as db:
        db.add(TreasuryAddress(network="tron", address=TREASURY_T, label="t"))
        db.add(TreasuryAddress(network="arc", address=TREASURY_A, label="a"))
        await db.commit()
        await pc.refresh(db, force=True)


def trx_transfers(t, to):
    out = []
    for b in t.broadcasts:
        c = tron.pb_fields(tron.pb_fields(tron.pb_fields(bytes.fromhex(b["raw_data_hex"]))[11][0])[2][0])[2][0]
        f = tron.pb_fields(c)
        if 3 in f and 2 in f and tron.encode_address(f[2][0]) == to:
            out.append(f[3][0])
    return out


async def events(source):
    async with get_sessionmaker()() as db:
        return list((await db.execute(select(RevenueEvent).where(RevenueEvent.source == source))).scalars())


async def test_sunpump_pays_the_launch_fee_in_trx_and_one_percent_on_trades(client, pads, monkeypatch):
    async def nosleep(*_):
        return None
    monkeypatch.setattr(asyncio, "sleep", nosleep)
    await fees_on(monkeypatch)
    t = FakeSunPump()
    t.accounts[TREASURY_T] = 1  # an activated treasury
    xc._clients["tron"] = t
    h = await signup(client, main=False)
    dev = (await client.post("/wallets", json={"network": "tron", "count": 1}, headers=h)).json()[0]
    t.accounts[dev["address"]] = 500 * ep.TRX
    lid = (await _launch(client, h, "sunpump", dev, dev_buy_sol=50))["id"]

    body = {}
    pf = (await client.post(f"/launches/{lid}/deploy", json=body, headers=h)).json()
    p = pf["preflight"]
    assert p["platform_fee"] == "$10, paid in TRX at the live price after the token is created", p
    assert p["launch_fee_estimate"] == "about 40 TRX right now" and p["launch_fee_max"] == "at most 44 TRX"
    r = await client.post(f"/launches/{lid}/deploy", json={"confirm": True, "preflight_hash": pf["preflight_hash"],
                                                          "quote_approval": pf["quote_approval"],
                                                          "idempotency_key": "sp-fee"}, headers=h)
    assert r.status_code == 200, r.text
    assert (await client.get(f"/launches/{lid}", headers=h)).json()["status"] == "live"
    assert trx_transfers(t, TREASURY_T) == [40 * ep.TRX]  # $10 / $0.25
    (ev,) = await events("launch_fee")
    assert ev.status == "received" and ev.network == "tron" and int(ev.amount_raw) == 40 * ep.TRX

    from app.services import extra_launch

    real_run = extra_launch.run_plan

    async def run_and_debit(session, job, wallet, kind, plan):  # the fake node doesn't debit a buy itself
        out = await real_run(session, job, wallet, kind, plan)
        if kind == "trade_buy":
            t.accounts[wallet.address] -= 200 * ep.TRX
        return out
    monkeypatch.setattr(extra_launch, "run_plan", run_and_debit)
    t.accounts[dev["address"]] = 500 * ep.TRX
    pf, r = await _go(client, h, f"/launches/{lid}/trade",
                      {"side": "buy", "wallet_ids": [dev["id"]], "amount": 200, "slippage_pct": 5}, "sp-buy")
    assert pf["preflight"]["platform_fee"].startswith("1% of the TRX spent")
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "confirmed", job
    assert trx_transfers(t, TREASURY_T)[-1] == 2 * ep.TRX  # 1% of 200 TRX
    assert [e.status for e in await events("trade_fee")] == ["received"]


async def test_a_trx_price_jump_after_the_preview_needs_a_new_review(client, pads, monkeypatch):
    await fees_on(monkeypatch, trx_price=0.25)
    t = FakeSunPump()
    xc._clients["tron"] = t
    h = await signup(client, main=False)
    dev = (await client.post("/wallets", json={"network": "tron", "count": 1}, headers=h)).json()[0]
    t.accounts[dev["address"]] = 500 * ep.TRX
    lid = (await _launch(client, h, "sunpump", dev, dev_buy_sol=50))["id"]
    pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()

    async def cheaper(market=None):
        return {"TRX": 0.10}  # 100 TRX now, far above the approved 44
    monkeypatch.setattr("app.services.portfolio.coin_prices", cheaper)
    r = await client.post(f"/launches/{lid}/deploy", json={"confirm": True, "preflight_hash": pf["preflight_hash"],
                                                          "quote_approval": pf["quote_approval"],
                                                          "idempotency_key": "sp-jump"}, headers=h)
    assert r.status_code == 400 and "moved more than 10%" in r.json()["detail"] and t.broadcasts == []


async def test_tolly_charges_the_launch_fee_in_usdc_and_meme_cooking_charges_nothing(client, pads, monkeypatch):
    from app.services import extra_launch
    from app.services.executor import Executor

    await fees_on(monkeypatch, trx_price=None)  # Arc needs no price feed: its coin is USDC
    arc = FakeArc()
    real_ctx, real_evm = extra_launch.ctx_for, Executor.evm

    def ctx_for(ex, network, w):
        c = real_ctx(ex, network, w) if network != "arc" else ep.PadContext(w.address, w.signer_ref or "", ex.signer)
        if network == "arc":
            c.evm = arc
        return c
    monkeypatch.setattr(extra_launch, "ctx_for", ctx_for)
    monkeypatch.setattr(Executor, "evm", lambda self, n: arc if n == "arc" else real_evm(self, n))
    h = await signup(client, main=False)
    dev = (await client.post("/wallets", json={"network": "arc", "count": 1}, headers=h)).json()[0]
    lid = (await _launch(client, h, "tolly", dev, dev_buy_sol=100))["id"]
    pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
    assert pf["preflight"]["platform_fee"].startswith("$10, paid in USDC"), pf["preflight"]
    r = await client.post(f"/launches/{lid}/deploy", json={"confirm": True, "preflight_hash": pf["preflight_hash"],
                                                          "quote_approval": pf["quote_approval"],
                                                          "idempotency_key": "tolly-fee"}, headers=h)
    assert r.status_code == 200, r.text
    fee_call = arc.sent[-1]
    assert fee_call.to.lower() == TREASURY_A and fee_call.value == 10 * 10**18 and fee_call.data == b""
    (ev,) = await events("launch_fee")
    assert ev.network == "arc" and ev.asset == "USDC" and ev.status == "received"

    from app.services import launch_fee
    async with get_sessionmaker()() as db:
        assert await launch_fee.quote(db, "near") == (None, None)  # no verified NEAR price source


async def test_a_failed_fee_never_undoes_the_launch(client, pads, monkeypatch):
    await fees_on(monkeypatch)
    t = FakeSunPump()
    xc._clients["tron"] = t
    h = await signup(client, main=False)
    dev = (await client.post("/wallets", json={"network": "tron", "count": 1}, headers=h)).json()[0]
    t.accounts[dev["address"]] = 500 * ep.TRX
    lid = (await _launch(client, h, "sunpump", dev, dev_buy_sol=50))["id"]

    async def boom(*a, **k):
        raise RuntimeError("tron node down")
    monkeypatch.setattr("app.services.launch_fee.send_native", boom)
    pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
    r = await client.post(f"/launches/{lid}/deploy", json={"confirm": True, "preflight_hash": pf["preflight_hash"],
                                                          "quote_approval": pf["quote_approval"],
                                                          "idempotency_key": "sp-boom"}, headers=h)
    assert r.status_code == 200, r.text
    assert (await client.get(f"/launches/{lid}", headers=h)).json()["status"] == "live"
    (ev,) = await events("launch_fee")
    assert ev.status == "failed"


async def test_a_sell_fee_is_capped_at_the_sales_proceeds(client, pads, monkeypatch):
    """Review finding: a deposit landing in the wallet during a sell must not be charged. The fee is 1% of the
    smaller of the balance gain and the sale's quoted proceeds."""
    from app.services import extra_launch

    async def nosleep(*_):
        return None
    monkeypatch.setattr(asyncio, "sleep", nosleep)
    await fees_on(monkeypatch)
    t = FakeSunPump()
    t.accounts[TREASURY_T] = 1
    xc._clients["tron"] = t
    h = await signup(client, main=False)
    dev = (await client.post("/wallets", json={"network": "tron", "count": 1}, headers=h)).json()[0]
    t.accounts[dev["address"]] = 500 * ep.TRX
    lid = (await _launch(client, h, "sunpump", dev, dev_buy_sol=0))["id"]
    pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
    await client.post(f"/launches/{lid}/deploy", json={"confirm": True, "preflight_hash": pf["preflight_hash"],
                                                      "quote_approval": pf["quote_approval"],
                                                      "idempotency_key": "sp-sell-setup"}, headers=h)
    real_run = extra_launch.run_plan

    async def run_with_deposit(session, job, wallet, kind, plan):
        out = await real_run(session, job, wallet, kind, plan)
        if kind == "trade_sell":
            t.accounts[wallet.address] += 10_000 * ep.TRX  # unrelated money arrives during the sell
        return out
    monkeypatch.setattr(extra_launch, "run_plan", run_with_deposit)
    n = len(trx_transfers(t, TREASURY_T))
    pf, r = await _go(client, h, f"/launches/{lid}/trade",
                      {"side": "sell", "wallet_ids": [dev["id"]], "amount": 50, "slippage_pct": 5}, "sp-sell")
    expected = int(pf["preflight"]["estimates"][0]["expected_out_native_raw"])
    fees = trx_transfers(t, TREASURY_T)[n:]
    assert all(f <= expected // 100 for f in fees)  # never 1% of the 10,000 TRX deposit
