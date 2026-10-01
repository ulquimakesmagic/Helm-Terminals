"""Admin dashboard and treasury revenue: superadmin identity, role limits, step-up, address validation, fee routing,
the revenue ledger (received vs pending vs claimable), creator-reward shares, and privacy of the user views."""

import json

from solders.keypair import Keypair
from sqlalchemy import select, update

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import AdminAudit, AdminRole, FeeRecord, RevenueEvent, Session, User
from app.services import platform_config as pc
from app.services.admin import SUPERADMIN_EMAIL

from .conftest import SOL, enable_2fa, signup, totp_code
from .test_api import confirm, live_launch, sol_transfers

PW = "correct-horse-battery"
EVM_TREASURY = "0x52908400098527886E0F7030069857D2E4169EE7"  # a valid EIP-55 checksummed address


async def boss(client):
    """The designated superadmin, signed in with 2FA on. Returns (headers, 2FA secret)."""
    h = await signup(client, SUPERADMIN_EMAIL, main=False)
    get_settings().superadmin_user_id = await uid_of(SUPERADMIN_EMAIL)
    await client.get("/auth/me", headers=h)  # bind only after the test's configured user ID is in place
    secret = await enable_2fa(client, h)
    return h, secret


async def proof(secret):
    return {"password": PW, "totp": await totp_code(secret)}


async def uid_of(email):
    async with get_sessionmaker()() as db:
        return (await db.execute(select(User.id).where(User.email == email))).scalar_one()


ADMIN_GETS = ["/admin/me", "/admin/config", "/admin/revenue/summary", "/admin/revenue/ledger", "/admin/treasury", "/admin/settings",
              "/admin/users", "/admin/roles", "/admin/audit", "/admin/metrics"]


# --- superadmin identity ---------------------------------------------------------------------------------


async def test_superadmin_needs_a_configured_user_id_and_2fa(client):
    h = await signup(client, "  MalikSucess@Gmail.com ".strip(), main=False)  # stored lowercased
    assert (await client.get("/admin/me", headers=h)).status_code == 403  # no 2FA yet
    uid = await uid_of(SUPERADMIN_EMAIL)
    get_settings().superadmin_user_id = uid
    await enable_2fa(client, h)
    r = await client.get("/admin/me", headers=h)
    assert r.status_code == 200 and r.json()["role"] == "superadmin"
    assert (await client.get("/auth/me", headers=h)).json()["admin_role"] == "superadmin"
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(AdminAudit).where(AdminAudit.action == "superadmin.bound"))).scalar_one()


async def test_admin_configuration_diagnostics_return_presence_only(client):
    h, _ = await boss(client)
    response = await client.get("/admin/config", headers=h)
    assert response.status_code == 200
    data = response.json()
    assert data["superadmin_id_configured"] is True
    assert "solana" in data["rpc_configured"]
    assert "turnkey_api_private_key" not in response.text
    assert "https://" not in response.text


async def test_designated_email_cannot_bind_superadmin_without_a_pin(client):
    h = await signup(client, SUPERADMIN_EMAIL, main=False)
    await enable_2fa(client, h)
    assert (await client.get("/admin/me", headers=h)).status_code == 403
    assert (await client.get("/auth/me", headers=h)).json()["admin_role"] is None
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(AdminRole).where(AdminRole.role == "superadmin"))).scalar_one_or_none() is None


async def test_nobody_else_can_reach_any_admin_route(client):
    await boss(client)
    h = await signup(client, "user@example.com", main=False)
    await enable_2fa(client, h)
    for path in ADMIN_GETS:
        assert (await client.get(path, headers=h)).status_code == 403, path
        assert (await client.get(path)).status_code == 401, path
    body = {"address": EVM_TREASURY, "confirm": {"password": PW, "totp": "000000"}}
    assert (await client.put("/admin/treasury/evm", json=body, headers=h)).status_code == 403
    assert (await client.put("/admin/settings/route_fee_bps", json={"bps": 1, "confirm": body["confirm"]},
                             headers=h)).status_code == 403
    assert (await client.get("/auth/me", headers=h)).json()["admin_role"] is None


async def test_the_role_stays_with_the_original_account_id(client):
    h, _ = await boss(client)
    first = await uid_of(SUPERADMIN_EMAIL)
    # Even if the bound account's email changed in the database, a new account registering the email gets nothing.
    async with get_sessionmaker()() as db:
        await db.execute(update(User).where(User.id == first).values(email="renamed@example.com"))
        await db.commit()
    h2 = await signup(client, SUPERADMIN_EMAIL, main=False)
    await enable_2fa(client, h2)
    assert (await client.get("/admin/me", headers=h2)).status_code == 403
    r = await client.get("/admin/me", headers=h)
    assert r.status_code == 200 and r.json()["id"] == first and r.json()["role"] == "superadmin", r.text


async def test_a_pinned_user_id_must_match(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "superadmin_user_id", "someone-else")
    h = await signup(client, SUPERADMIN_EMAIL, main=False)
    await enable_2fa(client, h)
    assert (await client.get("/admin/me", headers=h)).status_code == 403


async def test_the_telegram_bot_session_never_gets_admin(client):
    from datetime import timedelta

    from app.models import utcnow
    from app.security import new_token, token_hash

    await boss(client)
    uid = await uid_of(SUPERADMIN_EMAIL)
    tok = new_token()
    async with get_sessionmaker()() as db:
        db.add(Session(user_id=uid, token_hash=token_hash(tok), channel="bot",
                       expires_at=utcnow() + timedelta(minutes=5)))
        await db.commit()
    assert (await client.get("/admin/me", headers={"Authorization": f"Bearer {tok}"})).status_code == 403


# --- roles -----------------------------------------------------------------------------------------------


async def test_a_viewer_can_look_but_not_change_anything(client):
    hb, sb = await boss(client)
    hv = await signup(client, "viewer@example.com", main=False)
    sv = await enable_2fa(client, hv)
    vid = await uid_of("viewer@example.com")
    r = await client.post(f"/admin/roles/{vid}/viewer", json={"reason": "support staff", "confirm": await proof(sb)},
                          headers=hb)
    assert r.status_code == 200
    for path in ADMIN_GETS:
        assert (await client.get(path, headers=hv)).status_code == 200, path
    step = await proof(sv)
    assert (await client.put("/admin/treasury/evm", json={"address": EVM_TREASURY, "confirm": step},
                             headers=hv)).status_code == 403
    assert (await client.put("/admin/settings/route_fee_bps", json={"bps": 0, "confirm": step},
                             headers=hv)).status_code == 403
    assert (await client.post(f"/admin/roles/{vid}/viewer", json={"reason": "promote me", "confirm": step},
                              headers=hv)).status_code == 403
    assert (await client.post("/admin/revenue/reconcile", json={"ids": ["x"]}, headers=hv)).status_code == 403
    bid = await uid_of(SUPERADMIN_EMAIL)
    assert (await client.post(f"/admin/users/{bid}/suspend", json={"reason": "takeover", "confirm": step},
                              headers=hv)).status_code == 403
    # emails are masked for viewers
    emails = [u["email"] for u in (await client.get("/admin/users", headers=hv)).json()["items"]]
    assert emails and all("***@" in e for e in emails)


async def test_the_superadmin_role_cant_be_granted_or_taken(client):
    hb, sb = await boss(client)
    bid = await uid_of(SUPERADMIN_EMAIL)
    for path in (f"/admin/roles/{bid}/revoke", f"/admin/users/{bid}/suspend", f"/admin/roles/{bid}/viewer"):
        r = await client.post(path, json={"reason": "just testing", "confirm": await proof(sb)}, headers=hb)
        assert r.status_code == 400, path
    assert (await client.post("/admin/roles/x/superadmin", json={}, headers=hb)).status_code in (404, 405)
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(AdminRole.role).where(AdminRole.user_id == bid))).scalar_one() == "superadmin"


# --- step-up and audit -----------------------------------------------------------------------------------


async def test_sensitive_changes_need_password_and_a_fresh_2fa_code(client):
    hb, sb = await boss(client)
    bad_pw = {"address": EVM_TREASURY, "confirm": {"password": "wrong-password", "totp": await totp_code(sb)}}
    assert (await client.put("/admin/treasury/evm", json=bad_pw, headers=hb)).status_code == 400
    bad_code = {"address": EVM_TREASURY, "confirm": {"password": PW, "totp": "000000"}}
    assert (await client.put("/admin/treasury/evm", json=bad_code, headers=hb)).status_code == 401
    assert (await client.put("/admin/treasury/evm", json={"address": EVM_TREASURY}, headers=hb)).status_code == 422
    ok = await client.put("/admin/treasury/evm", json={"address": EVM_TREASURY.lower(), "confirm": await proof(sb)},
                          headers=hb)
    assert ok.status_code == 200 and ok.json()["effective"] == EVM_TREASURY  # stored checksummed
    log = (await client.get("/admin/audit", headers=hb)).json()["items"]
    change = next(e for e in log if e["action"] == "treasury.set")
    assert change["changes"]["address"] == [None, EVM_TREASURY] and "ip" in change["context"]
    assert any(e["action"] == "admin.session" for e in log)
    assert PW not in json.dumps(log)  # no secrets in the audit trail


async def test_treasury_addresses_are_checked_against_their_network(client):
    hb, sb = await boss(client)
    sol = str(Keypair().pubkey())
    cases = [("solana", EVM_TREASURY, 400), ("evm", sol, 400), ("base", "0x" + "0" * 40, 400),
             ("bsc", EVM_TREASURY[:-1] + ("e" if EVM_TREASURY[-1] != "e" else "f"), 400),  # checksum typo
             ("evm", "0x000000000000000000000000000000000000dEaD", 400), ("dogechain", EVM_TREASURY, 400),
             ("solana", "11111111111111111111111111111111", 400), ("solana", sol, 200), ("base", EVM_TREASURY, 200)]
    for net, addr, want in cases:
        r = await client.put(f"/admin/treasury/{net}", json={"address": addr, "confirm": await proof(sb)}, headers=hb)
        assert r.status_code == want, (net, addr, r.text)
    async with get_sessionmaker()() as db:
        await pc.refresh(db, force=True)
    assert pc.treasury_for("base") == EVM_TREASURY and pc.treasury_for("bsc") == ""  # no EVM default set
    assert pc.treasury_for("solana") == sol


# --- fee settings ----------------------------------------------------------------------------------------


async def test_fee_settings_are_bounded_and_need_their_treasury_first(client):
    hb, sb = await boss(client)
    too_high = {"bps": 201, "confirm": await proof(sb)}
    assert (await client.put("/admin/settings/trade_fee_bps_evm_dex", json=too_high, headers=hb)).status_code == 400
    assert (await client.put("/admin/settings/trade_fee_bps_evm_dex", json={"bps": -1, "confirm": await proof(sb)},
                             headers=hb)).status_code == 400
    assert (await client.put("/admin/settings/made_up", json={"bps": 1, "confirm": await proof(sb)},
                             headers=hb)).status_code == 404
    r = await client.put("/admin/settings/trade_fee_bps_evm_dex", json={"bps": 50, "confirm": await proof(sb)},
                         headers=hb)
    assert r.status_code == 400 and "treasury" in r.json()["detail"]  # no EVM treasury yet: stays off
    await client.put("/admin/treasury/evm", json={"address": EVM_TREASURY, "confirm": await proof(sb)}, headers=hb)
    r = await client.put("/admin/settings/trade_fee_bps_evm_dex", json={"bps": 50, "confirm": await proof(sb)},
                         headers=hb)
    assert r.status_code == 200 and pc.bps("trade_fee_bps_evm_dex") == 50
    s = (await client.get("/admin/settings", headers=hb)).json()
    unsupported = [p["id"] for p in s["paths"] if not p["supported"]]
    assert "trade_fee_evm_curve" in unsupported and "creator_share_other" in unsupported


async def test_deploying_this_changes_no_rate(client, monkeypatch):
    """With no admin settings saved, every rate is what the environment already said."""
    s = get_settings()
    monkeypatch.setattr(s, "platform_fee_bps", 75)
    monkeypatch.setattr(s, "route_fee_bps", 30)
    monkeypatch.setattr(s, "clanker_platform_reward_bps", 1000)
    monkeypatch.setattr(s, "platform_fee_wallet_evm", EVM_TREASURY)
    monkeypatch.setattr(s, "platform_fee_wallet", str(Keypair().pubkey()))
    async with get_sessionmaker()() as db:
        await pc.refresh(db, force=True)
    assert pc.bps("trade_fee_bps_solana") == 75 and pc.bps("trade_fee_bps_evm_dex") == 75
    assert pc.bps("route_fee_bps") == 30
    assert pc.creator_share("clanker")["bps"] == 1000  # the existing Clanker share
    assert pc.creator_share("bags")["bps"] == 0  # Bags had none, and still has none until an admin sets it
    assert pc.creator_share("pumpfun")["bps"] == 0  # also opt-in until the admin saves a creator-share rate
    assert pc.creator_share("pumpfun")["supported"] is True


# --- revenue routing and the ledger -----------------------------------------------------------------------


async def test_trade_fee_goes_to_the_admin_treasury_and_only_helms_part_is_revenue(client, fake_rpc):
    hb, sb = await boss(client)
    treasury = str(Keypair().pubkey())
    assert (await client.put("/admin/treasury/solana", json={"address": treasury, "confirm": await proof(sb)},
                             headers=hb)).status_code == 200
    assert (await client.put("/admin/settings/trade_fee_bps_solana", json={"bps": 100, "confirm": await proof(sb)},
                             headers=hb)).status_code == 200
    # a referred trader: 25% of the fee is the referrer's, owed to them because they set no payout address
    ha = await signup(client, "kol@example.com")
    code = (await client.post("/referrals", json={}, headers=ha)).json()["code"]
    await client.post("/auth/register", json={"email": "fan@example.com", "password": PW, "referral_code": code})
    tok = (await client.post("/auth/login", json={"email": "fan@example.com", "password": PW})).json()["token"]
    hf = {"Authorization": f"Bearer {tok}"}
    lid, dev, holders = await live_launch(client, hf, fake_rpc, holders=1)
    _, r = await confirm(client, hf, f"/launches/{lid}/trade", {"side": "buy", "wallet_ids": [holders[0]["id"]],
                                                                  "amount": 1.0})
    assert (treasury, 10_000_000) in sol_transfers(fake_rpc.sent[-1])  # fee plus the referral share held for them
    async with get_sessionmaker()() as db:
        ev = (await db.execute(select(RevenueEvent))).scalars().all()
    assert len(ev) == 1
    e = ev[0]
    assert (e.source, e.status, e.asset, e.network, e.amount_raw, e.decimals, e.destination, e.launch_id) == (
        "trade_fee", "received", "SOL", "solana", 7_500_000, 9, treasury, lid)
    # the 1 SOL trade amount and the referrer's 2.5M lamports are never Helm's
    summary = (await client.get("/admin/revenue/summary", headers=hb)).json()
    sol = next(t for t in summary["totals"] if t["asset"] == "SOL")
    assert sol["received"] == 7_500_000 and sol["claimable"] == 0 and sol["unreconciled"] == 7_500_000
    assert summary["referral_liabilities"] == [{"network": "solana", "asset": "SOL", "amount_raw": "2500000",
                                                 "decimals": 9, "entries": 1}]
    led = (await client.get("/admin/revenue/ledger", headers=hb)).json()
    assert led["total"] == 1 and led["items"][0]["launch"]["symbol"] and led["items"][0]["reference"]
    r = await client.post("/admin/revenue/reconcile", json={"ids": [e.id], "note": "matched on solscan"}, headers=hb)
    assert r.json()["reconciled"] == 1
    sol = next(t for t in (await client.get("/admin/revenue/summary", headers=hb)).json()["totals"]
               if t["asset"] == "SOL")
    assert sol["unreconciled"] == 0 and sol["received"] == 7_500_000


async def test_a_failed_trade_records_no_revenue(client, fake_rpc):
    h = await signup(client)
    lid, dev, holders = await live_launch(client, h, fake_rpc, holders=1)
    s = get_settings()
    s.platform_fee_bps, s.platform_fee_wallet = 100, str(Keypair().pubkey())
    fake_rpc.sim_error = "slippage exceeded"
    await confirm(client, h, f"/launches/{lid}/trade", {"side": "buy", "wallet_ids": [holders[0]["id"]], "amount": 1.0})
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(RevenueEvent))).scalars().all() == []


def test_an_unconfirmed_fee_is_pending_not_received():
    from app.services.revenue import trade_fee_event

    fee = FeeRecord(job_id="j", payer_user_id="u", wallet_id="w", side="buy", basis_lamports=SOL,
                    platform_lamports=5, referral_lamports=0, mode="follow_up", status="pending", network="solana")
    assert trade_fee_event(fee, "T", None).status == "pending"
    fee.status = "confirmed"
    assert trade_fee_event(fee, "T", None).status == "received"


async def test_failed_trade_fee_is_visible_separately_in_admin_ledger(client):
    hb, _ = await boss(client)
    async with get_sessionmaker()() as db:
        db.add(RevenueEvent(source="trade_fee", network="solana", asset="SOL", amount_raw=5_000,
                            decimals=9, estimated=False, status="failed", note="test failed fee"))
        await db.commit()
    summary = (await client.get("/admin/revenue/summary", headers=hb)).json()
    bucket = next(b for b in summary["totals"] if b["network"] == "solana" and b["asset"] == "SOL")
    assert bucket["failed"] == 5_000 and bucket["received"] == 0
    rows = (await client.get("/admin/revenue/ledger?status=failed", headers=hb)).json()
    assert rows["total"] == 1 and rows["items"][0]["status"] == "failed"


async def test_estimated_revenue_is_separate_from_measured_status_totals(client):
    hb, _ = await boss(client)
    async with get_sessionmaker()() as db:
        db.add_all([
            RevenueEvent(source="dex_fee", network="base", asset="ETH", amount_raw=200, decimals=18,
                         estimated=False, status="received", note="receipt measured"),
            RevenueEvent(source="dex_fee", network="base", asset="ETH", amount_raw=100, decimals=18,
                         estimated=True, status="received", note="quote estimate"),
            RevenueEvent(source="dex_fee", network="base", asset="ETH", amount_raw=25, decimals=18,
                         estimated=True, status="claimable", note="provider estimate"),
        ])
        await db.commit()
    summary = (await client.get("/admin/revenue/summary", headers=hb)).json()
    bucket = next(b for b in summary["totals"] if b["network"] == "base" and b["asset"] == "ETH")
    assert bucket["received"] == 200 and bucket["claimable"] == 0 and bucket["estimated"] == 125
    assert bucket["estimated_by_status"] == {"received": 100, "pending": 0, "claimable": 25, "failed": 0}


async def test_relay_route_fees_are_claimable_never_received(client):
    from app.services.revenue import route_fee_event

    hb, _ = await boss(client)
    quote = {"fees": {"app": {"amount": "250000", "currency": {"chainId": 8453, "symbol": "USDC", "decimals": 6}}}}
    ev = route_fee_event(quote, EVM_TREASURY, "j", None, "u", "req-1")
    assert (ev.status, ev.network, ev.asset, ev.amount_raw, ev.estimated) == ("claimable", "base", "USDC", 250000, True)
    assert route_fee_event({"fees": {}}, EVM_TREASURY, "j", None, "u", "r") is None  # no app fee, nothing recorded
    async with get_sessionmaker()() as db:
        db.add(ev)
        await db.commit()
    t = (await client.get("/admin/revenue/summary", headers=hb)).json()
    usdc = next(x for x in t["totals"] if x["asset"] == "USDC")
    assert usdc["claimable"] == 0 and usdc["received"] == 0 and usdc["estimated"] == 250000
    assert usdc["estimated_by_status"]["claimable"] == 250000 and usdc["network"] == "base"
    assert any("Relay" in w and "claimed" in w for w in t["warnings"])


async def test_kyber_dex_fee_is_one_fee_exact_on_buys_estimated_on_sells():
    import httpx

    from app.chains.kyber import KyberSwap

    def handler(req):
        if req.url.path.endswith("/routes"):
            if "feeAmount" in req.url.params:
                assert req.url.params["feeReceiver"] == EVM_TREASURY and req.url.params["feeAmount"] == "50"
            return httpx.Response(200, json={"data": {"routerAddress": "0x" + "11" * 20,
                                                      "routeSummary": {"amountOut": "995000"}}})
        raise AssertionError(req.url)

    class RPC:
        async def erc20_allowance(self, *a):
            return 10**30

    k = KyberSwap(fee_bps=50, fee_receiver=EVM_TREASURY, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    buy = await k.prepare_trade("base", RPC(), "0x" + "22" * 20, "0x" + "33" * 20, "buy", 10**18, 5)
    assert buy.notes["helm_fee"] == {"bps": 50, "receiver": EVM_TREASURY, "wei": 5 * 10**15, "estimated": False}
    sell = await k.prepare_trade("base", RPC(), "0x" + "22" * 20, "0x" + "33" * 20, "sell", 10**18, 5)
    assert sell.notes["helm_fee"]["estimated"] is True and sell.notes["helm_fee"]["wei"] == 995000 * 50 // 9950
    assert "helm_fee" not in (await KyberSwap(client=k._client).prepare_trade(
        "base", RPC(), "0x" + "22" * 20, "0x" + "33" * 20, "buy", 10**18, 5)).notes  # fee off = nothing recorded


def test_a_deploy_lists_every_helm_fee_once():
    from app.routes.launches import _helm_fees

    assert _helm_fees({"bps": 0}, None) == "no Helm fee on the deploy itself"
    both = _helm_fees({"bps": 500}, {"route_fee": "0.3% of the amount sent"})
    assert both == ("no Helm fee on the deploy itself; route fee on the USDC funding: 0.3% of the amount sent; "
                    "5% of this token's future creator rewards")


# --- creator-reward shares ---------------------------------------------------------------------------------


async def _set_share(client, hb, sb, bps):
    await client.put("/admin/treasury/solana", json={"address": str(Keypair().pubkey()), "confirm": await proof(sb)},
                     headers=hb)
    await client.put("/admin/treasury/evm", json={"address": EVM_TREASURY, "confirm": await proof(sb)}, headers=hb)
    r = await client.put("/admin/settings/creator_share_bps", json={"bps": bps, "confirm": await proof(sb)}, headers=hb)
    assert r.status_code == 200, r.text


async def test_creator_share_is_disclosed_and_only_the_share_goes_to_the_treasury(client):
    import httpx

    from app.chains.bags import BagsAdapter
    from app.chains.base import CreateRequest
    from app.routes.launches import _creator_share_terms

    hb, sb = await boss(client)
    await _set_share(client, hb, sb, 500)
    terms = _creator_share_terms("bags", {})
    assert terms["bps"] == 500 and terms["recipient"] == pc.treasury_for("solana") and "5%" in terms["note"]
    assert _creator_share_terms("bags", {"quote": {"mint": str(Keypair().pubkey())}})["bps"] == 0  # pair launch
    pump_terms = _creator_share_terms("pumpfun", {}, str(Keypair().pubkey()))
    assert pump_terms["bps"] == 500 and pump_terms["creator_bps"] == 9500

    seen = {}
    bags = BagsAdapter(api_key="k", client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))))

    async def fake_call(method, path, **kw):
        if path == "/fee-share/config":
            seen["config"] = kw["json"]
            raise RuntimeError("stop here")
        return {}

    async def fake_info(req):
        return str(Keypair().pubkey()), "ipfs://x"

    bags._call, bags._token_info = fake_call, fake_info
    dev = str(Keypair().pubkey())
    try:
        await bags.build_create_tx(CreateRequest(name="A", symbol="A", description="", image_bytes=b"x", socials={},
                                                 dev_pubkey=dev, dev_buy_sol=0, slippage_pct=5,
                                                 options={"helm_creator_share": terms}))
    except RuntimeError:
        pass
    assert seen["config"]["claimersArray"] == [dev, terms["recipient"]]
    assert seen["config"]["basisPointsArray"] == [9500, 500]  # 95% stays the creator's


async def test_clanker_uses_the_share_the_creator_confirmed():
    from app.chains.clanker import ClankerAdapter

    a = ClankerAdapter("clanker", "base", rpc=None)
    confirmed = {"helm_creator_share": {"bps": 500, "recipient": EVM_TREASURY}}
    assert a._share(confirmed) == (EVM_TREASURY, 500)
    assert a._share({"helm_creator_share": {"bps": 0, "recipient": ""}}) == ("", 0)
    notes = a.deploy_notes(confirmed)
    assert notes["rewards"].startswith("95%") and notes["platform_share"].startswith("5%")


async def test_pumpfun_share_is_required_after_create_and_recorded(client, fake_rpc, monkeypatch):
    from solders.hash import Hash
    from solders.pubkey import Pubkey

    from app.chains import registry
    from app.chains.solana import build_unsigned, sol_transfer_ix
    from app.models import Transaction

    hb, sb = await boss(client)
    await _set_share(client, hb, sb, 600)
    h = await signup(client, "pump-creator@example.com")
    dev = (await client.get("/wallets", headers=h)).json()[0]
    seen = {}

    async def sharing_tx(mint, creator, share):
        seen.update({"mint": mint, "creator": creator, "share": share})
        payer = Pubkey.from_string(creator)
        return build_unsigned(payer, [sol_transfer_ix(payer, payer, 1)], str(Hash.default()))

    monkeypatch.setattr(registry._cache["pumpfun"], "build_creator_share_tx", sharing_tx, raising=False)
    draft = (await client.post("/launches", json={"name": "Shared", "symbol": "SHR",
                                                  "dev_wallet_id": dev["id"]}, headers=h)).json()
    preview, result = await confirm(client, h, f"/launches/{draft['id']}/deploy", {})
    assert result.status_code == 200
    terms = preview["preflight"]["helm_creator_share"]
    assert terms["bps"] == 600 and terms["creator_bps"] == 9400
    assert terms["creator"] == dev["address"] and terms["recipient"] == pc.treasury_for("solana")
    launch = (await client.get(f"/launches/{draft['id']}", headers=h)).json()
    assert launch["status"] == "live" and launch["creator_share_setup"]["status"] == "active"
    assert seen["mint"] == launch["mint_address"] and seen["share"]["bps"] == 600
    async with get_sessionmaker()() as db:
        txs = (await db.execute(select(Transaction).where(Transaction.kind == "deploy_fee_share"))).scalars().all()
        event = (await db.execute(select(RevenueEvent).where(
            RevenueEvent.launch_id == draft["id"], RevenueEvent.source == "creator_share",
        ))).scalars().one()
    assert len(txs) == 1 and txs[0].status == "confirmed"
    assert event.destination == terms["recipient"] and event.status == "claimable"


async def test_existing_launches_keep_their_terms(client, fake_rpc):
    """Changing the share later never touches a launch that's already deployed."""
    h = await signup(client)
    lid, _, _ = await live_launch(client, h, fake_rpc, holders=1)  # pump.fun (fake): no share
    hb, sb = await boss(client)
    await _set_share(client, hb, sb, 500)
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(RevenueEvent).where(RevenueEvent.source == "creator_share"))).first() is None


# --- users -----------------------------------------------------------------------------------------------


async def test_user_list_and_detail_expose_no_secrets(client, fake_rpc):
    h = await signup(client, "trader@example.com")
    await live_launch(client, h, fake_rpc, holders=1)
    hb, _ = await boss(client)
    r = (await client.get("/admin/users?q=trader&sort=launches&order=desc&size=10", headers=hb)).json()
    assert r["total"] == 1 and r["timezone"] == "UTC"
    u = r["items"][0]
    assert u["email"] == "trader@example.com" and u["launches"] == 1 and u["wallets"] >= 2
    assert u["registered_at"].endswith("Z") and u["status"] == "active" and u["plan"] is None
    d = (await client.get(f"/admin/users/{u['id']}", headers=hb)).json()
    blob = json.dumps(r) + json.dumps(d)
    async with get_sessionmaker()() as db:
        user = await db.get(User, u["id"])
        from app.models import Wallet

        wallets = (await db.execute(select(Wallet).where(Wallet.user_id == user.id))).scalars().all()
    for secret in [user.password_hash, "signer_ref", "totp_secret", "token_hash"] + [w.address for w in wallets]:
        assert secret not in blob
    assert all("…" in s["address"] for n in d["wallets"]["by_network"] for s in n["sample"])
    page = (await client.get("/admin/users?size=1&page=2&sort=email&order=asc", headers=hb)).json()
    assert page["total"] == 2 and len(page["items"]) == 1
    assert (await client.get("/admin/users?sort=password_hash", headers=hb)).status_code == 400
    actions = [item["action"] for item in (await client.get("/admin/audit", headers=hb)).json()["items"]]
    assert "users.listed" in actions and "users.searched" in actions


async def test_suspending_needs_a_reason_ends_sessions_and_is_audited(client):
    hb, sb = await boss(client)
    h = await signup(client, "bad@example.com", main=False)
    uid = await uid_of("bad@example.com")
    r = await client.post(f"/admin/users/{uid}/suspend", json={"reason": "", "confirm": await proof(sb)}, headers=hb)
    assert r.status_code == 422
    r = await client.post(f"/admin/users/{uid}/suspend", json={"reason": "fraud report #12", "confirm": await proof(sb)},
                          headers=hb)
    assert r.status_code == 200
    assert (await client.get("/wallets", headers=h)).status_code == 401  # session ended
    tok = (await client.post("/auth/login", json={"email": "bad@example.com", "password": PW})).json()["token"]
    r = await client.get("/wallets", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403 and "suspended" in r.json()["detail"]
    items = (await client.get("/admin/users?status=suspended", headers=hb)).json()["items"]
    assert items[0]["flags"][0]["reason"] == "fraud report #12"
    r = await client.post(f"/admin/users/{uid}/restore", json={"reason": "appeal accepted", "confirm": await proof(sb)},
                          headers=hb)
    assert r.status_code == 200
    assert (await client.get("/wallets", headers={"Authorization": f"Bearer {tok}"})).status_code == 200
    actions = [e["action"] for e in (await client.get("/admin/audit", headers=hb)).json()["items"]]
    assert "user.suspend" in actions and "user.restore" in actions


async def test_metrics_are_aggregates(client, fake_rpc):
    h = await signup(client)
    await live_launch(client, h, fake_rpc, holders=1)
    hb, _ = await boss(client)
    m = (await client.get("/admin/metrics?days=7", headers=hb)).json()
    assert m["users_total"] == 2 and m["launches_by_status"]["live"] == 1
    assert m["trade_and_deploy_jobs"]["success_rate"] == 1.0 and "definition" in m["active_users"]


# --- review follow-ups -------------------------------------------------------------------------------------


async def test_a_pin_set_later_overrides_an_earlier_binding(client, monkeypatch):
    h, _ = await boss(client)
    assert (await client.get("/admin/me", headers=h)).status_code == 200
    monkeypatch.setattr(get_settings(), "superadmin_user_id", "the-real-owner-id")
    assert (await client.get("/admin/me", headers=h)).status_code == 403


async def test_viewers_cant_unmask_emails_by_searching(client):
    hb, sb = await boss(client)
    await signup(client, "alice.secret@example.com", main=False)
    hv = await signup(client, "viewer@example.com", main=False)
    await enable_2fa(client, hv)
    await client.post(f"/admin/roles/{await uid_of('viewer@example.com')}/viewer",
                      json={"reason": "support staff", "confirm": await proof(sb)}, headers=hb)
    assert (await client.get("/admin/users?q=alice", headers=hv)).json()["total"] == 0
    assert (await client.get("/admin/users?q=alice.secret@example.com", headers=hv)).json()["total"] == 1
    assert (await client.get("/admin/users?sort=email", headers=hv)).status_code == 400
    assert (await client.get("/admin/users?q=alice", headers=hb)).json()["total"] == 1
    assert (await client.get("/admin/users?q=%25", headers=hb)).json()["total"] == 0  # LIKE wildcards are literal
    assert all(e["context"] == {} for e in (await client.get("/admin/audit", headers=hv)).json()["items"])


async def test_a_helm_managed_wallet_cant_be_the_treasury(client):
    hb, sb = await boss(client)
    h = await signup(client, "someone@example.com")
    managed = (await client.get("/wallets", headers=h)).json()
    sol = next(w["address"] for w in managed if w["network"] == "solana")
    r = await client.put("/admin/treasury/solana", json={"address": sol, "confirm": await proof(sb)}, headers=hb)
    assert r.status_code == 400 and "managed" in r.json()["detail"]


async def test_a_step_up_code_is_spent_even_when_the_change_is_refused(client):
    import pyotp

    from app.models import TotpUse
    from sqlalchemy import delete

    hb, sb = await boss(client)
    async with get_sessionmaker()() as db:
        await db.execute(delete(TotpUse))
        await db.commit()
    code = pyotp.TOTP(sb).now()
    bad = {"address": "not-an-address", "confirm": {"password": PW, "totp": code}}
    assert (await client.put("/admin/treasury/solana", json=bad, headers=hb)).status_code == 400
    good = {"address": str(Keypair().pubkey()), "confirm": {"password": PW, "totp": code}}
    assert (await client.put("/admin/treasury/solana", json=good, headers=hb)).status_code == 401
    wrong = {"address": str(Keypair().pubkey()), "confirm": {"password": "nope-nope-nope", "totp": "123456"}}
    await client.put("/admin/treasury/solana", json=wrong, headers=hb)
    assert "stepup.denied" in [e["action"] for e in (await client.get("/admin/audit", headers=hb)).json()["items"]]


async def test_a_scheduled_plan_stops_if_the_fee_goes_up(client):
    from app.models import AutomationTerms
    from app.services.automation_runner import _fee_raised

    hb, sb = await boss(client)

    class A:
        id = "auto-1"

    async with get_sessionmaker()() as db:
        db.add(AutomationTerms(automation_id="auto-1", trade_fee_bps_solana=50, trade_fee_bps_evm_dex=0))
        await db.commit()
    await client.put("/admin/treasury/solana", json={"address": str(Keypair().pubkey()), "confirm": await proof(sb)},
                     headers=hb)
    await client.put("/admin/settings/trade_fee_bps_solana", json={"bps": 50, "confirm": await proof(sb)}, headers=hb)
    async with get_sessionmaker()() as db:
        assert await _fee_raised(db, A, "solana") is False
    await client.put("/admin/settings/trade_fee_bps_solana", json={"bps": 75, "confirm": await proof(sb)}, headers=hb)
    async with get_sessionmaker()() as db:
        assert await _fee_raised(db, A, "solana") is True


def test_no_share_when_the_treasury_is_the_dev_wallet(monkeypatch):
    from app.routes.launches import _creator_share_terms

    monkeypatch.setattr(pc, "creator_share", lambda lp: {"bps": 500, "recipient": EVM_TREASURY, "supported": True,
                                                         "note": "5%"})
    assert _creator_share_terms("clanker", {}, EVM_TREASURY.lower())["bps"] == 0
    assert _creator_share_terms("clanker", {}, "0x" + "ab" * 20)["bps"] == 500


async def test_large_evm_amounts_fit_the_ledger(client):
    hb, _ = await boss(client)
    async with get_sessionmaker()() as db:
        db.add(RevenueEvent(source="dex_trade_fee", network="base", asset="ETH", amount_raw=25 * 10**18, decimals=18,
                            status="received"))
        await db.commit()
    items = (await client.get("/admin/revenue/ledger", headers=hb)).json()["items"]
    assert items[0]["amount_raw"] == str(25 * 10**18)


# --- Relay app-fee verification --------------------------------------------------------------------------


class FakeRelay:
    def __init__(self, reqs):
        self.reqs = reqs

    async def request(self, rid):
        return self.reqs.get(rid)


def _route_ev(ref):
    return RevenueEvent(source="route_fee", network="base", asset="USDC", amount_raw=3000, decimals=6, estimated=True,
                        status="claimable", destination=EVM_TREASURY, reference=ref)


async def test_relay_verification_confirms_or_zeroes_route_fees():
    from app.services.revenue import verify_route_fee

    relay = FakeRelay({
        "paid": {"status": "success", "data": {"paidAppFees": [{"recipient": EVM_TREASURY.lower(), "amount": "3000",
                                                                "amountUsd": "0.003"}]}},
        "zero": {"status": "success", "data": {"appFees": [{"recipient": EVM_TREASURY}], "paidAppFees": []}},
        "other": {"status": "success", "data": {"paidAppFees": [{"recipient": "0x" + "9" * 40, "amount": "5"}]}},
        "refund": {"status": "refund", "data": {}},
        "wait": {"status": "pending", "data": {}},
    })
    evs = {k: _route_ev(k) for k in ("paid", "zero", "other", "refund", "wait", "missing")}
    got = {k: await verify_route_fee(relay, e) for k, e in evs.items()}
    assert got == {"paid": "confirmed", "zero": "not_collected", "other": "not_collected",
                   "refund": "not_collected", "wait": "pending", "missing": "unknown"}
    assert evs["paid"].reconciled_at and evs["paid"].amount_raw == 3000 and evs["paid"].status == "claimable"
    assert evs["zero"].amount_raw == 0 and evs["refund"].amount_raw == 0  # never counted as revenue
    assert evs["wait"].reconciled_at is None and evs["missing"].reconciled_at is None


async def test_relay_receipt_without_raw_amount_does_not_reuse_the_quote_amount():
    from app.services.revenue import verify_route_fee

    relay = FakeRelay({"paid": {"status": "success", "data": {"paidAppFees": [
        {"recipient": EVM_TREASURY, "amountUsd": "0.003"}]}}})
    event = _route_ev("paid")
    assert await verify_route_fee(relay, event) == "confirmed"
    assert event.amount_raw is None and event.estimated is False and event.reconciled_at


async def test_relay_balance_is_read_from_relay(client, monkeypatch):
    from app.chains.relay import Relay

    hb, sb = await boss(client)
    assert (await client.get("/admin/relay-fees", headers=hb)).json()["unavailable"].startswith("No EVM treasury")
    await client.put("/admin/treasury/evm", json={"address": EVM_TREASURY, "confirm": await proof(sb)}, headers=hb)
    seen = {}

    async def fake(self, wallet):
        seen["wallet"] = wallet
        return {"totalBalanceUsd": 1.25, "availableBalanceUsd": 1.25, "balances": [
            {"currency": {"symbol": "USDC", "chainId": 8453}, "amountFormatted": "1.25", "amountUsd": "1.25",
             "minimumAmount": "1000"}]}

    monkeypatch.setattr(Relay, "app_fee_balances", fake)
    r = (await client.get("/admin/relay-fees", headers=hb)).json()
    assert seen["wallet"] == EVM_TREASURY and r["available_usd"] == 1.25 and r["balances"][0]["asset"] == "USDC"

    async def fake_req(self, rid):
        return {"status": "success", "data": {"paidAppFees": [{"recipient": EVM_TREASURY, "amount": "3000",
                                                               "amountUsd": "0.003"}]}}

    monkeypatch.setattr(Relay, "request", fake_req)
    async with get_sessionmaker()() as db:
        db.add(_route_ev("req-1"))
        await db.commit()
    hv = await signup(client, "viewer2@example.com", main=False)
    assert (await client.post("/admin/revenue/verify-relay", json={}, headers=hv)).status_code == 403
    r = (await client.post("/admin/revenue/verify-relay", json={}, headers=hb)).json()
    assert r == {"checked": 1, "confirmed": 1}
    led = (await client.get("/admin/revenue/ledger?source=route_fee", headers=hb)).json()["items"][0]
    assert led["reconciled_at"] and "Relay confirmed" in led["note"]
