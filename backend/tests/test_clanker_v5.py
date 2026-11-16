"""V5 economic intent, protocol admission, uncertain execution and V4 compatibility."""
from types import SimpleNamespace
import hashlib
import json

from eth_abi import encode,decode
import pytest

from app.chains import clanker_v5 as v5
from app.chains.base import AdapterError,CreateRequest,ClaimRequest
from app.chains.registry import get_adapter
from app.deps import get_executor
from app.main import app
from app.services.executor import Executor
from app.signing import get_signer
from .conftest import signup
from .test_evm_wallets import FakeEvm

DEV='0x'+'ab'*20
HELM='0x'+'fe'*20
INIT_HASH=bytes.fromhex('11'*32)


class V5Node(FakeEvm):
    def __init__(self,network='base'):
        super().__init__(network)
        self.v5=v5.CHAINS[network]
        self.config_version=1
        self.creation_open=True
        self.quote_registered=True
        self.owed=12345
        self.dividend='0x'+'55'*20
        self.receiver=DEV
        self.excluded=False
        self.dividend_paid=True
        self.simulations=[]
        self.policy={'taxFeeBounds':{'minimumBuyTaxRate':1_000_000,'maximumBuyTaxRate':10_000_000,'minimumSellTaxRate':1_000_000,'maximumSellTaxRate':10_000_000},
            'protocolFeeConfiguration':{'protocolTaxShare':10_000_000,'minimumProtocolFeeRate':500_000,'maximumProtocolFeeRate':1_000_000,'protocolFeeRecipient':HELM},
            'antiSnipeConfiguration':{'startTotalFeeRate':99_000_000,'windowSeconds':20},'minimumDividendHoldingRequirement':10_000*10**18}

    async def get_chain_id(self):return self.chain_id

    async def call(self,method,params):
        if method=='eth_getBlockByNumber':return {'number':'0x64','timestamp':'0x65555555'}
        assert method=='eth_call'
        tx=params[0];raw=bytes.fromhex(tx['data'][2:]);sel=raw[:4]
        found=next((name for name,item in v5.ABI.items() if v5.calldata(name,*[dummy(i) for i in item['inputs']])[:4]==sel),None)
        if found:
            item=v5.ABI[found]
            args=decode([v5.abi_type(i) for i in item['inputs']],raw[4:])
            values={'configVersion':self.config_version,'nativeLaunchFee':0,'launchSupply':10**27,
                'tokenDeployer':self.v5['token_deployer'],'taxPolicy':self.policy,'launchCreationEnabled':self.creation_open,
                'launchBuyAdapter':'0x'+'12'*20,'hook':self.v5['hook'],'tickSpacing':200,
                'launchTokenInitCodeHash':INIT_HASH,'owed':self.owed,'dividendOf':self.dividend,
                'taxToken':getattr(self,'claim_token',DEV),'quoteToken':self.v5['stable'],'receiverOf':self.receiver,
                'excludedFromDividends':self.excluded,'withdrawableDividendOf':self.owed,'withdrawDividends':self.dividend_paid}
            if found=='quoteConfig':
                q=next((q for q in v5.QUOTES[self.network] if q['address'].lower()==args[0].lower()),None)
                value=(self.quote_registered,q['decimals'] if q else 18,-199400)
            elif found in ('createLaunch','createLaunchAndBuy'):
                launch=v5.named(item['inputs'][0],args[0]);self.simulations.append((found,launch,args[1:]));self.last_launch=launch
                value=(v5.predict(tx['from'],self.v5['token_deployer'],INIT_HASH,launch['creatorSalt']),bytes(32))
                if found=='createLaunchAndBuy':value=(*value,1000*10**18)
            elif found=='creatorRights':
                value={'originalCreator':DEV,'currentCreator':DEV,'pendingCreator':v5.ZERO,'creatorFeeRecipient':DEV,'poolId':bytes(32),'metadataEditable':True}
                self.claim_token=args[0]
            elif found=='poolConfig':
                value=(True,True,self.claim_token,self.v5['stable'],DEV,DEV,self.policy['protocolFeeConfiguration'],self.policy['antiSnipeConfiguration'],1,True,v5.ZERO,v5.ZERO,v5.ClankerV5Adapter('clanker',self.network,self).tax({}))
            else:value=values[found]
            outputs=item['outputs']
            vals=[value] if len(outputs)==1 else value
            return '0x'+encode([v5.abi_type(i) for i in outputs],[v5.abi_value(i,v) for i,v in zip(outputs,vals)]).hex()
        from app.chains.clanker import selector
        if sel==selector('wrappedNative()'):value='0x'+'22'*20;kind='address'
        elif sel==selector('uniswapV3Factory()'):value='0x'+'33'*20;kind='address'
        elif sel==selector('maxExternalPoolFee()'):value=3000;kind='uint24'
        elif sel==selector('getPool(address,address,uint24)'):value='0x'+'44'*20;kind='address'
        else:raise AssertionError('Unexpected RPC request')
        return '0x'+encode([kind],[value]).hex()


def dummy(item):
    kind=item['type']
    if kind.endswith('[]'):return []
    if kind=='tuple':return {c['name']:dummy(c) for c in item['components']}
    if kind=='address':return v5.ZERO
    if kind=='bytes32':return bytes(32)
    if kind=='bytes':return b''
    if kind=='string':return ''
    if kind=='bool':return False
    return 0


@pytest.fixture(autouse=True)
def fake_runtimes(monkeypatch):
    digest=hashlib.sha256(bytes.fromhex('6080')).hexdigest()
    for network in v5.CHAINS:
        monkeypatch.setitem(v5.RUNTIMES,network,{key:digest for key in ['factory','hook','escrow','token_deployer']})


async def request(adapter,**options):
    terms=await adapter.launch_terms(options)
    return CreateRequest(name='V5 test',symbol='TEST',description='Fixture',image_bytes=None,socials={},dev_pubkey=DEV,
        dev_buy_sol=options.pop('buy',0),slippage_pct=5,options={**options,'clanker_v5_terms':terms,'helm_creator_share':{'recipient':HELM,'bps':250}})


@pytest.mark.parametrize('network',['base','bsc','robinhood'])
async def test_native_create_freezes_creator_helm_slots_and_v5_configuration(network):
    node=V5Node(network);adapter=v5.ClankerV5Adapter('clanker',network,node)
    req=await request(adapter,dividend_share_pct=25,burn_share_pct=10,minimum_dividend_tokens=10000)
    plan=await adapter.prepare_create(req)
    launch=node.last_launch
    assert plan.token.lower().endswith('cccc')
    assert [s['relativeWeightBps'] for s in launch['feeSlots']]==[9750,250]
    assert launch['feeSlots'][0]['isCreator'] and not launch['feeSlots'][1]['isCreator']
    assert launch['feeSlots'][1]['admin'].lower()==HELM
    assert launch['taxConfiguration']['creatorAllocationShare']==65_000_000
    assert launch['taxConfiguration']['dividendAllocationShare']==25_000_000
    assert launch['taxConfiguration']['burnAllocationShare']==10_000_000
    assert plan.steps[0].to==node.v5['factory'] and plan.steps[0].value==0
    assert '99% opening' in plan.notes['anti_sniping']


async def test_stable_creator_buy_encodes_two_hops_and_enforces_simulated_minimum():
    node=V5Node('robinhood');adapter=v5.ClankerV5Adapter('clanker-robinhood','robinhood',node)
    req=await request(adapter,clanker_quote='USDG',buy=.01)
    plan=await adapter.prepare_create(req)
    assert req.options['clanker_v5_terms']['quote_decimals']==6
    name,launch,(buy,)=node.simulations[-1]
    assert name=='createLaunchAndBuy'
    assert buy[1]==10**16 and buy[2]==950*10**18
    hops=decode(['(uint8,address,address,address,uint24,int24,address,bytes)[]'],buy[3])[0]
    assert [hop[0] for hop in hops]==[1,2]
    assert hops[1][1].lower()==node.v5['stable'].lower() and hops[1][2].lower()==plan.token.lower()
    assert plan.steps[0].value==10**16


async def test_new_launch_rejects_wrong_chain_code_policy_change_and_unregistered_quote():
    node=V5Node();adapter=v5.ClankerV5Adapter('clanker','base',node);req=await request(adapter)
    node.config_version+=1
    with pytest.raises(AdapterError,match='terms changed'):await adapter.prepare_create(req)
    node.quote_registered=False
    with pytest.raises(AdapterError,match='no longer admits'):await adapter.launch_terms({})
    node.quote_registered=True;node.creation_open=False
    with pytest.raises(AdapterError,match='not open'):await adapter.launch_terms({})
    node.creation_open=True;node.chain_id=1
    with pytest.raises(AdapterError,match='wrong chain'):await adapter.launch_terms({})
    node.chain_id=8453;node.no_code.add(node.v5['factory'].lower());adapter._verified_at=-1
    with pytest.raises(AdapterError,match='bytecode'):await adapter.launch_terms({})


async def test_stock_pair_requires_eligibility_and_disables_unverified_initial_buy():
    node=V5Node();adapter=v5.ClankerV5Adapter('clanker','base',node)
    stock=next(q for q in v5.QUOTES['base'] if q['kind']=='stock')
    assert 'eligibility' in adapter.deploy_warnings(0,{'clanker_quote':stock['address']})[0]
    assert 'zero' in adapter.deploy_warnings(1,{'clanker_quote':stock['address'],'quote_eligibility':'confirmed'})[0]
    req=await request(adapter,clanker_quote=stock['address'],quote_eligibility='confirmed')
    await adapter.prepare_create(req)
    assert node.last_launch['quoteToken'].lower()==stock['address'].lower()
    assert req.options['clanker_v5_terms']['quote_decimals']==stock['decimals']
    with pytest.raises(AdapterError):adapter.quote({'clanker_quote':'0x'+'55'*20})


async def test_v5_claim_uses_actual_pair_currency_and_preserves_v4_claim_route():
    node=V5Node('robinhood');adapter=v5.ClankerV5Adapter('clanker-robinhood','robinhood',node)
    req=await request(adapter);plan=await adapter.prepare_create(req)
    claim=await adapter.prepare_claim(ClaimRequest(DEV,plan.token,options={'clanker_version':5}))
    recipient,currency=decode(['address','address'],claim.steps[0].data[4:])
    assert recipient.lower()==DEV and currency.lower()==node.v5['stable'].lower()
    assert 'across all' in claim.notes['claim_scope']
    from .test_clanker import ClankerNode,TOKEN
    legacy_node=ClankerNode('base');legacy=v5.ClankerV5Adapter('clanker','base',legacy_node)
    old=await legacy.prepare_claim(ClaimRequest(DEV,TOKEN,options={}))
    assert old.steps[0].label=='collect' and old.steps[0].to==legacy.cfg['locker']


async def test_registry_draft_edit_and_stale_factory_terms_require_new_approval(client,fake_rpc,monkeypatch):
    from app.chains import registry
    node=V5Node();adapter=v5.ClankerV5Adapter('clanker','base',node)
    registry._cache['clanker']=adapter
    app.dependency_overrides[get_executor]=lambda:Executor(fake_rpc,get_signer(),evm_rpcs={'base':node})
    try:
        headers=await signup(client,main=False)
        wallet=(await client.post('/wallets',headers=headers,json={'network':'base','count':1})).json()[0]
        node.balances[wallet['address'].lower()]=10**18
        schema=await client.get('/launchpads/clanker/options')
        assert schema.status_code==200,schema.text
        buy=next(f for f in schema.json()['fields'] if f['key']=='buy_tax_pct')
        assert buy['min']==1 and buy['max']==10
        body={'launchpad':'clanker','name':'V5 draft','symbol':'TEST','dev_wallet_id':wallet['id'],'options':{'buy_tax_pct':2,'sell_tax_pct':3}}
        launch=await client.post('/launches',headers=headers,json=body)
        assert launch.status_code==201,launch.text
        assert launch.json()['clanker_version']==5
        from app.routes import launches
        async def metadata(*args):
            return {'name':'Cloned token','symbol':'CLONE','description':'Fixture','image':None,'socials':{},'copied':['name','symbol']}
        monkeypatch.setattr(launches,'source_metadata',metadata)
        clone=await client.post('/launches/clone',headers=headers,json={'network':'base','mint':DEV,'launchpad':'clanker'})
        assert clone.status_code==201 and clone.json()['clanker_version']==5,clone.text
        from app.db import get_sessionmaker
        from app.models import Launch
        async with get_sessionmaker()() as db:
            legacy=await db.get(Launch,clone.json()['id'])
            legacy.launchpad_params={**legacy.launchpad_params,'clanker_version':4,'sniper_start_pct':80,'sniper_decay_s':120}
            await db.commit()
        migrated=await client.patch('/launches/'+clone.json()['id'],headers=headers,json={**body,'expected_revision':0})
        assert migrated.status_code==200 and migrated.json()['clanker_version']==5,migrated.text
        async with get_sessionmaker()() as db:
            migrated_launch=await db.get(Launch,clone.json()['id'])
            assert 'sniper_start_pct' not in migrated_launch.launchpad_params and 'sniper_decay_s' not in migrated_launch.launchpad_params
        path='/launches/'+launch.json()['id']+'/deploy'
        first=(await client.post(path,headers=headers,json={})).json()
        assert first['preflight']['clanker_v5_terms']['config_version']=='1'
        node.config_version=2
        stale=await client.post(path,headers=headers,json={'confirm':True,'preflight_hash':first['preflight_hash'],'idempotency_key':'outdated-v5'})
        assert stale.status_code==409 and not node.sent
        fresh=(await client.post(path,headers=headers,json={})).json()
        sent=await client.post(path,headers=headers,json={'confirm':True,'preflight_hash':fresh['preflight_hash'],'idempotency_key':'approved-v5'})
        assert sent.status_code==200,sent.text
        job=(await client.get('/jobs/'+sent.json()['job_id'],headers=headers)).json()
        assert job['status']=='confirmed',job
        live=(await client.get('/launches/'+launch.json()['id'],headers=headers)).json()
        assert live['status']=='live' and live['mint_address'].lower().endswith('cccc') and live['clanker_version']==5
        assert len(node.sent)==1 and node.sent[0]['to'].lower()==node.v5['factory'].lower()
        node.owed=0
        empty=(await client.post('/launches/'+live['id']+'/claim-fees',headers=headers,json={'wallet_id':wallet['id']})).json()
        assert empty['preflight']['warnings'] and 'across all' in empty['preflight']['claim_scope']
        node.owed=12345;node.receiver=wallet['address']
        dividends='/launches/'+live['id']+'/claim-dividends'
        preview=await client.post(dividends,headers=headers,json={'wallet_id':wallet['id']})
        assert preview.status_code==200,preview.text
        assert preview.json()['preflight']['recipient'].lower()==wallet['address'].lower()
        assert preview.json()['preflight']['platform_fee']=='none'
        confirmed=await client.post(dividends,headers=headers,json={'wallet_id':wallet['id'],'confirm':True,
            'preflight_hash':preview.json()['preflight_hash'],'idempotency_key':'claim-v5-dividends'})
        assert confirmed.status_code==200,confirmed.text
        dividend_job=(await client.get('/jobs/'+confirmed.json()['job_id'],headers=headers)).json()
        assert dividend_job['status']=='confirmed',dividend_job
        assert len(node.sent)==2 and node.sent[-1]['to'].lower()==node.dividend.lower()
        node.excluded=True
        empty=await client.post(dividends,headers=headers,json={'wallet_id':wallet['id']})
        assert empty.status_code==200 and empty.json()['preflight']['warnings']
        node.receiver=HELM
        blocked=await client.post(dividends,headers=headers,json={'wallet_id':wallet['id']})
        assert blocked.status_code==400 and 'custom dividend receiver' in blocked.text
        assert len(node.sent)==2
    finally:
        app.dependency_overrides.pop(get_executor,None)
        registry._cache.pop('clanker',None)


async def test_dividend_claim_rejects_wrong_token_receiver_chain_and_unpayable_balance():
    node=V5Node();adapter=v5.ClankerV5Adapter('clanker','base',node)
    node.claim_token=DEV
    req=ClaimRequest(DEV,DEV,options={'clanker_version':5})
    plan=await adapter.prepare_dividend_claim(req)
    assert plan.steps[0].data==v5.calldata('withdrawDividends') and plan.steps[0].to==node.dividend
    for change in ('excluded','empty','unpayable','disabled'):
        node.excluded=change=='excluded';node.owed=0 if change=='empty' else 12345
        node.dividend_paid=change!='unpayable';node.dividend=v5.ZERO if change=='disabled' else '0x'+'55'*20
        assert not (await adapter.prepare_dividend_claim(req)).steps
    node.dividend='0x'+'55'*20;node.claim_token=HELM
    with pytest.raises(AdapterError,match='different token'):await adapter.prepare_dividend_claim(req)
    node.claim_token=DEV;node.receiver=HELM
    with pytest.raises(AdapterError,match='custom dividend receiver'):await adapter.prepare_dividend_claim(req)
    node.chain_id=56
    with pytest.raises(AdapterError,match='wrong chain'):await adapter.prepare_dividend_claim(req)


async def test_replica_lag_retries_whole_snapshot_and_never_masks_contract_errors():
    from app.chains.evm import EvmRPCError
    node=V5Node();adapter=v5.ClankerV5Adapter('clanker','base',node)
    original=node.call;tags=[];failed=False
    async def lagging(method,params):
        nonlocal failed
        if method=='eth_call' and params[1]=='0x64':
            failed=True
            raise EvmRPCError('eth_call: unsupported block number 100')
        if method=='eth_call':tags.append(params[1])
        return await original(method,params)
    node.call=lagging
    assert (await adapter.launch_terms({}))['version']==5
    assert failed and set(tags)=={'0x62'}
    async def reverted(method,params):
        if method=='eth_call':raise EvmRPCError('execution reverted: LaunchClosed')
        return await original(method,params)
    node.call=reverted
    with pytest.raises(EvmRPCError,match='LaunchClosed'):await adapter.launch_terms({})
