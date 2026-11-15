"""Pricing acceptance: consent, exemptions, exact money, referral liabilities and no repeated payments."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from solders.keypair import Keypair
from sqlalchemy import select

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import AdminRole, Job, Launch, LaunchFeeExemption, PlatformCharge, RevenueEvent, User, Wallet
from app.services import charges, platform_config as pc
from app.services.executor import SubmitResult
from app.services.pricing import public_pricing


async def objects():
    async with get_sessionmaker()() as db:
        user = User(email="pricing@example.com", password_hash="not-a-login-hash")
        db.add(user)
        await db.commit()
        wallet = Wallet(user_id=user.id, network="solana", address=str(Keypair().pubkey()), kind="managed", signer_ref="fake")
        launch = Launch(user_id=user.id, network="solana", launchpad="pumpfun", name="Pricing", symbol="PRC")
        db.add_all([wallet, launch])
        await db.commit()
        job = Job(user_id=user.id, launch_id=launch.id, kind="deploy", params={})
        db.add(job)
        await db.commit()
        return user, wallet, launch, job


def test_published_policy_is_fixed_and_has_only_two_revenue_sources(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "platform_fee_bps", 500)
    monkeypatch.setattr(settings, "route_fee_bps", 100)
    monkeypatch.setattr(settings, "clanker_platform_reward_bps", 1000)
    assert pc.bps("trade_fee_bps_solana") == pc.bps("trade_fee_bps_evm_dex") == 250
    assert pc.bps("route_fee_bps") == 0
    assert pc.bps("creator_share_bps") == public_pricing()["creator_share_bps"] == 250
    assert public_pricing()["launch_usd"] == 0
    assert public_pricing()["referral_share_bps"] == 3000
    for pad in pc.CREATOR_SHARE_LAUNCHPADS:
        assert pc.creator_share(pad)["bps"] == 250


@pytest.mark.parametrize("raw", [0, 1, 99, 100, 20_000_000, 10**27 + 7])
def test_referral_split_conserves_each_base_unit(raw):
    platform, referral = charges.split_fee(raw, True)
    assert platform + referral == raw
    assert referral == raw * 3000 // 10000
    assert charges.split_fee(raw, False) == (raw, 0)


async def test_admin_and_explicit_exemption_apply_to_launches_only(client):
    user, wallet, launch, job = await objects()
    async with get_sessionmaker()() as db:
        assert await charges.exemption(db, user) == "Launches are free for every account"
        db.add(AdminRole(user_id=user.id, role="viewer", granted_by="test"))
        await db.commit()
        assert await charges.exemption(db, user) == "Launches are free for every account"
        assert pc.bps("trade_fee_bps_solana") == 250
        role = (await db.execute(select(AdminRole).where(AdminRole.user_id == user.id))).scalar_one()
        await db.delete(role)
        db.add(LaunchFeeExemption(user_id=user.id, enabled=True, reason="Approved launch promotion", changed_by=user.id))
        await db.commit()
        assert await charges.exemption(db, user) == "Launches are free for every account"
        terms, approval, warnings = await charges.launch_quote(db, user, None, launch, wallet)
        assert terms["exempt"] and terms["usd"] == 0 and approval is None and not warnings


@pytest.mark.parametrize("network", ["solana", "base", "ethereum", "bsc", "robinhood", "tron", "near", "arc"])
async def test_free_launch_quote_needs_no_stablecoin_price_or_treasury(client, network):
    user, wallet, launch, job = await objects()
    launch.network = wallet.network = network
    async with get_sessionmaker()() as db:
        terms, approval, warnings = await charges.launch_quote(db, user, None, launch, wallet, "obsolete-paid-approval")
        assert terms["usd"] == terms["standard_usd"] == 0 and terms["exempt"]
        assert approval is None and warnings == []
        assert await charges.pay_launch(db, None, job, launch)


async def test_confirmed_trade_payment_is_not_charged_twice_and_accounts_for_referrals(client, monkeypatch):
    from app.models import ReferralLink
    user, wallet, launch, job = await objects()
    treasury = str(Keypair().pubkey())
    calls = []
    async def send(ex, source, terms, persist):
        calls.append(terms)
        await persist("payment-signature")
        return SubmitResult("payment-signature", "confirmed")
    monkeypatch.setattr(charges, "_send", send)
    async with get_sessionmaker()() as db:
        referrer = User(email="referrer@example.com", password_hash="not-a-login-hash")
        db.add(referrer)
        await db.commit()
        db.add(ReferralLink(referred_user_id=user.id, referrer_user_id=referrer.id, code="TEST"))
        await db.commit()
        current = await db.get(Job, job.id)
        terms = dict(network="solana", asset="USDC", token="mint", decimals=6, amount_raw="20000000", destination=treasury)
        first = await charges.collect(db, None, current, wallet, "trade", terms, f"launch:{launch.id}")
        second = await charges.collect(db, None, current, wallet, "trade", terms, f"launch:{launch.id}")
        assert first.status == second.status == "confirmed" and len(calls) == 1
        row = (await db.execute(select(PlatformCharge))).scalar_one()
        assert (row.amount_raw, row.platform_raw, row.referral_raw) == (20_000_000, 14_000_000, 6_000_000)
        assert row.referral_status == "owed"
        event = (await db.execute(select(RevenueEvent))).scalar_one()
        assert event.amount_raw == 14_000_000 and event.status == "received"
        terms, _, _ = await charges.launch_quote(db, user, None, launch, wallet)
        assert terms["exempt"] and terms["reason"] == "Launches are free for every account"


async def test_uncertain_payment_is_never_replayed(client, monkeypatch):
    user, wallet, launch, job = await objects()
    calls = []
    async def send(ex, source, terms, persist):
        calls.append(1)
        await persist("uncertain-signature")
        return SubmitResult("uncertain-signature", "timeout")
    monkeypatch.setattr(charges, "_send", send)
    terms = dict(network="solana", asset="SOL", token=None, decimals=9, amount_raw="1000000", destination=str(Keypair().pubkey()))
    async with get_sessionmaker()() as db:
        current = await db.get(Job, job.id)
        assert (await charges.collect(db, None, current, wallet, "trade", terms, "same-launch")).status == "timeout"
        assert (await charges.collect(db, None, current, wallet, "trade", terms, "same-launch")).status == "timeout"
        assert len(calls) == 1
        event = (await db.execute(select(RevenueEvent))).scalar_one()
        assert event.status == "pending"


async def test_large_raw_amounts_round_trip_without_float_conversion(client, monkeypatch):
    user, wallet, launch, job = await objects()
    raw = 10**27 + 7
    async def send(ex, source, terms, persist):
        await persist("exact-large-payment")
        return SubmitResult("exact-large-payment", "confirmed")
    monkeypatch.setattr(charges, "_send", send)
    terms = dict(network="solana", asset="TEST", token=None, decimals=27, amount_raw=str(raw), destination=str(Keypair().pubkey()))
    async with get_sessionmaker()() as db:
        await charges.collect(db, None, await db.get(Job, job.id), wallet, "trade", terms, "large")
    async with get_sessionmaker()() as db:
        row = (await db.execute(select(PlatformCharge))).scalar_one()
        event = (await db.execute(select(RevenueEvent))).scalar_one()
        assert row.amount_raw == raw and event.amount_raw == raw


async def test_obsolete_launch_payment_cannot_reach_signer(client, monkeypatch):
    user, wallet, launch, job = await objects()
    async def forbidden(*args):
        raise AssertionError("A free launch must not request a payment signature")
    monkeypatch.setattr(charges, "_send", forbidden)
    terms = dict(network="solana", asset="SOL", decimals=9, amount_raw="100000000",
                 destination=str(Keypair().pubkey()), expires=1)
    async with get_sessionmaker()() as db:
        result = await charges.collect(db, None, await db.get(Job, job.id), wallet, "launch", terms, "obsolete")
        assert result.status == "failed" and "free" in result.error
        assert not (await db.execute(select(PlatformCharge))).scalars().all()
        assert not (await db.execute(select(RevenueEvent))).scalars().all()


async def test_definitive_failure_can_settle_without_leaving_old_debt(client, monkeypatch):
    user, wallet, launch, job = await objects()
    responses = iter(["failed", "confirmed"])
    async def send(ex, source, terms, persist):
        status = next(responses)
        if status == "confirmed":
            await persist("settled-fee")
        return SubmitResult("settled-fee" if status == "confirmed" else None, status)
    monkeypatch.setattr(charges, "_send", send)
    terms = dict(network="solana", asset="SOL", decimals=9, amount_raw="1000", destination=str(Keypair().pubkey()))
    async with get_sessionmaker()() as db:
        current = await db.get(Job, job.id)
        assert (await charges.collect(db, None, current, wallet, "trade", terms, "retry-fee")).status == "failed"
        assert (await charges.collect(db, None, current, wallet, "trade", terms, "retry-fee")).status == "confirmed"
        rows = (await db.execute(select(PlatformCharge))).scalars().all()
        assert sorted(row.status for row in rows) == ["confirmed", "superseded"]
        events = (await db.execute(select(RevenueEvent))).scalars().all()
        assert len(events) == 1 and events[0].status == "received"


async def test_reconciliation_resolves_known_signature_without_resending(client, fake_rpc, monkeypatch):
    user, wallet, launch, job = await objects()
    async def send(ex, source, terms, persist):
        await persist("later-confirmed-fee")
        return SubmitResult("later-confirmed-fee", "timeout")
    monkeypatch.setattr(charges, "_send", send)
    terms = dict(network="solana", asset="SOL", decimals=9, amount_raw="1000", destination=str(Keypair().pubkey()))
    async with get_sessionmaker()() as db:
        current = await db.get(Job, job.id)
        await charges.collect(db, None, current, wallet, "trade", terms, "reconcile-fee")
        row = (await db.execute(select(PlatformCharge))).scalar_one()
        async def forbidden(*args):
            raise AssertionError("Reconciliation must not sign")
        monkeypatch.setattr(charges, "_send", forbidden)
        await charges.reconcile(db, SimpleNamespace(rpc=fake_rpc), row)
        assert row.status == "confirmed"
        assert (await db.execute(select(RevenueEvent))).scalar_one().status == "received"


def test_approved_swap_floor_rejects_changed_assets_and_worse_execution(monkeypatch):
    from app.chains.base import AdapterError
    from app.services import quote_approval as qa
    q = {"inputMint": "IN", "outputMint": "OUT", "inAmount": "100", "otherAmountThreshold": "90"}
    token = qa.issue("user", {"purpose": "swap"}, qa.jupiter_bounds(q, "IN", "OUT", 100))
    bounds = qa.verify(token, "user", {"purpose": "swap"})
    qa.check_jupiter_bounds(q, bounds, "IN", "OUT", 100)
    for changed in ({**q, "otherAmountThreshold": "89"}, {**q, "inputMint": "WRONG"}):
        with pytest.raises(AdapterError):
            qa.check_jupiter_bounds(changed, bounds, "IN", "OUT", 100)
    with pytest.raises(HTTPException):
        qa.verify(token, "different-user", {"purpose": "swap"})
    with pytest.raises(HTTPException):
        qa.verify(token[:-1] + ("0" if token[-1] != "0" else "1"), "user", {"purpose": "swap"})


async def test_launch_api_is_free_without_stablecoin_and_deploys_once(client, fake_rpc):
    from .conftest import signup
    from .test_api import make_wallets
    h = await signup(client, "free-launch@example.com", launch_exempt=False)
    wallet = (await make_wallets(client, h))[0]
    launch = (await client.post("/launches", headers=h, json={"name": "Free", "symbol": "FREE",
        "dev_wallet_id": wallet["id"]})).json()
    path = f"/launches/{launch['id']}/deploy"
    preview = (await client.post(path, headers=h, json={})).json()
    assert preview["preflight"]["launch_fee_payment"]["usd"] == 0
    assert preview["launch_fee_approval"] is None and fake_rpc.tokens == {}
    body = {"confirm": True, "preflight_hash": preview["preflight_hash"], "idempotency_key": "free-launch"}
    first = await client.post(path, headers=h, json=body)
    assert first.status_code == 200, first.text
    assert len(fake_rpc.sent) == 2
    replay = await client.post(path, headers=h, json=body)
    assert replay.status_code == 200 and replay.json()["job_id"] == first.json()["job_id"]
    assert len(fake_rpc.sent) == 2
    assert (await client.get("/billing", headers=h)).json()["payments"] == []


async def test_launch_exemption_requires_owner_step_up_and_records_audit(client):
    from .test_admin import boss, proof, uid_of
    from .conftest import signup
    h = await signup(client, "promotion@example.com", launch_exempt=False)
    target = await uid_of("promotion@example.com")
    path = f"/admin/users/{target}/launch-fee-exemption"
    data = {"enabled": True, "reason": "Reviewed promotional launch"}
    assert (await client.put(path, headers=h, json={**data, "confirm": {"password": "bad", "totp": "000000"}})).status_code == 403
    owner, secret = await boss(client)
    denied = await client.put(path, headers=owner, json={**data, "confirm": {"password": "bad", "totp": "000000"}})
    assert denied.status_code in {400,401,403}
    allowed = await client.put(path, headers=owner, json={**data, "confirm": await proof(secret)})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["trade_fee_bps"] == 250
    result = (await client.get("/billing", headers=h)).json()
    assert result["launch_exempt"] and result["pricing"]["trade_bps"] == 250
    audit = (await client.get("/admin/audit", headers=owner)).json()["items"]
    assert any(row["action"] == "launch_fee.exemption" and row["target"] == target for row in audit)


@pytest.mark.parametrize("recovery", [False, True])
async def test_initial_buy_fee_is_not_waived_and_recovery_never_signs(client, monkeypatch, recovery):
    user, wallet, launch, job = await objects()
    calls = []
    async def send(ex, source, terms, persist):
        calls.append(int(terms["amount_raw"]))
        await persist("initial-buy-fee-signature")
        return SubmitResult("initial-buy-fee-signature", "confirmed")
    monkeypatch.setattr(charges, "_send", send)
    async with get_sessionmaker()() as db:
        current, draft = await db.get(Job, job.id), await db.get(Launch, launch.id)
        draft.status = "live"
        current.params = {"launch_fee_payment": {"exempt": True}, "initial_buy_fee": {
            "wallet_id": wallet.id, "amount_raw": "12500000", "basis_raw": "500000000", "asset": "SOL",
            "decimals": 9, "network": "solana", "policy": charges.POLICY_VERSION,
            "destination": str(Keypair().pubkey())}, "execution": {"version": 1,
            "dispatch_complete": True, "required": [], "outcomes": [{"signature": "deployment", "status": "confirmed"}]}}
        await db.commit()
        first = await charges.initial_buy_fee(db, None, current, draft, wallet, recovery=recovery)
        second = await charges.initial_buy_fee(db, None, current, draft, wallet, recovery=True)
        assert first.status == second.status == ("failed" if recovery else "confirmed")
        assert calls == ([] if recovery else [12_500_000])
        row = (await db.execute(select(PlatformCharge))).scalar_one()
        assert row.kind == "trade" and row.amount_raw == 12_500_000
        assert row.status == ("failed" if recovery else "confirmed")
        assert current.status == ("partial" if recovery else "confirmed")


async def test_solana_fee_references_distinguish_identical_payments_in_one_block(client, fake_rpc):
    _, wallet, _, _ = await objects()
    sent = []
    async def submit(raw, refs, on_signed):
        sent.append(raw)
        return SubmitResult(None, "confirmed")
    ex = SimpleNamespace(rpc=fake_rpc, submit=submit)
    terms = {"amount_raw": "1000", "destination": str(Keypair().pubkey()), "decimals": 9}
    for reference in ["first-charge", "second-charge"]:
        await charges._send(ex, wallet, {**terms, "payment_reference": reference}, None)
    assert len(sent) == 2 and sent[0] != sent[1]
