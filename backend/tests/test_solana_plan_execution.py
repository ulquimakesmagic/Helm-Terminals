"""Durable staged execution through the real executor; no external RPC or keys."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from solders.transaction import VersionedTransaction

from app.chains.base import AdapterError
from app.db import get_sessionmaker
from app.models import User,Wallet,Job,Transaction
from app.services.executor import Executor
from app.services.execution_journal import can_resolve
from app.services.solana_guard import SpendLimits,WalletEffects,check,SYSTEM_OWNER
from app.services.solana_plans import run_plan,reconcile_plan,receipt_debits,lookup_ready
from .test_solana_plans import WALLET,payload,stage,parse,setup_payload


class Signer:
    async def sign_message(self,ref,message):
        return bytes(WALLET.sign_message(message))


class RPC:
    def __init__(self,debits=(10,),unknown=False,receipt_error=False):
        self.debits=debits;self.sent=[];self.receipts={};self.unknown=unknown
        self.receipt_error=receipt_error;self.height=50;self.slot=50

    async def get_slot(self):return self.slot
    async def get_block_height(self):return self.height
    async def simulate(self,raw):return None

    async def send(self,raw):
        tx=VersionedTransaction.from_bytes(raw)
        assert all(tx.verify_with_results())
        signature=str(tx.signatures[0])
        # Executor must have committed tracking before the network sees bytes.
        async with get_sessionmaker()() as db:
            tracked=(await db.execute(select(Transaction).where(Transaction.signature==signature))).scalar_one()
            assert tracked.status=='pending'
        keys=[str(key) for key in tx.message.account_keys]
        pre=[1000]*len(keys);post=pre.copy()
        post[keys.index(str(WALLET.pubkey()))]-=self.debits[len(self.sent)]
        self.receipts[signature]={'transaction':{'signatures':[signature],'message':{'accountKeys':keys}},
            'meta':{'err':None,'preBalances':pre,'postBalances':post,'preTokenBalances':[],'postTokenBalances':[]}}
        self.sent.append(raw)
        return signature

    async def confirm(self,*args,**kwargs):
        return ('timeout','Uncertain') if self.unknown else ('confirmed',None)

    async def get_transaction(self,signature):
        if self.receipt_error:raise ConnectionError('offline')
        return self.receipts.get(signature)


@pytest.fixture
async def context(client,monkeypatch):
    async with get_sessionmaker()() as db:
        user=User(email='plan@example.com',password_hash='not-a-login-hash')
        db.add(user);await db.commit()
        wallet=Wallet(user_id=user.id,network='solana',address=str(WALLET.pubkey()),kind='managed',signer_ref='test-ref')
        job=Job(user_id=user.id,kind='deploy',params={})
        db.add_all([wallet,job]);await db.commit()
        yield db,wallet,job


def executor(monkeypatch,rpc):
    from app.services import solana_guard
    async def enforce(_rpc,raw,limits):
        debit=rpc.debits[len(rpc.sent)]
        return check(WalletEffects(1000,1000-debit,SYSTEM_OWNER,{},{}),limits)
    monkeypatch.setattr(solana_guard,'enforce',enforce)
    return Executor(rpc,Signer())


def limits(native=100):return SpendLimits(str(WALLET.pubkey()),native)


async def execute(context,ex,plan=None,bound=None):
    db,wallet,job=context
    return await run_plan(db,ex,job,wallet,'launch',plan or parse(payload()),bound or limits(),intent_hash='a'*64)


async def test_stages_share_one_budget_and_never_spend_successful_prefix_twice(context,monkeypatch):
    rpc=RPC((60,60));ex=executor(monkeypatch,rpc)
    plan=parse(payload(stage('a','config'),stage('b','launch',['a'])))
    result=await execute(context,ex,plan)
    db,wallet,job=context
    assert result.status=='failed' and len(rpc.sent)==1
    assert job.status=='partial' and job.params['provider_plan_business_pending']
    assert job.params['provider_plan']['native_spent']==60
    with pytest.raises(AdapterError,match='automatic replay'):
        await execute(context,ex,plan)
    rows=(await db.execute(select(Transaction).where(Transaction.job_id==job.id))).scalars().all()
    assert not can_resolve(job,rows)


async def test_expired_phase_pays_no_prefix(context,monkeypatch):
    rpc=RPC();rpc.height=101;ex=executor(monkeypatch,rpc)
    with pytest.raises(AdapterError,match='expired'):
        await execute(context,ex)
    assert not rpc.sent


async def test_uncertain_submission_reconciles_original_signature_without_resending(context,monkeypatch):
    rpc=RPC(unknown=True);ex=executor(monkeypatch,rpc)
    result=await execute(context,ex)
    db,wallet,job=context
    assert result.status=='timeout' and job.status=='unconfirmed' and len(rpc.sent)==1
    with pytest.raises(AdapterError,match='automatic replay'):await execute(context,ex)
    assert await reconcile_plan(db,ex,job)
    assert job.status=='confirmed' and not job.params['provider_plan_business_pending']
    assert job.params['provider_plan']['native_spent']==10 and len(rpc.sent)==1
    assert await reconcile_plan(db,ex,job)
    assert job.params['provider_plan']['native_spent']==10 and len(rpc.sent)==1


async def test_receipt_unavailable_stops_before_next_signature(context,monkeypatch):
    rpc=RPC((10,10),receipt_error=True);ex=executor(monkeypatch,rpc)
    plan=parse(payload(stage('a','config'),stage('b','launch',['a'])))
    result=await execute(context,ex,plan)
    assert result.status=='timeout' and len(rpc.sent)==1 and context[2].status=='unconfirmed'
    rpc.receipt_error=False
    assert not await reconcile_plan(context[0],ex,context[2])  # second business stage was never dispatched
    assert context[2].params['provider_plan_business_pending']


async def test_lookup_setup_is_not_launch_completion_and_output_terms_cannot_change(context,monkeypatch):
    value,table=setup_payload();plan=parse(value)
    rpc=RPC((10,10));ex=executor(monkeypatch,rpc)
    result=await execute(context,ex,plan)
    db,wallet,job=context
    assert result.status=='setup_confirmed' and job.status=='submitted'
    assert not (job.params.get('execution') or {}).get('dispatch_complete')
    assert not await reconcile_plan(db,ex,job)
    with pytest.raises(AdapterError,match='terms differ'):
        await execute(context,ex,bound=replace(limits(),token_in_min={'mint':1}))
    result=await execute(context,ex)
    assert result.status=='confirmed' and job.status=='confirmed' and len(rpc.sent)==2
    assert job.params['provider_plan']['native_spent']==20
    rows=(await db.execute(select(Transaction).where(Transaction.job_id==job.id))).scalars().all()
    assert can_resolve(job,rows)


async def test_pending_empty_setup_is_not_success(context,monkeypatch):
    value,table=setup_payload();value['transactions']=[]
    rpc=RPC();ex=executor(monkeypatch,rpc)
    result=await execute(context,ex,parse(value))
    assert result.status=='waiting' and context[2].status=='submitted' and not rpc.sent
    assert not await reconcile_plan(context[0],ex,context[2])


async def test_lookup_warmup_and_deactivation_refuse_business(context):
    value,table=setup_payload();value=payload();value['readiness']['lookupTables']=[str(table)]
    plan=parse(value)
    class TableRPC(RPC):
        async def get_lookup_table_state(self,address):return SimpleNamespace(meta=self.meta)
    rpc=TableRPC();rpc.meta=SimpleNamespace(deactivation_slot=2**64-1,last_extended_slot=50)
    with pytest.raises(AdapterError,match='warming'):await lookup_ready(rpc,plan)
    rpc.meta.deactivation_slot=51
    with pytest.raises(AdapterError,match='deactivating'):await lookup_ready(rpc,plan)


def test_receipts_reject_failed_transactions_and_duplicate_token_account_entries():
    receipt={'transaction':{'signatures':['sig'],'message':{'accountKeys':['wallet','ata']}},
        'meta':{'err':None,'preBalances':[100,0],'postBalances':[90,0],
            'preTokenBalances':[{'accountIndex':1,'owner':'wallet','mint':'mint','uiTokenAmount':{'amount':'10'}}],
            'postTokenBalances':[]}}
    assert receipt_debits(receipt,'sig','wallet')==(10,{'mint':10})
    receipt['meta']['preTokenBalances']*=2
    with pytest.raises(AdapterError,match='could not prove'):receipt_debits(receipt,'sig','wallet')
    receipt['meta']['err']={'InstructionError':[0,'failure']}
    with pytest.raises(AdapterError,match='could not prove'):receipt_debits(receipt,'sig','wallet')
