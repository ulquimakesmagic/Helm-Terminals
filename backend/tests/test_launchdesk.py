"""Launchdesk: one-wallet position plans with explicit limits, no loops or coordinated wallets, expiry, edits,
history, allocations & vesting (reconciliation, validation, locks, dispersal), and launch protections."""

from datetime import timedelta

from solders.keypair import Keypair
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Activity, Automation, AutomationLimits, Job, LaunchAllocation, utcnow
from app.services.automation_runner import run_due_automation_once
from app.services.executor import Executor
from app.signing import get_signer

from .conftest import SOL, signup
from .test_automation import confirm_plan, due, live_launch, make_plan

PLAN = {"total_orders": 2, "interval_minutes": 5, "slippage_pct": 5, "max_price_impact_pct": 5,
        "expires_in_hours": 24}


# --- no loops, no coordinated wallets, no microtrades -----------------------------------------------------


async def test_a_plan_runs_from_exactly_one_wallet(client, fake_rpc):
    h = await signup(client)
    lid, _, ws = await live_launch(client, h, fake_rpc)
    r = await client.post(f"/launches/{lid}/automations", json={
        "wallet_ids": [w["id"] for w in ws], "budget": 0.2, **PLAN}, headers=h)
    assert r.status_code == 422 and "exactly one wallet" in r.text
    r = await client.post(f"/launches/{lid}/automations", json={"wallet_ids": [ws[0]["id"]], "budget": 0.2, **PLAN},
                          headers=h)
    assert r.status_code == 200 and r.json()["preflight"]["wallet"]["address"] == ws[0]["address"]


async def test_one_active_plan_per_launch_so_buys_and_sells_never_alternate(client, fake_rpc):
    h = await signup(client)
    lid, _, ws = await live_launch(client, h, fake_rpc)
    body, pf, path = await make_plan(client, h, lid, ws[0]["id"])
    assert (await confirm_plan(client, h, path, body, pf, "plan-aaaa")).status_code == 200
    fake_rpc.tokens[ws[0]["address"]] = 10**6
    # a sell plan alongside the buy plan (a loop), from the same or another wallet (coordination): refused
    for wid in (ws[0]["id"], ws[1]["id"]):
        body, pf, path = await make_plan(client, h, lid, wid, side="sell", budget=None, sell_amount=0.5)
        assert any("one plan per launch" in w for w in pf["preflight"]["warnings"])
        assert (await confirm_plan(client, h, path, body, pf, f"plan-s-{wid[:6]}")).status_code == 400
    # a second buy plan from another wallet on the same token: also refused
    body, pf, path = await make_plan(client, h, lid, ws[1]["id"])
    assert (await confirm_plan(client, h, path, body, pf, "plan-bbbb")).status_code == 400


async def test_tiny_orders_are_refused(client, fake_rpc):
    h = await signup(client)
    lid, _, ws = await live_launch(client, h, fake_rpc)
    _, pf, _ = await make_plan(client, h, lid, ws[0]["id"], budget=0.02, total_orders=12)
    assert any("smallest order" in w for w in pf["preflight"]["warnings"])


async def test_limits_are_required(client, fake_rpc):
    h = await signup(client)
    lid, _, ws = await live_launch(client, h, fake_rpc)
    base = {"wallet_id": ws[0]["id"], "budget": 0.2, **PLAN}
    for missing in ("slippage_pct", "max_price_impact_pct", "expires_in_hours", "total_orders", "interval_minutes"):
        r = await client.post(f"/launches/{lid}/automations", json={k: v for k, v in base.items() if k != missing},
                              headers=h)
        assert r.status_code == 422, missing
    r = await client.post(f"/launches/{lid}/automations", json={**base, "total_orders": 13}, headers=h)
    assert r.status_code == 422  # finite: at most 12 executions
    r = await client.post(f"/launches/{lid}/automations", json={**base, "budget": None, "sell_amount": 1}, headers=h)
    assert r.status_code == 422  # a buy plan can't also sell


async def test_insufficient_balance_blocks_activation(client, fake_rpc):
    h = await signup(client)
    lid, _, ws = await live_launch(client, h, fake_rpc)
    fake_rpc.balances[ws[0]["address"]] = SOL // 100
    body, pf, path = await make_plan(client, h, lid, ws[0]["id"], budget=1)
    assert any("needs about" in w for w in pf["preflight"]["warnings"])
    assert (await confirm_plan(client, h, path, body, pf, "poor-plan")).status_code == 400


# --- lifecycle ---------------------------------------------------------------------------------------------


async def test_expiry_stops_a_plan_and_it_cannot_resume(client, fake_rpc):
    h = await signup(client)
    lid, _, ws = await live_launch(client, h, fake_rpc)
    _, pf, _ = await make_plan(client, h, lid, ws[0]["id"], interval_minutes=60, total_orders=3, expires_in_hours=1)
    assert any("expire before its last order" in w for w in pf["preflight"]["warnings"])
    r = await client.post(f"/launches/{lid}/automations", json={"wallet_id": ws[0]["id"], "budget": 0.2,
                                                                **{**PLAN, "expires_in_hours": 24 * 60}}, headers=h)
    assert r.status_code == 400 and "at most 30 days" in r.text
    body, pf, path = await make_plan(client, h, lid, ws[0]["id"])
    aid = (await confirm_plan(client, h, path, body, pf, "exp-plan")).json()["id"]
    sent = len(fake_rpc.sent)
    async with get_sessionmaker()() as db:
        lim = (await db.execute(select(AutomationLimits).where(AutomationLimits.automation_id == aid))).scalar_one()
        lim.expires_at = utcnow() - timedelta(minutes=1)
        await db.commit()
    await due(aid)
    assert await run_due_automation_once(Executor(fake_rpc, get_signer()))
    a = (await client.get(f"/automations/{aid}", headers=h)).json()
    assert a["status"] == "expired" and len(fake_rpc.sent) == sent
    assert (await client.post(f"/automations/{aid}/resume", headers=h)).status_code == 409


async def test_pause_resume_cancel_and_tighten_only_edits_are_audited(client, fake_rpc):
    h = await signup(client)
    lid, _, ws = await live_launch(client, h, fake_rpc)
    body, pf, path = await make_plan(client, h, lid, ws[0]["id"], total_orders=4, budget=0.4)
    aid = (await confirm_plan(client, h, path, body, pf, "edit-plan")).json()["id"]
    assert (await client.post(f"/automations/{aid}/pause", headers=h)).json()["status"] == "paused"
    for loosen in ({"total_orders": 6}, {"slippage_pct": 10}, {"max_price_impact_pct": 9}, {"interval_minutes": 1},
                   {"expires_in_hours": 48}):
        r = await client.patch(f"/automations/{aid}", json=loosen, headers=h)
        assert r.status_code in (400, 422), loosen
    r = await client.patch(f"/automations/{aid}", json={"total_orders": 2, "slippage_pct": 2, "expires_in_hours": 12},
                           headers=h)
    assert r.status_code == 200
    a = r.json()
    assert a["total_orders"] == 2 and a["budget"] == 0.2 and a["order_sol"] == 0.1  # order size never changes
    assert (await client.post(f"/automations/{aid}/resume", headers=h)).json()["status"] == "scheduled"
    assert (await client.post(f"/automations/{aid}/cancel", headers=h)).json()["status"] == "cancelled"
    assert (await client.post(f"/automations/{aid}/resume", headers=h)).status_code == 409
    events = [e["event"] for e in (await client.get(f"/automations/{aid}", headers=h)).json()["history"]]
    assert events == ["scheduled", "paused", "edited", "resumed", "cancelled"]


async def test_a_provider_failure_stops_the_plan_without_retrying(client, fake_rpc):
    h = await signup(client)
    lid, _, ws = await live_launch(client, h, fake_rpc)
    body, pf, path = await make_plan(client, h, lid, ws[0]["id"])
    aid = (await confirm_plan(client, h, path, body, pf, "fail-plan")).json()["id"]
    fake_rpc.sim_error = "launchpad program error"
    await due(aid)
    ex = Executor(fake_rpc, get_signer())
    assert await run_due_automation_once(ex)
    a = (await client.get(f"/automations/{aid}", headers=h)).json()
    assert a["status"] == "failed" and "stopped" in a["error"]
    assert a["executions"][0]["status"] == "failed" and a["executions"][0]["transactions"][0]["error"]
    fake_rpc.sim_error = None
    assert not await run_due_automation_once(ex)  # nothing is due: no automatic retry
    async with get_sessionmaker()() as db:
        assert len((await db.execute(select(Job).where(Job.kind == "scheduled_buy"))).scalars().all()) == 1


async def test_older_multi_wallet_plans_are_paused_not_run(client, fake_rpc):
    h = await signup(client)
    lid, _, ws = await live_launch(client, h, fake_rpc)
    async with get_sessionmaker()() as db:
        from app.models import Launch

        owner = (await db.get(Launch, lid)).user_id
        a = Automation(user_id=owner, launch_id=lid, wallet_ids=[w["id"] for w in ws], side="buy", order_sol=0.1,
                       total_orders=2, interval_seconds=300, slippage_pct=5, next_run_at=utcnow() - timedelta(seconds=1),
                       idempotency_key="legacy-plan-1", preflight_hash="0" * 64)
        db.add(a)
        await db.commit()
        aid = a.id
    sent = len(fake_rpc.sent)
    assert await run_due_automation_once(Executor(fake_rpc, get_signer()))
    got = (await client.get(f"/automations/{aid}", headers=h)).json()
    assert got["status"] == "paused" and "several wallets" in got["error"] and len(fake_rpc.sent) == sent
    r = await client.post(f"/automations/{aid}/resume", headers=h)
    assert r.status_code == 409 and "one wallet" in r.json()["detail"]
    assert (await client.post(f"/automations/{aid}/cancel", headers=h)).status_code == 200


async def test_evm_orders_over_the_price_impact_limit_are_not_sent(client, fake_rpc):
    from app.chains.base import EvmPlan, EvmStep
    from app.models import Job, Wallet

    from .test_flap import FlapNode

    h = await signup(client)
    node = FlapNode("bsc")
    ex = Executor(fake_rpc, get_signer(), evm_rpcs={"bsc": node})
    w = (await client.post("/wallets", json={"network": "bsc", "count": 1}, headers=h)).json()[0]

    class Quoted:
        async def prepare_trade(self, req):
            return EvmPlan([EvmStep("buy", "0x" + "11" * 20, b"", value=1)], token="0x" + "22" * 20,
                           notes={"price_impact_pct": 9.5})

    from app.services.executor import run_evm_trade

    async with get_sessionmaker()() as db:
        wallet = await db.get(Wallet, w["id"])
        job = Job(user_id=wallet.user_id, kind="scheduled_buy", params={})
        db.add(job)
        await db.commit()
        await run_evm_trade(db, ex, Quoted(), job, "0x" + "22" * 20, [wallet], "buy", 0.001, "stagger", 0, 5,
                            max_price_impact_pct=5)
        await db.refresh(job)
        assert job.status == "failed"
        from app.models import Transaction

        tx = (await db.execute(select(Transaction).where(Transaction.job_id == job.id))).scalar_one()
        assert tx.signature is None and "9.50% price impact" in tx.error


def test_kyber_quote_reports_price_impact():
    import asyncio

    import httpx

    from app.chains.kyber import KyberSwap

    def handler(req):
        return httpx.Response(200, json={"data": {"routerAddress": "0x" + "11" * 20, "routeSummary": {
            "amountOut": "1", "amountInUsd": "100", "amountOutUsd": "97"}}})

    class RPC:
        async def erc20_allowance(self, *a):
            return 10**30

    k = KyberSwap(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    plan = asyncio.run(k.prepare_trade("base", RPC(), "0x" + "22" * 20, "0x" + "33" * 20, "buy", 10**18, 5))
    assert abs(plan.notes["price_impact_pct"] - 3.0) < 1e-9


# --- allocations & vesting ---------------------------------------------------------------------------------


async def _draft(client, h, launchpad="pumpfun"):
    w = (await client.post("/wallets", json={"count": 1}, headers=h)).json()[0]
    r = await client.post("/launches", json={"launchpad": launchpad, "name": "Alloc", "symbol": "ALC",
                                             "dev_wallet_id": w["id"]}, headers=h)
    return r.json()["id"], w


async def test_allocations_reconcile_exactly_to_supply(client, fake_rpc):
    h = await signup(client)
    lid, dev = await _draft(client, h)
    team = str(Keypair().pubkey())
    rows = [{"category": "public", "amount": "900000000"},
            {"category": "team", "recipient": team, "amount": "50000000", "unlock": "linear",
             "unlock_start": "2027-01-01T00:00:00Z", "unlock_end": "2028-01-01T00:00:00Z",
             "conditions": "12-month linear after a cliff"}]
    r = await client.put(f"/launches/{lid}/allocations", json={"rows": rows}, headers=h)
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["supply"] == "1000000000" and v["supply_source"].startswith("pumpfun")
    assert v["unallocated"] == "50000000" and v["reconciled"] is False
    assert [x["pct"] for x in v["rows"]] == [90.0, 5.0] and v["rows"][1]["unlocked_now_pct"] == 0
    rows.append({"category": "creator", "recipient": dev["address"], "amount": "50000000"})
    v = (await client.put(f"/launches/{lid}/allocations", json={"rows": rows}, headers=h)).json()
    assert v["reconciled"] is True and v["unallocated"] == "0"
    assert "dev wallet" in v["rows"][2]["tags"] and "your Helm wallet" in v["rows"][2]["tags"]
    rows.append({"category": "marketing", "recipient": str(Keypair().pubkey()), "amount": "1"})
    r = await client.put(f"/launches/{lid}/allocations", json={"rows": rows}, headers=h)
    assert r.status_code == 400 and "more than the" in r.json()["detail"]
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(Activity).where(Activity.kind == "allocation.updated"))).scalars().all()


async def test_allocation_validation(client, fake_rpc):
    h = await signup(client)
    lid, _ = await _draft(client, h)
    a = str(Keypair().pubkey())
    cases = [
        ([{"category": "team", "recipient": a, "amount": "1"}, {"category": "advisors", "recipient": a, "amount": "1"}],
         "listed twice"),
        ([{"category": "team", "recipient": "0x" + "ab" * 20, "amount": "1"}], "not a valid"),
        ([{"category": "team", "recipient": "", "amount": "1"}], "recipient address is required"),
        ([{"category": "public", "recipient": a, "amount": "1"}], "leave the recipient empty"),
        ([{"category": "team", "recipient": a, "amount": "-5"}], "above zero"),
        ([{"category": "team", "recipient": a, "amount": "lots"}], "isn't a number"),
        ([{"category": "vip", "recipient": a, "amount": "1"}], "unknown category"),
        ([{"category": "team", "recipient": a, "amount": "1", "lock": True, "unlock": "date",
           "unlock_start": "2027-01-01T00:00:00Z"}], "own Helm wallets"),
        ([{"category": "team", "recipient": a, "amount": "1", "unlock": "linear",
           "unlock_start": "2027-01-01T00:00:00Z"}], "start (cliff) before its end"),
        ([{"category": "public", "amount": "1"}, {"category": "public", "amount": "1"}], "Only one public"),
    ]
    for rows, msg in cases:
        r = await client.put(f"/launches/{lid}/allocations", json={"rows": rows}, headers=h)
        assert r.status_code == 400 and msg in r.json()["detail"], (rows, r.text)


async def test_concentration_is_flagged(client, fake_rpc):
    h = await signup(client)
    lid, dev = await _draft(client, h)
    rows = [{"category": "team", "recipient": str(Keypair().pubkey()), "amount": "150000000"},
            {"category": "creator", "recipient": dev["address"], "amount": "100000000"}]
    v = (await client.put(f"/launches/{lid}/allocations", json={"rows": rows}, headers=h)).json()
    assert any("15.00% of the supply" in w for w in v["warnings"])
    assert any("25.0% of the supply" in w and "concentration" in w for w in v["warnings"])


async def test_supply_is_pending_for_launchpads_without_a_fixed_supply(client, fake_rpc):
    from app.models import Launch, User

    h = await signup(client)
    async with get_sessionmaker()() as db:
        uid = (await db.execute(select(User.id))).scalar_one()
        l = Launch(user_id=uid, network="solana", launchpad="bags", name="B", symbol="B")
        db.add(l)
        await db.commit()
        lid = l.id
    v = (await client.get(f"/launches/{lid}/allocations", headers=h)).json()
    assert v["supply"] is None and "at deploy" in v["supply_source"] and v["reconciled"] is False


async def test_locks_block_sells_and_sends_and_can_only_be_extended(client, fake_rpc):
    h = await signup(client)
    lid, dev, ws = await live_launch(client, h, fake_rpc)
    w = ws[0]
    fake_rpc.tokens[w["address"]] = 100 * 10**6  # 100 tokens
    lock = {"category": "team", "recipient": w["address"], "amount": "60", "lock": True, "unlock": "date",
            "unlock_start": "2030-01-01T00:00:00Z"}
    v = (await client.put(f"/launches/{lid}/allocations", json={"rows": [lock]}, headers=h)).json()
    row = v["rows"][0]
    assert row["lock_enforced"] is True and v["supply_source"] == "on-chain"
    # selling 50% (50 tokens) would leave 50 < 60 locked
    r = await client.post(f"/launches/{lid}/trade", json={"side": "sell", "wallet_ids": [w["id"]], "amount": 50},
                          headers=h)
    assert any("locked by your allocation plan" in x for x in r.json()["preflight"]["warnings"])
    r = await client.post(f"/launches/{lid}/trade", json={"side": "sell", "wallet_ids": [w["id"]], "amount": 40},
                          headers=h)
    assert not r.json()["preflight"]["warnings"]
    r = await client.post(f"/launches/{lid}/send", json={"wallet_id": w["id"], "recipient": str(Keypair().pubkey()),
                                                         "percent": 100}, headers=h)
    assert any("locked" in x for x in r.json()["preflight"]["warnings"])
    fresh = (await client.post("/wallets", json={"count": 1}, headers=h)).json()
    mint = (await client.get(f"/launches/{lid}", headers=h)).json()["mint_address"]
    r = await client.post("/wallets/disperse", json={"from_wallet_id": w["id"], "to_wallet_ids": [fresh[0]["id"]],
                                                     "asset": mint, "percent": 100}, headers=h)
    assert any("locked" in x for x in r.json()["preflight"]["warnings"])
    # a staged exit can't plan to sell locked tokens either
    _, pf, _ = await make_plan(client, h, lid, w["id"], side="sell", budget=None, sell_amount=50)
    assert any("locked by your allocation plan" in x for x in pf["preflight"]["warnings"])
    # shortening, shrinking or removing the lock is refused; extending it is fine
    for change in ({**lock, "unlock_start": "2029-01-01T00:00:00Z"}, {**lock, "amount": "10"}, {**lock, "lock": False}):
        r = await client.put(f"/launches/{lid}/allocations", json={"rows": [{**change, "id": row["id"]}]}, headers=h)
        assert r.status_code == 400 and "extended" in r.json()["detail"], change
    r = await client.put(f"/launches/{lid}/allocations", json={"rows": []}, headers=h)
    assert r.status_code == 400
    r = await client.put(f"/launches/{lid}/allocations", json={"rows": [
        {**lock, "id": row["id"], "unlock_start": "2031-01-01T00:00:00Z"}]}, headers=h)
    assert r.status_code == 200


async def test_dispersing_supply_to_a_folder_is_recorded_in_the_plan(client, fake_rpc):
    from .test_distribute import confirm

    h = await signup(client)
    lid, dev, _ = await live_launch(client, h, fake_rpc, holders=1)
    mint = (await client.get(f"/launches/{lid}", headers=h)).json()["mint_address"]
    folder = (await client.post("/folders", json={"name": "Team"}, headers=h)).json()
    team = (await client.post("/wallets", json={"count": 2, "folder_id": folder["id"]}, headers=h)).json()
    fake_rpc.tokens[dev["address"]] = 100 * 10**6
    await client.put(f"/launches/{lid}/allocations", json={"rows": [{"category": "public", "amount": "1000000000"}]},
                     headers=h)
    body = {"from_wallet_id": dev["id"], "to_wallet_ids": [w["id"] for w in team], "asset": mint, "total": 10}
    pf, job = await confirm(client, h, "/wallets/disperse", body, "disperse-team-1")
    v = (await client.get(f"/launches/{lid}/allocations", headers=h)).json()
    lines = {r["recipient"]: r for r in v["rows"]}
    assert lines[team[0]["address"]]["amount"] == "5" and lines[team[0]["address"]]["source"] == "dispersal"
    assert "your Helm wallet" in lines[team[0]["address"]]["tags"]
    assert lines[None]["amount"] == "999999990" and v["reconciled"] is True  # moved off the public line
    async with get_sessionmaker()() as db:
        ev = (await db.execute(select(Activity).where(Activity.kind == "supply.dispersed"))).scalar_one()
        assert ev.payload["signature"] and len(ev.payload["to"]) == 2
    # a dispersal line keeps its recipient and amount even if a client tries to rewrite it
    rows = [{"id": r["id"], "category": r["category"], "recipient": r["recipient"] or "", "amount": r["amount"]}
            for r in v["rows"]]
    rows[1]["amount"] = "999"
    v2 = (await client.put(f"/launches/{lid}/allocations", json={"rows": rows}, headers=h)).json()
    assert {r["amount"] for r in v2["rows"] if r["source"] == "dispersal"} == {"5"}


# --- launch protections ------------------------------------------------------------------------------------


async def test_protections_are_honest_about_each_launchpad(client):
    r = (await client.get("/launchpads/clanker/protections")).json()
    items = {i["id"]: i for i in r["items"]}
    assert items["sniper_fee"]["available"] and "80%" in items["sniper_fee"]["effect"]
    assert not items["per_wallet_limit"]["available"] and not items["allowlist"]["available"]
    p = (await client.get("/launchpads/pumpfun/protections")).json()
    assert not any(i["available"] for i in p["items"]) and "no configurable" in p["summary"]
    m = (await client.get("/launchpads/meteora-dbc/protections")).json()
    assert any(i["id"] == "anti_sniping" and i["available"] for i in m["items"])


async def test_protection_settings_are_validated_and_applied(client):
    from eth_abi import decode as abi_decode

    from app.chains.clanker import SNIPER, sniper_params

    h = await signup(client)
    w = (await client.post("/wallets", json={"network": "base", "count": 1}, headers=h)).json()[0]
    bad = await client.post("/launches", json={"launchpad": "clanker", "name": "S", "symbol": "S",
                                               "dev_wallet_id": w["id"], "options": {"sniper_start_pct": 95}},
                            headers=h)
    assert bad.status_code == 400
    assert sniper_params({}) == SNIPER  # the default is unchanged
    assert sniper_params({"sniper_start_pct": 80, "sniper_decay_s": 120}) == (800000, SNIPER[1], 120)
    r = await client.post("/launches", json={"launchpad": "pumpfun", "name": "P", "symbol": "P",
                                             "options": {"sniper_start_pct": 80}}, headers=h)
    assert r.status_code == 400  # a setting the launchpad doesn't have


# --- review follow-ups -------------------------------------------------------------------------------------


async def test_fewer_orders_shrink_the_sell_total_and_no_order_grows(client, fake_rpc):
    h = await signup(client)
    lid, _, ws = await live_launch(client, h, fake_rpc)
    fake_rpc.tokens[ws[0]["address"]] = 120 * 10**6
    body, pf, path = await make_plan(client, h, lid, ws[0]["id"], side="sell", budget=None, sell_amount=120,
                                     total_orders=12)
    aid = (await confirm_plan(client, h, path, body, pf, "shrink-plan")).json()["id"]
    r = (await client.patch(f"/automations/{aid}", json={"total_orders": 2}, headers=h)).json()
    assert r["sell_amount"] == 20 and r["sell_per_order"] == 10
    ex = Executor(fake_rpc, get_signer())
    for _ in range(2):
        await due(aid)
        assert await run_due_automation_once(ex)
    async with get_sessionmaker()() as db:
        amounts = [j.params["amount"] for j in (await db.execute(select(Job).where(Job.kind == "scheduled_sell")
                                                                 .order_by(Job.created_at))).scalars()]
    assert amounts == [10, 10]


async def test_resuming_keeps_the_schedule(client, fake_rpc):
    h = await signup(client)
    lid, _, ws = await live_launch(client, h, fake_rpc)
    body, pf, path = await make_plan(client, h, lid, ws[0]["id"], start_in_minutes=60)
    aid = (await confirm_plan(client, h, path, body, pf, "resume-plan")).json()["id"]
    before = (await client.get(f"/automations/{aid}", headers=h)).json()["next_run_at"]
    await client.post(f"/automations/{aid}/pause", headers=h)
    after = (await client.post(f"/automations/{aid}/resume", headers=h)).json()["next_run_at"]
    assert after == before


async def test_the_launch_slot_blocks_a_second_active_plan_even_without_the_process_lock(client, fake_rpc):
    from fastapi import HTTPException

    from app.routes.automations import _take_slot

    h = await signup(client)
    lid, _, ws = await live_launch(client, h, fake_rpc)
    body, pf, path = await make_plan(client, h, lid, ws[0]["id"])
    aid = (await confirm_plan(client, h, path, body, pf, "slot-plan")).json()["id"]
    async with get_sessionmaker()() as db:
        try:
            await _take_slot(db, lid, "another-plan")
            raise AssertionError("slot was taken twice")
        except HTTPException as e:
            assert e.status_code == 409
    await client.post(f"/automations/{aid}/cancel", headers=h)
    async with get_sessionmaker()() as db:
        await _take_slot(db, lid, "another-plan")  # a finished plan's slot can be reused


async def test_sell_orders_under_one_percent_of_holdings_are_refused(client, fake_rpc):
    h = await signup(client)
    lid, _, ws = await live_launch(client, h, fake_rpc)
    fake_rpc.tokens[ws[0]["address"]] = 1000 * 10**6
    _, pf, _ = await make_plan(client, h, lid, ws[0]["id"], side="sell", budget=None, sell_amount=12, total_orders=12)
    assert any("less than 1%" in w for w in pf["preflight"]["warnings"])


async def test_bags_and_meteora_sell_exact_token_amounts():
    import httpx

    from app.chains.base import TradeRequest
    from app.chains.bags import BagsAdapter

    class RPC:
        async def get_token_supply(self, mint):
            return 10**18, 9

        async def get_token_balance(self, owner, mint):
            return 5 * 10**9

    seen = {}
    b = BagsAdapter(api_key="k", rpc=RPC(), client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))))

    async def swap(owner, a, b_, raw, slip):
        seen["raw"] = raw
        from app.chains.base import BuiltTx
        return BuiltTx(raw=b"")

    b._swap = swap
    await b.build_sell_tx(TradeRequest("M", "W", "sell", 1.5, False, 5))
    assert seen["raw"] == 1_500_000_000
    await b.build_sell_tx(TradeRequest("M", "W", "sell", 9, False, 5))
    assert seen["raw"] == 5 * 10**9  # never more than the balance


async def test_evm_staged_exit_sells_the_exact_raw_amount(client, fake_rpc):
    from app.chains.base import EvmPlan, EvmStep
    from app.models import Job, Wallet
    from app.services.executor import run_evm_trade

    from .test_flap import FlapNode

    h = await signup(client)
    node = FlapNode("bsc")
    ex = Executor(fake_rpc, get_signer(), evm_rpcs={"bsc": node})
    w = (await client.post("/wallets", json={"network": "bsc", "count": 1}, headers=h)).json()[0]
    node.tokens = getattr(node, "tokens", {})
    seen = {}

    async def bal(token, owner):
        return 10**24

    node.erc20_balance = bal

    class A:
        async def prepare_trade(self, req):
            seen["amt"] = req.amount_wei if hasattr(req, "amount_wei") else req.amount
            raise __import__("app.chains.base", fromlist=["AdapterError"]).AdapterError("stop")

    async with get_sessionmaker()() as db:
        wallet = await db.get(Wallet, w["id"])
        job = Job(user_id=wallet.user_id, kind="scheduled_sell", params={})
        db.add(job)
        await db.commit()
        await run_evm_trade(db, ex, A(), job, "0x" + "22" * 20, [wallet], "sell", 0.00006, "stagger", 0, 5,
                            sell_raw=6 * 10**19)
    assert seen["amt"] == 6 * 10**19


def test_allocation_math_keeps_full_precision_for_large_supplies():
    from app.services.allocations import dec

    assert dec("50000000000.000000000000000001") + dec("50000000000") != dec("100000000000")


async def test_burns_and_dispersals_into_locked_wallets_respect_locks(client, fake_rpc):
    h = await signup(client)
    lid, dev, ws = await live_launch(client, h, fake_rpc)
    w = ws[0]
    fake_rpc.tokens[w["address"]] = 100 * 10**6
    fake_rpc.tokens[dev["address"]] = 100 * 10**6
    lock = {"category": "team", "recipient": w["address"], "amount": "60", "lock": True, "unlock": "date",
            "unlock_start": "2030-01-01T00:00:00Z"}
    await client.put(f"/launches/{lid}/allocations", json={"rows": [lock]}, headers=h)
    r = await client.post(f"/launches/{lid}/burn", json={"wallet_id": w["id"], "percent": 100}, headers=h)
    assert any("locked" in x for x in r.json()["preflight"]["warnings"])
    mint = (await client.get(f"/launches/{lid}", headers=h)).json()["mint_address"]
    r = await client.post("/wallets/disperse", json={"from_wallet_id": dev["id"], "to_wallet_ids": [w["id"]],
                                                     "asset": mint, "total": 1}, headers=h)
    assert any("locked allocation line" in x for x in r.json()["preflight"]["warnings"])
