import pyotp
from sqlalchemy import select

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Transaction
from app.signing import get_signer  # noqa: F401

from .conftest import SOL, signup

EXCLUDED_WORDS = ("marketplace", "volume", "nuke", "cleanup", "sniper", "generate-activity", "bundle", "block0")


async def confirm(client, h, path, body, **extra):
    """Two-step spend: preflight first, then confirm with its hash."""
    pre = await client.post(path, json=body, headers=h)
    assert pre.status_code == 200, pre.text
    data = pre.json()
    r = await client.post(path, json={**body, "confirm": True, "preflight_hash": data["preflight_hash"],
                                      "idempotency_key": extra.pop("key", "k-" + path), **extra}, headers=h)
    return data, r


async def make_wallets(client, h, n=1):
    r = await client.post("/wallets", json={"count": n, "label_prefix": "W"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


async def live_launch(client, h, fake_rpc, holders=2):
    ws = await make_wallets(client, h, holders + 1)
    dev, others = ws[0], ws[1:]
    r = await client.post("/launches", json={"name": "Test", "symbol": "tst", "dev_wallet_id": dev["id"],
                                             "dev_buy_sol": 0.5}, headers=h)
    assert r.status_code == 201, r.text
    lid = r.json()["id"]
    _, r = await confirm(client, h, f"/launches/{lid}/deploy", {})
    assert r.status_code == 200, r.text
    launch = (await client.get(f"/launches/{lid}", headers=h)).json()
    assert launch["status"] == "live" and launch["mint_address"]
    a = await client.post(f"/launches/{lid}/wallets", json={"wallet_ids": [w["id"] for w in others]}, headers=h)
    assert a.status_code == 200
    return lid, dev, others


async def test_auth_required_and_wallet_never_leaks_keys(client):
    assert (await client.get("/wallets")).status_code == 401
    h = await signup(client)
    ws = await make_wallets(client, h, 2)
    listed = (await client.get("/wallets", headers=h)).json()
    assert len(listed) == 2
    for w in ws + listed:
        assert "signer_ref" not in w and "private" not in str(w).lower()


async def test_users_cannot_see_each_others_launches(client):
    h1 = await signup(client, "one@example.com")
    h2 = await signup(client, "two@example.com")
    r = await client.post("/launches", json={"name": "Mine", "symbol": "M"}, headers=h1)
    lid = r.json()["id"]
    assert (await client.get(f"/launches/{lid}", headers=h2)).status_code == 404
    assert (await client.get("/launches", headers=h2)).json() == []


async def test_deploy_requires_matching_preflight_and_is_idempotent(client, fake_rpc):
    h = await signup(client)
    dev = (await make_wallets(client, h))[0]
    lid = (await client.post("/launches", json={"name": "T", "symbol": "T", "dev_wallet_id": dev["id"]},
                             headers=h)).json()["id"]
    path = f"/launches/{lid}/deploy"
    pre = (await client.post(path, json={}, headers=h)).json()
    assert pre["preflight"]["dev_wallet"] == dev["address"]

    bad = await client.post(path, json={"confirm": True, "preflight_hash": "nope", "idempotency_key": "x"}, headers=h)
    assert bad.status_code == 409
    no_key = await client.post(path, json={"confirm": True, "preflight_hash": pre["preflight_hash"]}, headers=h)
    assert no_key.status_code == 400

    ok = await client.post(path, json={"confirm": True, "preflight_hash": pre["preflight_hash"],
                                       "idempotency_key": "same"}, headers=h)
    assert ok.status_code == 200
    sent = len(fake_rpc.sent)
    again = await client.post(path, json={"confirm": True, "preflight_hash": pre["preflight_hash"],
                                          "idempotency_key": "same"}, headers=h)
    assert again.json()["job_id"] == ok.json()["job_id"]
    assert len(fake_rpc.sent) == sent  # replay did not send again


async def test_simulation_failure_blocks_send_and_marks_failure(client, fake_rpc):
    h = await signup(client)
    dev = (await make_wallets(client, h))[0]
    lid = (await client.post("/launches", json={"name": "T", "symbol": "T", "dev_wallet_id": dev["id"]},
                             headers=h)).json()["id"]
    fake_rpc.sim_error = "InstructionError"
    _, r = await confirm(client, h, f"/launches/{lid}/deploy", {})
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "failed" and fake_rpc.sent == []
    assert "simulation failed" in job["transactions"][0]["error"]
    assert (await client.get(f"/launches/{lid}", headers=h)).json()["status"] == "failed"


async def test_deploy_blocked_when_dev_wallet_underfunded(client, fake_rpc):
    h = await signup(client)
    dev = (await make_wallets(client, h))[0]
    fake_rpc.balances[dev["address"]] = 1_000
    lid = (await client.post("/launches", json={"name": "T", "symbol": "T", "dev_wallet_id": dev["id"],
                                                "dev_buy_sol": 1}, headers=h)).json()["id"]
    pre = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
    assert pre["preflight"]["warnings"]
    r = await client.post(f"/launches/{lid}/deploy", json={"confirm": True, "idempotency_key": "k",
                                                          "preflight_hash": pre["preflight_hash"]}, headers=h)
    assert r.status_code == 400


async def test_trade_flow_buy_and_workspace_transparency(client, fake_rpc):
    h = await signup(client)
    lid, dev, holders = await live_launch(client, h, fake_rpc)
    for w in holders:  # pretend the user's own wallets now hold 15% each
        fake_rpc.tokens[w["address"]] = int(fake_rpc.supply * 0.15)
    fake_rpc.holders = [(holders[0]["address"], int(fake_rpc.supply * 0.15)), ("someone-else", 1_000)]

    body = {"side": "buy", "wallet_ids": [w["id"] for w in holders], "amount": 0.1, "mode": "parallel"}
    pre, r = await confirm(client, h, f"/launches/{lid}/trade", body)
    assert pre["preflight"]["platform_fee"] == "none"
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "confirmed" and len(job["transactions"]) == 2

    ws = (await client.get(f"/launches/{lid}/workspace", headers=h)).json()
    t = ws["transparency"]
    assert t["controlled_pct"] == 30.0 and t["warnings"]  # 2 wallets x 15%
    assert any(x["is_yours"] for x in t["top_holders"]) and any(not x["is_yours"] for x in t["top_holders"])
    assert ws["pnl"]["confirmed_txs"] >= 2


async def test_trade_rejects_wallets_not_attached_to_launch(client, fake_rpc):
    h = await signup(client)
    lid, dev, holders = await live_launch(client, h, fake_rpc)
    stray = (await make_wallets(client, h))[0]
    r = await client.post(f"/launches/{lid}/trade", json={"side": "buy", "wallet_ids": [stray["id"]], "amount": 0.1},
                          headers=h)
    assert r.status_code == 400


async def test_sell_charges_optional_platform_fee_after_confirmation(client, fake_rpc):
    h = await signup(client)
    lid, dev, holders = await live_launch(client, h, fake_rpc, holders=1)
    s = get_settings()
    s.platform_fee_bps, s.platform_fee_wallet = 500, dev["address"]
    fake_rpc.next_delta = 1 * SOL  # the sale returns 1 SOL

    body = {"side": "sell", "wallet_ids": [holders[0]["id"]], "amount": 100, "stagger_ms": 0}
    pre, r = await confirm(client, h, f"/launches/{lid}/trade", body)
    assert pre["preflight"]["platform_fee"] == "5.00% of SOL received"  # shown before confirming
    async with get_sessionmaker()() as db:
        txs = (await db.execute(select(Transaction).where(Transaction.job_id == r.json()["job_id"]))).scalars().all()
    kinds = {t.kind: t for t in txs}
    assert kinds["sell"].status == "confirmed" and kinds["platform_fee"].sol_delta_lamports == -50_000_000


async def test_two_factor_is_required_for_spend_once_enabled(client, fake_rpc):
    h = await signup(client)
    secret = (await client.post("/auth/totp/enable", headers=h)).json()["secret"]
    assert (await client.post("/auth/totp/verify", json={"code": pyotp.TOTP(secret).now()}, headers=h)).status_code == 200

    dev = (await make_wallets(client, h))[0]
    lid = (await client.post("/launches", json={"name": "T", "symbol": "T", "dev_wallet_id": dev["id"]},
                             headers=h)).json()["id"]
    path = f"/launches/{lid}/deploy"
    pre = (await client.post(path, json={}, headers=h)).json()
    base = {"confirm": True, "preflight_hash": pre["preflight_hash"], "idempotency_key": "2fa"}
    assert (await client.post(path, json=base, headers=h)).status_code == 401
    ok = await client.post(path, json={**base, "totp": pyotp.TOTP(secret).now()}, headers=h)
    assert ok.status_code == 200

    # login now needs a code too
    r = await client.post("/auth/login", json={"email": "a@example.com", "password": "correct-horse-battery"})
    assert r.status_code == 401


async def test_fund_and_withdraw(client, fake_rpc):
    h = await signup(client)
    funder, a, b = await make_wallets(client, h, 3)
    pre, r = await confirm(client, h, "/wallets/fund", {
        "funder_wallet_id": funder["id"],
        "allocations": [{"wallet_id": a["id"], "sol": 0.1}, {"wallet_id": b["id"], "sol": 0.2}]})
    assert pre["preflight"]["total_sol"] == 0.30000000000000004 or abs(pre["preflight"]["total_sol"] - 0.3) < 1e-9
    assert (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()["status"] == "confirmed"

    dest = funder["address"]
    pre, r = await confirm(client, h, "/wallets/withdraw", {"wallet_ids": [a["id"], b["id"]], "destination": dest},
                           key="w1")
    assert (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()["status"] == "confirmed"

    bad = await client.post("/wallets/withdraw", json={"wallet_ids": [a["id"]], "destination": "not-an-address"},
                            headers=h)
    assert bad.status_code == 400


async def test_burn_and_send_build_valid_transactions(client, fake_rpc):
    h = await signup(client)
    lid, dev, holders = await live_launch(client, h, fake_rpc, holders=1)
    fake_rpc.tokens[holders[0]["address"]] = 1_000_000
    pre, r = await confirm(client, h, f"/launches/{lid}/burn", {"wallet_id": holders[0]["id"], "percent": 50})
    assert pre["preflight"]["irreversible"] is True and pre["preflight"]["tokens"] == 0.5
    assert (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()["status"] == "confirmed"

    _, r = await confirm(client, h, f"/launches/{lid}/send",
                         {"wallet_id": holders[0]["id"], "recipient": dev["address"], "percent": 10}, key="s1")
    assert (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()["status"] == "confirmed"


async def test_import_clone_archive_restore_tracker_dashboard(client, fake_rpc):
    h = await signup(client)
    mint = dev = (await make_wallets(client, h))[0]["address"]
    imp = await client.post("/launches/import", json={"mint": mint}, headers=h)
    assert imp.status_code == 201 and imp.json()["imported"] and imp.json()["status"] == "live"
    cl = await client.post("/launches/clone", json={"mint": mint}, headers=h)
    assert cl.json()["name"] == "Cloned" and cl.json()["status"] == "draft"

    lid = cl.json()["id"]
    await client.post(f"/launches/{lid}/archive", headers=h)
    assert lid not in [l["id"] for l in (await client.get("/launches", headers=h)).json()]
    assert (await client.post(f"/launches/{lid}/restore", headers=h)).json()["status"] == "draft"

    t = await client.post("/tracker", json={"address": mint, "label": "x"}, headers=h)
    assert t.status_code == 201
    assert (await client.post("/tracker", json={"address": mint}, headers=h)).status_code == 409
    assert (await client.post("/tracker", json={"address": "bad"}, headers=h)).status_code == 400

    d = (await client.get("/dashboard?period=7d", headers=h)).json()
    assert d["total_launches"] >= 1 and "series" in d
    assert len((await client.get("/activity", headers=h)).json()) > 0


async def test_planned_launchpads_are_listed_but_not_deployable(client):
    h = await signup(client)
    pads = {p["id"]: p for p in (await client.get("/launchpads")).json()}
    assert pads["pumpfun"]["status"] == "available" and pads["clanker"]["status"] == "planned"
    r = await client.post("/launches", json={"launchpad": "clanker", "name": "x", "symbol": "x"}, headers=h)
    assert r.status_code == 400
    assert (await client.post("/launches/x/lock", headers=h)).status_code == 501


async def test_rpc_outage_is_a_clear_502_with_cors_headers(client, fake_rpc):
    import httpx

    h = await signup(client)
    dev = (await make_wallets(client, h))[0]
    lid = (await client.post("/launches", json={"name": "T", "symbol": "T", "dev_wallet_id": dev["id"]},
                             headers=h)).json()["id"]

    async def down(_):
        raise httpx.ConnectError("refused")

    fake_rpc.get_balance = down
    r = await client.post(f"/launches/{lid}/deploy", json={}, headers={**h, "Origin": "http://localhost:5173"})
    assert r.status_code == 502 and "unreachable" in r.json()["detail"]
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


async def test_excluded_features_are_not_exposed(client):
    """Scope guard: the deceptive-activity features from the spec's exclusion list must not exist."""
    paths = " ".join((await client.get("/openapi.json")).json()["paths"]).lower()
    for word in EXCLUDED_WORDS:
        assert word not in paths, f"excluded feature leaked into API: {word}"
