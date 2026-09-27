"""Launch from your dollar balance.

Keep your money in USDC (USDG on Robinhood Chain). When a launch's dev wallet is short of its chain's coin, the
deploy preview includes a funding step: Helm picks one of your wallets holding enough USDC (and a little gas to
send it), sizes a Relay route to cover the shortfall with headroom, and shows the amount, source and route fee.
Confirming the deploy runs that route first, waits for the coin to land, then deploys. If the route doesn't
complete, nothing is deployed.
"""

import asyncio
import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.relay import DONE
from ..config import get_settings
from ..models import Job, Wallet
from . import portfolio
from .chain_balances import STABLE_DECIMALS, chain_name, fee_reserve, native_coin, stable_for
from .executor import Executor
from .routing import ROUTE_GAS, asset_balance, execute_route

BUFFER = 1.05  # price movement and Relay's own fees between preview and fill
FLAT_USD = 1.0  # destination gas and rounding headroom
MAX_SCAN = 60  # wallets checked for a funding source
SLIPPAGE_PCT = 1.0


def _coin_decimals(network: str) -> int:
    return 9 if network == "solana" else 18


async def plan_funding(db: AsyncSession, ex: Executor, user_id: str, dst: Wallet,
                       shortfall_raw: int) -> tuple[dict | None, str | None]:
    """(plan, None) when a USDC route can cover `shortfall_raw` of dst's coin, else (None, why not)."""
    coin = native_coin(dst.network)
    price = (await portfolio.coin_prices()).get(coin)
    if not price:
        return None, f"{coin}'s price couldn't be read, so a USDC top-up can't be sized right now."
    fee_bps = ex.relay.fee_bps
    wallets = (await db.execute(select(Wallet).where(Wallet.user_id == user_id, Wallet.kind == "managed"))).scalars().all()
    # Prefer the dev wallet itself, then its chain, then anywhere else.
    ordered = sorted(wallets, key=lambda w: (w.id != dst.id, w.network != dst.network))[:MAX_SCAN]
    sem = asyncio.Semaphore(8)

    async def read(w: Wallet):
        async with sem:
            try:
                stable = await asset_balance(ex, w.network, "stable", w.address)
                native = await asset_balance(ex, w.network, "native", w.address)
                gas = await fee_reserve(ex, w.network, 2, ROUTE_GAS)
            except Exception:
                return None
            return w, stable, native, gas

    reads = [r for r in await asyncio.gather(*(read(w) for w in ordered)) if r]
    need_coin = shortfall_raw / 10 ** _coin_decimals(dst.network)
    best = None
    for w, stable, native, gas in reads:
        gas_raw = int(gas * 10 ** _coin_decimals(w.network))
        # Swapping in the dev wallet itself spends some of its own coin on gas, so cover that too.
        cover = need_coin + (gas if w.id == dst.id else 0)
        usd = math.ceil(cover * price * BUFFER / (1 - fee_bps / 10_000) + FLAT_USD)
        if native < gas_raw or stable < usd * 10 ** STABLE_DECIMALS[w.network]:
            continue
        group = (w.id != dst.id, w.network != dst.network)
        if best is None or group < best[0] or (group == best[0] and stable > best[2]):
            best = (group, w, stable, usd)
    if best is None:
        usd = math.ceil(need_coin * price * BUFFER / (1 - fee_bps / 10_000) + FLAT_USD)
        return None, (f"No wallet holds about ${usd} in USDC plus gas to route it. Fund one with USDC "
                      f"(and a little of its chain's coin for fees), or add {coin} to the dev wallet directly.")
    _, src, _, usd = best
    symbol = stable_for(src.network)[0]
    return {
        "from_wallet_id": src.id, "from": src.label or "Wallet", "from_address": src.address,
        "from_chain": chain_name(src.network), "from_network": src.network, "send": f"{usd} {symbol}", "amount_usd": usd,
        "receive": f"{coin} in the dev wallet on {chain_name(dst.network)}",
        "provider": "Relay (relay.link)", "route_fee": ex.relay.fee_note,
        "leftover": "Anything not used by the launch stays in the dev wallet",
    }, None


def detail(plan: dict, shortfall_raw: int, network: str) -> dict:
    """Display-only numbers that move block to block, kept out of the confirmation hash."""
    return {"covers": f"{shortfall_raw / 10 ** _coin_decimals(network):.6f} {native_coin(network)} short"}


async def usd_value(network: str, amount: float) -> float | None:
    """Rough USD value of an amount of a chain's coin at the live price (display only)."""
    price = (await portfolio.coin_prices()).get(native_coin(network))
    return round(amount * price, 2) if price else None


async def run_funding(session: AsyncSession, ex: Executor, job: Job, plan: dict, dst: Wallet,
                      need_raw: int) -> str | None:
    """Run the planned route, then wait for dst to hold `need_raw`. Returns an error, or None when funded."""
    src = await session.get(Wallet, plan["from_wallet_id"])
    if src is None:
        return "the funding wallet no longer exists"
    amount_raw = int(plan["amount_usd"]) * 10 ** STABLE_DECIMALS[src.network]
    res, status = await execute_route(session, ex, job, src, dst, "stable", "native", amount_raw, SLIPPAGE_PCT,
                                      kind="fund_route", key="funding")
    if res.status != "confirmed":
        return f"Funding route wasn't sent: {res.error or 'failed'}. Nothing was deployed."
    if status not in DONE:
        return (f"Funding route is {status} on Relay's side, so nothing was deployed yet. "
                "Deploy again once the coin arrives in the dev wallet.")
    s = get_settings()
    for _ in range(10):  # the fill can land a moment before our RPC sees it
        if await asset_balance(ex, dst.network, "native", dst.address) >= need_raw:
            return None
        await asyncio.sleep(s.route_poll_seconds)
    return "The route completed but the dev wallet still reads short, so nothing was deployed. Refresh and retry."
