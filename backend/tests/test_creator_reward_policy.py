"""Collection readiness, exact native receipt accounting and no double charge."""
import pytest
from eth_abi import encode
from sqlalchemy import select

from app.chains import pons_rewards as rewards
from app.chains.base import AdapterError
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Job, Launch, PlatformCharge, ReferralLink, RevenueEvent, User, Wallet
from app.routes.launches import _creator_share_terms
from app.services import platform_config as pc
from app.services.pons_revenue import record_payout
from .test_pricing_policy import objects

CREATOR = "0x" + "ab" * 20
TREASURY = "0x" + "cd" * 20
FACTORY = "0x" + "fa" * 20


def test_new_policy_never_substitutes_the_historical_6_percent_factory():
    with pytest.raises(AdapterError):
        rewards.configuration("new", CREATOR, {"bps": 250, "recipient": TREASURY}, FACTORY)
    current = rewards.configuration("new", CREATOR, {"bps": 250, "recipient": TREASURY}, FACTORY, version=2)
    legacy = rewards.configuration("new", CREATOR, {"bps": 600, "recipient": TREASURY}, FACTORY)
    assert current["splitter"] != legacy["splitter"]
    for config in (current, legacy):
        rewards.validate(config)
    with pytest.raises(AdapterError):
        rewards.validate({**current, "bps": 600})


async def test_new_reward_routes_fail_closed_without_collection(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "pons_rewards_factory_v2", "")
    assert not _creator_share_terms("pons", {}, CREATOR)["supported"]
    assert not _creator_share_terms("printr", {}, CREATOR)["supported"]
    assert not _creator_share_terms("meteora-dbc", {}, CREATOR)["supported"]
    assert not _creator_share_terms("bags", {"quote": {"mint": "other-asset"}})["supported"]
    assert not _creator_share_terms("bags", {})["supported"]
    assert pc.creator_share('bags')['reward_streams']==['creator','deployer']
    for pad in ("pumpfun", "clanker", "clanker-robinhood"):
        terms = _creator_share_terms(pad, {})
        assert terms["supported"] and terms["bps"] == 250 and terms["creator_bps"] == 9750
    assert _creator_share_terms("four-meme", {})["collection"] == "not_applicable"
    assert _creator_share_terms("flap-sh", {"tax_rate_pct": 0})["bps"] == 0
    assert not _creator_share_terms("flap-sh", {"tax_rate_pct": 1})["supported"]


async def test_bags_credentials_do_not_enable_an_incomplete_combined_reward_route(client,monkeypatch):
    from .conftest import TEST_OPENAPI
    monkeypatch.setattr(get_settings(),'bags_api_key',TEST_OPENAPI)
    pads=(await client.get('/launchpads')).json()
    bags=next(pad for pad in pads if pad['id']=='bags')
    assert bags['status']=='setup_required' and bags['setup_kind']=='reward_integration'
    assert 'both normal creator and separate deployer' in bags['reason']
    assert not bags['capabilities']['create']
    assert bags['capabilities']['buy'] and bags['capabilities']['sell']
    assert bags['capabilities']['claim_mode']=='in-app'


async def test_native_reward_receipt_reserves_referral_once_without_signing(client):
    user, wallet, launch, job = await objects()
    config = rewards.configuration(launch.id, CREATOR, {"bps": 250, "recipient": TREASURY}, FACTORY, version=2)
    signature = "0x" + "12" * 32
    receipt = {"status": "0x1", "logs": [{"address": config["splitter"], "topics": [rewards.RELEASED,
        "0x" + "00" * 12 + TREASURY[2:]], "data": "0x" + encode(["uint256"], [250]).hex()}]}
    async with get_sessionmaker()() as db:
        referrer = User(email="reward-referrer@example.com", password_hash="not-a-login-hash")
        db.add(referrer)
        await db.commit()
        db.add(ReferralLink(referred_user_id=user.id, referrer_user_id=referrer.id, code="reward-test"))
        current = await db.get(Launch, launch.id)
        current.network, current.dev_wallet_id = "robinhood", wallet.id
        current.launchpad_params = {"pons_rewards": config}
        selected = await db.get(Wallet, wallet.id)
        selected.network, selected.address = "robinhood", CREATOR
        claim = await db.get(Job, job.id)
        claim.params = {"wallet": CREATOR}
        await db.commit()
        for _ in range(2):
            await record_payout(db, current, claim, signature, receipt)
            await db.commit()
        charge = (await db.execute(select(PlatformCharge))).scalar_one()
        assert (charge.kind, charge.amount_raw, charge.platform_raw, charge.referral_raw) == (
            "creator_reward", 250, 175, 75)
        assert charge.status == "confirmed" and charge.referral_status == "owed"
        assert charge.terms["no_additional_claim_fee"] and len(charge.reference_key) <= 120
        income = (await db.execute(select(RevenueEvent))).scalar_one()
        assert income.amount_raw == 175 and income.status == "received" and not income.estimated


async def test_liquidity_principal_is_not_billed_as_an_initial_developer_trade(client):
    from types import SimpleNamespace
    from app.services.charges import attach_launch_fee
    from app.services.preflight import build_preflight
    launch = SimpleNamespace(launchpad="uniswap-v2", network="ethereum", dev_buy_sol=0.5, launchpad_params={})
    body = SimpleNamespace(confirm=False, launch_fee_approval=None)
    result = await attach_launch_fee(build_preflight("deploy", {"helm_fees": "Free launch"}),
                                    None, None, None, launch, None, body)
    assert result["preflight"]["launch_fee_payment"]["usd"] == 0
    assert "initial_buy_fee" not in result["preflight"]
    assert "trade_fee_terms" not in result["preflight"]
