"""Synthetic protocol fixtures encoded from the pinned official IDL, never chain receipts."""
import base64
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import httpx
import pytest

from app.chains.bags import BagsAdapter
from app.chains.bags_rewards import (
    PROGRAM, TOKEN, WSOL, EVENT_TAG, Event, HistoryEntry, Positions, VaultState, ata,
    audit_deployer_vault, decode_event, deployer_payouts, pda, reward_events, treasury_payout,
)
from app.chains.base import AdapterError
from .test_adapters import b58encode as _encode58


def b58encode(raw):
    return _encode58(raw).encode()

DATA = json.loads((Path(__file__).parent/'fixtures/bags-reward-events.json').read_text())
KEYS = DATA['keys']
WALLET, MINT, TREASURY = (KEYS[k] for k in ('wallet', 'mint', 'treasury'))


def event(label, ordinal=0):
    return decode_event(base64.b64decode(DATA['fixtures'][label]['base64']), ordinal)


def positions():
    return {'wallet': WALLET, 'quoteMint': WSOL,
        'positions': [{'tokenMint': MINT, 'pendingDbcLamports': '1000000', 'pendingDammLamports': '2000000',
            'accruedLamports': '3000000', 'totalClaimableLamports': '6000000'}],
        'vaultClaimableLamports': '3500000', 'unattributedVaultLamports': '500000', 'totalClaimableLamports': '6500000'}


def receipt(labels=('creator',), signature='sig', slot=100, transfer=True):
    owner = KEYS['vault'] if 'withdraw' in labels else KEYS['fee_authority']
    recipient = WALLET if 'withdraw' in labels else TREASURY
    keys = [PROGRAM, pda(b'__event_authority'), TOKEN, owner, ata(owner), ata(recipient), recipient, KEYS['vault']]
    # The source/owner can coincide with the vault; canonical account keys are unique.
    keys = list(dict.fromkeys(keys))
    instructions = [{'programIdIndex': keys.index(PROGRAM), 'accounts': [keys.index(pda(b'__event_authority'))],
        'data': b58encode(EVENT_TAG+base64.b64decode(DATA['fixtures'][label]['base64'])).decode()} for label in labels]
    if transfer:
        paid = 4000000 if 'withdraw' in labels else 25000
        instructions.append({'programIdIndex': keys.index(TOKEN), 'accounts': [keys.index(ata(owner)), keys.index(ata(recipient)), keys.index(owner)],
            'data': b58encode(b'\x03'+paid.to_bytes(8, 'little')).decode()})
    return {'slot': slot, 'transaction': {'signatures': [signature], 'message': {'accountKeys': keys}},
        'meta': {'err': None, 'logMessages': [f'Program {PROGRAM} invoke [1]', f'Program {PROGRAM} success'],
            'innerInstructions': [{'index': 0, 'instructions': instructions}]}}


def vault_account(state):
    raw = bytes([66,189,74,88,226,57,203,5])+state.claimed.to_bytes(16,'little')+state.pending.to_bytes(8,'little')+state.lifetime.to_bytes(16,'little')
    from solders.pubkey import Pubkey
    raw += bytes(Pubkey.from_string(state.wallet))+bytes(48)
    return {'owner': PROGRAM, 'data': [base64.b64encode(raw).decode(), 'base64']}


def test_indexed_positions_reconcile_without_double_counting_the_vault():
    parsed = Positions.parse(positions(), WALLET)
    assert parsed.total == 6500000 and parsed.vault == 3500000
    public = parsed.public()
    assert public['total_raw'] == '6500000' and public['helm_fee_basis'] is None
    assert public['scope'] == 'wallet_wide' and not public['collection_ready']


def test_unattributed_only_vault_is_never_assigned_to_a_token_and_large_units_stay_exact():
    value=positions();value['positions']=[]
    value['vaultClaimableLamports']=value['unattributedVaultLamports']=value['totalClaimableLamports']=str(2**53+1)
    parsed=Positions.parse(value,WALLET)
    assert parsed.public()['total_raw']=='9007199254740993' and not parsed.positions


@pytest.mark.parametrize('mutate', [
    lambda p:p.update(wallet=TREASURY), lambda p:p.update(quoteMint=MINT),
    lambda p:p.update(totalClaimableLamports='10000000'), lambda p:p.update(vaultClaimableLamports='0'),
    lambda p:p.update(unattributedVaultLamports='-1'), lambda p:p['positions'][0].update(accruedLamports=True),
    lambda p:p['positions'].append(deepcopy(p['positions'][0])), lambda p:p.pop('totalClaimableLamports'),
])
def test_wrong_wallet_asset_duplicate_or_inconsistent_balances_are_unavailable(mutate):
    value = positions();mutate(value)
    with pytest.raises(AdapterError):Positions.parse(value, WALLET)


async def test_adapter_uses_dedicated_deployer_endpoint_and_preserves_unavailable():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={'success': True, 'response': positions()})
    adapter = BagsAdapter('test-only', client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert (await adapter.deployer_positions(WALLET)).total == 6500000
    assert seen[0].url.path.endswith('/fee-share/deployer/claimable-positions')
    assert seen[0].url.params['wallet'] == WALLET
    unavailable = BagsAdapter('test-only', client=httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request:httpx.Response(503, json={'success': False, 'response': 'Attribution unavailable'}))))
    with pytest.raises(AdapterError, match='503'):await unavailable.deployer_positions(WALLET)


def test_binary_events_match_independently_encoded_official_idl_fixtures():
    for label, fixture in DATA['fixtures'].items():
        decoded = event(label)
        assert decoded.name == fixture['event'] and decoded.values == fixture['values']
    assert event('withdraw').values['claimed'] == 4000000


def test_event_identifier_uses_physical_instruction_position_not_decoder_coverage():
    value=receipt()
    events=reward_events(value,'sig')
    assert events[0].ordinal == 1000000
    value['meta']['innerInstructions'][0]['instructions'].insert(0,{
        'programIdIndex':0,'accounts':[1],'data':b58encode(EVENT_TAG+b'unknown!').decode()})
    assert reward_events(value,'sig')[0].ordinal == 1000001


def test_treasury_income_requires_both_attribution_and_actual_wsOL_transfer():
    value = receipt()
    assert treasury_payout(value, 'sig', MINT, TREASURY) == 25000
    assert treasury_payout(value, 'sig', KEYS['mint_b'], TREASURY) == 0
    assert treasury_payout(value, 'sig', MINT, WALLET) == 0
    with pytest.raises(AdapterError, match='matching token transfers'):
        treasury_payout(receipt(transfer=False), 'sig', MINT, TREASURY)
    value['meta']['innerInstructions'][0]['instructions'][-1]['data'] = b58encode(b'\x03'+(25001).to_bytes(8,'little')).decode()
    with pytest.raises(AdapterError, match='matching token transfers'):treasury_payout(value, 'sig', MINT, TREASURY)


@pytest.mark.parametrize('mutate', [
    lambda p:p['meta'].update(err={'InstructionError': [0,'bad']}),
    lambda p:p['meta'].pop('err'),
    lambda p:p['transaction']['message'].update(addressTableLookups=[{'writableIndexes':[0],'readonlyIndexes':[]}]),
    lambda p:p['transaction'].update(signatures=['other']),
    lambda p:p['meta'].update(logMessages=[f'Program {PROGRAM} invoke [1]']),
    lambda p:p['meta']['logMessages'].insert(1, 'Log truncated'),
    lambda p:p['meta']['logMessages'].insert(1, f'Program {TOKEN} failed: rejected'),
    lambda p:p['meta']['innerInstructions'][0]['instructions'][0].update(accounts=[2]),
    lambda p:p['meta']['innerInstructions'][0]['instructions'][0].update(programIdIndex=-1),
])
def test_reverted_truncated_forged_or_misattributed_receipts_never_book_income(mutate):
    value = receipt();mutate(value)
    with pytest.raises(AdapterError):treasury_payout(value, 'sig', MINT, TREASURY)


def test_foreign_program_log_data_and_mixed_event_encodings_are_not_income():
    raw = DATA['fixtures']['creator']['base64']
    value = receipt(transfer=False);value['meta']['innerInstructions'] = []
    value['meta']['logMessages'] = [f'Program {PROGRAM} invoke [1]', f'Program {TOKEN} invoke [2]',
        'Program data: '+raw, f'Program {TOKEN} success', f'Program {PROGRAM} success']
    assert not reward_events(value, 'sig')
    value = receipt();value['meta']['logMessages'].insert(1, 'Program data: '+raw)
    with pytest.raises(AdapterError):reward_events(value, 'sig')
    with pytest.raises(AdapterError, match='malformed'):
        decode_event(base64.b64decode(raw)+b'\x00', 0)


def history():
    return [HistoryEntry('accrue',100,0,(event('accrue_a'),event('accrue_b',1))),
            HistoryEntry('withdraw',100,1,(event('withdraw'),))]


def test_shared_deployer_withdrawal_keeps_tokens_separate_and_excludes_donations():
    state = VaultState(WALLET,4000000,0,3000000)
    payouts = deployer_payouts(history(),state)
    assert {p.mint:p.amount for p in payouts} == {MINT:1000000,KEYS['mint_b']:2000000}
    assert sum(p.amount for p in payouts) == 3000000  # remaining 1m is unattributed, never a token's fee basis
    assert VaultState.parse(WALLET,vault_account(state)) == state


@pytest.mark.parametrize('rows,state', [
    (history()[1:],VaultState(WALLET,4000000,0,3000000)),
    (history()[::-1],VaultState(WALLET,4000000,0,3000000)),
    (history()+history(),VaultState(WALLET,4000000,0,3000000)),
    (history(),VaultState(WALLET,4000001,0,3000000)),
    (history(),VaultState(WALLET,4000000,1,3000000)),
])
def test_incomplete_reordered_or_duplicate_history_cannot_supply_a_fee_basis(rows,state):
    with pytest.raises(AdapterError):deployer_payouts(rows,state)


class AuditRPC:
    def __init__(self):
        self.state = VaultState(WALLET,4000000,0,3000000)
        self.receipts = {'accrue': receipt(('accrue_a','accrue_b'),'accrue',transfer=False),
                         'withdraw': receipt(('withdraw',),'withdraw')}
        self.calls = []
    async def _call(self,method,args):
        self.calls.append((method,args))
        assert args[-1]['commitment'] == 'finalized'
        if method == 'getAccountInfo':return {'context': {'slot':100}, 'value':vault_account(self.state)}
        if method == 'getSignaturesForAddress':
            return [] if args[-1].get('before') else [{'signature':s,'slot':100,'err':None} for s in ['withdraw','accrue']]
        if method == 'getBlock':return {'signatures':['accrue','withdraw']}
        raise AssertionError(method)
    async def get_transaction(self,signature,commitment):
        assert commitment == 'finalized'
        return self.receipts.get(signature)


async def test_finalized_audit_uses_canonical_block_order_not_api_page_order():
    rpc = AuditRPC();audit = await audit_deployer_vault(rpc,WALLET)
    assert audit.signatures == ('accrue','withdraw') and len(audit.payouts) == 2
    assert any(method == 'getBlock' for method,_ in rpc.calls)


async def test_missing_receipts_audit_limits_and_unsupported_state_never_become_zero():
    rpc = AuditRPC()
    with pytest.raises(AdapterError,match='audit limit'):await audit_deployer_vault(rpc,WALLET,maximum_transactions=1)
    rpc.receipts['accrue'] = None
    with pytest.raises(AdapterError,match='missing'):await audit_deployer_vault(rpc,WALLET)
    value = vault_account(rpc.state);value['owner'] = WALLET
    with pytest.raises(AdapterError):VaultState.parse(WALLET,value)
