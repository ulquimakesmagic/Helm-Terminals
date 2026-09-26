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
