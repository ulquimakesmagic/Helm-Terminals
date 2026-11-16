"""Plan integrity and actual Ed25519 co-signatures, without network access."""
import base64
from copy import deepcopy
import hashlib

import pytest
from solders.compute_budget import set_compute_unit_limit,set_compute_unit_price
from solders.hash import Hash
from solders.instruction import AccountMeta,Instruction
from solders.keypair import Keypair
from solders.message import MessageV0,to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import TransferParams,transfer
from solders.transaction import VersionedTransaction

from app.chains.base import AdapterError
from app.chains.solana import sign_versioned
from app.chains.solana_plan import Asset,FeeLimit,Plan
from app.services.tx_guard import check_solana_signers
from app.signing.base import SigningError

WSOL='So11111111111111111111111111111111111111112'
TOKEN='TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
QUOTE=Asset(WSOL,TOKEN,9)
WALLET=Keypair()
SPONSOR=Keypair()
MINT=str(Keypair().pubkey())


def stage(stage_id='business',kind='launch',depends=(),sponsor=True,instructions=None):
    fee_payer=SPONSOR.pubkey() if sponsor else WALLET.pubkey()
    operations=instructions if instructions is not None else [
        transfer(TransferParams(from_pubkey=WALLET.pubkey(),to_pubkey=SPONSOR.pubkey(),lamports=0)),
        Instruction(Pubkey.from_string('MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr'),stage_id.encode(),[])]
    ixs=[set_compute_unit_limit(250000),set_compute_unit_price(1000),*operations]
    message=MessageV0.try_compile(fee_payer,ixs,[],Hash.default())
    raw_message=to_bytes_versioned(message)
    signers=list(message.account_keys)[:message.header.num_required_signatures]
    signatures=[SPONSOR.sign_message(raw_message) if key==SPONSOR.pubkey() and sponsor else Signature.default() for key in signers]
    raw=bytes(VersionedTransaction.populate(message,signatures))
    return {'id':stage_id,'kind':kind,'dependsOn':list(depends),'format':'v0','encoding':'base64',
        'transaction':base64.b64encode(raw).decode(),'messageSha256':hashlib.sha256(raw_message).hexdigest(),
        'requiredSigners':[str(key) for key in signers],'lifetime':{'blockhash':str(message.recent_blockhash),'lastValidBlockHeight':'100'},
        'budget':{'computeUnitLimit':250000,'loadedAccountsDataSizeLimit':64*1024*1024,'priorityFee':{'unit':'micro_lamports_per_compute_unit','amount':'1000'}},
        'size':{'bytes':len(raw),'accounts':len(message.account_keys),'signatures':len(signers)}}


def payload(*stages):
    return {'contractVersion':1,'operation':{'kind':'launch','baseMint':MINT,'flow':'dbc'},
        'asset':{'mint':WSOL,'tokenProgram':TOKEN,'decimals':9},'feeLimits':[],
        'transactions':list(stages or [stage()]),'readiness':{'status':'ready'}}


def parse(value,limits=()):
    return Plan.parse(value,wallet=str(WALLET.pubkey()),kind='launch',base_mint=MINT,flow='dbc',asset=QUOTE,approved_fee_limits=limits)


async def test_valid_sponsor_signature_is_retained_and_only_owned_slot_is_signed():
    entry=stage();plan=parse(payload(entry));before=VersionedTransaction.from_bytes(plan.stages[0].raw)
    calls=[]
    class Signer:
        async def sign_message(self,ref,message):
            calls.append((ref,message));return bytes(WALLET.sign_message(message))
    signed=VersionedTransaction.from_bytes(await sign_versioned(plan.stages[0].raw,Signer(),{str(WALLET.pubkey()):'owned-ref'}))
    assert signed.message==before.message and signed.signatures[0]==before.signatures[0]
    assert all(signed.verify_with_results()) and len(calls)==1 and calls[0][0]=='owned-ref'


async def test_forged_or_missing_sponsor_never_passes_guard_or_signer():
    entry=stage();raw=base64.b64decode(entry['transaction']);tx=VersionedTransaction.from_bytes(raw)
    forged=VersionedTransaction.populate(tx.message,[WALLET.sign_message(to_bytes_versioned(tx.message)),Signature.default()])
    assert 'invalid existing signature' in check_solana_signers(bytes(forged),{str(WALLET.pubkey())})
    with pytest.raises(SigningError,match='invalid existing provider signature'):
        await sign_versioned(bytes(forged),None,{})
    assert 'authorized signer' in check_solana_signers(raw,set())
    empty=VersionedTransaction.populate(tx.message,[Signature.default()]*2)
    assert 'does not hold' in check_solana_signers(bytes(empty),{str(WALLET.pubkey())})
    # A missing gas sponsor is not a launchpad-submit signature: slot zero stays mandatory.
    assert 'does not hold' in check_solana_signers(bytes(empty),{str(WALLET.pubkey())},allow_missing=True)


@pytest.mark.parametrize('mutation,match',[
    (lambda p:p.update(contractVersion=True),'contract version'),
    (lambda p:p['operation'].update(baseMint=str(Keypair().pubkey())),'different operation'),
    (lambda p:p['asset'].update(decimals=6),'changes the selected quote'),
    (lambda p:p['transactions'][0].update(format='v1'),'v0 plan stages only'),
    (lambda p:p['transactions'][0].update(messageSha256='0'*64),'message hash'),
    (lambda p:p['transactions'][0]['lifetime'].update(blockhash=str(Hash.new_unique())),'blockhash'),
    (lambda p:p['transactions'][0]['lifetime'].update(lastValidBlockHeight=-1),'block height'),
    (lambda p:p['transactions'][0].update(requiredSigners=[str(WALLET.pubkey())]),'signers'),
    (lambda p:p['transactions'][0]['budget']['priorityFee'].update(unit='total_lamports'),'priority-fee units'),
    (lambda p:p['transactions'][0]['budget']['priorityFee'].update(amount='1000000'),'Resource budget'),
    (lambda p:p['transactions'][0]['size'].update(bytes=1),'size metadata'),
    (lambda p:p['transactions'][0].update(dependsOn=['later']),'out-of-order'),
])
def test_provider_metadata_cannot_change_reviewed_message_or_intent(mutation,match):
    value=payload();mutation(value)
    with pytest.raises(AdapterError,match=match):parse(value)


def test_dependencies_are_ordered_and_messages_cannot_be_replayed_as_another_stage():
    a=stage('a','config');b=stage('b','launch',['a'])
    assert len(parse(payload(a,b)).stages)==2
    b['transaction']=a['transaction'];b['messageSha256']=a['messageSha256'];b['size']=a['size']
    with pytest.raises(AdapterError,match='Duplicate'):parse(payload(a,b))


def test_fee_schedule_limits_require_exact_asset_and_approved_ceiling():
    value=payload();limit={'asset':{'mint':WSOL,'tokenProgram':TOKEN},'basisPoints':25,'maximumFeeRaw':'1234'}
    value['feeLimits']=[limit]
    assert parse(value,[FeeLimit(WSOL,TOKEN,25,1234)]).fee_limits[0].maximum_raw==1234
    with pytest.raises(AdapterError,match='limits changed'):parse(value,[])
    value['feeLimits'].append(deepcopy(limit))
    with pytest.raises(AdapterError,match='Duplicate'):parse(value,[FeeLimit(WSOL,TOKEN,25,1234)])


def test_lookup_warming_does_not_count_as_completed_business_work():
    value=payload();value['transactions']=[]
    with pytest.raises(AdapterError,match='empty plan'):parse(value)
    value['readiness']={'status':'setup_required','lookupTables':[str(Keypair().pubkey())],'minimumSlotExclusive':'101'}
    warming=parse(value)
    assert warming.readiness=='setup_required' and not warming.stages and warming.minimum_slot_exclusive==101
    value['transactions']=[stage()]
    with pytest.raises(AdapterError,match='business work'):parse(value)


def setup_payload():
    from solders.system_program import create_lookup_table
    instruction,table=create_lookup_table({'authority_address':WALLET.pubkey(),
        'payer_address':WALLET.pubkey(),'recent_slot':40})
    value=payload(stage('setup','lookup_table_create',instructions=[instruction]))
    value['readiness']={'status':'setup_required','lookupTables':[str(table)]}
    return value,table


def test_setup_instructions_must_match_declared_tables_and_cannot_hide_transfers():
    value,table=setup_payload()
    assert len(parse(value).stages)==1
    value['readiness']['lookupTables']=[str(Keypair().pubkey())]
    with pytest.raises(AdapterError,match='undeclared'):parse(value)
    value['readiness']['lookupTables']=[str(table)]
    value['transactions']=[stage('fake','lookup_table_create')]
    with pytest.raises(AdapterError,match='non-lookup'):parse(value)
    from solders.system_program import deactivate_lookup_table
    ix=deactivate_lookup_table({'lookup_table_address':table,'authority_address':WALLET.pubkey()})
    value['transactions']=[stage('close','lookup_table_setup',instructions=[ix])]
    with pytest.raises(AdapterError,match='only create or extend'):parse(value)
