"""Execute one reviewed provider-plan phase, with no automatic financial replay.

Lookup-table setup is not launch/claim completion. Each subsequent phase needs
the same approved business intent, asset, fee ceilings and cumulative spend cap.
"""
import base64
from dataclasses import replace
import json
import re

from ..chains.base import AdapterError
from ..chains.solana_plan import Plan,number
from .executor import SubmitResult,_finish,_persist_pending_transaction,_record
from .execution_journal import require_steps
from .solana_guard import SpendLimits


def receipt_debits(receipt,signature,wallet):
    """Only the canonical transaction's changes count; unrelated wallet deposits
    cannot replenish the approved debit budget for a later stage.
    """
    try:
        transaction=receipt['transaction'];meta=receipt['meta']
        if not isinstance(meta,dict) or meta.get('err') is not None or transaction['signatures'][0]!=signature:
            raise ValueError()
        keys=transaction['message']['accountKeys']
        keys=[key['pubkey'] if isinstance(key,dict) else key for key in keys]
        loaded=meta.get('loadedAddresses') or {}
        keys=keys+loaded.get('writable',[])+loaded.get('readonly',[])
        if len(keys)!=len(set(keys)) or len(meta['preBalances'])!=len(keys) or len(meta['postBalances'])!=len(keys):raise ValueError()
        index=keys.index(wallet)
        native=max(0,number(meta['preBalances'][index],'receipt balance')-number(meta['postBalances'][index],'receipt balance'))
        totals=[]
        for field in ('preTokenBalances','postTokenBalances'):
            amounts={};seen=set()
            for balance in meta.get(field) or []:
                index=number(balance['accountIndex'],'token account index',len(keys)-1)
                if index in seen:raise ValueError()
                seen.add(index)
                if balance.get('owner')!=wallet:continue
                mint=balance['mint'];amount=number(balance['uiTokenAmount']['amount'],'receipt token amount')
                amounts[mint]=amounts.get(mint,0)+amount
            totals.append(amounts)
        tokens={mint:max(0,before-totals[1].get(mint,0)) for mint,before in totals[0].items()}
        return native,{mint:raw for mint,raw in tokens.items() if raw}
    except (KeyError,TypeError,ValueError,IndexError,AdapterError):
        raise AdapterError('Confirmed stage receipt could not prove the selected wallet’s debits') from None


async def lookup_ready(rpc,plan):
    """Check readiness and every lookup used by an immutable message."""
    from solders.transaction import VersionedTransaction
    current=await rpc.get_slot()
    if plan.minimum_slot_exclusive is not None and current<=plan.minimum_slot_exclusive:
        raise AdapterError('Lookup table is still warming up; no business transaction was sent')
    tables=set(plan.lookup_tables)
    for stage in plan.stages:
        tables.update(str(table.account_key) for table in VersionedTransaction.from_bytes(stage.raw).message.address_table_lookups)
    for address in tables:
        # A setup phase may be creating this table; its transaction is checked
        # independently, and the next business phase verifies the created state.
        if plan.readiness=='setup_required':continue
        state=await rpc.get_lookup_table_state(address)
        if state is None or state.meta.deactivation_slot!=2**64-1:
            raise AdapterError('A required lookup table is unavailable or deactivating')
        if current<=state.meta.last_extended_slot:
            raise AdapterError('A required lookup table has not finished warming up')
        table=await rpc.get_lookup_table(address)
        if table is None:raise AdapterError('A required lookup table is unavailable')
        for stage in plan.stages:
            for lookup in VersionedTransaction.from_bytes(stage.raw).message.address_table_lookups:
                if str(lookup.account_key)==address and any(i>=len(table.addresses) for i in [*lookup.writable_indexes,*lookup.readonly_indexes]):
                    raise AdapterError('A lookup table does not contain the required accounts')


async def run_plan(db,ex,job,wallet,kind,plan:Plan,limits:SpendLimits,*,intent_hash:str):
    if wallet.network!='solana' or wallet.kind!='managed' or wallet.user_id!=job.user_id or limits.wallet!=wallet.address:
        raise AdapterError('A provider plan requires the selected owned managed Solana wallet')
    if not isinstance(intent_hash,str) or not re.fullmatch(r'[0-9a-f]{64}',intent_hash):
        raise AdapterError('A provider plan requires a reviewed business-intent hash')
    if plan.kind not in ('config','launch','claim','update'):
        raise AdapterError('Unsupported provider-plan operation')
    if kind!=plan.kind:
        raise AdapterError('Execution kind differs from the reviewed provider plan')
    # A plan can only spend the budget explicitly approved by its caller. Never
    # derive permission from provider-supplied priority/resource metadata.
    budget={'native':number(limits.max_lamports_out,'approved native budget'),
            'tokens':{key:number(value,'approved token budget') for key,value in limits.token_out.items()}}
    identity={'intent_hash':intent_hash,'wallet_id':wallet.id,'kind':kind,'asset':vars(plan.asset),
        'mint':plan.base_mint,'flow':plan.flow,'budget':budget,'fee_limits':sorted([vars(limit) for limit in plan.fee_limits],key=lambda item:(item['mint'],item['token_program'])),
        'minimum_outputs':{'native':limits.min_lamports_in,'tokens':dict(limits.token_in_min)},'watch_mints':sorted(limits.watch_mints)}
    saved=(job.params or {}).get('provider_plan')
    if saved:
        if saved['identity']!=identity:raise AdapterError('Provider plan terms differ from the reviewed operation')
        if any(phase['status']!='setup_confirmed' for phase in saved['phases']):
            raise AdapterError('Previous provider stages require reconciliation; automatic replay is disabled')
    else:
        saved={'identity':identity,'phases':[],'native_spent':0,'tokens_spent':{}}
    if sum(len(phase['stages']) for phase in saved['phases'])+len(plan.stages)>40:
        raise AdapterError('Provider operation exceeded its bounded stage limit')
    if not plan.stages:
        job.params={**job.params,'provider_plan_business_pending':True,'provider_plan':saved,
                    'provider_plan_waiting':{'lookup_tables':list(plan.lookup_tables),'minimum_slot_exclusive':plan.minimum_slot_exclusive}}
        job.status='submitted';await db.commit()
        return SubmitResult(None,'waiting','Lookup-table setup is not a completed business operation')
    previous_messages={stage['message_sha256'] for phase in saved['phases'] for stage in phase['stages']}
    if previous_messages & {stage.message_sha256 for stage in plan.stages}:
        raise AdapterError('Provider tried to repeat an already submitted stage')
    await lookup_ready(ex.rpc,plan)
    # Prove the whole phase has not already expired before paying for its prefix.
    if await ex.rpc.get_block_height()>min(stage.last_valid_block_height for stage in plan.stages):
        raise AdapterError('Provider plan expired; reconcile any earlier work before requesting another plan')
    offset=sum(len(phase['stages']) for phase in saved['phases'])
    rows=[{'id':stage.id,'kind':stage.kind,'depends_on':list(stage.depends_on),
        'message_sha256':stage.message_sha256,'raw_base64':base64.b64encode(stage.raw).decode(),
        'last_valid_block_height':str(stage.last_valid_block_height),'blockhash':stage.blockhash,
        'tx_kind':kind if plan.readiness=='ready' and i==len(plan.stages)-1 else f'{kind[:16]}_p{offset+i:03d}'} for i,stage in enumerate(plan.stages)]
    phase={'status':'dispatching','readiness':plan.readiness,'stages':rows,'lookup_tables':list(plan.lookup_tables),
        'minimum_slot_exclusive':plan.minimum_slot_exclusive}
    saved={**saved,'phases':[*saved['phases'],phase]}
    job.params={**job.params,'provider_plan':saved,'provider_plan_business_pending':True};job.status='submitted'
    await db.commit()
    await require_steps(db,job,wallet.id,[row['tx_kind'] for row in rows])
    results=[];completed=set()
    for stage,row in zip(plan.stages,rows):
        if not set(stage.depends_on)<=completed:raise AdapterError('A provider-stage dependency is unconfirmed')
        if await ex.rpc.get_block_height()>stage.last_valid_block_height:
            result=SubmitResult(None,'failed','Provider stage expired; nothing more was sent')
        else:
            remaining_tokens={mint:max(0,amount-saved['tokens_spent'].get(mint,0)) for mint,amount in budget['tokens'].items()}
            remaining=max(0,budget['native']-saved['native_spent'])
            stage_limits=replace(limits,max_lamports_out=remaining,token_out=remaining_tokens,
                token_in_min=limits.token_in_min if plan.readiness=='ready' and stage==plan.stages[-1] else {},
                min_lamports_in=limits.min_lamports_in if plan.readiness=='ready' and stage==plan.stages[-1] else None)
            async def persist(signature):
                await _persist_pending_transaction(job,wallet,row['tx_kind'],signature)
            result=await ex.submit(stage.raw,{wallet.address:wallet.signer_ref or ''},limits=stage_limits,on_signed=persist)
        if result.status=='confirmed':
            try:
                receipt=await ex.rpc.get_transaction(result.signature)
                native,tokens=receipt_debits(receipt,result.signature,wallet.address)
                row['debits']={'native':native,'tokens':tokens}
                saved['native_spent']+=native
                for mint,raw in tokens.items():saved['tokens_spent'][mint]=saved['tokens_spent'].get(mint,0)+raw
                if saved['native_spent']>budget['native'] or any(raw>budget['tokens'].get(mint,0) for mint,raw in saved['tokens_spent'].items()):
                    raise AdapterError('Confirmed stage exceeded the approved budget; later stages were stopped')
                completed.add(stage.id)
            except Exception as error:
                detail=str(error) if isinstance(error,AdapterError) else f'Confirmed stage receipt unavailable ({type(error).__name__})'
                result=SubmitResult(result.signature,'timeout',detail)
        await _record(db,job,wallet,row['tx_kind'],result,None)
        row['signature'],row['status']=result.signature,result.status
        # Assign a fresh JSON tree, including deeply changed stage state.
        job.params={**job.params,'provider_plan':json.loads(json.dumps(saved))}
        await db.commit();results.append(result)
        if result.status!='confirmed':break
    if len(completed)==len(plan.stages) and plan.readiness=='setup_required':
        phase['status']='setup_confirmed'
        job.status='submitted'
        result=SubmitResult(results[-1].signature,'setup_confirmed','Lookup-table setup confirmed; fetch the next phase with the original business terms')
    else:
        all_business=len(completed)==len(plan.stages) and plan.readiness=='ready'
        phase['status']='business_confirmed' if all_business else 'incomplete'
        job.params={**job.params,'provider_plan_business_pending':not all_business}
        _finish(job,results)
        result=results[-1]
    job.params={**job.params,'provider_plan':json.loads(json.dumps(saved))}
    await db.commit()
    return result


async def reconcile_plan(db,ex,job):
    """Resolve only the original signatures. This function never signs or sends."""
    from sqlalchemy import select
    from ..models import Transaction,Wallet
    saved=(job.params or {}).get('provider_plan')
    if not saved:return False
    identity=saved['identity'];wallet=await db.get(Wallet,identity['wallet_id'])
    if not wallet or wallet.user_id!=job.user_id or wallet.network!='solana':return False
    native_spent=0;tokens_spent={};all_business=False;outcomes=[]
    for phase in saved['phases']:
        complete=True
        for stage in phase['stages']:
            row=(await db.execute(select(Transaction).where(Transaction.job_id==job.id,
                Transaction.wallet_id==wallet.id,Transaction.kind==stage['tx_kind']))).scalars().all()
            if len(row)!=1 or not row[0].signature:
                complete=False;continue
            tx=row[0]
            if stage.get('signature') not in (None,tx.signature):return False
            stage['signature']=tx.signature
            try:receipt=await ex.rpc.get_transaction(tx.signature)
            except Exception:
                complete=False;continue
            if not receipt:
                complete=False;continue
            try:
                native,tokens=receipt_debits(receipt,tx.signature,wallet.address)
            except AdapterError:
                complete=False;continue
            native_spent+=native
            for mint,amount in tokens.items():tokens_spent[mint]=tokens_spent.get(mint,0)+amount
            stage['status']='confirmed';stage['debits']={'native':native,'tokens':tokens}
            tx.status='confirmed';tx.error=None
            outcomes.append(SubmitResult(tx.signature,'confirmed'))
        if complete:
            phase['status']='setup_confirmed' if phase['readiness']=='setup_required' else 'business_confirmed'
            all_business=phase['readiness']=='ready'
        else:
            all_business=False;break
    budget=identity['budget']
    if native_spent>budget['native'] or any(amount>budget['tokens'].get(mint,0) for mint,amount in tokens_spent.items()):
        job.params={**job.params,'provider_plan_error':'Receipt debits exceed approved budget; manual review required'}
        await db.commit();return False
    saved['native_spent'],saved['tokens_spent']=native_spent,tokens_spent
    job.params={**job.params,'provider_plan':json.loads(json.dumps(saved)),'provider_plan_business_pending':not all_business}
    if all_business:_finish(job,outcomes)
    elif saved['phases'] and all(phase['status']=='setup_confirmed' for phase in saved['phases']):
        job.status='submitted'
    await db.commit()
    return all_business
