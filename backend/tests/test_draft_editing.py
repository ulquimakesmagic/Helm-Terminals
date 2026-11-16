import base64
from sqlalchemy import select
from app.db import get_sessionmaker
from app.models import Job, Launch, LaunchWallet, Transaction
from .conftest import signup

PNG = base64.b64encode(b'\x89PNG\r\n\x1a\n' + bytes(20)).decode()


async def test_draft_artwork_is_private_and_missing_dev_can_be_assigned(client, fake_rpc):
    owner = await signup(client, 'draft-owner@example.com', main=False)
    other = await signup(client, 'draft-other@example.com', main=False)
    wallets = (await client.post('/wallets', headers=owner, json={'count':2})).json()
    body = {'name':'Draft token','symbol':'TEST','image_b64':PNG}
    created = await client.post('/launches', headers=owner, json=body)
    assert created.status_code == 201, created.text
    draft = created.json(); path = f"/launches/{draft['id']}"
    assert draft['has_image'] and not draft['dev_wallet_id']
    assert (await client.get(path+'/image')).status_code == 401
    assert (await client.get(path+'/image',headers=other)).status_code == 404
    image = await client.get(path+'/image',headers=owner)
    assert image.content == base64.b64decode(PNG) and image.headers['cache-control']=='private, no-store'
    assert (await client.get(f"/public/launch-images/{draft['id']}")).status_code == 404
    edited = await client.patch(path,headers=owner,json={**body,'image_b64':None,'dev_wallet_id':wallets[0]['id'],'expected_revision':0})
    assert edited.status_code==200, edited.text
    assert edited.json()['has_image'] and edited.json()['draft_revision']==1
    assert edited.json()['dev_wallet_id']==wallets[0]['id']
    pf = (await client.post(path+'/deploy',headers=owner,json={})).json()
    assert pf['preflight']['draft_revision']==1
    changed = await client.patch(path,headers=owner,json={**body,'name':'Edited token','dev_wallet_id':wallets[1]['id'],'expected_revision':1})
    assert changed.status_code==200, changed.text
    stale = await client.patch(path,headers=owner,json={**body,'expected_revision':1})
    assert stale.status_code==409
    attempt = await client.post(path+'/deploy',headers=owner,json={'confirm':True,'preflight_hash':pf['preflight_hash'],'idempotency_key':'stale-draft'})
    assert attempt.status_code==409 and fake_rpc.sent==[]
    async with get_sessionmaker()() as db:
        links = (await db.execute(select(LaunchWallet).where(LaunchWallet.launch_id==draft['id']))).scalars().all()
        assert {link.wallet_id:link.role for link in links}=={wallets[0]['id']:'holder',wallets[1]['id']:'dev'}
    assert (await client.patch(path,headers=other,json={**body,'expected_revision':2})).status_code==404


async def test_edit_refuses_live_or_previously_submitted_launches(client):
    headers=await signup(client,main=False)
    body={'name':'Draft','symbol':'DRAFT'}
    draft=(await client.post('/launches',headers=headers,json=body)).json()
    path=f"/launches/{draft['id']}"
    async with get_sessionmaker()() as db:
        launch=await db.get(Launch,draft['id']);launch.status='live';launch.mint_address='already-deployed'
        await db.commit()
    assert (await client.patch(path,headers=headers,json=body)).status_code==409
    async with get_sessionmaker()() as db:
        launch=await db.get(Launch,draft['id']);launch.status='failed';launch.mint_address=None
        job=Job(user_id=launch.user_id,launch_id=launch.id,kind='deploy',status='failed',params={});db.add(job);await db.flush()
        db.add(Transaction(job_id=job.id,kind='deploy',signature='previously-signed',status='failed'));await db.commit()
    assert (await client.patch(path,headers=headers,json=body)).status_code==409
