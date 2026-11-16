from sqlalchemy import func, select
from app.db import get_sessionmaker
from app.models import AdminAudit, AdminRole, PlatformCharge, RevenueEvent, User, Job
from app.services import charges
from app.services.fees import fee_plan_for
from .conftest import signup
from .test_admin import boss, proof
from .test_api import live_launch


async def test_admin_and_account_waivers_are_authoritative_and_audited(client,fake_rpc):
    admin,secret=await boss(client)
    normal=await signup(client,'waiver-target@example.com',main=False)
    me=(await client.get('/auth/me',headers=normal)).json();uid=me['id']
    assert me['trade_fee']['bps']==250
    assert (await client.get('/auth/me',headers=admin)).json()['trade_fee']['bps']==0
    launch,dev,holders=await live_launch(client,normal,fake_rpc,holders=1)
    trade={'side':'buy','wallet_ids':[holders[0]['id']],'amount':.1}
    path=f'/launches/{launch}/trade'
    old=(await client.post(path,headers=normal,json=trade)).json()
    assert old['preflight']['trade_fee_terms']['bps']==250
    change={'enabled':True,'reason':'Partner trading fee waiver','confirm':await proof(secret)}
    assert (await client.put(f'/admin/users/{uid}/trade-fee-exemption',headers=normal,json=change)).status_code==403
    granted=await client.put(f'/admin/users/{uid}/trade-fee-exemption',headers=admin,json=change)
    assert granted.status_code==200 and granted.json()['trade_fee']['bps']==0
    sent=len(fake_rpc.sent)
    stale=await client.post(path,headers=normal,json={**trade,'confirm':True,'preflight_hash':old['preflight_hash'],'idempotency_key':'old-fee-preview'})
    assert stale.status_code==409 and len(fake_rpc.sent)==sent
    fresh=(await client.post(path,headers=normal,json=trade)).json()
    assert fresh['preflight']['trade_fee_terms']['bps']==0
    async with get_sessionmaker()() as db:
        target=await db.get(User,uid)
        for net in ['solana','base','bsc','ethereum','robinhood','tron','near','arc']:
            assert (await charges.trade_terms(db,net,target))['bps']==0
        assert not (await fee_plan_for(db,target)).active
        before=(await db.execute(select(func.count()).select_from(PlatformCharge))).scalar_one()
        job=Job(user_id=uid,kind='trade_buy',params={'trade_fee_terms':{'bps':0}},status='confirmed');db.add(job);await db.commit()
        assert (await charges.charge_trade(db,None,job,dev,0)).status=='confirmed'
        assert (await db.execute(select(func.count()).select_from(PlatformCharge))).scalar_one()==before
        audit=(await db.execute(select(AdminAudit).where(AdminAudit.action=='trade_fee.exemption'))).scalars().all()
        assert len(audit)==1 and audit[0].changes['enabled']==[False,True]
    revoked=await client.put(f'/admin/users/{uid}/trade-fee-exemption',headers=admin,json={**change,'enabled':False,'confirm':await proof(secret)})
    assert revoked.status_code==200 and revoked.json()['trade_fee']['bps']==250


async def test_viewer_admin_trades_are_free_but_cannot_grant_waivers(client):
    headers=await signup(client,'viewer-trader@example.com',main=False)
    uid=(await client.get('/auth/me',headers=headers)).json()['id']
    async with get_sessionmaker()() as db:
        db.add(AdminRole(user_id=uid,role='viewer',granted_by='fixture'));await db.commit()
    assert (await client.get('/auth/me',headers=headers)).json()['trade_fee']['bps']==0
    response=await client.put(f'/admin/users/{uid}/trade-fee-exemption',headers=headers,json={
        'enabled':True,'reason':'Cannot elevate privileges','confirm':{'password':'wrong','totp':'000000'}})
    assert response.status_code==403
