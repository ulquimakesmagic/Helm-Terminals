"""Route wallet swaps for recognized Elpida pairs to the launchpad's EVM adapter."""
import time

from fastapi import HTTPException

from ..chains.base import EvmTradeRequest
from ..chains.elpida import NETWORKS, ZERO, amount_label, raw_amount
from ..chains.registry import get_adapter
from ..deps import begin_spend, run_job, two_fa_warnings
from .charges import trade_terms
from .executor import run_evm_trade
from .preflight import build_preflight
from .quote_approval import issue, verify


async def quote_pair(network: str, input_asset: str, output_asset: str):
    config = NETWORKS.get(network)
    if not config:
        return None
    adapter = get_adapter(f"elpida-{network}")
    candidates = ((output_asset, input_asset, "buy"), (input_asset, output_asset, "sell"))
    for token, counter_asset, side in candidates:
        if token.lower() in {ZERO, config.stable.lower()} or not await adapter.token_deployment(token):
            continue
        _, quote, _ = await adapter.token_pair(token)
        if counter_asset.lower() != (quote["token"] or ZERO).lower():
            raise HTTPException(400, f"This Elpida pool trades in {quote['asset']}. Convert to that asset first.")
        return adapter, token, side, quote
    return None


async def swap(body, user, db, ex, wallet, pair):
    adapter, token, side, quote = pair
    rpc = adapter.rpc
    token_asset = await adapter.asset_info(token)
    source, dest = (quote, token_asset) if side == "buy" else (token_asset, quote)
    amount = raw_amount(body.amount, source["decimals"])
    signed_scope = {"purpose": "elpida_wallet_swap", "wallet": wallet.id, "network": wallet.network,
                    "token": token.lower(), "side": side, "amount": str(amount), "slippage": body.slippage_pct,
                    "quote": quote}
    approved = verify(body.quote_approval, user.id, signed_scope) if body.confirm else None
    minimum = int(approved["minimum"]) if approved else 0
    plan = await adapter.prepare_trade(EvmTradeRequest(token, wallet.address, side, amount, body.slippage_pct, minimum))
    fee = await trade_terms(db, wallet.network, user)
    current = plan.notes["current_fee_bps"]
    gas = await ex.evm(wallet.network).max_gas_cost(adapter.trade_gas_limit)
    if approved and (current > approved["fee_bps"] or gas > int(approved["gas"]) or plan.notes["quote_asset"] != quote):
        raise HTTPException(409, "Elpida's trading fee or gas estimate increased. Review a new quote.")
    bounds = approved or {"minimum": str(plan.notes["minimum_out"]), "fee_bps": current, "gas": str(gas)}
    gas = int(bounds["gas"])
    minimum = int(bounds["minimum"])
    input_fee = amount * fee["bps"] // 10_000 if side == "buy" else 0
    warnings = two_fa_warnings(user, "swap")
    native_balance = await rpc.get_balance(wallet.address)
    if source["token"]:
        if await rpc.erc20_balance(source["token"], wallet.address) < amount + input_fee:
            warnings.append(f"Keep {amount_label(amount + input_fee, source['decimals'], source['asset'])} in this wallet.")
        native_need = gas
    else:
        native_need = gas + amount + input_fee
    if native_balance < native_need:
        warnings.append(f"Keep {amount_label(native_need, 18, adapter.native)} in this wallet for the order and gas.")
    platform_fee = f"{fee['bps'] / 100:g}% in {quote['asset']}"
    if fee["bps"]:
        platform_fee += " of the buy amount" if side == "buy" else " of confirmed sale proceeds"
    summary = {"wallet": wallet.address, "network": wallet.network, "token": token, "side": side,
               "input": amount_label(amount, source["decimals"], source["asset"]),
               "output": "At least " + amount_label(minimum, dest["decimals"], dest["asset"]),
               "route": "Elpida router", "slippage": f"{body.slippage_pct:g}%",
               "launchpad_trade_fee": f"{bounds['fee_bps'] / 100:g}%", "max_gas": gas / 10**18,
               "platform_fee": platform_fee, "trade_fee_terms": fee, "trade_quote_asset": quote,
               "raw_buy": str(amount) if side == "buy" else None, "minimum_out": str(minimum),
               "max_launchpad_fee_bps": bounds["fee_bps"]}
    preflight = build_preflight("swap", summary, warnings)
    if not body.confirm:
        preflight["quote_approval"] = issue(user.id, signed_scope, bounds)
        return preflight
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, preflight, "swap", None, summary)
    if created:
        async def work(session, row):
            async def not_expired():
                return time.time() < approved["expires"]

            await run_evm_trade(session, ex, adapter, row, token, [wallet], side, body.amount,
                                "stagger", 0, body.slippage_pct, execution_guard=not_expired,
                                sell_raw=amount if side == "sell" else None)
        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}
