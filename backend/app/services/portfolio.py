"""Portfolio across every chain: each chain's coin and dollar stablecoin in your managed wallets, valued in USD.

Balances are read live (a few at a time, so large wallet lists don't flood the RPCs) and cached briefly per user.
Coin prices come from DefiLlama's public price API; if it's unreachable, amounts still show without USD values.
Stablecoins are counted at $1.
"""

import asyncio
import time
from datetime import datetime, timezone

import httpx

from ..models import Wallet
from .chain_balances import STABLES, chain_name, native_balance, native_coin, rpc_detail, token_balance_raw, token_decimals

CHAINS = ["solana", "base", "bsc", "robinhood", "ethereum"]
PRICE_IDS = {"SOL": "coingecko:solana", "ETH": "coingecko:ethereum", "BNB": "coingecko:binancecoin"}
PRICE_URL = "https://coins.llama.fi/prices/current/"
MAX_WALLETS = 300

_price_cache: tuple[float, dict[str, float]] = (0.0, {})
_cache: dict[str, tuple[float, dict]] = {}
_decimals: dict[tuple[str, str], int] = {}


async def coin_prices(client: httpx.AsyncClient | None = None) -> dict[str, float]:
    global _price_cache
    if time.monotonic() - _price_cache[0] < 60 and _price_cache[1]:
        return _price_cache[1]
    own = client is None
    client = client or httpx.AsyncClient(timeout=8)
    try:
        r = await client.get(PRICE_URL + ",".join(PRICE_IDS.values()))
        coins = r.json().get("coins", {})
        prices = {sym: float(coins[cid]["price"]) for sym, cid in PRICE_IDS.items() if cid in coins}
    except Exception:
        prices = {}
    finally:
        if own:
            await client.aclose()
    if prices:
        _price_cache = (time.monotonic(), prices)
    return prices or _price_cache[1]


async def _stable_decimals(ex, network: str, token: str) -> int:
    key = (network, token)
    if key not in _decimals:
        _decimals[key] = await token_decimals(ex, network, token)
    return _decimals[key]


async def _chain(ex, network: str, wallets: list[Wallet], sem: asyncio.Semaphore) -> dict:
    stable_symbol, token = STABLES[network]
    name = chain_name(network)
    coin = native_coin(network)
    # status: "ok" (every balance read), "partial" (some read), "unavailable" (nothing read) or "no_wallets".
    # The coin and the stablecoin are read separately, so one failing doesn't hide the other. Amounts only count
    # what was read, so a zero under "unavailable" means "unknown", not "empty".
    out = {"network": network, "name": name, "coin": coin, "wallets": len(wallets),
           "native": 0.0, "stable_symbol": stable_symbol, "stable": 0.0, "error": None,
           "status": "no_wallets" if not wallets else "ok", "wallets_read": 0,
           "native_read": 0, "stable_read": 0, "top": []}
    if not wallets:
        return out
    try:
        dec: int | None = await _stable_decimals(ex, network, token)
        dec_err: Exception | None = None
    except Exception as e:
        dec, dec_err = None, e
    rows = []
    errors: dict[str, Exception] = {}

    async def one(w: Wallet):
        nat = st = None
        async with sem:
            try:
                nat = await native_balance(ex, network, w.address)
            except Exception as e:
                errors.setdefault("native", e)
            if dec is not None:
                try:
                    st = await token_balance_raw(ex, network, token, w.address) / 10**dec
                except Exception as e:
                    errors.setdefault("stable", e)
        rows.append((w, nat, st))

    await asyncio.gather(*(one(w) for w in wallets))
    if dec_err is not None:
        errors.setdefault("stable", dec_err)
    n_ok = sum(1 for _, n, _ in rows if n is not None)
    s_ok = sum(1 for _, _, s in rows if s is not None)
    total = len(wallets)
    out["native_read"], out["stable_read"] = n_ok, s_ok
    out["wallets_read"] = sum(1 for _, n, s in rows if n is not None and s is not None)
    plural = "s" if total != 1 else ""
    problems = [f"{label} unread for {total - ok} of {total} wallet{plural} ({rpc_detail(errors[part])})"
                for label, part, ok in ((stable_symbol, "stable", s_ok), (coin, "native", n_ok))
                if ok < total and part in errors]
    if problems:
        out["error"] = f"{name}: " + "; ".join(problems)
        out["status"] = "unavailable" if n_ok == 0 and s_ok == 0 else "partial"
    out["native"] = sum(n or 0.0 for _, n, _ in rows)
    out["stable"] = sum(s or 0.0 for _, _, s in rows)
    rows.sort(key=lambda r: -((r[2] or 0) + (r[1] or 0)))
    out["top"] = [{"id": w.id, "label": w.label, "address": w.address, "native": n or 0.0, "stable": s or 0.0}
                  for w, n, s in rows[:3] if n or s]
    return out


async def build_portfolio(ex, user_id: str, wallets: list[Wallet], fresh: bool = False) -> dict:
    cached = _cache.get(user_id)
    if cached and not fresh and time.monotonic() - cached[0] < 20:
        return cached[1]
    managed = [w for w in wallets if w.kind == "managed" and w.network in CHAINS][:MAX_WALLETS]
    sem = asyncio.Semaphore(8)
    chains, prices = await asyncio.gather(
        asyncio.gather(*(_chain(ex, n, [w for w in managed if w.network == n], sem) for n in CHAINS)),
        coin_prices())
    total = stable_total = native_usd = 0.0
    for c in chains:
        price = prices.get(c["coin"])
        c["price_usd"] = price
        c["native_usd"] = c["native"] * price if price is not None else None
        c["usd"] = c["stable"] + (c["native_usd"] or 0)
        stable_total += c["stable"]
        native_usd += c["native_usd"] or 0
        total += c["usd"]
    incomplete = [c["network"] for c in chains if c["status"] in ("partial", "unavailable")]
    out = {"chains": chains, "totals": {"usd": total, "stable_usd": stable_total, "native_usd": native_usd},
           "complete": not incomplete, "incomplete_chains": incomplete,
           "checked_at": datetime.now(timezone.utc).isoformat(),
           "prices": prices, "priced": bool(prices), "wallets": len(managed),
           "note": "Coins and dollar stablecoins in your managed wallets. Launch tokens are not valued here."}
    _cache[user_id] = (time.monotonic(), out)
    return out
