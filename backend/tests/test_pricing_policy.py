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
    assert pc.bps("trade_fee_bps_solana") == pc.bps("trade_fee_bps_evm_dex") == 100
    assert pc.bps("route_fee_bps") == pc.bps("creator_share_bps") == 0
    assert public_pricing()["launch_usd"] == 20
    assert public_pricing()["referral_share_bps"] == 3000
    for pad in pc.CREATOR_SHARE_LAUNCHPADS:
        assert pc.creator_share(pad)["bps"] == 0


@pytest.mark.parametrize("raw", [0, 1, 99, 100, 20_000_000, 10**27 + 7])
def test_referral_split_conserves_each_base_unit(raw):
    platform, referral = charges.split_fee(raw, True)
    assert platform + referral == raw
    assert referral == raw * 3000 // 10000
    assert charges.split_fee(raw, False) == (raw, 0)


async def test_admin_and_explicit_exemption_apply_to_launches_only(client):
    user, wallet, launch, job = await objects()
    async with get_sessionmaker()() as db:
        assert await charges.exemption(db, user) is None
        db.add(AdminRole(user_id=user.id, role="viewer", granted_by="test"))
        await db.commit()
        assert await charges.exemption(db, user) == "Admin account"
        assert pc.bps("trade_fee_bps_solana") == 100
        role = (await db.execute(select(AdminRole).where(AdminRole.user_id == user.id))).scalar_one()
        await db.delete(role)
        db.add(LaunchFeeExemption(user_id=user.id, enabled=True, reason="Approved launch promotion", changed_by=user.id))
        await db.commit()
        assert await charges.exemption(db, user) == "Account launch-fee exemption"
        terms, approval, warnings = await charges.launch_quote(db, user, None, launch, wallet)
        assert terms["exempt"] and terms["usd"] == 0 and approval is None and not warnings


async def test_stablecoin_quote_is_exact_and_bound_to_the_account_and_treasury(client, fake_rpc, monkeypatch):
    user, wallet, launch, job = await objects()
    treasury = str(Keypair().pubkey())
    monkeypatch.setattr(get_settings(), "platform_fee_wallet", treasury)
    fake_rpc.tokens[wallet.address] = 20_000_000
    ex = SimpleNamespace(rpc=fake_rpc)
    async with get_sessionmaker()() as db:
        terms, approval, warnings = await charges.launch_quote(db, user, ex, launch, wallet)
        assert terms["asset"] == "USDC" and terms["amount_raw"] == "20000000" and not warnings
        confirmed, _, _ = await charges.launch_quote(db, user, ex, launch, wallet, approval)
        assert confirmed == terms
        monkeypatch.setattr(get_settings(), "platform_fee_wallet", str(Keypair().pubkey()))
        pc.reset_cache()
        with pytest.raises(HTTPException) as error:
            await charges.launch_quote(db, user, ex, launch, wallet, approval)
        assert error.value.status_code == 409


async def test_confirmed_launch_credit_is_not_charged_twice_and_accounts_for_referrals(client, monkeypatch):
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
        first = await charges.collect(db, None, current, wallet, "launch", terms, f"launch:{launch.id}")
        second = await charges.collect(db, None, current, wallet, "launch", terms, f"launch:{launch.id}")
        assert first.status == second.status == "confirmed" and len(calls) == 1
        row = (await db.execute(select(PlatformCharge))).scalar_one()
        assert (row.amount_raw, row.platform_raw, row.referral_raw) == (20_000_000, 14_000_000, 6_000_000)
        assert row.referral_status == "owed"
        event = (await db.execute(select(RevenueEvent))).scalar_one()
        assert event.amount_raw == 14_000_000 and event.status == "received"
        terms, _, _ = await charges.launch_quote(db, user, None, launch, wallet)
        assert terms["exempt"] and terms["reason"] == "Launch fee already paid for this draft"


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
        assert (await charges.collect(db, None, current, wallet, "launch", terms, "same-launch")).status == "timeout"
        assert (await charges.collect(db, None, current, wallet, "launch", terms, "same-launch")).status == "timeout"
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
        await charges.collect(db, None, await db.get(Job, job.id), wallet, "launch", terms, "large")
    async with get_sessionmaker()() as db:
        row = (await db.execute(select(PlatformCharge))).scalar_one()
        event = (await db.execute(select(RevenueEvent))).scalar_one()
        assert row.amount_raw == raw and event.amount_raw == raw


async def test_expired_launch_approval_never_signs_or_creates_income(client, monkeypatch):
    user, wallet, launch, job = await objects()
    async def forbidden(*args):
        raise AssertionError("Expired approval reached the signer")
    monkeypatch.setattr(charges, "_send", forbidden)
    terms = dict(network="solana", asset="SOL", decimals=9, amount_raw="10",
                 destination=str(Keypair().pubkey()), expires=1)
    async with get_sessionmaker()() as db:
        result = await charges.collect(db, None, await db.get(Job, job.id), wallet, "launch", terms, "expired")
        assert result.status == "failed" and "expired" in result.error
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


async def test_launch_api_requires_reviewed_stable_payment_then_deploys_once(client, fake_rpc):
    from .conftest import signup
    from .test_api import make_wallets
    h = await signup(client, "paid-launch@example.com", launch_exempt=False)
    wallet = (await make_wallets(client, h))[0]
    fake_rpc.tokens[wallet["address"]] = 20_000_000
    launch = (await client.post("/launches", headers=h, json={"name": "Paid", "symbol": "PAID",
        "dev_wallet_id": wallet["id"]})).json()
    path = f"/launches/{launch['id']}/deploy"
    preview = (await client.post(path, headers=h, json={})).json()
    terms = preview["preflight"]["launch_fee_payment"]
    assert terms["amount_raw"] == "20000000" and terms["asset"] == "USDC" and not terms["exempt"]
    rejected = await client.post(path, headers=h, json={"confirm": True, "preflight_hash": preview["preflight_hash"],
        "idempotency_key": "missing-payment-approval"})
    assert rejected.status_code == 409 and not fake_rpc.sent
    body = {"confirm": True, "preflight_hash": preview["preflight_hash"], "idempotency_key": "paid-launch",
            "launch_fee_approval": preview["launch_fee_approval"]}
    first = await client.post(path, headers=h, json=body)
    assert first.status_code == 200, first.text
    assert len(fake_rpc.sent) == 2  # one exact USDC fee payment, then one deployment
    replay = await client.post(path, headers=h, json=body)
    assert replay.status_code == 200 and replay.json()["job_id"] == first.json()["job_id"]
    assert len(fake_rpc.sent) == 2
    payments = (await client.get("/billing", headers=h)).json()["payments"]
    assert len(payments) == 1 and payments[0]["amount_raw"] == "20000000" and payments[0]["status"] == "confirmed"


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
    assert allowed.json()["trade_fee_bps"] == 100
    result = (await client.get("/billing", headers=h)).json()
    assert result["launch_exempt"] and result["pricing"]["trade_bps"] == 100
    audit = (await client.get("/admin/audit", headers=owner)).json()["items"]
    assert any(row["action"] == "launch_fee.exemption" and row["target"] == target for row in audit)
