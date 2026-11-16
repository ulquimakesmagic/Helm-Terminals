"""Locally encoded Clanker V5 launches. V4 management remains in clanker.py.

ABI and routes: official clanker-sdk 5.1.0, pinned in contracts/ClankerV5.json.
No hosted deployment API, imported key, arbitrary route bytes or external signer.
"""
import asyncio
from decimal import Decimal
import hashlib
import json
import os
import time
from pathlib import Path

from eth_abi import encode, decode
from eth_utils import keccak, to_checksum_address

from .base import AdapterError, CreateRequest, ClaimRequest, EvmPlan, EvmStep
from .clanker import ClankerAdapter, CLANKER_CHAINS, ZERO, selector
from .evm import EvmRPCError

ARTIFACT = json.loads((Path(__file__).parent / 'contracts/ClankerV5.json').read_text(encoding='utf-8'))
ABI = {item['name']: item for item in ARTIFACT['abi'] if item['type'] == 'function'}
QUOTES = json.loads((Path(__file__).parent/'contracts/ClankerV5Quotes.json').read_text(encoding='utf-8'))['quotes']
RUNTIMES = json.loads((Path(__file__).parent/'contracts/ClankerV5Runtimes.json').read_text(encoding='utf-8'))['networks']
SCALE = 100_000_000
CHAINS = {
    'base': {'chain_id':8453, 'factory':'0x572e88D3cD7f23E4241EB6ceDD96667271bF09A0',
        'hook':'0x5E320eA6278107968C119942E57d876c5F922AcC', 'escrow':'0x5dd237d1f716C65426CCd2FAe82B95109825FA61',
        'token_deployer':'0xc25FfE6374EdcCb5bFCcA3b9684046a426AbCE6A',
        'stable':'0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', 'stable_symbol':'USDC', 'stable_decimals':6, 'v3_fee':500},
    'bsc': {'chain_id':56, 'factory':'0xcA111dcdd2cE924c3378d6514fDCbC46217bcA16',
        'hook':'0xA8F0c64D5E5CF05379c13263EbdD03e8A835EACC', 'escrow':'0xFBB6e837A2eA96d6b4beb4788c2749Cf34A997e7',
        'token_deployer':'0xE76c413b26c7006CF3ac747b46A8F05E0Ccf3287',
        'stable':'0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d', 'stable_symbol':'USDC', 'stable_decimals':18, 'v3_fee':100},
    'robinhood': {'chain_id':4663, 'factory':'0xcA111dcdd2cE924c3378d6514fDCbC46217bcA16',
        'hook':'0x565189a6780D1a50FC1792F64db8e4881119AaCC', 'escrow':'0xFBB6e837A2eA96d6b4beb4788c2749Cf34A997e7',
        'token_deployer':'0xE76c413b26c7006CF3ac747b46A8F05E0Ccf3287',
        'stable':'0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168', 'stable_symbol':'USDG', 'stable_decimals':6, 'v3_fee':100},
}


def abi_type(item):
    return '(' + ','.join(abi_type(c) for c in item['components']) + ')' + item['type'][5:] if item['type'].startswith('tuple') else item['type']


def abi_value(item, value):
    if item['type'].startswith('tuple'):
        if item['type'].endswith('[]'):
            return [abi_value({**item, 'type':'tuple'}, row) for row in value]
        return tuple(abi_value(c, value[c['name']]) for c in item['components']) if isinstance(value, dict) else tuple(value)
    return value


def named(item, value):
    if item['type'].startswith('tuple'):
        if item['type'].endswith('[]'):
            return [named({**item,'type':'tuple'}, row) for row in value]
        return {c['name']:named(c, v) for c,v in zip(item['components'],value)}
    return value


def calldata(name, *values):
    inputs = ABI[name]['inputs']
    types = [abi_type(item) for item in inputs]
    return selector(name + '(' + ','.join(types) + ')') + encode(types, [abi_value(item, value) for item,value in zip(inputs,values)])


async def read(rpc, address, name, *values, block='latest'):
    raw = await rpc.call('eth_call', [{'to':address,'data':'0x'+calldata(name,*values).hex()},block])
    outputs = ABI[name]['outputs']
    decoded = decode([abi_type(item) for item in outputs],bytes.fromhex(raw.removeprefix('0x')))
    values = tuple(named(item,value) for item,value in zip(outputs,decoded))
    return values[0] if len(values)==1 else values


def units(value, decimals=6):
    amount = Decimal(str(value)) * 10**decimals
    if not amount.is_finite() or amount != amount.to_integral_value() or amount < 0:
        raise AdapterError('Use a nonnegative amount within the supported precision')
    return int(amount)


def predict(caller, deployer, init_hash, salt):
    scoped = keccak(encode(['address','bytes32'],[caller,salt]))
    return to_checksum_address(keccak(b'\xff'+bytes.fromhex(deployer[2:])+scoped+init_hash)[-20:])


def mine(caller, deployer, init_hash, max_tries=1_000_000):
    # Fresh cryptographic starting point; bounded local search, never a remote vanity service.
    salt = int.from_bytes(os.urandom(32),'big')
    caller_word=bytes.fromhex(caller[2:]).rjust(32,b'\0')
    prefix=b'\xff'+bytes.fromhex(deployer[2:])
    for i in range(max_tries):
        candidate = ((salt+i) % 2**256).to_bytes(32,'big')
        raw=keccak(prefix+keccak(caller_word+candidate)+init_hash)[-20:]
        if raw[-2:]==b'\xcc\xcc':
            return candidate,to_checksum_address(raw)
    raise AdapterError('No valid Clanker token address found in the bounded search; review and try again')


class ClankerV5Adapter(ClankerAdapter):
    quote_assets = ['native','stable']
    deploy_gas_limit = 8_000_000

    def __init__(self, launchpad_id, network, rpc, platform_wallet=None, platform_reward_bps=None):
        if network in CLANKER_CHAINS:
            super().__init__(launchpad_id,network,rpc,platform_wallet,platform_reward_bps)
        else:
            self.id,self.network,self.rpc = launchpad_id,network,rpc
            self.cfg={}
            self._wallet,self._reward_bps=platform_wallet,platform_reward_bps
        self.v5 = CHAINS[network]
        self._verified_at = -1.0
        self._options_cache = None

    def options_schema(self):
        coin = 'BNB' if self.network=='bsc' else 'ETH'
        return {'requires_image':False,'socials':['twitter','telegram','website'],'fields':[
            {'key':'dev_buy_sol','type':'number','min':0,'default':0,'label':f'Creator buy ({coin})',
             'hint':'Atomic native-coin buy. Stable pairs route the native coin through the verified launch-buy adapter.'},
            {'key':'clanker_quote','type':'select','options':['native',self.v5['stable_symbol']]+[q['address'] for q in QUOTES[self.network] if q['kind'] not in ('native','stable')],
             'option_labels':{q['address']:q['symbol']+' · '+q['name']+' · '+q['address'][:6]+'…'+q['address'][-4:]+(' · stock token' if q['kind']=='stock' else '') for q in QUOTES[self.network]},
             'asset_kinds':{q['address']:q['kind'] for q in QUOTES[self.network]},'default':'native','label':'Pair asset',
             'hint':'Official quote discovery snapshot. The factory rechecks availability before deployment. Other quote assets require a zero creator buy in Helm.'},
            {'key':'quote_eligibility','type':'select','options':['not_confirmed','confirmed'],'default':'not_confirmed','label':'Stock-token eligibility',
             'option_labels':{'not_confirmed':'Not confirmed (not needed for native, stable or crypto pairs)','confirmed':'I reviewed the issuer’s eligibility terms and am eligible to use this stock token'},
             'hint':'Required for stock-token pairs. A tokenized asset does not itself establish ownership of the underlying shares.'},
            {'key':'buy_tax_pct','type':'number','min':0,'max':99,'default':1,'label':'Buy tax (%)','hint':'Total launchpad tax including Clanker’s fee. Checked against the live factory bounds.'},
            {'key':'sell_tax_pct','type':'number','min':0,'max':99,'default':1,'label':'Sell tax (%)'},
            {'key':'dividend_share_pct','type':'number','min':0,'max':100,'default':0,'label':'Holder dividends (%)','hint':'Share of tax after the Clanker fee; the rest, less burns, is creator rewards.'},
            {'key':'minimum_dividend_tokens','type':'number','min':1,'max':1_000_000_000,'default':10000,'label':'Minimum holder balance (tokens)','hint':'Whole tokens. Checked against the factory minimum and launch supply when dividends are enabled.'},
            {'key':'burn_share_pct','type':'number','min':0,'max':100,'default':0,'label':'Burn allocation (%)','hint':'Share of tax after the Clanker fee. Burns plus dividends cannot exceed 100%.'},
            {'key':'anti_sniping','type':'select','options':['on','off'],'default':'on','label':'Launch anti-sniping','hint':'The factory controls the opening fee and decay; there is no custom V4 sniper schedule.'},
            {'key':'metadata_editable','type':'select','options':['on','off'],'default':'on','label':'Allow creator metadata edits'},
            {'key':'slippage_pct','type':'number','min':0,'max':49,'default':5}]}

    def quote(self, params):
        selected=params.get('clanker_quote','native')
        if selected in ('native',self.v5['stable_symbol']):
            return ZERO if selected=='native' else self.v5['stable']
        found=next((q for q in QUOTES[self.network] if q['address'].lower()==str(selected).lower()),None)
        if found is None:
            raise AdapterError('Choose an asset from the supported Clanker quote list')
        return found['address']

    def quote_info(self, params):
        address=self.quote(params)
        return next(q for q in QUOTES[self.network] if q['address'].lower()==address.lower())

    def tax(self, params):
        buy,sell=units(params.get('buy_tax_pct',1)),units(params.get('sell_tax_pct',1))
        dividend,burn=units(params.get('dividend_share_pct',0)),units(params.get('burn_share_pct',0))
        if buy>99_000_000 or sell>99_000_000 or dividend+burn>SCALE:
            raise AdapterError('Tax must be at most 99%; dividend and burn allocations together must be at most 100%')
        if max(buy,sell)==0 and dividend+burn:
            raise AdapterError('A zero-tax launch cannot allocate tax to dividends or burns')
        minimum=units(params.get('minimum_dividend_tokens',10000),0)
        if dividend and not minimum:
            raise AdapterError('Holder dividends require a positive minimum holding')
        return {'buyTaxRate':buy,'sellTaxRate':sell,'creatorAllocationShare':SCALE-dividend-burn if buy or sell else 0,
                'burnAllocationShare':burn,'dividendAllocationShare':dividend,'integratorAllocationShare':0,
                'minimumDividendHolding':minimum*10**18 if dividend else 0}

    def deploy_warnings(self, dev_buy, params):
        try:
            self.tax(params);quote=self.quote_info(params)
            if quote['kind']=='stock' and params.get('quote_eligibility')!='confirmed':
                return ['Confirm the stock-token issuer’s eligibility terms before launching this pair']
            if quote['kind'] not in ('native','stable') and dev_buy:
                return ['This pair has no verified atomic creator-buy route in Helm. Set the creator buy to zero']
            return []
        except AdapterError as error:
            return [str(error)]

    async def launch_terms(self, params, *, validate=True):
        # Load-balanced RPCs can advertise a head their read replicas do not yet serve.
        # Retry the entire coherent snapshot, never mix fields from different block tags.
        for lag in (0,2,5):
            try:
                return await self._launch_terms_at(params,validate=validate,lag=lag)
            except EvmRPCError as error:
                if not any(text in str(error).lower() for text in ('unsupported block number','header not found','unknown block')):
                    raise
        raise AdapterError('Clanker RPC could not serve a consistent recent block; retry the review')

    async def _launch_terms_at(self, params, *, validate, lag):
        factory=self.v5['factory']
        if await self.rpc.get_chain_id()!=self.v5['chain_id']:
            raise AdapterError('Clanker RPC is on the wrong chain')
        await self.verify_contracts()
        block=await self.rpc.call('eth_getBlockByNumber',['latest',False])
        tag=hex(max(0,int(block['number'],16)-lag))
        names=['configVersion','nativeLaunchFee','launchSupply','tokenDeployer','taxPolicy','launchCreationEnabled','launchBuyAdapter','hook','tickSpacing']
        values=await asyncio.gather(*(read(self.rpc,factory,name,block=tag) for name in names))
        state=dict(zip(names,values));quote=self.quote(params)
        if not state['launchCreationEnabled']:
            raise AdapterError('Clanker V5 launches are not open on this chain')
        if state['tokenDeployer'].lower()!=self.v5['token_deployer'].lower() or state['hook'].lower()!=self.v5['hook'].lower():
            raise AdapterError('Clanker factory contracts differ from the pinned SDK deployment')
        registered,decimals,start_tick=await read(self.rpc,factory,'quoteConfig',quote,block=tag)
        if not registered:
            raise AdapterError('Clanker no longer admits this quote asset')
        expected_decimals=self.quote_info(params)['decimals']
        if decimals!=expected_decimals:
            raise AdapterError('Clanker quote decimals differ from the verified asset')
        tax=self.tax(params);bounds=state['taxPolicy']['taxFeeBounds']
        if validate and (not bounds['minimumBuyTaxRate']<=tax['buyTaxRate']<=bounds['maximumBuyTaxRate'] or not bounds['minimumSellTaxRate']<=tax['sellTaxRate']<=bounds['maximumSellTaxRate']):
            raise AdapterError('Buy or sell tax is outside Clanker’s current factory bounds')
        minimum=tax['minimumDividendHolding']
        if validate and tax['dividendAllocationShare'] and not state['taxPolicy']['minimumDividendHoldingRequirement']<=minimum<=state['launchSupply']:
            raise AdapterError('The dividend holding threshold is outside the factory minimum and token supply')
        return {'version':5,'network':self.network,'factory':factory,'hook':state['hook'],
            'token_deployer':state['tokenDeployer'],'escrow':self.v5['escrow'],'launch_buy_adapter':state['launchBuyAdapter'],
            'config_version':str(state['configVersion']),'native_launch_fee_raw':str(state['nativeLaunchFee']),
            'supply_raw':str(state['launchSupply']),'quote_token':quote,'quote_decimals':decimals,
            'quote_start_tick':start_tick,'tick_spacing':state['tickSpacing'],'policy':state['taxPolicy']}

    async def verify_contracts(self):
        if await self.rpc.get_chain_id()!=self.v5['chain_id']:
            raise AdapterError('Clanker RPC is on the wrong chain')
        if self._verified_at>=0 and time.monotonic()-self._verified_at<300:
            return  # Published V5 contracts are non-upgradeable; policy is read separately on each preview.
        for key,expected in RUNTIMES[self.network].items():
            raw=await self.rpc.get_code(self.v5[key])
            if hashlib.sha256(bytes.fromhex(raw.removeprefix('0x'))).hexdigest()!=expected:
                raise AdapterError('Clanker deployed bytecode differs from the pinned network contracts; creation is disabled')
        self._verified_at=time.monotonic()

    async def live_options_schema(self):
        if self._options_cache and time.monotonic()-self._options_cache[0]<30:
            return self._options_cache[1]
        schema=self.options_schema()
        terms=await self.launch_terms({},validate=False);bounds=terms['policy']['taxFeeBounds']
        for field in schema['fields']:
            if field['key'] in ('buy_tax_pct','sell_tax_pct'):
                side='Buy' if field['key']=='buy_tax_pct' else 'Sell'
                field.update(min=bounds['minimum'+side+'TaxRate']/1_000_000,max=bounds['maximum'+side+'TaxRate']/1_000_000)
                field['default']=max(field['min'],min(field['max'],field['default']))
            if field['key']=='minimum_dividend_tokens':
                minimum=-(-terms['policy']['minimumDividendHoldingRequirement']//10**18)
                field.update(min=minimum,default=minimum)
        schema['notes']=['V5 opening anti-sniping fee and decay follow the live factory policy. Dividends with no eligible holders go to Clanker. Claims pay the whole recipient balance in one pair currency, across V5 pools.']
        self._options_cache=(time.monotonic(),schema)
        return schema

    def deploy_notes(self, params):
        tax=self.tax(params);coin=self.quote_info(params)['symbol']
        sniper=((params.get('clanker_v5_terms') or {}).get('policy') or {}).get('antiSnipeConfiguration')
        return {'protocol':'Clanker V5','pair':coin,'pool':'Uniswap V4, whole supply in permanently locked single-sided liquidity',
            'launchpad_tax':f"{tax['buyTaxRate']/1_000_000:g}% buy / {tax['sellTaxRate']/1_000_000:g}% sell; includes Clanker’s protocol fee",
            'tax_allocations':f"After Clanker: {tax['creatorAllocationShare']/1_000_000:g}% creator rewards, {tax['dividendAllocationShare']/1_000_000:g}% holder dividends, {tax['burnAllocationShare']/1_000_000:g}% burn",
            'creator_rewards':'97.5% creator / 2.5% Helm, paid in the pair asset from the fee escrow',
            'anti_sniping':(f"{sniper['startTotalFeeRate']/1_000_000:g}% opening total fee, decaying over {sniper['windowSeconds']} seconds; extra anti-snipe fee goes to Clanker" if sniper else 'Factory-controlled opening fee and decay') if params.get('anti_sniping','on')=='on' else 'Off',
            'claim_scope':'Each V5 claim pays this recipient’s whole balance in the pair currency, across all V5 pools'}

    async def _buy_route(self, quote, token, terms):
        hop=lambda src:(2,src,token,ZERO,0,terms['tick_spacing'],terms['hook'],b'')
        hops=[]
        if quote!=ZERO:
            adapter=terms['launch_buy_adapter']
            async def simple(address,sig,outputs,args=(),types=()):
                raw=await self.rpc.call('eth_call',[{'to':address,'data':'0x'+(selector(sig)+encode(types,args)).hex()},'latest'])
                return decode(outputs,bytes.fromhex(raw[2:]))[0]
            wrapped,factory,maximum=await asyncio.gather(
                simple(adapter,'wrappedNative()',['address']),simple(adapter,'uniswapV3Factory()',['address']),simple(adapter,'maxExternalPoolFee()',['uint24']))
            fee=self.v5['v3_fee']
            if factory==ZERO or fee>maximum:
                raise AdapterError('The Clanker stable-pair buy route is unavailable')
            pool=await simple(factory,'getPool(address,address,uint24)',['address'],(wrapped,quote,fee),('address','address','uint24'))
            if pool==ZERO:
                raise AdapterError('The required native-to-stable V3 pool is unavailable')
            hops.append((1,wrapped,quote,pool,fee,0,ZERO,b''))
        hops.append(hop(quote))
        return encode(['(uint8,address,address,address,uint24,int24,address,bytes)[]'],[hops])

    async def prepare_create(self, req:CreateRequest):
        params=req.options or {};terms=await self.launch_terms(params)
        if warnings:=self.deploy_warnings(req.dev_buy_sol,params):
            raise AdapterError(warnings[0])
        if params.get('clanker_v5_terms')!=terms:
            raise AdapterError('Clanker launch terms changed; review deployment again before signing')
        treasury,share=self._share(params)
        if share!=250 or not treasury or treasury.lower()==req.dev_pubkey.lower():
            raise AdapterError('New Helm Clanker launches require distinct creator and treasury recipients with the 2.5% split')
        block=await self.rpc.call('eth_getBlockByNumber',['latest',False])
        metadata={'description':req.description or '', 'socialMediaUrls':[{'platform':key,'url':value} for key,value in (req.socials or {}).items() if key in ('twitter','telegram','website') and value]}
        launch={'creator':ZERO,'feeSlots':[{'recipient':req.dev_pubkey,'admin':req.dev_pubkey,'relativeWeightBps':9750,'isCreator':True},
            {'recipient':treasury,'admin':treasury,'relativeWeightBps':250,'isCreator':False}],
            'tokenName':req.name,'tokenSymbol':req.symbol,'tokenContractURI':'','creatorSalt':os.urandom(32),
            'quoteToken':terms['quote_token'],'expectedConfigVersion':int(terms['config_version']),
            'deadline':int(block['timestamp'],16)+300,'metadataEditable':params.get('metadata_editable','on')=='on',
            'metadataKeys':['image','metadata','context'],'metadataValues':[params.get('logo_url',''),json.dumps(metadata,separators=(',',':')),json.dumps({'interface':'Helm Terminals'},separators=(',',':'))],
            'taxConfiguration':self.tax(params),'creatorFeeRecipient':req.dev_pubkey,'integratorFeeRecipient':ZERO,
            'antiSnipeEnabled':params.get('anti_sniping','on')=='on'}
        init_hash=await read(self.rpc,terms['factory'],'launchTokenInitCodeHash',launch)
        salt,token=await asyncio.to_thread(mine,req.dev_pubkey,terms['token_deployer'],init_hash)
        launch['creatorSalt']=salt
        native_buy=units(req.dev_buy_sol,18);fee=int(terms['native_launch_fee_raw'])
        name,args,value='createLaunch',[launch],fee
        if native_buy:
            if terms['launch_buy_adapter']==ZERO:
                raise AdapterError('This Clanker factory has no atomic creator-buy adapter')
            route=await self._buy_route(terms['quote_token'],token,terms)
            buy={'fundingToken':ZERO,'amountIn':native_buy,'minAmountOut':1,'routeData':route}
            name,args,value='createLaunchAndBuy',[launch,buy],fee+native_buy
        async def simulate():
            data=calldata(name,*args)
            raw=await self.rpc.call('eth_call',[{'from':req.dev_pubkey,'to':terms['factory'],'value':hex(value),'data':'0x'+data.hex()},'latest'])
            return decode([abi_type(o) for o in ABI[name]['outputs']],bytes.fromhex(raw[2:]))
        result=await simulate()
        if result[0].lower()!=token.lower():
            raise AdapterError('Clanker dry run returned a different token than the locally predicted address')
        if native_buy:
            slippage=units(req.slippage_pct,2)
            if not 0<=slippage<5000 or result[2]<=0:
                raise AdapterError('Invalid creator-buy quote or slippage')
            args[1]['minAmountOut']=max(1,result[2]*(10_000-slippage)//10_000)
            await simulate()  # exact final minimum, not the one-unit probe
        data=calldata(name,*args)
        return EvmPlan([EvmStep('launch',terms['factory'],data,value)],token=token,
            notes={**self.deploy_notes(params),'clanker_version':5,'minimum_creator_tokens_raw':str(args[1]['minAmountOut']) if native_buy else None})

    async def prepare_claim(self, req:ClaimRequest):
        if int((req.options or {}).get('clanker_version',4))!=5:
            if self.network not in CLANKER_CHAINS:
                raise AdapterError('No legacy Clanker deployment is supported on this chain')
            return await super().prepare_claim(req)
        if not req.mint:
            raise AdapterError('This launch has no token yet')
        await self.verify_contracts()
        rights=await read(self.rpc,self.v5['factory'],'creatorRights',req.mint)
        raw_pool=await read(self.rpc,self.v5['hook'],'poolConfig',rights['poolId'])
        pool=dict(zip([item['name'] for item in ABI['poolConfig']['outputs']],raw_pool))
        if not pool['initialized'] or pool['token'].lower()!=req.mint.lower():
            raise AdapterError('Clanker pool does not match this token')
        currency=pool['quoteToken']
        owed=await read(self.rpc,self.v5['escrow'],'owed',req.wallet_pubkey,currency)
        steps=[EvmStep('claim',self.v5['escrow'],calldata('claimFor',req.wallet_pubkey,currency))] if owed else []
        return EvmPlan(steps,token=req.mint,notes={'pending_raw':str(owed),'currency':currency,
            'claim_scope':'Whole recipient balance in this currency across all Clanker V5 pools; never a token-only balance'})

    async def prepare_dividend_claim(self, req:ClaimRequest):
        if (req.options or {}).get('clanker_version')!=5 or not req.mint:
            raise AdapterError('Holder dividends require a deployed Clanker V5 token')
        await self.verify_contracts()
        dividend=await read(self.rpc,self.v5['factory'],'dividendOf',req.mint)
        if dividend==ZERO:
            return EvmPlan([],token=req.mint,notes={'claim_scope':'This token has no holder-dividend contract'})
        token,currency,receiver,excluded,owed=await asyncio.gather(
            read(self.rpc,dividend,'taxToken'),read(self.rpc,dividend,'quoteToken'),
            read(self.rpc,dividend,'receiverOf',req.wallet_pubkey),
            read(self.rpc,dividend,'excludedFromDividends',req.wallet_pubkey),
            read(self.rpc,dividend,'withdrawableDividendOf',req.wallet_pubkey))
        if token.lower()!=req.mint.lower():
            raise AdapterError('The dividend contract belongs to a different token')
        if receiver.lower()!=req.wallet_pubkey.lower():
            raise AdapterError('Reset this wallet’s custom dividend receiver to its own address before claiming in Helm')
        steps=[]
        if owed and not excluded:
            data=calldata('withdrawDividends')
            raw=await self.rpc.call('eth_call',[{'from':req.wallet_pubkey,'to':dividend,'data':'0x'+data.hex()},'latest'])
            if decode(['bool'],bytes.fromhex(raw.removeprefix('0x')))[0]:
                steps=[EvmStep('claim-dividends',dividend,data)]
        return EvmPlan(steps,token=req.mint,notes={'currency':currency,'recipient':receiver,
            'claim_scope':'All currently withdrawable holder dividends for this token and wallet; no creator-reward deduction'})
