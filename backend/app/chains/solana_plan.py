"""Strict reader for Bags' version-1 *plan contract*, carrying Solana v0 messages.

No message reconstruction, blockhash replacement, or signing occurs here. A plan
is a provider proposal, never authority to spend. Execution also needs an owned
signer, reviewed wallet-effect limits, chain state and a durable stage journal.
"""
import base64
import hashlib
import re
from dataclasses import dataclass

from solders.message import MessageV0,to_bytes_versioned
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from .base import AdapterError
from ..services.tx_guard import check_solana_signers

TOKEN_PROGRAMS={'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA','TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'}
MAX_STAGES=20
MAX_LOOKUPS=8
MAX_PACKET_BYTES=1232
LOOKUP_PROGRAM='AddressLookupTab1e1111111111111111111111111'
COMPUTE_PROGRAM='ComputeBudget111111111111111111111111111111'


def check_setup(stage,tables):
    """Setup can create/extend the declared tables, never close one or do business work."""
    message=VersionedTransaction.from_bytes(stage.raw).message
    if message.address_table_lookups:
        raise AdapterError('Lookup-table setup must use static accounts')
    found=False
    for ix in message.instructions:
        program=str(message.account_keys[ix.program_id_index])
        if program==COMPUTE_PROGRAM:continue
        data=bytes(ix.data);accounts=list(ix.accounts)
        if program!=LOOKUP_PROGRAM or len(data)<4 or len(accounts)<2:
            raise AdapterError('Setup stage contains a non-lookup-table instruction')
        opcode=int.from_bytes(data[:4],'little')
        if opcode not in (0,2):
            raise AdapterError('Setup may only create or extend a lookup table')
        if str(message.account_keys[accounts[0]]) not in tables:
            raise AdapterError('Setup modifies an undeclared lookup table')
        if opcode==0 and len(data)!=13:
            raise AdapterError('Malformed lookup-table creation')
        if opcode==2 and (len(data)<12 or len(data)!=12+32*int.from_bytes(data[4:12],'little')):
            raise AdapterError('Malformed lookup-table extension')
        found=True
    if not found:raise AdapterError('Setup stage contains no lookup-table operation')


def number(value,label,maximum=2**64-1):
    if isinstance(value,bool) or not isinstance(value,(int,str)) or not re.fullmatch(r'0|[1-9][0-9]{0,77}',str(value)):
        raise AdapterError(f'Invalid plan {label}')
    result=int(value)
    if result>maximum:
        raise AdapterError(f'Plan {label} is out of range')
    return result


def address(value,label):
    try:
        if not isinstance(value,str):raise ValueError()
        key=Pubkey.from_string(value)
        if key==Pubkey.default():raise ValueError()
        return str(key)
    except ValueError:
        raise AdapterError(f'Invalid plan {label}') from None


@dataclass(frozen=True)
class Asset:
    mint:str
    token_program:str
    decimals:int

    @classmethod
    def parse(cls,value):
        if not isinstance(value,dict):raise AdapterError('Plan has no asset identity')
        mint=address(value.get('mint'),'asset mint')
        program=address(value.get('tokenProgram'),'token program')
        if program not in TOKEN_PROGRAMS:raise AdapterError('Unsupported plan token program')
        return cls(mint,program,number(value.get('decimals'),'asset decimals',255))


@dataclass(frozen=True)
class FeeLimit:
    mint:str
    token_program:str
    bps:int
    maximum_raw:int

    @classmethod
    def parse(cls,value):
        if not isinstance(value,dict) or not isinstance(value.get('asset'),dict):
            raise AdapterError('Invalid token transfer-fee limit')
        asset=value['asset']
        mint=address(asset.get('mint'),'fee asset')
        program=address(asset.get('tokenProgram'),'fee token program')
        if program not in TOKEN_PROGRAMS:raise AdapterError('Unsupported fee token program')
        return cls(mint,program,number(value.get('basisPoints'),'transfer fee bps',10000),
            number(value.get('maximumFeeRaw'),'transfer fee maximum'))


@dataclass(frozen=True)
class Stage:
    id:str
    kind:str
    depends_on:tuple[str,...]
    raw:bytes
    message_sha256:str
    blockhash:str
    last_valid_block_height:int
    required_signers:tuple[str,...]

    @classmethod
    def parse(cls,value,wallet):
        if not isinstance(value,dict) or value.get('format')!='v0' or value.get('encoding')!='base64':
            raise AdapterError('Helm supports base64 Solana v0 plan stages only')
        stage_id=value.get('id');kind=value.get('kind');depends=value.get('dependsOn')
        if not isinstance(stage_id,str) or not 1<=len(stage_id)<=128 or not isinstance(kind,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}',kind):
            raise AdapterError('Invalid plan stage identity')
        if not isinstance(depends,list) or len(depends)>MAX_STAGES or any(not isinstance(key,str) for key in depends) or len(set(depends))!=len(depends):
            raise AdapterError('Invalid stage dependencies')
        try:
            encoded=value['transaction']
            if not isinstance(encoded,str) or len(encoded)>4*((MAX_PACKET_BYTES+2)//3):raise ValueError()
            raw=base64.b64decode(encoded,validate=True)
            if not 1<=len(raw)<=MAX_PACKET_BYTES:raise ValueError()
            tx=VersionedTransaction.from_bytes(raw)
            if not isinstance(tx.message,MessageV0):raise ValueError()
            tx.sanitize()
        except Exception:
            raise AdapterError('Malformed or oversized v0 plan transaction') from None
        message=to_bytes_versioned(tx.message)
        digest=hashlib.sha256(message).hexdigest()
        if value.get('messageSha256')!=digest:
            raise AdapterError('Plan transaction message hash does not match its bytes')
        required=tuple(str(key) for key in tx.message.account_keys[:tx.message.header.num_required_signatures])
        if value.get('requiredSigners')!=list(required) or wallet not in required:
            raise AdapterError('Plan signers do not match the selected wallet and message')
        if reason:=check_solana_signers(raw,{wallet}):raise AdapterError(reason)
        lifetime=value.get('lifetime') or {}
        if not isinstance(lifetime,dict) or lifetime.get('blockhash')!=str(tx.message.recent_blockhash):
            raise AdapterError('Plan blockhash does not match its message')
        height=number(lifetime.get('lastValidBlockHeight'),'last valid block height')
        # Priority/resource budgets stay inside the exact reviewed message. Do not
        # reinterpret V1 total-lamport fields as V0 micro-lamports per compute unit.
        budget=value.get('budget')
        if not isinstance(budget,dict) or not isinstance(budget.get('priorityFee'),dict):
            raise AdapterError('Plan stage has no resource budget')
        priority=budget['priorityFee']
        if priority.get('unit') not in ('micro_lamports_per_compute_unit','micro_lamports_per_cu'):
            raise AdapterError('Unsupported priority-fee units for a v0 stage')
        units=number(budget.get('computeUnitLimit'),'compute limit',1_400_000)
        loaded=number(budget.get('loadedAccountsDataSizeLimit'),'loaded account limit',64*1024*1024)
        price=number(priority.get('amount'),'compute unit price')
        observed={}
        for instruction in tx.message.instructions:
            if instruction.program_id_index>=len(tx.message.account_keys):
                raise AdapterError('Plan program identity must be a static account')
            if str(tx.message.account_keys[instruction.program_id_index])!='ComputeBudget111111111111111111111111111111':continue
            data=bytes(instruction.data)
            if not data or data[0] not in (1,2,3,4) or data[0] in observed:
                raise AdapterError('Unsupported or repeated compute-budget instruction')
            expected=9 if data[0]==3 else 5
            if len(data)!=expected:raise AdapterError('Malformed compute-budget instruction')
            observed[data[0]]=int.from_bytes(data[1:],'little')
        if observed.get(2)!=units or observed.get(3,0)!=price or observed.get(4,64*1024*1024)!=loaded:
            raise AdapterError('Resource budget differs from the signed transaction message')
        if units==0 or loaded==0:raise AdapterError('A stage requires nonzero execution resources')
        sizes=value.get('size') or {}
        accounts=len(tx.message.account_keys)+sum(len(table.writable_indexes)+len(table.readonly_indexes) for table in tx.message.address_table_lookups)
        if not isinstance(sizes,dict) or sizes.get('bytes')!=len(raw) or sizes.get('accounts')!=accounts or sizes.get('signatures')!=len(required):
            raise AdapterError('Plan size metadata does not match its transaction')
        return cls(stage_id,kind,tuple(depends),raw,digest,str(tx.message.recent_blockhash),height,required)


@dataclass(frozen=True)
class Plan:
    kind:str
    base_mint:str
    flow:str
    asset:Asset
    fee_limits:tuple[FeeLimit,...]
    stages:tuple[Stage,...]
    readiness:str
    lookup_tables:tuple[str,...]
    minimum_slot_exclusive:int|None

    @classmethod
    def parse(cls,value,*,wallet,kind,base_mint,flow,asset,approved_fee_limits):
        if not isinstance(value,dict) or type(value.get('contractVersion')) is not int or value['contractVersion']!=1:
            raise AdapterError('Unsupported Bags plan contract version')
        operation=value.get('operation')
        if not isinstance(operation,dict) or any(operation.get(key)!=expected for key,expected in {'kind':kind,'baseMint':base_mint,'flow':flow}.items()):
            raise AdapterError('Bags plan belongs to a different operation, mint or flow')
        if Asset.parse(value.get('asset'))!=asset:raise AdapterError('Bags plan changes the selected quote asset')
        fees=value.get('feeLimits')
        if not isinstance(fees,list) or len(fees)>4:raise AdapterError('Invalid plan transfer-fee limits')
        limits=tuple(FeeLimit.parse(fee) for fee in fees)
        if len({(fee.mint,fee.token_program) for fee in limits})!=len(limits):raise AdapterError('Duplicate plan transfer-fee asset')
        if set(limits)!=set(approved_fee_limits):raise AdapterError('Transfer-fee limits changed; review again')
        rows=value.get('transactions')
        if not isinstance(rows,list) or len(rows)>MAX_STAGES:raise AdapterError('Invalid plan stage count')
        stages=tuple(Stage.parse(row,wallet) for row in rows)
        seen=set();messages=set()
        for stage in stages:
            if stage.id in seen or stage.message_sha256 in messages or any(dependency not in seen for dependency in stage.depends_on):
                raise AdapterError('Duplicate, circular or out-of-order plan stages')
            seen.add(stage.id);messages.add(stage.message_sha256)
        readiness=value.get('readiness') or {}
        if not isinstance(readiness,dict) or readiness.get('status') not in ('ready','setup_required'):
            raise AdapterError('Unknown Bags plan readiness')
        tables=readiness.get('lookupTables',[])
        if not isinstance(tables,list) or len(tables)>MAX_LOOKUPS:raise AdapterError('Invalid lookup-table readiness')
        lookups=tuple(address(table,'lookup table') for table in tables)
        if len(set(lookups))!=len(lookups):raise AdapterError('Duplicate lookup-table readiness')
        minimum=readiness.get('minimumSlotExclusive')
        if minimum is not None:minimum=number(minimum,'lookup-table minimum slot')
        status=readiness['status']
        if status=='setup_required' and (not lookups or any(stage.kind not in ('lookup_table_create','lookup_table_extend','lookup_table_setup','lut_setup') for stage in stages)):
            raise AdapterError('Setup-required plan contains business work or lacks lookup tables')
        if status=='setup_required':
            for stage in stages:check_setup(stage,set(lookups))
        if status=='ready' and not stages:raise AdapterError('An empty plan is not a completed business operation')
        if status=='ready' and stages[-1].kind!=kind:
            raise AdapterError('The last plan stage does not complete the requested business operation')
        return cls(kind,base_mint,flow,asset,limits,stages,status,lookups,minimum)
