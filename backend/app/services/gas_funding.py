"""Reviewed native-coin gas funding for ordinary EVM wallets. Never a hidden relay."""
from fastapi import HTTPException
from sqlalchemy import select
from ..models import Wallet
from .executor import SubmitResult, _record, send_evm_tracked
from .evm_exec import EvmCall


async def allowance(ex, network, gas_limit):
    from .evm_exec import GAS_HEADROOM, NATIVE_RESERVE_WEI
    import math
    return await ex.evm(network).max_gas_cost(math.ceil(gas_limit * GAS_HEADROOM)) + NATIVE_RESERVE_WEI.get(network, 0)


async def plan(db, ex, user, requirements, fee_wallet_id=None):
    """requirements: (source wallet, maximum gas cost, native principal).

    An explicitly chosen different fee wallet supplies the approved gas allowance.
    Automatic mode funds only the shortfall. Principal is never funded by this path.
    """
    if not requirements:
        return [], []
    network = requirements[0][0].network
    sender = ex.evm(network)
    funding_gas = await allowance(ex, network, 21_000)
    source_ids = {wallet.id for wallet, _, _ in requirements}
    reserved = {wallet.id: gas + principal for wallet, gas, principal in requirements}
    if fee_wallet_id:
        wallet = await db.get(Wallet, fee_wallet_id)
        if wallet is None or wallet.user_id != user.id:
            raise HTTPException(404, 'Fee wallet not found')
        if wallet.network != network or wallet.kind != 'managed':
            raise HTTPException(400, 'The fee wallet must be managed by you on this network')
        candidates = [wallet]
    else:
        candidates = list((await db.execute(select(Wallet).where(Wallet.user_id == user.id,
            Wallet.network == network, Wallet.kind == 'managed', Wallet.id.not_in(source_ids)).limit(20))).scalars())
    balances, funding, warnings = {}, [], []
    for wallet, gas, principal in requirements:
        balance = int(await sender.rpc.get_balance(wallet.address))
        forced = fee_wallet_id and fee_wallet_id != wallet.id
        need = gas if forced else max(0, gas - max(0, balance - principal))
        if not need:
            continue
        chosen = None
        for payer in candidates:
            if payer.id == wallet.id:
                continue
            if payer.id not in balances:
                balances[payer.id] = int(await sender.rpc.get_balance(payer.address))
            if balances[payer.id] - reserved.get(payer.id, 0) >= need + funding_gas:
                chosen = payer
                reserved[payer.id] = reserved.get(payer.id, 0) + need + funding_gas
                break
        if chosen is None:
            warnings.append(f"{wallet.label or wallet.address[:8]} needs gas; the selected or checked fee wallets cannot cover it and their own funding fees.")
            continue
        funding.append({'payer_wallet_id': chosen.id, 'source_wallet_id': wallet.id, 'network': network,
            'from_address': chosen.address, 'to_address': wallet.address, 'amount_raw': str(need),
            'funding_fee_max_raw': str(funding_gas), 'decimals': 18,
            'note': 'Native-coin gas allowance transferred before the operation. Unused allowance stays in the source wallet.'})
    return funding, warnings


async def execute(db, ex, job, funding):
    from .execution_journal import require_steps
    for i, leg in enumerate(funding):
        await require_steps(db, job, leg['payer_wallet_id'], [f'gas_fund_{i}'])
    results = []
    for i, leg in enumerate(funding):
        payer = await db.get(Wallet, leg['payer_wallet_id'])
        source = await db.get(Wallet, leg['source_wallet_id'])
        if (not payer or not source or payer.user_id != job.user_id or source.user_id != job.user_id
                or payer.kind != 'managed' or source.kind != 'managed'
                or payer.network != leg['network'] or source.network != leg['network']
                or payer.address != leg['from_address'] or source.address != leg['to_address']):
            results.append(SubmitResult(None, 'failed', 'Gas-funding wallets changed; review again'))
            break
        result = await send_evm_tracked(ex.evm(leg['network']), job, payer, f'gas_fund_{i}',
                                        EvmCall(to=source.address, value=int(leg['amount_raw'])))
        await _record(db, job, payer, f'gas_fund_{i}', result, None)
        await db.commit()
        results.append(result)
        if result.status != 'confirmed':
            break
    return results
