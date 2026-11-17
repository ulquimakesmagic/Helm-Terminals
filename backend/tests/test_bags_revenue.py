from copy import deepcopy

import pytest
from sqlalchemy import func, select

from app.chains.bags_rewards import VaultAudit, VaultState, deployer_payouts
from app.chains.base import AdapterError
from app.db import get_sessionmaker
from app.models import Job, Launch, PlatformCharge, ReferralLink, RevenueEvent, User, Wallet
from app.services import bags_revenue
from .test_bags_rewards import KEYS, WALLET, MINT, TREASURY, history, positions, receipt
from .test_pricing_policy import objects


@pytest.fixture
async def setup(client):
    user, wallet, launch, job = await objects()
    async with get_sessionmaker()() as db:
        wallet = await db.get(Wallet, wallet.id);wallet.address = WALLET
        launch = await db.get(Launch, launch.id)
        launch.launchpad = 'bags';launch.status = 'live';launch.mint_address = MINT;launch.dev_wallet_id = wallet.id
        launch.launchpad_params = {'helm_creator_share': {'bps':250, 'recipient':TREASURY}}
        job = await db.get(Job, job.id);job.kind = 'claim_fees'
        referrer = User(email='bags-referrer@example.com',password_hash='not-a-login-hash')
        db.add(referrer);await db.commit()
        db.add(ReferralLink(referred_user_id=user.id,referrer_user_id=referrer.id,code='bags-referrer'))
        await db.commit()
        yield db,wallet,launch,job


async def test_native_receipt_books_only_actual_helm_income_and_referral_once(setup):
    db,wallet,launch,job = setup
    for _ in range(2):
        assert await bags_revenue.record_native_payout(db,launch,job,'sig',receipt())
        await db.commit()
    charge = (await db.execute(select(PlatformCharge))).scalar_one()
    income = (await db.execute(select(RevenueEvent))).scalar_one()
    assert (charge.amount_raw,charge.platform_raw,charge.referral_raw) == (25000,17500,7500)
    assert charge.kind == 'creator_reward' and charge.status == 'confirmed' and charge.referral_status == 'owed'
    assert charge.terms['no_additional_claim_fee'] and charge.terms['stream'] == 'creator'
    assert income.amount_raw == 17500 and income.status == 'received' and not income.estimated
    assert income.asset == 'WSOL' and charge.asset == 'WSOL' and charge.token is not None
    assert income.receipt_evidence['method'] == 'finalized_bags_event_and_token_transfer'


async def test_imported_launch_or_missing_native_split_never_becomes_a_new_fee(setup):
    db,wallet,launch,job = setup
    launch.imported = True
    assert not await bags_revenue.record_native_payout(db,launch,job,'sig',receipt())
    launch.imported = False;launch.launchpad_params = {}
    assert not await bags_revenue.record_native_payout(db,launch,job,'sig',receipt())
    assert await db.scalar(select(func.count()).select_from(PlatformCharge)) == 0


async def test_historical_native_percentage_is_recorded_without_repricing(setup):
    db,wallet,launch,job=setup
    launch.status='archived'  # closing the workspace cannot erase actual received income
    launch.launchpad_params={'helm_creator_share':{'bps':600,'recipient':TREASURY}}
    await bags_revenue.record_native_payout(db,launch,job,'sig',receipt())
    charge=(await db.execute(select(PlatformCharge))).scalar_one()
    assert charge.terms['bps']==600 and charge.amount_raw==25000  # actual payout, not 2.5% of an estimated balance
    assert launch.launchpad_params['helm_creator_share']['bps']==600


def audit():
    state = VaultState(WALLET,4000000,0,3000000)
    return VaultAudit(state,100,('accrue','withdraw'),tuple(deployer_payouts(history(),state)))


async def test_deployer_debt_requires_new_combined_policy_and_excludes_foreign_and_unattributed_income(setup):
    db,wallet,launch,job = setup
    assert await bags_revenue.record_deployer_debts(db,wallet,job,audit()) == 0  # historical saved native-only terms
    launch.launchpad_params = {**launch.launchpad_params,'bags_reward_policy':{'version':1,'bps':250,
        'streams':['creator','deployer'],'deployer':WALLET,'quote_mint':'So11111111111111111111111111111111111111112','creation_slot':50}}
    assert await bags_revenue.record_deployer_debts(db,wallet,job,audit()) == 1
    await db.commit()
    assert await bags_revenue.record_deployer_debts(db,wallet,job,audit()) == 0
    charge = (await db.execute(select(PlatformCharge))).scalar_one()
    assert charge.amount_raw == 25000 and charge.status == 'owed' and charge.signature is None
    assert charge.terms['basis_raw'] == '1000000' and charge.terms['stream'] == 'deployer'
    assert charge.referral_raw == 0 and charge.referral_status == 'none'
    assert await db.scalar(select(func.count()).select_from(RevenueEvent)) == 0
    # Two million foreign-token units plus one million unattributed units were not billed.


async def test_deployer_policy_cannot_retroactively_bill_earlier_or_imported_launches(setup):
    db,wallet,launch,job=setup
    terms={'version':1,'bps':250,'streams':['creator','deployer'],'deployer':WALLET,
           'quote_mint':'So11111111111111111111111111111111111111112','creation_slot':100}
    launch.launchpad_params={**launch.launchpad_params,'bags_reward_policy':terms}
    assert await bags_revenue.record_deployer_debts(db,wallet,job,audit())==0
    launch.imported=True
    launch.launchpad_params={**launch.launchpad_params,'bags_reward_policy':{**terms,'creation_slot':50}}
    assert await bags_revenue.record_deployer_debts(db,wallet,job,audit())==0
    assert await db.scalar(select(func.count()).select_from(PlatformCharge))==0


async def test_receipt_recovery_reads_finalized_evidence_without_signing(setup):
    from app.models import Transaction
    db,wallet,launch,job = setup
    db.add(Transaction(job_id=job.id,wallet_id=wallet.id,kind='claim_fees',signature='sig',status='confirmed'))
    await db.commit()
    calls=[]
    class RPC:
        async def get_transaction(self,signature,commitment):
            calls.append((signature,commitment));return receipt()
    for _ in range(2):await bags_revenue.reconcile(db,launch,RPC(),job.id)
    assert calls == [('sig','finalized')]  # the second recovery uses durable receipt accounting
    assert await db.scalar(select(func.count()).select_from(PlatformCharge)) == 1
    assert await db.scalar(select(func.count()).select_from(RevenueEvent)) == 1


async def test_unavailable_and_invalid_receipts_create_no_income(setup):
    from app.models import Transaction
    db,wallet,launch,job = setup
    db.add(Transaction(job_id=job.id,wallet_id=wallet.id,kind='claim_fees',signature='sig',status='confirmed'))
    await db.commit()
    class RPC:
        async def get_transaction(self,*args,**kwargs):raise ConnectionError('offline')
    await bags_revenue.reconcile(db,launch,RPC(),job.id)
    assert await db.scalar(select(func.count()).select_from(RevenueEvent)) == 0
    with pytest.raises(AdapterError):
        await bags_revenue.record_native_payout(db,launch,job,'sig',receipt(transfer=False))
    launch.dev_wallet_id=None
    with pytest.raises(AdapterError,match='wallet attribution'):
        await bags_revenue.record_native_payout(db,launch,job,'sig',receipt())
    assert await db.scalar(select(func.count()).select_from(PlatformCharge)) == 0


async def test_headless_recovery_finishes_accounting_after_browser_has_stopped(setup):
    from datetime import timedelta
    from types import SimpleNamespace
    from app.models import Transaction, utcnow
    from app.services.reconciliation import reconcile_once
    db,wallet,launch,job = setup
    job.status='confirmed';job.updated_at=utcnow()-timedelta(minutes=2)
    db.add(Transaction(job_id=job.id,wallet_id=wallet.id,kind='claim_fees',signature='sig',status='confirmed'))
    await db.commit()
    class RPC:
        async def get_transaction(self,*args,**kwargs):return receipt()
    await reconcile_once(SimpleNamespace(rpc=RPC()))
    await db.refresh(job)
    assert job.params['bags_reward_accounting_complete']
    assert await db.scalar(select(func.count()).select_from(RevenueEvent)) == 1
    await reconcile_once(SimpleNamespace(rpc=RPC()))
    assert await db.scalar(select(func.count()).select_from(PlatformCharge)) == 1


async def test_balance_endpoint_is_owner_only_and_unavailable_is_not_zero(client,monkeypatch):
    from app.chains import registry
    from app.chains.bags_rewards import Positions
    from .conftest import signup
    first = await signup(client,'bags-balances@example.com',main=False)
    second = await signup(client,'bags-other@example.com',main=False)
    async with get_sessionmaker()() as db:
        owner = (await db.execute(select(User).where(User.email=='bags-balances@example.com'))).scalar_one()
        wallet = Wallet(user_id=owner.id,network='solana',address=WALLET,kind='watch')
        db.add(wallet);await db.commit();wallet_id=wallet.id
    class Adapter:
        broken=False
        async def deployer_positions(self,address):
            if self.broken:raise AdapterError('Attribution unavailable')
            return Positions.parse(positions(),address)
    adapter=Adapter();monkeypatch.setitem(registry._cache,'bags',adapter)
    path=f'/wallets/{wallet_id}/bags-deployer-rewards'
    assert (await client.get(path)).status_code == 401
    assert (await client.get(path,headers=second)).status_code == 404
    response=await client.get(path,headers=first)
    assert response.status_code == 200 and response.json()['total_raw']=='6500000'
    assert response.json()['helm_fee_basis'] is None
    adapter.broken=True
    response=await client.get(path,headers=first)
    assert response.status_code == 503 and response.headers['retry-after']=='15'
    assert 'total_raw' not in response.json()
