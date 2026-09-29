"""Portfolio across every chain: each chain's coin and dollar stablecoin in your managed wallets, valued in USD.

Balances are read live (a few at a time, so large wallet lists don't flood the RPCs) and cached briefly per user.
Coin prices come from DefiLlama's public price API; if it's unreachable, amounts still show without USD values.
Stablecoins are counted at $1.
"""

import asyncio
import json
import math
import time
from datetime import datetime, timezone

import httpx

from ..models import Wallet
from ..chains.networks import EXTRA_NETWORKS, extra_enabled
from . import extra_chains
from .chain_balances import STABLES, chain_name, native_balance, native_coin, rpc_detail, token_balance_raw, token_decimals

CHAINS = ["solana", "base", "bsc", "robinhood", "ethereum", *EXTRA_NETWORKS]
PRICE_IDS = {"SOL": "coingecko:solana", "ETH": "coingecko:ethereum", "BNB": "coingecko:binancecoin",
             "TRX": "coingecko:tron", "NEAR": "coingecko:near", "TON": "coingecko:the-open-network"}
PRICE_URL = "https://coins.llama.fi/prices/current/"
MAX_WALLETS = 300
CHAIN_TIMEOUT_S = 20

_price_cache: tuple[float, dict[str, float]] = (0.0, {})
_cache: dict[str, tuple[float, tuple, dict]] = {}
_inflight: dict[tuple, asyncio.Task] = {}
_decimals: dict[tuple[str, str], int] = {}


async def coin_prices(client: httpx.AsyncClient | None = None) -> dict[str, float]:
    global _price_cache
    if time.monotonic() - _price_cache[0] < 60:
        return _price_cache[1]
    own = client is None
    client = client or httpx.AsyncClient(timeout=8)
    try:
        r = await client.get(PRICE_URL + ",".join(PRICE_IDS.values()))
        r.raise_for_status()
        coins = r.json().get("coins", {})
        prices = {}
        for sym, cid in PRICE_IDS.items():
            quote = coins.get(cid) or {}
            try:
                price = float(quote["price"])
                age = time.time() - float(quote["timestamp"])
                if math.isfinite(price) and price > 0 and -60 <= age <= 300:
                    prices[sym] = price
            except (KeyError, TypeError, ValueError):
                continue
    except Exception:
        prices = {}
    finally:
        if own:
            await client.aclose()
    _price_cache = (time.monotonic(), prices)
    return prices  # never relabel an indefinitely old cached quote as a fresh portfolio value


HIST_URL = "https://coins.llama.fi/batchHistorical"
_day_prices: dict[tuple[str, str], float] = {}  # (coin, YYYY-MM-DD) -> USD; past days never change


async def _fetch_history(coin: str, cid: str, days: list[str], client: httpx.AsyncClient | None) -> dict[str, float]:
    stamps = {int(datetime.fromisoformat(d).replace(hour=12, tzinfo=timezone.utc).timestamp()): d for d in days}
    got: dict[str, float] = {}
    own = client is None
    client = client or httpx.AsyncClient(timeout=10)
    try:
        keys = list(stamps)
        for i in range(0, len(keys), 100):  # keep the query string a sane length
            r = await client.get(HIST_URL, params={"coins": json.dumps({cid: keys[i:i + 100]}), "searchWidth": "43200"})
            for pt in ((r.json().get("coins") or {}).get(cid) or {}).get("prices") or []:
                day = datetime.fromtimestamp(int(pt["timestamp"]), timezone.utc).date().isoformat()
                if day in days:
                    _day_prices[(coin, day)] = got[day] = float(pt["price"])
    except Exception:
        pass
    finally:
        if own:
            await client.aclose()
    return got


async def daily_prices(coin: str, days: list[str], client: httpx.AsyncClient | None = None) -> dict[str, float]:
    """USD price of `coin` (SOL, ETH, BNB) on each day (noon UTC), from DefiLlama. Days it can't price are left out;
    today uses the live price."""
    cid = PRICE_IDS.get(coin)
    if not cid or not days:
        return {}
    today = datetime.now(timezone.utc).date().isoformat()
    out = {d: _day_prices[(coin, d)] for d in days if (coin, d) in _day_prices}
    missing = [d for d in days if d not in out and d != today]
    if missing:
        out.update(await _fetch_history(coin, cid, missing, client))
    if today in days:
        live = (await coin_prices()).get(coin)
        if live is not None:
            out[today] = live
    # Anything still unpriced (API down, a gap) uses the nearest priced day, else today's price.
    live = out.get(today) or (await coin_prices()).get(coin)
    for d in days:
        if d not in out:
            near = min((k for k in out), key=lambda k: abs(datetime.fromisoformat(k) - datetime.fromisoformat(d)), default=None)
            if near is not None or live is not None:
                out[d] = out[near] if near is not None else live
    return out


async def _stable_decimals(ex, network: str, token: str) -> int:
    key = (network, token)
    if key not in _decimals:
        _decimals[key] = await token_decimals(ex, network, token)
    return _decimals[key]


async def _chain(ex, network: str, wallets: list[Wallet], sem: asyncio.Semaphore,
                 read_ids: set[str], enabled: set[str]) -> dict:
    stable_symbol, token = STABLES.get(network, (None, None))
    supported = network in CHAINS
    name = chain_name(network) if supported else network
    coin = native_coin(network) if supported else ""
    native_is_stable = network == "arc"
    # status: "ok" (every balance read), "partial" (some read), "unavailable" (nothing read) or "no_wallets".
    # The coin and the stablecoin are read separately, so one failing doesn't hide the other. Amounts only count
    # what was read; entirely unread amounts are null rather than zero.
    out = {"network": network, "name": name, "coin": coin, "wallets": len(wallets),
           "native": 0.0, "stable_symbol": stable_symbol, "stable": 0.0, "error": None,
           "status": "no_wallets" if not wallets else "ok", "wallets_read": 0,
           "native_read": 0, "stable_read": 0, "top": [], "message": None,
           "stable_tracked": token is not None, "native_is_stable": native_is_stable,
           "scope": ("Available NEAR after storage; tokens not included." if network == "near" else
                     "Native USDC, counted once; other tokens not included." if native_is_stable else
                     "Native coin only; tokens not included." if network in EXTRA_NETWORKS else None)}
    if not wallets:
        return out
    if not supported or (network in EXTRA_NETWORKS and network not in enabled):
        out.update(status="unavailable", native=None, stable=None,
                   message="Balance reads are not supported for this chain." if not supported else
                           "This chain is disabled on this server. Its wallets are still counted.")
        return out
    dec = dec_err = None
    if token:
        try:
            dec = await _stable_decimals(ex, network, token)
        except Exception as e:
            dec_err = e
    rows = []
    errors: dict[str, Exception] = {}

    async def one(w: Wallet):
        nat = st = None
        if w.id not in read_ids:
            rows.append((w, nat, st))
            return
        try:
            async with sem:
                try:
                    if network in EXTRA_NETWORKS:
                        raw = await extra_chains.native_balance(ex, network, w.address)
                        nat = float(extra_chains.human(raw, EXTRA_NETWORKS[network].decimals))
                    else:
                        nat = await native_balance(ex, network, w.address)
                except Exception as e:
                    errors.setdefault("native", e)
                if dec is not None:
                    try:
                        st = await token_balance_raw(ex, network, token, w.address) / 10**dec
                    except Exception as e:
                        errors.setdefault("stable", e)
        finally:
            rows.append((w, nat, st))  # preserve a successful coin read even if the token read times out

    # One wallet at a time per chain: a rate-limited endpoint can cool down before the next wallet is read.
    # Different chains still run concurrently, and callers share an in-flight portfolio refresh below.
    timed_out = False
    try:
        async with asyncio.timeout(CHAIN_TIMEOUT_S):
            for w in wallets:
                await one(w)
    except TimeoutError:
        timed_out = True
        attempted = {w.id for w, _, _ in rows}
        rows.extend((w, None, None) for w in wallets if w.id not in attempted)
    if dec_err is not None:
        errors.setdefault("stable", dec_err)
    n_ok = sum(1 for _, n, _ in rows if n is not None)
    s_ok = sum(1 for _, _, s in rows if s is not None)
    total = len(wallets)
    out["native_read"], out["stable_read"] = n_ok, s_ok
    out["wallets_read"] = sum(1 for _, n, s in rows if n is not None and (not token or s is not None))
    plural = "s" if total != 1 else ""
    problems = [f"{label} unread for {total - ok} of {total} wallet{plural} ({rpc_detail(errors[part])})"
                for label, part, ok in ((stable_symbol, "stable", s_ok), (coin, "native", n_ok))
                if ok < total and part in errors]
    if problems:
        out["error"] = f"{name}: " + "; ".join(problems)
    if n_ok < total or (token and s_ok < total):
        out["status"] = "unavailable" if n_ok == 0 and s_ok == 0 else "partial"
        missing = [f"{label} read for {ok} of {total} wallets" for label, ok in
                   ((coin, n_ok), (stable_symbol, s_ok)) if label and ok < total]
        out["message"] = ". ".join(missing) + "."
        if any(w.id not in read_ids for w in wallets):
            out["message"] += " The wallet scan limit was reached. Unread balances are excluded."
        elif timed_out:
            out["message"] += " The balance request timed out. Try again shortly."
        elif any("429" in str(e) or "cooling down" in str(e) or "rate limit" in str(e).lower()
                 for e in errors.values()):
            out["message"] += " The balance provider is busy; try again shortly."
        elif any("not on its plan" in str(e) for e in errors.values()):
            out["message"] += " The provider does not support these balance reads."
        else:
            out["message"] += " Some balance reads are temporarily unavailable."
    out["native"] = sum(n or 0.0 for _, n, _ in rows) if n_ok else None
    out["stable"] = sum(s or 0.0 for _, _, s in rows) if s_ok else None
    rows.sort(key=lambda r: -((r[2] or 0) + (r[1] or 0)))
    out["top"] = [{"id": w.id, "label": w.label, "address": w.address, "native": n, "stable": s}
                  for w, n, s in rows[:3] if n or s]
    return out


async def _read_portfolio(ex, managed: list[Wallet], enabled: set[str]) -> dict:
    read_ids = {w.id for w in managed[:MAX_WALLETS]}
    networks = list(dict.fromkeys([*CHAINS, *(w.network for w in managed)]))
    sem = asyncio.Semaphore(8)
    chains, prices = await asyncio.gather(
        asyncio.gather(*(_chain(ex, n, [w for w in managed if w.network == n], sem, read_ids, enabled)
                        for n in networks)),
        coin_prices())
    total = stable_total = native_usd = 0.0
    stable_known = native_known = False
    for c in chains:
        price = 1.0 if c["native_is_stable"] else prices.get(c["coin"])
        native = c["native"]
        c["native_complete"] = c["native_read"] == c["wallets"]
        c["stable_complete"] = c["stable_read"] == c["wallets"] if c["stable_tracked"] else True
        c["price_complete"] = not c["wallets"] or native is None or price is not None or native == 0
        c["value_complete"] = c["native_complete"] and c["stable_complete"] and c["price_complete"]
        c["price_usd"] = price
        c["native_usd"] = native * price if native is not None and price is not None else 0.0 if native == 0 else None
        known = c["native_usd"] is not None or c["stable"] is not None
        c["usd"] = (c["stable"] or 0) + (c["native_usd"] or 0) if known else None
        stable_total += (c["stable"] or 0) + ((c["native_usd"] or 0) if c["native_is_stable"] else 0)
        if not c["native_is_stable"]:
            native_usd += c["native_usd"] or 0
        stable_known |= bool(c["stable_read"] or (c["native_is_stable"] and c["native_read"]))
        native_known |= bool(c["native_read"] and not c["native_is_stable"] and c["native_usd"] is not None)
        total += c["usd"] or 0
    incomplete = [c["network"] for c in chains if c["status"] in ("partial", "unavailable")]
    stable_complete = all(c["native_complete"] if c["native_is_stable"] else c["stable_complete"] for c in chains)
    native_complete = all(c["native_complete"] and c["price_complete"] for c in chains if not c["native_is_stable"])
    unpriced = [c["network"] for c in chains if not c["price_complete"]]
    out = {"chains": chains, "totals": {"usd": total if stable_known or native_known or not managed else None,
                                         "stable_usd": stable_total if stable_known or stable_complete else None,
                                         "native_usd": native_usd if native_known or native_complete else None},
           "complete": not incomplete and not unpriced, "incomplete_chains": incomplete,
           "stable_complete": stable_complete, "native_complete": native_complete, "unpriced_chains": unpriced,
           "checked_at": datetime.now(timezone.utc).isoformat(),
           "prices": prices, "priced": not unpriced, "wallets": len(managed),
           "note": "Managed wallets only. Native coins and listed stablecoins; launch tokens and other tokens are excluded. "
                   "NEAR is available after storage. USDC and USDG are valued at $1; Arc USDC is counted once."}
    return out


async def build_portfolio(ex, user_id: str, wallets: list[Wallet], fresh: bool = False) -> dict:
    managed = sorted((w for w in wallets if w.kind == "managed"), key=lambda w: w.id)
    enabled = set(extra_enabled())
    signature = (tuple((w.id, w.network, w.address) for w in managed), tuple(sorted(enabled)))
    cached = _cache.get(user_id)
    if cached and cached[1] == signature and time.monotonic() - cached[0] < (5 if fresh else 20):
        return cached[2]
    key = (user_id, signature)
    task = _inflight.get(key)
    if task is None:
        async def refresh():
            out = await _read_portfolio(ex, managed, enabled)
            _cache[user_id] = (time.monotonic(), signature, out)
            return out

        task = asyncio.create_task(refresh())
        _inflight[key] = task
        task.add_done_callback(lambda done: _inflight.pop(key, None))
    return await asyncio.shield(task)
