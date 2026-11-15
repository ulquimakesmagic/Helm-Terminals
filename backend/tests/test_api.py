import pyotp
from sqlalchemy import select

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import FeeRecord, Transaction
from app.signing import get_signer  # noqa: F401

from .conftest import SOL, TEST_OPENAPI, TEST_PW, enable_2fa, signup, totp_code

EXCLUDED_WORDS = ("marketplace", "volume", "nuke", "cleanup", "sniper", "generate-activity", "bundle", "block0")


async def confirm(client, h, path, body, **extra):
    """Two-step spend: preflight first, then confirm with its hash."""
    pre = await client.post(path, json=body, headers=h)
    assert pre.status_code == 200, pre.text
    data = pre.json()
    approval = {"quote_approval": data["quote_approval"]} if data.get("quote_approval") else {}
    r = await client.post(path, json={**body, "confirm": True, "preflight_hash": data["preflight_hash"],
                                      "idempotency_key": extra.pop("key", "k-" + path),
                                      "quote_approval": data.get("quote_approval"),
                                      "launch_fee_approval": data.get("launch_fee_approval"), **extra}, headers=h)
    return data, r


async def make_wallets(client, h, n=1):
    r = await client.post("/wallets", json={"count": n, "label_prefix": "W"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


async def live_launch(client, h, fake_rpc, holders=2):
    from app.models import User, Session, LaunchFeeExemption
    from app.security import token_hash
    async with get_sessionmaker()() as db:
        session = (await db.execute(select(Session).where(Session.token_hash == token_hash(h["Authorization"][7:])))).scalar_one()
        if await db.get(LaunchFeeExemption, session.user_id) is None:
            db.add(LaunchFeeExemption(user_id=session.user_id, enabled=True, reason="Feature-test launch promotion", changed_by=session.user_id))
            await db.commit()
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
    h = await signup(client, main=False)
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


async def test_launchpad_number_options_are_type_and_range_checked(client, monkeypatch):
    from app.chains import registry

    monkeypatch.setattr(registry._cache["pumpfun"], "options_schema", lambda: {"fields": [
        {"key": "priority_fee_sol", "type": "number", "min": 0, "default": 0.0005},
    ]})
    h = await signup(client)
    base = {"launchpad": "pumpfun", "name": "Typed", "symbol": "TYP"}
    bad_type = await client.post("/launches", json={**base, "options": {"priority_fee_sol": "fast"}}, headers=h)
    assert bad_type.status_code == 400
    assert "must be a number" in bad_type.json()["detail"]
    too_low = await client.post("/launches", json={**base, "options": {"priority_fee_sol": -0.1}}, headers=h)
    assert too_low.status_code == 400
    assert "at least 0" in too_low.json()["detail"]
    valid = await client.post("/launches", json={**base, "options": {"priority_fee_sol": 0.001}}, headers=h)
    assert valid.status_code == 201
    assert valid.json()["settings"] == {"priority_fee_sol": 0.001}


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
    assert pre["preflight"]["platform_fee"].startswith("1.00%")
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


def sol_transfers(raw: bytes) -> list[tuple[str, int]]:
    """(recipient, lamports) for every System Program transfer in a sent transaction."""
    from solders.system_program import decode_transfer
    from solders.instruction import AccountMeta, Instruction
    from solders.transaction import VersionedTransaction
    from app.chains.solana import SYSTEM_PROGRAM

    msg = VersionedTransaction.from_bytes(raw).message
    keys = list(msg.account_keys)
    out = []
    for ix in msg.instructions:
        if keys[ix.program_id_index] == SYSTEM_PROGRAM and ix.data[:4] == bytes([2, 0, 0, 0]):
            a, b = keys[ix.accounts[0]], keys[ix.accounts[1]]
            d = decode_transfer(Instruction(SYSTEM_PROGRAM, ix.data, [AccountMeta(a, True, True), AccountMeta(b, False, True)]))
            out.append((str(d["to_pubkey"]), d["lamports"]))
    return out


from solders.keypair import Keypair as _Kp  # noqa: E402

PLATFORM = str(_Kp().pubkey())


async def test_sell_fee_is_priced_by_simulation_and_taken_in_the_same_transaction(client, fake_rpc):
    h = await signup(client)
    lid, dev, holders = await live_launch(client, h, fake_rpc, holders=1)
    s = get_settings()
    s.platform_fee_bps, s.platform_fee_wallet = 500, PLATFORM
    fake_rpc.next_delta = 1 * SOL  # the sale returns 1 SOL

    body = {"side": "sell", "wallet_ids": [holders[0]["id"]], "amount": 100, "stagger_ms": 0}
    sent_before = len(fake_rpc.sent)
    pre, r = await confirm(client, h, f"/launches/{lid}/trade", body)
    assert pre["preflight"]["platform_fee"].startswith("1.00% of the SOL each sell receives")  # shown up front
    assert len(fake_rpc.sent) == sent_before + 1  # one transaction: the sale and its fee together
    assert (PLATFORM, 10_000_000) in sol_transfers(fake_rpc.sent[-1])
    async with get_sessionmaker()() as db:
        fee = (await db.execute(select(FeeRecord).where(FeeRecord.job_id == r.json()["job_id"]))).scalar_one()
    assert (fee.mode, fee.status, fee.platform_lamports, fee.basis_lamports) == ("in_tx", "confirmed", 10_000_000, SOL)


async def test_buy_fee_splits_with_referrer_on_chain(client, fake_rpc):
    ha = await signup(client, "kol@example.com")
    code = (await client.post("/referrals", json={}, headers=ha)).json()["code"]
    payout = str(_Kp().pubkey())
    secret = await enable_2fa(client, ha)
    assert (await client.put("/referrals/payout", json={"address": payout, "password": TEST_PW,
                                                         "totp": await totp_code(secret)}, headers=ha)).status_code == 200

    r = await client.post("/auth/register", json={"email": "fan@example.com", "password": "correct-horse-battery",
                                                  "referral_code": code.lower()})
    assert r.status_code == 201
    tok = (await client.post("/auth/login", json={"email": "fan@example.com", "password": "correct-horse-battery"})).json()["token"]
    hb = {"Authorization": f"Bearer {tok}"}
    lid, dev, holders = await live_launch(client, hb, fake_rpc, holders=1)
    s = get_settings()
    s.platform_fee_bps, s.platform_fee_wallet = 100, PLATFORM  # 1%, of which 25% to the referrer

    body = {"side": "buy", "wallet_ids": [holders[0]["id"]], "amount": 1.0, "stagger_ms": 0}
    pre, r = await confirm(client, hb, f"/launches/{lid}/trade", body)
    assert "30% of this fee goes to the person who referred you" in pre["preflight"]["platform_fee"]
    transfers = sol_transfers(fake_rpc.sent[-1])
    assert (PLATFORM, 7_000_000) in transfers and (payout, 3_000_000) in transfers

    summary = (await client.get("/referrals", headers=ha)).json()
    assert summary["referred_users"] == 1 and summary["earned_sol"] == 0.003 and summary["owed_sol"] == 0


async def test_referral_share_is_owed_when_referrer_has_no_payout_address(client, fake_rpc):
    ha = await signup(client, "kol2@example.com")
    code = (await client.post("/referrals", json={}, headers=ha)).json()["code"]
    await client.post("/auth/register", json={"email": "fan2@example.com", "password": "correct-horse-battery",
                                              "referral_code": code})
    tok = (await client.post("/auth/login", json={"email": "fan2@example.com", "password": "correct-horse-battery"})).json()["token"]
    hb = {"Authorization": f"Bearer {tok}"}
    lid, dev, holders = await live_launch(client, hb, fake_rpc, holders=1)
    s = get_settings()
    s.platform_fee_bps, s.platform_fee_wallet = 100, PLATFORM
    await confirm(client, hb, f"/launches/{lid}/trade", {"side": "buy", "wallet_ids": [holders[0]["id"]], "amount": 1.0})
    fees = [t for t in sol_transfers(fake_rpc.sent[-1]) if t[1] > 1]  # skip the fake adapter's 1-lamport self-transfer
    assert fees == [(PLATFORM, 10_000_000)]  # the referrer's share is held for them
    assert (await client.get("/referrals", headers=ha)).json()["owed_sol"] == 0.003


async def test_failed_trade_charges_no_fee(client, fake_rpc):
    h = await signup(client)
    lid, dev, holders = await live_launch(client, h, fake_rpc, holders=1)
    s = get_settings()
    s.platform_fee_bps, s.platform_fee_wallet = 100, PLATFORM
    fake_rpc.sim_error = "slippage exceeded"
    pre, r = await confirm(client, h, f"/launches/{lid}/trade", {"side": "buy", "wallet_ids": [holders[0]["id"]], "amount": 1.0})
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(FeeRecord))).scalars().all() == []


async def test_bad_referral_code_is_rejected(client):
    r = await client.post("/auth/register", json={"email": "x@example.com", "password": "correct-horse-battery",
                                                  "referral_code": "NOPE1234"})
    assert r.status_code == 400


async def test_referral_codes_are_capped_per_user(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "referral_max_codes_per_user", 1)
    h = await signup(client, "referral-cap@example.com", main=False)
    assert (await client.post("/referrals", json={}, headers=h)).status_code == 201
    assert (await client.post("/referrals", json={}, headers=h)).status_code == 429


async def test_referral_payout_cannot_target_a_known_referred_wallet(client):
    from app.models import AdminAudit, User, Wallet

    referrer = await signup(client, "referrer@example.com", main=False)
    code = (await client.post("/referrals", json={}, headers=referrer)).json()["code"]
    created = await client.post("/auth/register", json={"email": "referred@example.com",
                                                         "password": TEST_PW,
                                                         "referral_code": code})
    assert created.status_code == 201
    secret = await enable_2fa(client, referrer)
    async with get_sessionmaker()() as db:
        referred_id = (await db.execute(select(User.id).where(User.email == "referred@example.com"))).scalar_one()
        referred_wallet = (await db.execute(select(Wallet).where(
            Wallet.network == "solana", Wallet.user_id == referred_id))).scalars().first()
        address = referred_wallet.address
    response = await client.put("/referrals/payout", json={"address": address, "password": TEST_PW,
                                                            "totp": await totp_code(secret)}, headers=referrer)
    assert response.status_code == 400 and "referred" in response.json()["detail"]
    async with get_sessionmaker()() as db:
        audit_event = (await db.execute(select(AdminAudit).where(
            AdminAudit.action == "referral.self_referral_suspected"))).scalar_one_or_none()
        assert audit_event is not None and address not in str(audit_event.changes)


async def test_two_factor_policy_only_gates_assets_leaving_the_wallets(client, fake_rpc):
    h = await signup(client)
    funder, other = await make_wallets(client, h, 2)
    body = {"wallet_ids": [funder["id"]], "destination": other["address"]}

    # Withdrawals need 2FA to be set up first: the preflight says so and confirming is refused.
    pre = (await client.post("/wallets/withdraw", json=body, headers=h)).json()
    assert pre["requires_2fa"] is True and any("Enable 2FA" in w for w in pre["preflight"]["warnings"])
    refused = await client.post("/wallets/withdraw", json={**body, "confirm": True, "idempotency_key": "a",
                                                          "preflight_hash": pre["preflight_hash"]}, headers=h)
    assert refused.status_code == 400

    secret = await enable_2fa(client, h)
    pre = (await client.post("/wallets/withdraw", json=body, headers=h)).json()
    base = {**body, "confirm": True, "preflight_hash": pre["preflight_hash"], "idempotency_key": "b"}
    assert (await client.post("/wallets/withdraw", json=base, headers=h)).status_code == 401  # no code
    assert (await client.post("/wallets/withdraw", json={**base, "totp": "000000"}, headers=h)).status_code == 401
    ok = await client.post("/wallets/withdraw", json={**base, "totp": await totp_code(secret)}, headers=h)
    assert ok.status_code == 200

    # Everything inside your own wallets (deploy, trades, ...) does not ask for a code, even with 2FA on.
    lid = (await client.post("/launches", json={"name": "T", "symbol": "T", "dev_wallet_id": funder["id"]},
                             headers=h)).json()["id"]
    pre = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
    assert pre["requires_2fa"] is False
    r = await client.post(f"/launches/{lid}/deploy", json={"confirm": True, "idempotency_key": "d",
                                                          "preflight_hash": pre["preflight_hash"]}, headers=h)
    assert r.status_code == 200

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

    secret = await enable_2fa(client, h)
    dest = funder["address"]

    # Fixed amount per wallet: exactly amount_sol leaves each wallet, not the whole balance.
    pre, r = await confirm(client, h, "/wallets/withdraw",
                           {"wallet_ids": [a["id"], b["id"]], "destination": dest, "all": False, "amount_sol": 0.03},
                           key="w2", totp=await totp_code(secret))
    assert [f["sol"] for f in pre["preflight"]["from"]] == [0.03, 0.03]
    assert (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()["status"] == "confirmed"
    assert all(sol_transfers(raw) == [(dest, 30_000_000)] for raw in fake_rpc.sent[-2:])

    # A fixed amount above a balance previews with a per-wallet warning and refuses to confirm.
    pre, r = await confirm(client, h, "/wallets/withdraw",
                           {"wallet_ids": [a["id"]], "destination": dest, "all": False, "amount_sol": 500},
                           key="w3", totp=await totp_code(secret))
    assert any("too little SOL" in w for w in pre["preflight"]["warnings"])
    assert r.status_code == 400 and "too little SOL" in r.json()["detail"]

    pre, r = await confirm(client, h, "/wallets/withdraw", {"wallet_ids": [a["id"], b["id"]], "destination": dest},
                           key="w1", totp=await totp_code(secret))
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

    secret = await enable_2fa(client, h)
    _, r = await confirm(client, h, f"/launches/{lid}/send",
                         {"wallet_id": holders[0]["id"], "recipient": dev["address"], "percent": 10}, key="s1",
                         totp=await totp_code(secret))
    assert (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()["status"] == "confirmed"


async def test_import_clone_archive_restore_tracker_dashboard(client, fake_rpc):
    h = await signup(client)
    mint = dev = (await make_wallets(client, h))[0]["address"]
    # Import and clone both copy the token's metadata into a relaunch draft; neither pretends the token is launched.
    imp = await client.post("/launches/import", json={"mint": mint}, headers=h)
    assert imp.status_code == 201, imp.text
    assert imp.json()["status"] == "draft" and not imp.json()["imported"] and imp.json()["mint_address"] is None
    assert imp.json()["name"] == "Cloned" and imp.json()["symbol"] == "CLN" and imp.json()["description"] == "d"
    cl = await client.post("/launches/clone", json={"mint": mint}, headers=h)
    assert cl.json()["name"] == "Cloned" and cl.json()["status"] == "draft"
    # an unused draft can be deleted outright
    assert (await client.delete(f"/launches/{imp.json()['id']}", headers=h)).json() == {"deleted": True}
    assert (await client.get(f"/launches/{imp.json()['id']}", headers=h)).status_code == 404

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
    assert pads["pumpfun"]["status"] == "available" and pads["bankr"]["status"] == "planned"
    r = await client.post("/launches", json={"launchpad": "bankr", "name": "x", "symbol": "x"}, headers=h)
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


async def test_me_reports_email_and_2fa(client):
    await client.post("/auth/register", json={"email": "me@example.com", "password": "correct-horse-1"})
    r = await client.post("/auth/login", json={"email": "me@example.com", "password": "correct-horse-1"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    me = (await client.get("/auth/me", headers=h)).json()
    assert me["email"] == "me@example.com" and me["totp_enabled"] is False


async def test_watch_and_tracker_accept_evm_addresses(client):
    h = await signup(client, "evm@example.com")
    evm = "0x" + "ab" * 20
    r = await client.post("/wallets/watch", json={"network": "base", "address": evm}, headers=h)
    assert r.status_code == 201 and r.json()["network"] == "base"
    r = await client.post("/tracker", json={"network": "bsc", "address": evm, "label": "dev"}, headers=h)
    assert r.status_code == 201 and r.json()["network"] == "bsc"
    r = await client.post("/wallets/watch", json={"network": "robinhood", "address": "0x" + "cd" * 20}, headers=h)
    assert r.status_code == 201 and r.json()["network"] == "robinhood"
    pads = {p["id"]: p["network"] for p in (await client.get("/launchpads")).json()}
    assert pads["pons"] == "robinhood" and pads["flap-sh-robinhood"] == "robinhood"
    bad = await client.post("/tracker", json={"network": "base", "address": "not-an-address"}, headers=h)
    assert bad.status_code == 400
    sol_on_evm = await client.post("/wallets/watch", json={"network": "ethereum", "address": "11111111111111111111111111111111"}, headers=h)
    assert sol_on_evm.status_code == 400


async def test_launchpads_list_shows_letsbonk_live_and_bags_needing_a_key(client):
    pads = {p["id"]: p for p in (await client.get("/launchpads")).json()}
    assert pads["letsbonk"]["status"] == "available" and pads["letsbonk"]["network"] == "solana"
    assert pads["bags"]["status"] == "setup_required" and "BAGS_API_KEY" in pads["bags"]["reason"]
    h = await signup(client, "bagsless@example.com")
    r = await client.post("/launches", json={"launchpad": "bags", "name": "B", "symbol": "B"}, headers=h)
    assert r.status_code == 400 and "API key" in r.json()["detail"]


async def test_failed_setup_transaction_stops_the_launch(client, fake_rpc):
    """If a launchpad's setup transaction (like Bags' fee-share config) fails, the launch itself is never sent."""
    from app.chains import registry
    from .conftest import FakeAdapter

    class WithSetup(FakeAdapter):
        async def build_create_tx(self, req):
            built = await super().build_create_tx(req)
            built.pre = [await self._simple_raw(req.dev_pubkey)]
            return built

        async def _simple_raw(self, who):
            from solders.hash import Hash
            from solders.pubkey import Pubkey
            from app.chains.solana import build_unsigned, sol_transfer_ix
            p = Pubkey.from_string(who)
            return build_unsigned(p, [sol_transfer_ix(p, p, 2)], str(Hash.default()))

    registry._cache["pumpfun"] = WithSetup()
    calls = {"n": 0}
    real = fake_rpc.simulate

    async def fail_first(tx):
        calls["n"] += 1
        return "config failed" if calls["n"] == 1 else await real(tx)

    fake_rpc.simulate = fail_first
    h = await signup(client, "setup@example.com")
    dev = (await make_wallets(client, h))[0]
    lid = (await client.post("/launches", json={"name": "S", "symbol": "S", "dev_wallet_id": dev["id"]}, headers=h)).json()["id"]
    _, r = await confirm(client, h, f"/launches/{lid}/deploy", {})
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "failed" and [t["kind"] for t in job["transactions"]] == ["deploy_setup"]
    assert fake_rpc.sent == []  # nothing reached the chain
    assert (await client.get(f"/launches/{lid}", headers=h)).json()["status"] == "failed"


async def test_pair_token_launch_needs_bags_eligibility_and_a_supported_token(client, fake_rpc):
    from app.chains import registry
    from .conftest import FakeAdapter

    NVDA = str(__import__("solders.keypair", fromlist=["Keypair"]).Keypair().pubkey())

    class FakeBags(FakeAdapter):
        id = "bags"

        async def supported_quotes(self):
            return [{"mint": NVDA, "symbol": "NVDAx", "name": "NVIDIA xStock", "decimals": 8, "image": None}]

        async def build_create_tx(self, req):
            built = await super().build_create_tx(req)
            self.seen_options = req.options
            return built

    fake = FakeBags()
    registry._cache["bags"] = fake
    s = get_settings()
    s.bags_api_key = TEST_OPENAPI
    try:
        assets = (await client.get("/launchpads/bags/quote-assets")).json()
        assert [a["symbol"] for a in assets["assets"]] == ["SOL", "NVDAx"] and "US" in assets["notice"]

        h = await signup(client, "stocks@example.com")
        dev = (await make_wallets(client, h))[0]
        base = {"launchpad": "bags", "name": "Chip Cat", "symbol": "CHIP", "image_b64": "iVBORw0KGgo=",
                "dev_wallet_id": dev["id"], "quote_mint": NVDA}
        assert (await client.post("/launches", json=base, headers=h)).status_code == 400  # not confirmed eligible
        assert (await client.post("/launches", json={**base, "eligibility_confirmed": True, "quote_mint": "So1nope"},
                                  headers=h)).status_code == 400  # unsupported pair
        assert (await client.post("/launches", json={**base, "launchpad": "pumpfun", "eligibility_confirmed": True},
                                  headers=h)).status_code == 400  # only Bags pairs with other tokens
        r = await client.post("/launches", json={**base, "eligibility_confirmed": True}, headers=h)
        assert r.status_code == 201 and r.json()["quote"]["symbol"] == "NVDAx"

        pre, d = await confirm(client, h, f"/launches/{r.json()['id']}/deploy", {})
        assert pre["preflight"]["paired_with"].startswith("NVDAx") and "no bonding curve" in pre["preflight"]["pool"]
        assert fake.seen_options["quote"]["mint"] == NVDA  # the adapter was told what to pair with
    finally:
        s.bags_api_key = ""
        registry._cache.pop("bags", None)


async def test_import_explains_rpc_outage_vs_unknown_token(client, fake_rpc):
    from app.chains.solana import RPCError

    h = await signup(client, "importer@example.com")
    mint = str(__import__("solders.keypair", fromlist=["Keypair"]).Keypair().pubkey())

    async def unknown(m):
        raise RPCError("getAsset: Asset Not Found")

    fake_rpc.get_asset = unknown
    r = await client.post("/launches/import", json={"mint": mint}, headers=h)
    assert r.status_code == 404 and "no token found" in r.json()["detail"]

    async def blocked(m):
        raise ConnectionError("403 Forbidden")

    fake_rpc.get_asset = blocked
    r = await client.post("/launches/import", json={"mint": mint}, headers=h)
    assert r.status_code == 502 and "DAS-capable" in r.json()["detail"] and "403" not in r.json()["detail"]
    assert (await client.post("/launches/import", json={"mint": "nope"}, headers=h)).status_code == 400


async def test_clone_copies_image_and_socials_but_refuses_private_hosts(monkeypatch):
    import httpx

    from app.services import token_meta as tm

    class Rpc:
        async def get_asset(self, m):
            return {"content": {"metadata": {"name": "Helm Cat", "symbol": "HCAT"},
                                "json_uri": "https://meta.example/hcat.json", "links": {}}}

    class Ex:
        rpc = Rpc()

    def handler(req):
        if req.url.path == "/hcat.json":
            return httpx.Response(200, json={"description": "gm", "image": "ipfs://QmCat", "twitter": "https://x.com/hcat",
                                             "website": "https://hcat.example", "telegram": "javascript:alert(1)"})
        if req.url.host == "ipfs.io":
            return httpx.Response(200, content=b"\x89PNG....", headers={"content-type": "image/png"})
        raise AssertionError(req.url)

    async def public(url):
        return "internal" not in url

    monkeypatch.setattr(tm, "_public_host", public)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    m = await tm.source_metadata(Ex(), "solana", "mint", client=client)
    assert m["name"] == "Helm Cat" and m["description"] == "gm" and m["image"].startswith(b"\x89PNG")
    assert m["socials"] == {"twitter": "https://x.com/hcat", "website": "https://hcat.example"}  # the javascript: link is dropped
    assert set(m["copied"]) >= {"name", "symbol", "description", "image", "twitter", "website"}

    class RpcPrivate(Rpc):
        async def get_asset(self, m):
            return {"content": {"metadata": {"name": "X", "symbol": "X"}, "json_uri": "https://internal.host/meta.json",
                                "links": {"image": "http://plain.example/i.png"}}}

    Ex.rpc = RpcPrivate()
    m = await tm.source_metadata(Ex(), "solana", "mint", client=client)  # nothing fetched: private host, non-https image
    assert m["image"] is None and m["socials"] == {} and m["name"] == "X"


async def test_delete_is_only_for_unused_drafts(client, fake_rpc):
    h = await signup(client, "deleter@example.com")
    lid, dev, holders = await live_launch(client, h, fake_rpc)
    r = await client.delete(f"/launches/{lid}", headers=h)
    assert r.status_code == 409 and "archive" in r.json()["detail"]  # deployed: archive, not delete
    d = (await client.post("/launches", json={"name": "D", "symbol": "D", "dev_wallet_id": dev["id"]}, headers=h)).json()
    h2 = await signup(client, "other@example.com")
    assert (await client.delete(f"/launches/{d['id']}", headers=h2)).status_code == 404  # not yours
    assert (await client.delete(f"/launches/{d['id']}", headers=h)).json() == {"deleted": True}


async def test_evm_watch_wallet_balance_is_in_its_own_coin(client, fake_rpc, monkeypatch):
    from app.deps import get_executor
    from app.main import app
    from app.services.executor import Executor
    from app.signing import get_signer

    class FakeEvm:
        async def get_balance(self, addr):
            return 2 * 10**18 + 5 * 10**17

        async def erc20_balance(self, token, owner):
            return 40 * 10**6  # 40 USDG

    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"robinhood": FakeEvm()})
    try:
        h = await signup(client, "evmbal@example.com")
        await client.post("/wallets/watch", json={"network": "robinhood", "address": "0x" + "ab" * 20}, headers=h)
        ws = (await client.get("/wallets?with_balances=true", headers=h)).json()
        rh = next(w for w in ws if w["network"] == "robinhood")
        assert rh["balance"] == 2.5 and rh["stable"] == 40 and rh["stable_symbol"] == "USDG"
        assert rh["usd"] == 2.5 * 3000 + 40
    finally:
        app.dependency_overrides.pop(get_executor, None)


async def test_health_reports_rpc_setup_per_chain(client):
    health = (await client.get("/health")).json()
    assert health == {"ok": True, "build": "unknown"}
    assert "evm_rpc" not in health and "signer" not in health and "env" not in health
    info = (await client.get("/public-info")).json()
    assert info["test_signer"] is True and set(info) == {"test_signer", "capabilities", "pricing"}
    assert info["pricing"]["launch_usd"] == 20 and info["pricing"]["trade_bps"] == 100
    assert "rpc" not in str(info).lower() and "url" not in str(info).lower()  # no configuration detail
