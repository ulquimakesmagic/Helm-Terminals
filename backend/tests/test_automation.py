from datetime import timedelta

from app.db import get_sessionmaker
from app.models import Automation, Job, utcnow
from app.services.automation_runner import run_due_automation_once
from app.services.executor import Executor
from app.signing import get_signer

from .conftest import signup


async def live_launch(client, headers, fake_rpc, holders=2):
    wallets = (await client.post("/wallets", json={"count": holders + 1, "label_prefix": "W"}, headers=headers)).json()
    dev, others = wallets[0], wallets[1:]
    created = await client.post("/launches", json={
        "name": "Test", "symbol": "tst", "dev_wallet_id": dev["id"], "dev_buy_sol": 0.5,
    }, headers=headers)
    assert created.status_code == 201, created.text
    launch_id = created.json()["id"]
    path = f"/launches/{launch_id}/deploy"
    preview = await client.post(path, json={}, headers=headers)
    deployed = await client.post(path, json={
        "confirm": True, "preflight_hash": preview.json()["preflight_hash"],
        "idempotency_key": f"deploy-{launch_id}",
    }, headers=headers)
    assert deployed.status_code == 200, deployed.text
    attached = await client.post(f"/launches/{launch_id}/wallets", json={
        "wallet_ids": [w["id"] for w in others],
    }, headers=headers)
    assert attached.status_code == 200, attached.text
    return launch_id, dev, others


async def make_plan(client, headers, launch_id, wallet_id, **overrides):
    body = {
        "wallet_id": wallet_id,
        "budget": 0.2,
        "total_orders": 2,
        "interval_minutes": 5,
        "start_in_minutes": 1,
        "slippage_pct": 5,
        "max_price_impact_pct": 5,
        "expires_in_hours": 24,
        **overrides,
    }
    body = {k: v for k, v in body.items() if v is not None}
    path = f"/launches/{launch_id}/automations"
    preview = await client.post(path, json=body, headers=headers)
    assert preview.status_code == 200, preview.text
    return body, preview.json(), path


async def confirm_plan(client, headers, path, body, preview, key):
    return await client.post(path, json={**body, "confirm": True, "preflight_hash": preview["preflight_hash"],
                                         "idempotency_key": key}, headers=headers)


async def due(aid):
    async with get_sessionmaker()() as db:
        automation = await db.get(Automation, aid)
        automation.next_run_at = utcnow() - timedelta(seconds=1)
        await db.commit()


async def test_scheduled_buy_preview_is_bounded_and_worker_records_each_tranche(client, fake_rpc):
    headers = await signup(client)
    launch_id, _, wallets = await live_launch(client, headers, fake_rpc)
    sent_before_preview = len(fake_rpc.sent)
    body, preview, path = await make_plan(client, headers, launch_id, wallets[0]["id"])

    pf = preview["preflight"]
    assert pf["strategy"] == "scheduled accumulation" and pf["budget"] == 0.2 and pf["amount_per_order"] == 0.1
    assert pf["maximum_executions"] == 2 and pf["expires_in_hours"] == 24 and pf["max_price_impact_pct"] == 5
    assert pf["estimated_network_fees_max"] > 0 and "platform_fee" in pf and "volume" in pf["what_this_is"]
    assert len(fake_rpc.sent) == sent_before_preview

    created = await confirm_plan(client, headers, path, body, preview, "schedule-one")
    assert created.status_code == 200, created.text
    automation_id = created.json()["id"]
    assert created.json()["status"] == "scheduled" and created.json()["budget"] == 0.2
    assert (await client.get(f"/launches/{launch_id}/automations", headers=headers)).json()[0]["id"] == automation_id

    executor = Executor(fake_rpc, get_signer())
    await due(automation_id)
    assert await run_due_automation_once(executor)
    async with get_sessionmaker()() as db:
        automation = await db.get(Automation, automation_id)
        assert automation.status == "scheduled" and automation.runs_completed == 1
    await due(automation_id)
    assert await run_due_automation_once(executor)
    async with get_sessionmaker()() as db:
        automation = await db.get(Automation, automation_id)
        assert automation.status == "completed" and automation.runs_completed == 2
    assert len(fake_rpc.sent) == sent_before_preview + 2  # one wallet, one order each time
    detail = (await client.get(f"/automations/{automation_id}", headers=headers)).json()
    assert [e["status"] for e in detail["executions"]] == ["confirmed", "confirmed"]
    assert detail["executions"][0]["transactions"][0]["signature"]
    assert [h["event"] for h in detail["history"]][:1] == ["scheduled"]


async def test_scheduled_buy_requires_live_owned_attached_wallets_and_caps_budget(client, fake_rpc):
    h1 = await signup(client)
    launch_id, _, wallets = await live_launch(client, h1, fake_rpc)
    _, preview, path = await make_plan(client, h1, launch_id, wallets[0]["id"], budget=12, total_orders=6)
    assert any("limit" in w for w in preview["preflight"]["warnings"])

    h2 = await signup(client, "second@example.com")
    other = await client.post(path, json={
        "wallet_id": wallets[0]["id"], "budget": 0.2, "total_orders": 2, "interval_minutes": 5, "slippage_pct": 5,
        "max_price_impact_pct": 5, "expires_in_hours": 24}, headers=h2)
    assert other.status_code == 404


async def test_scheduled_buy_idempotency_returns_same_plan_and_rejects_key_reuse(client, fake_rpc):
    headers = await signup(client)
    launch_id, _, wallets = await live_launch(client, headers, fake_rpc)
    body, preview, path = await make_plan(client, headers, launch_id, wallets[0]["id"])
    confirmation = {**body, "confirm": True, "preflight_hash": preview["preflight_hash"],
                    "idempotency_key": "schedule-replay"}
    first = await client.post(path, json=confirmation, headers=headers)
    replay = await client.post(path, json=confirmation, headers=headers)
    assert first.status_code == replay.status_code == 200
    assert first.json()["id"] == replay.json()["id"]

    changed_body = {**body, "budget": 0.4}
    changed = await client.post(path, json={**changed_body, "confirm": True, "preflight_hash": "0" * 64,
                                            "idempotency_key": "schedule-replay"}, headers=headers)
    assert changed.status_code == 409


async def test_staged_exit_sells_a_fixed_amount_per_order_and_stops_when_empty(client, fake_rpc):
    headers = await signup(client)
    launch_id, _, wallets = await live_launch(client, headers, fake_rpc)
    sent_before = len(fake_rpc.sent)
    fake_rpc.tokens[wallets[0]["address"]] = 1_000_000  # 1 token at 6 decimals
    fake_rpc.next_delta = 1_000_000  # the sale must actually return native proceeds
    body, preview, path = await make_plan(client, headers, launch_id, wallets[0]["id"], side="sell", budget=None,
                                          sell_amount=0.5)
    pf = preview["preflight"]
    assert pf["kind"] == "scheduled_sell" and pf["strategy"] == "staged exit"
    assert pf["sell_amount"] == 0.5 and pf["sell_per_order"] == 0.25 and pf["wallet_token_balance"] == 1
    assert not pf["warnings"], pf["warnings"]
    created = await confirm_plan(client, headers, path, body, preview, "sell-schedule-one")
    assert created.status_code == 200, created.text
    automation_id = created.json()["id"]

    executor = Executor(fake_rpc, get_signer())
    await due(automation_id)
    assert await run_due_automation_once(executor)
    async with get_sessionmaker()() as db:
        automation = await db.get(Automation, automation_id)
        assert automation.status == "scheduled" and automation.runs_completed == 1
        job = await db.get(Job, automation.last_job_id)
        assert job.kind == "scheduled_sell" and job.params["amount"] == 0.25
    fake_rpc.tokens[wallets[0]["address"]] = 0  # the position is gone
    await due(automation_id)
    assert await run_due_automation_once(executor)
    async with get_sessionmaker()() as db:
        automation = await db.get(Automation, automation_id)
        assert automation.status == "completed" and "no unlocked tokens" in automation.error
    assert len(fake_rpc.sent) == sent_before + 1


async def test_sell_plan_rejects_wallet_without_tokens_or_selling_more_than_held(client, fake_rpc):
    headers = await signup(client)
    launch_id, _, wallets = await live_launch(client, headers, fake_rpc)
    _, preview, _ = await make_plan(client, headers, launch_id, wallets[0]["id"], side="sell", budget=None,
                                    sell_amount=1)
    assert any("holds no" in w for w in preview["preflight"]["warnings"])
    fake_rpc.tokens[wallets[0]["address"]] = 1_000_000
    _, preview, _ = await make_plan(client, headers, launch_id, wallets[0]["id"], side="sell", budget=None,
                                    sell_amount=2)
    assert any("more than this wallet can" in w for w in preview["preflight"]["warnings"])


async def test_budget_cap_counts_every_active_buy_plan_on_the_chain(client, fake_rpc, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "automation_max_budget_sol", 0.5)
    h = await signup(client)
    launch_id, _, wallets = await live_launch(client, h, fake_rpc)
    body, preview, path = await make_plan(client, h, launch_id, wallets[0]["id"], budget=0.4)
    assert not preview["preflight"]["warnings"]
    first = await confirm_plan(client, h, path, body, preview, "cap-one-key")
    assert first.status_code == 200, first.text
    # a second launch: the cap counts every active buy plan on the chain
    launch2, _, wallets2 = await live_launch(client, h, fake_rpc)
    body, preview, path = await make_plan(client, h, launch2, wallets2[0]["id"], budget=0.2)
    assert any("0.4 SOL still to spend" in w for w in preview["preflight"]["warnings"])
    assert (await confirm_plan(client, h, path, body, preview, "cap-two-key")).status_code == 400
    assert (await client.post(f"/automations/{first.json()['id']}/cancel", headers=h)).status_code == 200
    body, preview, path = await make_plan(client, h, launch2, wallets2[0]["id"], budget=0.2)
    assert not preview["preflight"]["warnings"]


async def test_scheduled_buys_and_sells_run_on_evm_chains(client, fake_rpc):
    import base64

    import httpx

    from app.chains import registry
    from app.chains.flap import FlapAdapter
    from app.deps import get_executor
    from app.main import app

    from .test_evm_wallets import ETH
    from .test_flap import PNG, FlapNode, upload_transport

    node = FlapNode("bsc")
    registry._cache["flap-sh"] = FlapAdapter("flap-sh", "bsc", node, client=httpx.AsyncClient(transport=upload_transport([])))
    make_ex = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"bsc": node})  # noqa: E731
    app.dependency_overrides[get_executor] = make_ex
    try:
        h = await signup(client)
        dev, holder = (await client.post("/wallets", json={"network": "bsc", "count": 2}, headers=h)).json()
        for w in (dev, holder):
            node.balances[w["address"].lower()] = ETH
        r = await client.post("/launches", json={"launchpad": "flap-sh", "name": "Helm Cat", "symbol": "hcat",
                                                 "dev_wallet_id": dev["id"], "dev_buy_sol": 0,
                                                 "image_b64": base64.b64encode(PNG).decode()}, headers=h)
        lid = r.json()["id"]
        pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
        await client.post(f"/launches/{lid}/deploy", json={"confirm": True, "preflight_hash": pf["preflight_hash"],
                                                           "idempotency_key": "d1"}, headers=h)
        await client.post(f"/launches/{lid}/wallets", json={"wallet_ids": [holder["id"]]}, headers=h)

        body, preview, path = await make_plan(client, h, lid, holder["id"], budget=0.002)
        assert preview["preflight"]["coin"] == "BNB" and preview["preflight"]["amount_per_order"] == 0.001
        assert not preview["preflight"]["warnings"], preview
        created = await confirm_plan(client, h, path, body, preview, "evm-buy-key")
        assert created.status_code == 200, created.text
        aid = created.json()["id"]
        for _ in range(2):
            async with get_sessionmaker()() as db:
                a = await db.get(Automation, aid)
                a.next_run_at = utcnow() - timedelta(seconds=1)
                await db.commit()
            assert await run_due_automation_once(make_ex())
        async with get_sessionmaker()() as db:
            a = await db.get(Automation, aid)
            assert a.status == "completed" and a.runs_completed == 2, a.error
            job = await db.get(Job, a.last_job_id)
            assert job.status == "confirmed"

        node.tokens[holder["address"].lower()] = 10**21
        body, preview, path = await make_plan(client, h, lid, holder["id"], budget=None, side="sell",
                                              sell_amount=500)
        assert not preview["preflight"]["warnings"], preview
        assert preview["preflight"]["wallet_token_balance"] == 1000
        created = await confirm_plan(client, h, path, body, preview, "evm-sell-key")
        aid = created.json()["id"]
        async with get_sessionmaker()() as db:
            a = await db.get(Automation, aid)
            a.next_run_at = utcnow() - timedelta(seconds=1)
            await db.commit()
        assert await run_due_automation_once(make_ex())
        async with get_sessionmaker()() as db:
            a = await db.get(Automation, aid)
            assert a.runs_completed == 1 and a.status == "scheduled", a.error
    finally:
        registry._cache.pop("flap-sh", None)
        app.dependency_overrides.pop(get_executor, None)
