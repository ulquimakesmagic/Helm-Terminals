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


async def make_plan(client, headers, launch_id, wallet_ids, **overrides):
    body = {
        "wallet_ids": wallet_ids,
        "amount_sol": 0.1,
        "total_orders": 2,
        "interval_minutes": 5,
        "start_in_minutes": 1,
        "slippage_pct": 5,
        **overrides,
    }
    path = f"/launches/{launch_id}/automations/dca"
    preview = await client.post(path, json=body, headers=headers)
    assert preview.status_code == 200, preview.text
    return body, preview.json(), path


async def test_scheduled_buy_preview_is_bounded_and_worker_records_each_tranche(client, fake_rpc):
    headers = await signup(client)
    launch_id, _, wallets = await live_launch(client, headers, fake_rpc)
    sent_before_preview = len(fake_rpc.sent)
    body, preview, path = await make_plan(client, headers, launch_id, [w["id"] for w in wallets])

    assert preview["preflight"]["strategy"] == "finite scheduled buys"
    assert preview["preflight"]["planned_trade_sol"] == 0.4
    assert len(fake_rpc.sent) == sent_before_preview

    created = await client.post(path, json={
        **body, "confirm": True, "preflight_hash": preview["preflight_hash"], "idempotency_key": "schedule-one",
    }, headers=headers)
    assert created.status_code == 200, created.text
    automation_id = created.json()["id"]
    assert created.json()["status"] == "scheduled"
    assert (await client.get("/automations", headers=headers)).json()[0]["id"] == automation_id

    async with get_sessionmaker()() as db:
        automation = await db.get(Automation, automation_id)
        automation.next_run_at = utcnow() - timedelta(seconds=1)
        await db.commit()

    executor = Executor(fake_rpc, get_signer())
    assert await run_due_automation_once(executor)
    async with get_sessionmaker()() as db:
        automation = await db.get(Automation, automation_id)
        assert automation.status == "scheduled"
        assert automation.runs_completed == 1
        automation.next_run_at = utcnow() - timedelta(seconds=1)
        await db.commit()

    assert await run_due_automation_once(executor)
    async with get_sessionmaker()() as db:
        automation = await db.get(Automation, automation_id)
        assert automation.status == "completed"
        assert automation.runs_completed == 2
    assert len(fake_rpc.sent) == sent_before_preview + 4  # two selected wallets per tranche; buys only


async def test_scheduled_buy_requires_live_owned_attached_solana_wallets_and_caps_budget(client, fake_rpc):
    h1 = await signup(client)
    launch_id, _, wallets = await live_launch(client, h1, fake_rpc)
    _, preview, path = await make_plan(client, h1, launch_id, [w["id"] for w in wallets], amount_sol=1, total_orders=6)
    assert any("budget limit" in w for w in preview["preflight"]["warnings"])

    h2 = await signup(client, "second@example.com")
    other = await client.post(path, json={
        "wallet_ids": [wallets[0]["id"]], "amount_sol": 0.1, "total_orders": 2, "interval_minutes": 5,
    }, headers=h2)
    assert other.status_code == 404


async def test_scheduled_buy_idempotency_returns_same_plan_and_rejects_key_reuse(client, fake_rpc):
    headers = await signup(client)
    launch_id, _, wallets = await live_launch(client, headers, fake_rpc)
    body, preview, path = await make_plan(client, headers, launch_id, [wallets[0]["id"]])
    confirmation = {
        **body, "confirm": True, "preflight_hash": preview["preflight_hash"], "idempotency_key": "schedule-replay",
    }

    first = await client.post(path, json=confirmation, headers=headers)
    replay = await client.post(path, json=confirmation, headers=headers)
    assert first.status_code == replay.status_code == 200
    assert first.json()["id"] == replay.json()["id"]

    changed_body = {**body, "amount_sol": 0.2}
    changed_preview = await client.post(path, json=changed_body, headers=headers)
    changed = await client.post(path, json={
        **changed_body, "confirm": True, "preflight_hash": changed_preview.json()["preflight_hash"],
        "idempotency_key": "schedule-replay",
    }, headers=headers)
    assert changed.status_code == 409


async def test_scheduled_sells_are_finite_staged_exits_and_stop_when_position_is_empty(client, fake_rpc):
    headers = await signup(client)
    launch_id, _, wallets = await live_launch(client, headers, fake_rpc)
    sent_before = len(fake_rpc.sent)
    for wallet in wallets:
        fake_rpc.tokens[wallet["address"]] = 1_000_000
    body = {
        "side": "sell", "wallet_ids": [w["id"] for w in wallets], "sell_percent": 50,
        "total_orders": 2, "interval_minutes": 5, "start_in_minutes": 1, "slippage_pct": 5,
    }
    path = f"/launches/{launch_id}/automations"
    preview_response = await client.post(path, json=body, headers=headers)
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["preflight"]["kind"] == "scheduled_sell"
    assert preview["preflight"]["maximum_position_exit_percent"] == 75
    assert preview["preflight"]["wallets"][0]["token_balance"] == 1
    assert len(fake_rpc.sent) == sent_before

    created = await client.post(path, json={
        **body, "confirm": True, "preflight_hash": preview["preflight_hash"], "idempotency_key": "sell-schedule-one",
    }, headers=headers)
    assert created.status_code == 200, created.text
    automation_id = created.json()["id"]

    async with get_sessionmaker()() as db:
        automation = await db.get(Automation, automation_id)
        automation.next_run_at = utcnow() - timedelta(seconds=1)
        await db.commit()

    executor = Executor(fake_rpc, get_signer())
    assert await run_due_automation_once(executor)
    async with get_sessionmaker()() as db:
        automation = await db.get(Automation, automation_id)
        assert automation.side == "sell"
        assert automation.status == "scheduled"
        assert automation.runs_completed == 1
        job = await db.get(Job, automation.last_job_id)
        assert job.kind == "scheduled_sell"
        assert job.params["amount"] == "50%"
        automation.next_run_at = utcnow() - timedelta(seconds=1)
        await db.commit()

    assert await run_due_automation_once(executor)
    async with get_sessionmaker()() as db:
        automation = await db.get(Automation, automation_id)
        assert automation.status == "completed"
        assert automation.runs_completed == 2
    assert len(fake_rpc.sent) == sent_before + 4


async def test_sell_schedule_rejects_wallet_without_tokens(client, fake_rpc):
    headers = await signup(client)
    launch_id, _, wallets = await live_launch(client, headers, fake_rpc)
    response = await client.post(f"/launches/{launch_id}/automations", json={
        "side": "sell", "wallet_ids": [wallets[0]["id"]], "sell_percent": 25,
        "total_orders": 2, "interval_minutes": 5,
    }, headers=headers)
    assert response.status_code == 200
    assert any("no tokens to sell" in warning for warning in response.json()["preflight"]["warnings"])


async def confirm_plan(client, headers, path, body, preview, key):
    return await client.post(path, json={**body, "confirm": True, "preflight_hash": preview["preflight_hash"],
                                         "idempotency_key": key}, headers=headers)


async def test_budget_cap_counts_every_active_buy_plan_on_the_chain(client, fake_rpc, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "automation_max_budget_sol", 0.5)
    h = await signup(client)
    launch_id, _, wallets = await live_launch(client, h, fake_rpc)
    ids = [w["id"] for w in wallets]
    body, preview, path = await make_plan(client, h, launch_id, ids)  # 0.1 x 2 wallets x 2 orders = 0.4
    assert not preview["preflight"]["warnings"]
    first = await confirm_plan(client, h, path, body, preview, "cap-one-key")
    assert first.status_code == 200, first.text

    body, preview, path = await make_plan(client, h, launch_id, ids)
    warning = " ".join(preview["preflight"]["warnings"])
    assert "other active scheduled buys" in warning and "0.4 SOL still to spend" in warning
    assert (await confirm_plan(client, h, path, body, preview, "cap-two-key")).status_code == 400

    # Sell plans don't count against the buy budget, and a cancelled plan frees its budget.
    assert (await client.post(f"/automations/{first.json()['id']}/cancel", headers=h)).status_code == 200
    body, preview, path = await make_plan(client, h, launch_id, ids)
    assert not preview["preflight"]["warnings"]
    listed = (await client.get("/automations", headers=h)).json()
    assert listed[0]["remaining_trade_sol"] == 0.4 and listed[0]["coin"] == "SOL"


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

        body, preview, path = await make_plan(client, h, lid, [holder["id"]], amount_sol=0.001)
        assert preview["preflight"]["coin"] == "BNB" and preview["preflight"]["amount_unit"] == "BNB per wallet per order"
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
        body, preview, path = await make_plan(client, h, lid, [holder["id"]], amount_sol=None, side="sell",
                                              sell_percent=50)
        assert not preview["preflight"]["warnings"], preview
        assert preview["preflight"]["wallets"][0]["token_balance"] == 1000
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
