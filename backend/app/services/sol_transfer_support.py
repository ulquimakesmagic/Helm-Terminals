"""Select an owned fee wallet and quote signatures/rent for locally built sends."""
from solders.pubkey import Pubkey
from ..chains.solana import build_unsigned, sol_transfer_ix
from .sol_token_send import (ATA_RENT, SOL_FEE, build_token_send, choose_fee_payer,
                            missing_token_accounts)
from .solana_cash import message_fee


async def plan(db, ex, user, source, asset, destinations, *, fee_wallet_id=None,
               principal=0, committed=None, cache=None, exclude_ids=frozenset(), prefer=()):
    committed = committed if committed is not None else {}
    cache = cache if cache is not None else {}
    is_token = asset['kind'] == 'token'
    per_tx = 5 if is_token else 15
    count = -(-len(destinations) // per_tx)
    missing = await missing_token_accounts(ex.rpc, destinations, asset['token'], asset['program']) if is_token else set()
    rent = len(missing) * ATA_RENT
    if principal:
        committed[source.id] = committed.get(source.id, 0) + principal
    choice = await choose_fee_payer(db, ex.rpc, user.id, source, count * SOL_FEE + rent,
        explicit_id=fee_wallet_id, committed=committed, cache=cache, exclude_ids=exclude_ids, prefer=prefer)
    payer = choice.wallet or source
    blockhash = await ex.rpc.get_latest_blockhash()
    if is_token:
        probe = build_token_send(source.address, payer.address, [(destinations[0], 1)],
            asset['token'], asset['decimals'], asset['program'], blockhash)
    else:
        probe = build_unsigned(Pubkey.from_string(payer.address),
            [sol_transfer_ix(Pubkey.from_string(source.address), Pubkey.from_string(destinations[0]), 1)], blockhash)
    fee = await message_fee(ex.rpc, probe)
    warnings = [] if choice.wallet else ['No eligible fee wallet can cover these network fees and token-account costs. Select or fund one of your managed Solana wallets.']
    return payer, count * fee + rent, warnings


def describe(source, payer):
    return {'source': source.address, 'address': payer.address, 'label': payer.label or 'Wallet',
            'method': 'Source pays' if source.id == payer.id else 'Separate transaction fee payer',
            'note': 'Pays network fees and any required recipient token-account rent. No reimbursement or Helm fee.'}
