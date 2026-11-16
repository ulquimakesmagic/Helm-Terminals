from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from app.deps import get_executor
from app.main import app
from app.services.executor import Executor
from app.signing import get_signer
from .conftest import signup, enable_2fa, totp_code
from .test_distribute import confirm
from .test_flap import FlapNode
from app.services.executor import SubmitResult


async def test_solana_token_moves_use_chosen_owned_payer_and_never_foreign_wallet(client,fake_rpc):
    headers=await signup(client,main=False)
    source,dest,payer=(await client.post('/wallets',headers=headers,json={'count':3})).json()
    fake_rpc.balances[source['address']]=0
    fake_rpc.tokens[source['address']]=10_000_000
    body={'from_wallet_id':source['id'],'to_wallet_ids':[dest['id']],'asset':'stable','total':'2.000001','fee_wallet_id':payer['id']}
    pf,job=await confirm(client,headers,'/wallets/disperse',body,'owned-payer-disperse')
    assert job['status']=='confirmed',job
    assert pf['preflight']['fee_payers'][0]['address']==payer['address']
    tx=VersionedTransaction.from_bytes(fake_rpc.sent[-1])
    assert str(tx.message.account_keys[0])==payer['address'] and tx.message.header.num_required_signatures==2
    assert {str(k) for k in tx.message.account_keys[:2]}=={source['address'],payer['address']}
    n=len(fake_rpc.sent)
    replay=await client.post('/wallets/disperse',headers=headers,json={**body,'confirm':True,'preflight_hash':pf['preflight_hash'],'idempotency_key':'owned-payer-disperse'})
    assert replay.status_code==200 and len(fake_rpc.sent)==n
    other=await signup(client,'foreign-payer@example.com',main=False)
    foreign=(await client.post('/wallets',headers=other,json={'count':1})).json()[0]
    attempt=await client.post('/wallets/disperse',headers=headers,json={**body,'fee_wallet_id':foreign['id']})
    assert attempt.status_code==404 and len(fake_rpc.sent)==n
    consolidate={'from_wallet_ids':[source['id']],'to_wallet_id':dest['id'],'asset':'stable','fee_wallet_id':payer['id']}
    pf,job=await confirm(client,headers,'/wallets/consolidate',consolidate,'owned-payer-consolidate')
    assert job['status']=='confirmed' and str(VersionedTransaction.from_bytes(fake_rpc.sent[-1]).message.account_keys[0])==payer['address']


async def test_solana_native_withdrawal_keeps_2fa_and_payer_consent(client,fake_rpc):
    headers=await signup(client,main=False)
    source,payer=(await client.post('/wallets',headers=headers,json={'count':2})).json()
    secret=await enable_2fa(client,headers)
    fake_rpc.balances[source['address']]=1_000_000_000
    body={'wallet_ids':[source['id']],'destination':str(Keypair().pubkey()),'all':True,'fee_wallet_id':payer['id']}
    response=await client.post('/wallets/withdraw',headers=headers,json=body)
    assert response.status_code==200,response.text
    pf=response.json();assert pf['requires_2fa']
    assert pf['preflight']['from'][0]['amount_raw']=='1000000000'
    missing=await client.post('/wallets/withdraw',headers=headers,json={**body,'confirm':True,'preflight_hash':pf['preflight_hash'],'idempotency_key':'needs-proof'})
    assert missing.status_code==401 and not fake_rpc.sent
    sent=await client.post('/wallets/withdraw',headers=headers,json={**body,'confirm':True,'preflight_hash':pf['preflight_hash'],'idempotency_key':'with-proof','totp':await totp_code(secret)})
    assert sent.status_code==200,sent.text
    job=(await client.get('/jobs/'+sent.json()['job_id'],headers=headers)).json()
    assert job['status']=='confirmed',job
    message=VersionedTransaction.from_bytes(fake_rpc.sent[-1]).message
    assert str(message.account_keys[0])==payer['address'] and message.header.num_required_signatures==2


async def test_evm_gas_allowance_is_reviewed_and_sent_before_token_disperse(client,fake_rpc):
    node=FlapNode('bsc')
    async def estimate(tx):
        return 65_000 if tx.get('data') not in (None,'0x','') else 21_000
    node.estimate_gas=estimate
    app.dependency_overrides[get_executor]=lambda:Executor(fake_rpc,get_signer(),evm_rpcs={'bsc':node})
    try:
        headers=await signup(client,main=False)
        source,dest,payer=(await client.post('/wallets',headers=headers,json={'network':'bsc','count':3})).json()
        node.balances[payer['address'].lower()]=10**18
        node.balances[source['address'].lower()]=0
        node.tokens[source['address'].lower()]=10**20
        body={'from_wallet_id':source['id'],'to_wallet_ids':[dest['id']],'asset':'0x'+'ab'*20,'total':'1','fee_wallet_id':payer['id']}
        pf,job=await confirm(client,headers,'/wallets/disperse',body,'evm-gas-support')
        assert job['status']=='confirmed',job
        leg=pf['preflight']['fee_funding'][0]
        assert leg['from_address']==payer['address'] and leg['to_address']==source['address']
        assert [t['kind'] for t in job['transactions']]==['gas_fund_0','disperse']
        assert node.sent[0]['to'].lower()==source['address'].lower()
        assert node.sent[0]['value']==int(leg['amount_raw'])
        assert pf['preflight']['platform_fee']=='none'
    finally:
        app.dependency_overrides.pop(get_executor,None)


async def test_random_split_review_is_repeatable_and_seed_changes_need_new_consent(client,fake_rpc):
    headers=await signup(client,main=False)
    source,*targets=(await client.post('/wallets',headers=headers,json={'count':4})).json()
    body={'from_wallet_id':source['id'],'to_wallet_ids':[t['id'] for t in targets],
          'total':'1','split':'random','split_seed':'review-fixed-seed-12345'}
    first=(await client.post('/wallets/disperse',headers=headers,json=body)).json()
    second=(await client.post('/wallets/disperse',headers=headers,json=body)).json()
    assert first['preflight_hash']==second['preflight_hash']
    amounts=[int(row['amount_raw']) for row in first['preflight']['to']]
    assert sum(amounts)==1_000_000_000 and len(set(amounts))>1
    changed=await client.post('/wallets/disperse',headers=headers,json={**body,'split_seed':'changed-fixed-seed-12345','confirm':True,'preflight_hash':first['preflight_hash'],'idempotency_key':'changed-seed'})
    assert changed.status_code==409 and not fake_rpc.sent
    _,job=await confirm(client,headers,'/wallets/disperse',body,'frozen-random-split')
    assert job['status']=='confirmed',job


async def test_uncertain_gas_funding_stops_the_asset_transfer(client,fake_rpc,monkeypatch):
    node=FlapNode('bsc')
    app.dependency_overrides[get_executor]=lambda:Executor(fake_rpc,get_signer(),evm_rpcs={'bsc':node})
    from app.services import gas_funding
    async def uncertain(*_):
        return SubmitResult('0x'+'ab'*32,'timeout','Funding receipt is not known')
    monkeypatch.setattr(gas_funding,'send_evm_tracked',uncertain)
    try:
        headers=await signup(client,main=False)
        source,dest,payer=(await client.post('/wallets',headers=headers,json={'network':'bsc','count':3})).json()
        node.balances[payer['address'].lower()]=10**18
        node.balances[source['address'].lower()]=0
        node.tokens[source['address'].lower()]=10**20
        _,job=await confirm(client,headers,'/wallets/disperse',{'from_wallet_id':source['id'],
            'to_wallet_ids':[dest['id']],'asset':'0x'+'ab'*20,'total':'1','fee_wallet_id':payer['id']},'uncertain-gas')
        assert job['status']=='unconfirmed' and not node.sent
        # A late funding confirmation still cannot prove that the asset moved.
        again=(await client.get('/jobs/'+job['id'],headers=headers)).json()
        assert again['status']=='unconfirmed' and not node.sent
    finally:
        app.dependency_overrides.pop(get_executor,None)


async def test_native_batch_cannot_drain_its_selected_fee_wallet(client,fake_rpc):
    headers=await signup(client,main=False)
    source,other,dest=(await client.post('/wallets',headers=headers,json={'count':3})).json()
    for path,body in [('/wallets/consolidate',{'from_wallet_ids':[source['id'],other['id']], 'to_wallet_id':dest['id']}),
                      ('/wallets/withdraw',{'wallet_ids':[source['id'],other['id']], 'destination':dest['address']})]:
        response=await client.post(path,headers=headers,json={**body,'fee_wallet_id':source['id']})
        assert response.status_code==400 and 'outside the source' in response.json()['detail']
    assert not fake_rpc.sent
