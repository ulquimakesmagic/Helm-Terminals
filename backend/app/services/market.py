"""Indicative market data for a launched token: DEX pools and current metrics from DEX Screener, price candles from
GeckoTerminal. Read-only and display-only. Nothing here feeds execution quotes, previews or confirmations.

Providers and what each is used for (both documented public APIs, no scraping, no embeds):
- DEX Screener `GET /token-pairs/v1/{chainId}/{tokenAddress}`: the pools that trade a token, with each pool's DEX,
  pair address, quote asset, liquidity, price, price change, volume, transaction counts, FDV and market cap.
  DEX Screener documents no candle endpoint, so it is not used for chart history.
- GeckoTerminal `GET /api/v2/networks/{network}/pools/{pool}/ohlcv/{timeframe}`: OHLCV candles for one pool.
  Public tier: 30 calls/min per its FAQ (10/min on its product page), so candles are cached and requests throttled.

Pools are resolved from the launch's own chain and token address, never by ticker. Responses are cached briefly per
token/pool; when a provider fails or rate-limits, the last good answer is served and marked stale.
"""

import asyncio
import time
from dataclasses import dataclass, field

import httpx

from ..config import get_settings

DEXSCREENER = "https://api.dexscreener.com"
GECKOTERMINAL = "https://api.geckoterminal.com/api/v2"

# Helm network -> provider chain/network id (checked against each provider's own chain pages).
DEXSCREENER_CHAIN = {"solana": "solana", "base": "base", "bsc": "bsc", "ethereum": "ethereum", "robinhood": "robinhood"}
GECKO_NETWORK = {"solana": "solana", "base": "base", "bsc": "bsc", "ethereum": "eth", "robinhood": "robinhood"}

# Chart timeframes offered in the UI -> GeckoTerminal (timeframe, aggregate). Only documented combinations.
TIMEFRAMES = {"5m": ("minute", 5), "15m": ("minute", 15), "1h": ("hour", 1), "4h": ("hour", 4), "1d": ("day", 1)}
CANDLE_LIMIT = 200

PAIRS_TTL = 30  # seconds a pool list is reused before asking again
CANDLES_TTL = 60
STALE_MAX = 30 * 60  # beyond this, a cached answer isn't shown at all
GECKO_PER_MIN = 8  # stay under the public tier's documented limit, shared by every user of this server


class MarketError(Exception):
    """A provider couldn't answer. `kind` is 'rate_limited', 'unavailable' or 'not_found'."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class _Entry:
    at: float
    value: object


@dataclass
class Market:
    client: httpx.AsyncClient | None = None
    _cache: dict = field(default_factory=dict)
    _gecko_calls: list = field(default_factory=list)
    _backoff_until: dict = field(default_factory=dict)  # provider -> monotonic time
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self.client = self.client or httpx.AsyncClient(timeout=12, headers={"Accept": "application/json"})

    # --- caching helpers ------------------------------------------------------------------------

    def _fresh(self, key, ttl):
        e = self._cache.get(key)
        return e if e and time.monotonic() - e.at < ttl else None

    def _stale(self, key):
        e = self._cache.get(key)
        return e if e and time.monotonic() - e.at < STALE_MAX else None

    def _age(self, e: _Entry) -> float:
        return time.monotonic() - e.at

    async def _get(self, provider: str, url: str, params: dict | None = None) -> dict | list:
        if time.monotonic() < self._backoff_until.get(provider, 0):
            raise MarketError("rate_limited", f"{provider} asked us to slow down; trying again shortly")
        try:
            r = await self.client.get(url, params=params)
        except httpx.HTTPError as e:
            raise MarketError("unavailable", f"{provider} is unreachable ({type(e).__name__})")
        if r.status_code == 429:
            retry = r.headers.get("retry-after")
            self._backoff_until[provider] = time.monotonic() + (float(retry) if retry and retry.isdigit() else 60)
            raise MarketError("rate_limited", f"{provider} rate limit reached; showing the last data we had")
        if r.status_code == 404:
            raise MarketError("not_found", f"{provider} has no data for this")
        if r.status_code >= 400:
            raise MarketError("unavailable", f"{provider} answered HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise MarketError("unavailable", f"{provider} sent an unreadable response")

    # --- pools ------------------------------------------------------------------------------------

    async def pairs(self, network: str, token: str) -> dict:
        """Pools trading `token` on `network`, largest liquidity first, with metrics as the provider reports them."""
        chain = DEXSCREENER_CHAIN.get(network)
        if not chain:
            return {"supported": False, "pairs": [], "source": "DEX Screener", "fetched_at": None, "stale": False}
        key = ("pairs", chain, token.lower())
        hit = self._fresh(key, PAIRS_TTL)
        if hit:
            return {**hit.value, "stale": False, "age_s": round(self._age(hit))}
        try:
            raw = await self._get("DEX Screener", f"{DEXSCREENER}/token-pairs/v1/{chain}/{token}")
        except MarketError as e:
            old = self._stale(key)
            if old and e.kind != "not_found":
                return {**old.value, "stale": True, "age_s": round(self._age(old)), "error": str(e)}
            if e.kind == "not_found":
                raw = []
            else:
                raise
        pairs = [_pair(p, token, network) for p in (raw if isinstance(raw, list) else raw.get("pairs") or [])
                 if _matches(p, chain, token, network)]
        pairs.sort(key=lambda p: -(p["liquidity_usd"] or 0))
        value = {"supported": True, "pairs": pairs, "source": "DEX Screener", "fetched_at": time.time()}
        self._cache[key] = _Entry(time.monotonic(), value)
        return {**value, "stale": False, "age_s": 0}

    # --- candles ----------------------------------------------------------------------------------

    async def candles(self, network: str, pool: str, timeframe: str, token_side: str) -> dict:
        gnet = GECKO_NETWORK.get(network)
        if not gnet:
            return {"supported": False, "candles": [], "source": "GeckoTerminal", "fetched_at": None, "stale": False}
        tf, agg = TIMEFRAMES[timeframe]
        key = ("candles", gnet, pool.lower(), timeframe, token_side)
        hit = self._fresh(key, CANDLES_TTL)
        if hit:
            return {**hit.value, "stale": False, "age_s": round(self._age(hit))}
        try:
            await self._throttle()
            base = (get_settings().geckoterminal_api_url or GECKOTERMINAL).rstrip("/")
            raw = await self._get("GeckoTerminal", f"{base}/networks/{gnet}/pools/{pool}/ohlcv/{tf}",
                                  {"aggregate": agg, "limit": CANDLE_LIMIT, "currency": "usd", "token": token_side})
        except MarketError as e:
            old = self._stale(key)
            if old and e.kind != "not_found":
                return {**old.value, "stale": True, "age_s": round(self._age(old)), "error": str(e)}
            if e.kind == "not_found":
                value = {"supported": True, "candles": [], "source": "GeckoTerminal", "fetched_at": time.time(),
                         "reason": "GeckoTerminal hasn't indexed this pool yet"}
                self._cache[key] = _Entry(time.monotonic(), value)
                return {**value, "stale": False, "age_s": 0}
            raise
        rows = (((raw or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        candles = []
        for row in rows:
            try:
                t, o, h, lo, c, v = (float(x) for x in row[:6])
            except (TypeError, ValueError):
                continue  # a malformed row is dropped, never filled in
            candles.append({"t": int(t), "o": o, "h": h, "l": lo, "c": c, "v": v})
        candles.sort(key=lambda x: x["t"])  # the provider returns newest first
        value = {"supported": True, "candles": candles, "source": "GeckoTerminal", "fetched_at": time.time(),
                 "interval": timeframe}
        self._cache[key] = _Entry(time.monotonic(), value)
        return {**value, "stale": False, "age_s": 0}

    async def _throttle(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._gecko_calls[:] = [t for t in self._gecko_calls if now - t < 60]
            if len(self._gecko_calls) >= GECKO_PER_MIN:
                raise MarketError("rate_limited", "Chart requests are paused for a moment to stay within GeckoTerminal's limit")
            self._gecko_calls.append(now)


def _same(a: str | None, b: str, network: str) -> bool:
    if not a:
        return False
    return a == b if network == "solana" else a.lower() == b.lower()  # Solana addresses are case-sensitive


def _matches(p: dict, chain: str, token: str, network: str) -> bool:
    return (p.get("chainId") == chain and bool(p.get("pairAddress"))
            and (_same((p.get("baseToken") or {}).get("address"), token, network)
                 or _same((p.get("quoteToken") or {}).get("address"), token, network)))


def _num(v) -> float | None:
    """A provider number, or None when it's absent or unreadable. Missing never becomes zero."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pair(p: dict, token: str, network: str) -> dict:
    base, quote = p.get("baseToken") or {}, p.get("quoteToken") or {}
    side = "base" if _same(base.get("address"), token, network) else "quote"
    # DEX Screener's prices, FDV and market cap describe the pool's base token. When the launch token is the quote
    # side they would describe the other token, so they're withheld rather than shown under the wrong name.
    ours = side == "base"
    txns = p.get("txns") or {}
    windows = ("m5", "h1", "h6", "h24")
    return {
        "pair_address": p.get("pairAddress"), "dex_id": p.get("dexId"), "url": p.get("url"),
        "labels": p.get("labels") or [], "token_side": side,
        "base": {"address": base.get("address"), "symbol": base.get("symbol"), "name": base.get("name")},
        "quote": {"address": quote.get("address"), "symbol": quote.get("symbol"), "name": quote.get("name")},
        "price_usd": _num(p.get("priceUsd")) if ours else None,
        "price_native": _num(p.get("priceNative")) if ours else None,
        "price_change": {w: _num((p.get("priceChange") or {}).get(w)) for w in windows} if ours else {},
        "volume_usd": {w: _num((p.get("volume") or {}).get(w)) for w in windows},
        "txns": {w: {"buys": (txns.get(w) or {}).get("buys"), "sells": (txns.get(w) or {}).get("sells")}
                 for w in windows if w in txns},
        "liquidity_usd": _num((p.get("liquidity") or {}).get("usd")),
        "fdv": _num(p.get("fdv")) if ours else None,
        "market_cap": _num(p.get("marketCap")) if ours else None,
        "pair_created_at": p.get("pairCreatedAt"),
    }


_market: Market | None = None


def get_market() -> Market:
    """Shared instance, so every user of this server shares one cache and one provider budget."""
    global _market
    if _market is None:
        _market = Market()
    return _market
