"""Bags reward evidence, never permission to charge or sign.

Wire definitions: bagsfm/bags-idl, 450a04fa94e129f05229eac6ae49c22e81691914,
fee-share-v2/idl.json. Anchor EVENT_IX_TAG is SHA256(anchor:event)[:8].
Amounts are integer quote units; SOL deployer endpoints use WSOL with 9 decimals.
"""
import base64
from dataclasses import dataclass
from hashlib import sha256
import re

from solders.pubkey import Pubkey

from .base import AdapterError
from .solana import b58decode
from .solana_plan import address, number

PROGRAM = 'FEE2tBhCKAt7shrod19QttSVREUYPiyMzoku1mL1gqVK'
WSOL = 'So11111111111111111111111111111111111111112'
TOKEN = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
ASSOCIATED = 'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL'
EVENT_TAG = bytes.fromhex('e445a52e51cb9a1d')
MAX_POSITIONS = 2000
MAX_EVENTS = 2000
MAX_U128 = 2**128-1


def pda(seed, *keys):
    return str(Pubkey.find_program_address([seed, *(bytes(Pubkey.from_string(key)) for key in keys)],
                                          Pubkey.from_string(PROGRAM))[0])


def ata(owner):
    return str(Pubkey.find_program_address([bytes(Pubkey.from_string(owner)), bytes(Pubkey.from_string(TOKEN)),
        bytes(Pubkey.from_string(WSOL))], Pubkey.from_string(ASSOCIATED))[0])


@dataclass(frozen=True)
class Position:
    mint: str
    pending_dbc: int
    pending_damm: int
    accrued: int

    @property
    def total(self):
        return self.pending_dbc + self.pending_damm + self.accrued


@dataclass(frozen=True)
class Positions:
    wallet: str
    positions: tuple[Position, ...]
    vault: int
    unattributed: int
    total: int

    @classmethod
    def parse(cls, value, wallet):
        if not isinstance(value, dict) or value.get('wallet') != address(wallet, 'deployer wallet') or value.get('quoteMint') != WSOL:
            raise AdapterError('Bags deployer balances belong to another wallet or quote asset')
        rows = value.get('positions')
        if not isinstance(rows, list) or len(rows) > MAX_POSITIONS:
            raise AdapterError('Bags deployer positions are incomplete or exceed the supported bound')
        entries = []
        for row in rows:
            if not isinstance(row, dict):raise AdapterError('Malformed Bags deployer position')
            entry = Position(address(row.get('tokenMint'), 'reward token'),
                number(row.get('pendingDbcLamports'), 'pending DBC rewards'),
                number(row.get('pendingDammLamports'), 'pending DAMM rewards'),
                number(row.get('accruedLamports'), 'accrued deployer rewards'))
            if entry.total != number(row.get('totalClaimableLamports'), 'position total', MAX_U128) or not entry.total:
                raise AdapterError('Bags deployer position total is inconsistent')
            entries.append(entry)
        if len({entry.mint for entry in entries}) != len(entries):
            raise AdapterError('Bags returned duplicate deployer positions')
        vault = number(value.get('vaultClaimableLamports'), 'shared deployer vault')
        unattributed = number(value.get('unattributedVaultLamports'), 'unattributed deployer vault')
        total = number(value.get('totalClaimableLamports'), 'deployer wallet total', MAX_U128)
        if vault != sum(entry.accrued for entry in entries) + unattributed or total != sum(entry.total for entry in entries) + unattributed:
            raise AdapterError('Bags shared-vault attribution does not reconcile')
        return cls(wallet, tuple(entries), vault, unattributed, total)

    def public(self):
        return {'wallet': self.wallet, 'quote_mint': WSOL, 'asset': 'SOL', 'decimals': 9,
            'scope': 'wallet_wide', 'source': 'bags_indexed_finalized_attribution',
            'positions': [{'mint': p.mint, 'pending_dbc_raw': str(p.pending_dbc),
                'pending_damm_raw': str(p.pending_damm), 'accrued_raw': str(p.accrued), 'total_raw': str(p.total)} for p in self.positions],
            'vault_raw': str(self.vault), 'unattributed_raw': str(self.unattributed), 'total_raw': str(self.total),
            'helm_fee_basis': None, 'collection_ready': False,
            'notice': 'The deployer vault is shared across tokens. These balances are not collected Helm fees. '
                      'A token filter does not restrict the accrued vault withdrawn. Normal recipient rewards are separate.'}


# Minimal, fixed Borsh layouts. Unknown versions never become payout evidence.
LAYOUTS = {
    'DeployerAccumulatedEvent': [('timestamp', 'i64'), ('base_mint', 'key'), ('quote_mint', 'key'),
        ('pool', 'key'), ('deployer_config', 'key'), ('deployer_vault', 'key'), ('deployer', 'key'),
        ('accumulated', 'u64'), ('total_accumulated_fees', 'u64'), ('total_lifetime_accumulated_fees', 'u128'), ('protocol', 'protocol')],
    'DeployerClaimEvent': [('timestamp', 'i64'), ('deployer', 'key'), ('deployer_vault', 'key'),
        ('claimed', 'u64'), ('total_claimed_fees', 'u128')],
    'DeployerVaultClaimedEvent': [('timestamp', 'i64'), ('deployer', 'key'), ('deployer_vault', 'key'),
        ('mint', 'key'), ('claimed', 'u64'), ('signer', 'key')],
    'BagsFeeShareUserClaimEvent': [('timestamp', 'i64'), ('base_mint', 'key'), ('quote_mint', 'key'),
        ('user', 'key'), ('fee_share_config', 'key'), ('fee_share_authority', 'key'), ('claimer_index', 'u32'),
        ('claimed', 'u64'), ('is_forced', 'bool')],
    'BagsFeeShareUserClaimV2Event': [('timestamp', 'i64'), ('base_mint', 'key'), ('quote_mint', 'key'),
        ('user', 'key'), ('fee_share_config', 'key'), ('fee_share_authority', 'key'), ('claimer_index', 'u32'),
        ('claimed', 'u64'), ('force_claim_executor', 'optional_key'), ('force_claim_type', 'optional_force')],
    'BagsFeeShareUserVaultClaimEvent': [('timestamp', 'i64'), ('user', 'key'), ('base_mint', 'key'),
        ('quote_mint', 'key'), ('claimed', 'u64')],
}
DISCRIMINATORS = {sha256(f'event:{name}'.encode()).digest()[:8]: name for name in LAYOUTS}


@dataclass(frozen=True)
class Event:
    name: str
    values: dict
    ordinal: int


def decode_event(raw, ordinal):
    name = DISCRIMINATORS.get(raw[:8])
    if name is None:return None
    cursor = 8
    fields = {}

    def read(kind):
        nonlocal cursor
        if kind.startswith('optional_'):
            tag = read('u8')
            if tag not in (0, 1):raise ValueError()
            return read(kind[9:]) if tag else None
        width = {'key': 32, 'u8': 1, 'u32': 4, 'u64': 8, 'i64': 8, 'u128': 16,
                 'protocol': 1, 'force': 1, 'bool': 1}[kind]
        data = raw[cursor:cursor+width];cursor += width
        if len(data) != width:raise ValueError()
        if kind == 'key':return str(Pubkey.from_bytes(data))
        value = int.from_bytes(data, 'little', signed=kind == 'i64')
        if kind == 'protocol' and value > 1 or kind == 'force' and value > 2 or kind == 'bool' and value > 1:
            raise ValueError()
        return value

    try:
        for field, kind in LAYOUTS[name]:fields[field] = read(kind)
        if cursor != len(raw):raise ValueError()
    except (ValueError, KeyError):
        raise AdapterError('Unsupported or malformed Bags reward event') from None
    return Event(name, fields, ordinal)


def account_keys(receipt):
    message = receipt['transaction']['message']
    keys = message['accountKeys']
    keys = [row['pubkey'] if isinstance(row, dict) else row for row in keys]
    loaded = receipt['meta'].get('loadedAddresses') or {}
    lookups = message.get('addressTableLookups') or []
    if lookups and (len(loaded.get('writable', [])) != sum(len(table['writableIndexes']) for table in lookups)
            or len(loaded.get('readonly', [])) != sum(len(table['readonlyIndexes']) for table in lookups)):
        raise ValueError('Receipt is missing loaded lookup-table accounts')
    keys += loaded.get('writable', []) + loaded.get('readonly', [])
    if not keys or len(keys) != len(set(keys)):raise ValueError()
    for key in keys:Pubkey.from_string(key)
    return keys


def key_at(keys, index):
    return keys[number(index, 'receipt account index', len(keys)-1)]


def reward_events(receipt, signature):
    """Only successful, complete execution traces can authenticate event bytes.

    A caught/reverted CPI is rejected conservatively, even in a successful outer
    transaction. Never accept an arbitrary program's imitation of Program data.
    """
    try:
        meta = receipt['meta']
        number(receipt['slot'], 'reward receipt slot')
        if meta['err'] is not None or receipt['transaction']['signatures'][0] != signature:raise ValueError()
        logs = meta.get('logMessages')
        if not isinstance(logs, list) or not logs or len(logs) > 20000:raise ValueError()
        stack = [];logged = [];program_seen = False
        for log_index, line in enumerate(logs):
            if not isinstance(line, str) or 'Log truncated' in line:raise ValueError()
            if invoke := re.fullmatch(r'Program (\w+) invoke \[(\d+)\]', line):
                if int(invoke[2]) != len(stack)+1:raise ValueError()
                stack.append(invoke[1]);program_seen |= invoke[1] == PROGRAM
            elif done := re.fullmatch(r'Program (\w+) success', line):
                if not stack or stack.pop() != done[1]:raise ValueError()
            elif re.fullmatch(r'Program (\w+) failed:.*', line):
                raise ValueError()
            elif line.startswith('Program data: ') and stack and stack[-1] == PROGRAM:
                raw = base64.b64decode(line[14:], validate=True)
                if event := decode_event(raw, log_index):logged.append(event)
        if stack or not program_seen:raise ValueError()
        keys = account_keys(receipt);cpi = [];previous_group = -1
        for group in meta.get('innerInstructions') or []:
            group_index = number(group['index'], 'inner instruction group', 255)
            if group_index <= previous_group:raise ValueError()
            previous_group = group_index
            if len(group['instructions']) > 10000:raise ValueError()
            for inner_index, ix in enumerate(group['instructions']):
                if key_at(keys, ix['programIdIndex']) != PROGRAM:continue
                raw = b58decode(ix.get('data') or '')
                if not raw.startswith(EVENT_TAG):continue
                accounts = ix.get('accounts') or []
                if not accounts or key_at(keys, accounts[0]) != pda(b'__event_authority'):raise ValueError()
                # A physical trace location stays stable when the decoder learns
                # more event types; recognized-event counts are not receipt IDs.
                ordinal = 1000000+group_index*10000+inner_index
                if event := decode_event(raw[8:], ordinal):cpi.append(event)
        if len(cpi)+len(logged) > MAX_EVENTS:raise ValueError()
        # Refuse ambiguous mixed encodings rather than count one payment twice.
        if cpi and logged:raise ValueError()
        return cpi or logged
    except (KeyError, TypeError, ValueError, IndexError):
        raise AdapterError('Bags reward receipt is incomplete, reverted or unauthenticated') from None


def matching_transfers(receipt, owners, recipient):
    """Gross classic WSOL transfers from the named PDA-owned accounts only."""
    try:
        keys = account_keys(receipt);paid = 0
        for group in receipt['meta'].get('innerInstructions') or []:
            for ix in group['instructions']:
                if key_at(keys, ix['programIdIndex']) != TOKEN:continue
                data = b58decode(ix['data']);accounts = ix['accounts']
                if data[:1] == b'\x03' and len(data) == 9 and len(accounts) >= 3:
                    source, target, owner = [key_at(keys, index) for index in accounts[:3]]
                elif data[:1] == b'\x0c' and len(data) == 10 and data[9] == 9 and len(accounts) >= 4:
                    source, quote, target, owner = [key_at(keys, index) for index in accounts[:4]]
                    if quote != WSOL:continue
                else:continue
                if owner in owners and source == ata(owner) and target == ata(recipient):
                    paid += int.from_bytes(data[1:9], 'little')
        return paid
    except (KeyError, TypeError, ValueError, IndexError):
        raise AdapterError('Bags reward receipt contains malformed token transfers') from None


def treasury_payout(receipt, signature, mint, treasury):
    """Prove both the attributed claim event and the actual WSOL transfer.

    Vault accrual, normal user income, tips and unrelated treasury transfers are
    excluded. A successful claim is already the native split; never bill it again.
    """
    events = reward_events(receipt, signature)
    config = pda(b'fee_share_config', mint, WSOL)
    authority = pda(b'fee_share_authority', mint, WSOL)
    user_vault = pda(b'user_fee_vault', treasury, mint, WSOL)
    total = 0
    for event in events:
        fields = event.values
        if fields.get('base_mint') != mint or fields.get('quote_mint') != WSOL or fields.get('user') != treasury:continue
        if event.name in ('BagsFeeShareUserClaimEvent', 'BagsFeeShareUserClaimV2Event'):
            if fields['fee_share_config'] != config or fields['fee_share_authority'] != authority:
                raise AdapterError('Bags payout event has an unexpected fee configuration')
        elif event.name != 'BagsFeeShareUserVaultClaimEvent':continue
        total += fields['claimed']
    if not total:return 0
    if matching_transfers(receipt, (authority, user_vault), treasury) != total:
        raise AdapterError('Bags treasury reward is not backed by matching token transfers')
    return total


@dataclass(frozen=True)
class VaultState:
    wallet: str
    claimed: int
    pending: int
    lifetime: int

    @classmethod
    def parse(cls, wallet, account):
        try:
            if account['owner'] != PROGRAM or account.get('executable'):raise ValueError()
            data = base64.b64decode(account['data'][0], validate=True)
            if len(data) != 128 or data[:8] != bytes([66,189,74,88,226,57,203,5]):raise ValueError()
            owner = str(Pubkey.from_bytes(data[48:80]))
            if owner != wallet:raise ValueError()
            return cls(wallet, int.from_bytes(data[8:24], 'little'), int.from_bytes(data[24:32], 'little'),
                       int.from_bytes(data[32:48], 'little'))
        except (KeyError, TypeError, ValueError, IndexError):
            raise AdapterError('Bags deployer vault state is invalid or has changed layout') from None


@dataclass(frozen=True)
class DeployerPayout:
    signature: str
    ordinal: int
    mint: str
    amount: int
    slot: int


@dataclass(frozen=True)
class HistoryEntry:
    signature: str
    slot: int
    index: int
    events: tuple[Event, ...]


def deployer_payouts(history, state: VaultState):
    """Replay a complete, chain-ordered finalized event history against its vault
    counters. Provider balance estimates are not a substitute for this proof.

    Each supported withdrawal drains the shared vault. Known accruals retain
    their token identity; any excess withdrawal is unattributed and never billed.
    """
    pending = {};lifetime = 0;claimed = 0;seen = set();payouts = []
    vault = pda(b'deployer_vault', state.wallet)
    previous = (-1, -1)
    if len(history) > 2000:raise AdapterError('Bags vault history exceeds the supported bound')
    for entry in history:
        signature, events = entry.signature, entry.events
        order = (entry.slot, entry.index)
        if order <= previous:raise AdapterError('Bags vault history is not in canonical execution order')
        previous = order
        if signature in seen:raise AdapterError('Repeated transaction in Bags vault history')
        seen.add(signature)
        for event in events:
            fields = event.values
            if fields.get('deployer_vault') != vault:continue
            if fields.get('deployer') != state.wallet:raise AdapterError('Bags vault event changes deployer ownership')
            if event.name == 'DeployerAccumulatedEvent':
                if fields['quote_mint'] != WSOL:raise AdapterError('Non-SOL deployer history requires a separate accounting path')
                lifetime += fields['accumulated']
                mint = fields['base_mint'];pending[mint] = pending.get(mint, 0)+fields['accumulated']
                if fields['total_lifetime_accumulated_fees'] != lifetime or fields['total_accumulated_fees'] != sum(pending.values()):
                    raise AdapterError('Bags accrual history has a gap or is out of order')
            elif event.name == 'DeployerClaimEvent':
                amount = fields['claimed'];claimed += amount
                if amount < sum(pending.values()) or fields['total_claimed_fees'] != claimed:
                    raise AdapterError('Bags withdrawal history is incomplete or is not a full-vault claim')
                payouts.extend(DeployerPayout(signature, event.ordinal, mint, raw, entry.slot) for mint, raw in pending.items() if raw)
                pending = {}
            elif event.name == 'DeployerVaultClaimedEvent':
                raise AdapterError('Administrative deployer-vault withdrawal requires separate attribution review')
    if (claimed, sum(pending.values()), lifetime) != (state.claimed, state.pending, state.lifetime):
        raise AdapterError('Bags vault history does not reconcile with finalized on-chain counters')
    return payouts


@dataclass(frozen=True)
class VaultAudit:
    state: VaultState
    slot: int
    signatures: tuple[str, ...]
    payouts: tuple[DeployerPayout, ...]


async def audit_deployer_vault(rpc, wallet, *, maximum_transactions=100):
    """Bounded, read-only audit against one finalized bank. Never signs or sends.

    Pruned/missing receipts, history limits, ambiguous order and changed counters
    all refuse attribution. The public provider's balances are a separate view.
    """
    address(wallet, 'deployer wallet')
    maximum_transactions = number(maximum_transactions, 'vault audit limit', 2000)
    if not maximum_transactions:raise AdapterError('Vault audit requires a positive transaction limit')
    vault = pda(b'deployer_vault', wallet)
    snapshot = await rpc._call('getAccountInfo', [vault, {'encoding': 'base64', 'commitment': 'finalized'}])
    checkpoint = number(snapshot['context']['slot'], 'finalized vault slot')
    if not snapshot.get('value'):return None
    state = VaultState.parse(wallet, snapshot['value'])
    signatures = [];before = None
    while True:
        options = {'commitment': 'finalized', 'limit': min(1000, maximum_transactions-len(signatures)+1)}
        if before:options['before'] = before
        page = await rpc._call('getSignaturesForAddress', [vault, options])
        if not isinstance(page, list):raise AdapterError('Bags vault signature history is unavailable')
        if not page:break
        for row in page:
            signature = row.get('signature')
            if not isinstance(signature, str) or any(previous['signature'] == signature for previous in signatures):
                raise AdapterError('Bags vault history repeated a pagination cursor')
            signatures.append(row)
            if len(signatures) > maximum_transactions:raise AdapterError('Bags vault history exceeds the audit limit; no fee basis was inferred')
        before = signatures[-1]['signature']
    entries = [];slot_groups = {}
    for row in signatures:
        slot = number(row.get('slot'), 'vault transaction slot')
        if slot > checkpoint or row.get('err') is not None:continue
        receipt = await rpc.get_transaction(row['signature'], commitment='finalized')
        if not receipt or receipt.get('slot') != slot or receipt['transaction']['signatures'][0] != row['signature']:
            raise AdapterError('A finalized Bags vault receipt is missing or inconsistent')
        try:
            keys = account_keys(receipt)
            if vault not in keys or receipt['meta']['err'] is not None:raise ValueError()
            events = reward_events(receipt, row['signature']) if PROGRAM in keys else []
        except (KeyError, TypeError, ValueError, IndexError):
            raise AdapterError('Bags vault receipt does not prove its account identity') from None
        withdrawn = sum(e.values['claimed'] for e in events if e.name == 'DeployerClaimEvent'
            and e.values['deployer_vault'] == vault and e.values['deployer'] == wallet)
        if withdrawn and matching_transfers(receipt, (vault,), wallet) != withdrawn:
            raise AdapterError('Bags deployer withdrawal lacks matching WSOL transfers')
        slot_groups.setdefault(slot, []).append(row['signature'])
        entries.append(HistoryEntry(row['signature'], slot, 0, tuple(events)))
    positions = {}
    for slot, group in slot_groups.items():
        if len(group) == 1:continue
        block = await rpc._call('getBlock', [slot, {'commitment': 'finalized', 'transactionDetails': 'signatures', 'rewards': False}])
        ordered = (block or {}).get('signatures')
        if not isinstance(ordered, list) or len(ordered) != len(set(ordered)) or not set(group) <= set(ordered):
            raise AdapterError('The finalized block cannot prove Bags vault transaction order')
        positions.update({(slot, sig): ordered.index(sig) for sig in group})
    entries = [HistoryEntry(e.signature, e.slot, positions.get((e.slot, e.signature), 0), e.events) for e in entries]
    entries.sort(key=lambda e: (e.slot, e.index))
    payouts = deployer_payouts(entries, state)
    return VaultAudit(state, checkpoint, tuple(e.signature for e in entries), tuple(payouts))
