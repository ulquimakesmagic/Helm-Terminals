"""Display-only token market data. Never used by execution, signing or confirmation services.

Providers: Codex's GraphQL data API (keyed; the widest chain coverage, serving tokens DexScreener does
not index), GMGN's OpenAPI (keyed; adds holder counts and the GMGN page) and DexScreener's public API
(keyless, not IP-filtered). A configured Codex key is primary; when it is missing, denied or comes up
empty, the same published shape is served from DexScreener — or from GMGN when no Codex key is set.
Contract: docs.codex.io GraphQL reference (token, listPairsWithMetadataForToken, pairMetadata,
getDetailedTokenStats), GMGNAI/gmgn-skills OpenApiClient, DexScreener tokens/v1 reference. No website
scraping. Candles are rendered by GMGN's hosted embed, never constructed from market snapshots.
Market cap and FDV are provider-reported (DexScreener) or derived from the provider's reported fields
exactly as each provider's own field reference defines them (price × circulating/total supply).
"""

import asyncio
import logging
import math
import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from ..config import get_settings

GMGN_API = "https://openapi.gmgn.ai"
DEXSCREENER_API = "https://api.dexscreener.com"
CODEX_API = "https://graph.codex.io/graphql"
# OpenAPI token commands support more chains than the separately documented embed.
API_CHAIN = {"solana": "sol", "bsc": "bsc", "base": "base", "ethereum": "eth",
             "robinhood": "robinhood", "arc": "arc"}
# Explicit provider IDs; coverage depends on an eligible pool being indexed.
# This does not promise every Helm chain, DEX or launch stage is supported.
DEX_CHAIN = {"solana": "solana", "bsc": "bsc", "base": "base", "ethereum": "ethereum",
             "robinhood": "robinhood", "arc": "arc"}
# Codex networkId per chain (docs.codex.io/networks). Same six networks as the other providers.
CODEX_NETWORK = {"solana": 1399811149, "ethereum": 1, "bsc": 56, "base": 8453,
                 "robinhood": 4663, "arc": 5042}
DEX_WINDOWS = ("m5", "h1", "h6", "h24")  # same windows GMGN reports
# Codex publishes 5m/1h/4h/12h/24h — the 6h window has no equivalent and stays unreported.
CODEX_WINDOWS = {"m5": "min5", "h1": "hour1", "h24": "day1"}
CHART_CHAIN = {"solana": "sol", "bsc": "bsc", "ethereum": "eth"}
CACHE_TTL, STALE_MAX, ERROR_TTL, CACHE_LIMIT = 60, 300, 15, 256
WINDOWS = {"m5": "5m", "h1": "1h", "h6": "6h", "h24": "24h"}
# One batched document: token supplies, pair discovery with liquidity, and token-wide trade counts.
_CODEX_OVERVIEW_QUERY = """query HelmTokenOverview($address: String!, $networkId: Int!) {
  token(input: {address: $address, networkId: $networkId}) {
    address networkId symbol name isScam
    info { circulatingSupply totalSupply }
  }
  listPairsWithMetadataForToken(tokenAddress: $address, networkId: $networkId, limit: 10) {
    results {
      volume liquidity quoteToken
      token { address symbol networkId }
      backingToken { address symbol networkId }
      pair { address id networkId }
      exchange { name }
    }
  }
  getDetailedTokenStats(tokenAddress: $address, networkId: $networkId,
      durations: [min5, hour1, day1], bucketCount: 1) {
    stats_min5 { statsNonCurrency { buys { currentValue } sells { currentValue } } }
    stats_hour1 { statsNonCurrency { buys { currentValue } sells { currentValue } } }
    stats_day1 { statsNonCurrency { buys { currentValue } sells { currentValue } } }
  }
}"""
# Price and the documented price-change windows come from the chosen pair's metadata; the non-liquidity
# token (the launch token) is the price perspective. Price change fields are decimal (0.01 = 1%).
_CODEX_PAIR_QUERY = """query HelmPair($pairId: String!) {
  pairMetadata(pairId: $pairId, useNonLiquidityTokenAsQuoteToken: true) {
    pairAddress networkId liquidity liquidityToken nonLiquidityToken
    priceChange5m priceChange1 priceChange24
    volume5m volume1 volume24
    token0 { address symbol price pooled }
    token1 { address symbol price pooled }
  }
}"""
log = logging.getLogger(__name__)


class MarketError(Exception):
    def __init__(self, kind: str, message: str, *, retry_until: float = 0) -> None:
        super().__init__(message)
        self.kind = kind
        self.retry_until = retry_until

    def details(self) -> dict:
        return {"error_kind": self.kind, "error": str(self),
                "retry_after_s": max(0, math.ceil(self.retry_until - time.monotonic()))}


def valid_address(network: str, token: str) -> bool:
    pattern = r"[1-9A-HJ-NP-Za-km-z]{32,44}" if network == "solana" else r"0x[0-9a-fA-F]{40}"
    return isinstance(token, str) and re.fullmatch(pattern, token) is not None


def _same(a, b: str, network: str) -> bool:
    return isinstance(a, str) and (a == b if network == "solana" else a.lower() == b.lower())


def _num(value, *, signed=False) -> float | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and (signed or result >= 0) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _count(value) -> int | None:
    result = _num(value)
    return int(result) if result is not None and result.is_integer() else None


def _https(url) -> str | None:
    if not isinstance(url, str) or len(url) > 500 or any(c.isspace() for c in url):
        return None
    try:
        p = urlsplit(url)
        return url if p.scheme == "https" and p.hostname and not p.username and not p.password else None
    except ValueError:
        return None


def _text(value) -> str | None:
    return value[:100] if isinstance(value, str) and value else None


def _product(a, b) -> float | None:
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    result = a * b
    return result if math.isfinite(result) else None


def _normalize(raw: dict, network: str, token: str) -> dict:
    """Only published fields. Never derive FDV/market cap or invent a native price."""
    if not _same(raw.get("address"), token, network):
        raise MarketError("invalid_response", "GMGN returned data for a different token.")
    price, pool = raw.get("price") or {}, raw.get("pool") or {}
    if not isinstance(price, dict) or not isinstance(pool, dict):
        raise MarketError("invalid_response", "GMGN returned an unexpected data format.")
    if price.get("address") and not _same(price["address"], token, network):
        raise MarketError("invalid_response", "GMGN returned a price for a different token.")
    current = _num(price.get("price"))
    changes = {}
    for window, name in WINDOWS.items():
        before = _num(price.get(f"price_{name}"))
        changes[window] = _num((current / before - 1) * 100, signed=True) if current is not None and before else None
    main_pool = None
    address = pool.get("pool_address")
    if address:
        if not valid_address(network, address) or (pool.get("base_address") and not _same(pool["base_address"], token, network)):
            raise MarketError("invalid_response", "GMGN returned an inconsistent pool.")
        main_pool = {"address": address, "dex": _text(pool.get("exchange")),
                     "quote_address": pool.get("quote_address") if valid_address(network, pool.get("quote_address")) else None,
                     "quote_symbol": _text(pool.get("quote_symbol")), "liquidity_usd": _num(pool.get("liquidity"))}
    link = _https((raw.get("link") or {}).get("gmgn")) if isinstance(raw.get("link"), dict) else None
    if link and urlsplit(link).hostname not in {"gmgn.ai", "www.gmgn.ai"}:
        link = None
    # Market cap and FDV follow GMGN's own field reference (market cap = price × circulating_supply):
    # derived strictly from reported fields, never extrapolated.
    market_cap = _product(current, _num(raw.get("circulating_supply")))
    fdv = _product(current, _num(raw.get("total_supply")))
    return {"state": "ok" if main_pool else "no_pool", "pool": main_pool,
            "symbol": _text(raw.get("symbol")), "gmgn_url": link, "provider": "GMGN", "metric_scope": "token",
            "metrics": {"price_usd": current, "holder_count": _count(raw.get("holder_count")),
                        "market_cap_usd": market_cap, "fdv_usd": fdv,
                        "liquidity_usd": _num(raw.get("liquidity")),
                        "price_change": changes,
                        "volume_usd": {w: _num(price.get(f"volume_{k}")) for w, k in WINDOWS.items()},
                        "txns": {w: {"buys": _count(price.get(f"buys_{k}")), "sells": _count(price.get(f"sells_{k}")),
                                     "swaps": _count(price.get(f"swaps_{k}"))} for w, k in WINDOWS.items()}}}


def _dex_best_pair(pairs, network: str, token: str) -> dict | None:
    """The token's most liquid pair on this chain, among pairs whose base token is the token asked for."""
    best, best_liq = None, -1.0
    for p in pairs if isinstance(pairs, list) else []:
        if (not isinstance(p, dict) or p.get("chainId") != DEX_CHAIN.get(network)
                or not any(isinstance(p.get(side), dict) and _same(p[side].get("address"), token, network)
                           for side in ("baseToken", "quoteToken"))):
            continue
        liq = _num((p.get("liquidity") or {}).get("usd")) if isinstance(p.get("liquidity"), dict) else None
        if (liq or 0) > best_liq:
            best, best_liq = p, liq or 0
    return best


def _normalize_dex(pair: dict, network: str, token: str) -> dict:
    """DexScreener pair -> the same published shape as GMGN's. DexScreener reports market cap and FDV
    directly; it has no holder count and no GMGN page link."""
    for field in ("baseToken", "quoteToken", "txns", "priceChange", "volume"):
        if pair.get(field) is not None and not isinstance(pair[field], dict):
            raise MarketError("invalid_response", "DexScreener returned an unexpected data format.")
    base_side = _same((pair.get("baseToken") or {}).get("address"), token, network)
    quote_side = _same((pair.get("quoteToken") or {}).get("address"), token, network)
    if pair.get("chainId") != DEX_CHAIN.get(network) or not (base_side or quote_side):
        raise MarketError("invalid_response", "DexScreener returned data for a different token.")
    pool_addr = pair.get("pairAddress")
    main_pool = None
    if pool_addr:
        if not valid_address(network, pool_addr):
            raise MarketError("invalid_response", "DexScreener returned an inconsistent pool.")
        quote = pair.get("quoteToken" if base_side else "baseToken") or {}
        liq = _num(pair["liquidity"].get("usd")) if isinstance(pair.get("liquidity"), dict) else None
        main_pool = {"address": pool_addr, "dex": _text(pair.get("dexId")),
                     "quote_address": quote.get("address") if valid_address(network, quote.get("address") or "") else None,
                     "quote_symbol": _text(quote.get("symbol")), "liquidity_usd": liq,
                     "market_url": f"https://dexscreener.com/{DEX_CHAIN[network]}/{pool_addr}",
                     "orientation": "base" if base_side else "quote"}
    txns = {}
    for w in DEX_WINDOWS:
        t = (pair.get("txns") or {}).get(w) or {}
        if not isinstance(t, dict):
            raise MarketError("invalid_response", "DexScreener returned an unexpected transaction format.")
        buys, sells = _count(t.get("buys")), _count(t.get("sells"))
        total = buys + sells if buys is not None and sells is not None else None
        txns[w] = {"buys": buys, "sells": sells, "swaps": total}
    return {"state": "ok" if main_pool else "no_pool", "pool": main_pool,
            "symbol": _text((pair.get("baseToken" if base_side else "quoteToken") or {}).get("symbol")), "gmgn_url": None,
            "provider": "DexScreener", "metric_scope": "pair",
            "metrics": {"price_usd": _num(pair.get("priceUsd")) if base_side else None,
                        "price_native": _num(pair.get("priceNative")) if base_side else None, "holder_count": None,
                        "market_cap_usd": _num(pair.get("marketCap")) if base_side else None,
                        "fdv_usd": _num(pair.get("fdv")) if base_side else None,
                        "liquidity_usd": main_pool["liquidity_usd"] if main_pool else None,
                        "price_change": {w: _num((pair.get("priceChange") or {}).get(w), signed=True) if base_side else None for w in DEX_WINDOWS},
                        "volume_usd": {w: _num((pair.get("volume") or {}).get(w)) for w in DEX_WINDOWS},
                        "txns": txns}}


def _normalize_codex(raw: dict, network: str, token: str) -> dict:
    """Codex GraphQL -> the same published shape as GMGN's. Market cap and FDV follow the provider's
    own definition (price × circulating/total supply). Codex reports no holder count and there is no
    GMGN page link; the 6h window stays unreported (Codex publishes 5m/1h/4h/12h/24h)."""
    token_data = raw.get("token")
    if not isinstance(token_data, dict) or not _same(token_data.get("address"), token, network):
        raise MarketError("invalid_response", "Codex returned data for a different token.")
    pair, meta, stats = raw.get("pair"), raw.get("meta"), raw.get("stats")
    if pair is not None and not isinstance(pair, dict):
        raise MarketError("invalid_response", "Codex returned an unexpected data format.")
    if meta is not None and not isinstance(meta, dict):
        raise MarketError("invalid_response", "Codex returned an unexpected data format.")
    if pair is not None and not pair.get("address"):
        raise MarketError("invalid_response", "Codex returned an inconsistent pool.")
    main_pool = None
    if isinstance(pair, dict):
        if not valid_address(network, pair["address"]):
            raise MarketError("invalid_response", "Codex returned an inconsistent pool.")
        quote_address = pair.get("quote_address")
        liq = _num(pair.get("liquidity_usd"))
        # Codex defines the pool's USD value as the sum of both sides (pooled × price). Prefer it
        # when every side reports both fields; otherwise keep the pair's reported liquidity.
        sides = meta.get("sides") if isinstance(meta, dict) else None
        if isinstance(sides, list) and sides:
            parts = []
            for side in sides:
                part = _product(_num(side.get("price")) if isinstance(side, dict) else None,
                                _num(side.get("pooled")) if isinstance(side, dict) else None)
                if part is None:
                    parts = None
                    break
                parts.append(part)
            if parts and math.isfinite(sum(parts)):
                liq = sum(parts)
        main_pool = {"address": pair["address"], "dex": _text(pair.get("exchange")),
                     "quote_address": quote_address if valid_address(network, quote_address or "") else None,
                     "quote_symbol": _text(pair.get("quote_symbol")), "liquidity_usd": liq}
    current = _num(meta.get("price")) if isinstance(meta, dict) else None
    # priceChange* fields are decimal fractions (0.01 = 1%); volume fields are USD per window.
    changes = {w: None for w in DEX_WINDOWS}
    volumes = {w: None for w in DEX_WINDOWS}
    if isinstance(meta, dict):
        raw_changes = meta.get("price_change") if isinstance(meta.get("price_change"), dict) else {}
        raw_volumes = meta.get("volume") if isinstance(meta.get("volume"), dict) else {}
        for w in DEX_WINDOWS:
            change = _num(raw_changes.get(w), signed=True)
            changes[w] = change * 100 if change is not None else None
            volumes[w] = _num(raw_volumes.get(w))
    txns = {w: {"buys": None, "sells": None, "swaps": None} for w in DEX_WINDOWS}
    if isinstance(stats, dict):
        for w in DEX_WINDOWS:
            entry = stats.get(w)
            if not isinstance(entry, dict):
                continue
            def count_of(key, entry=entry):
                value = entry.get(key)  # NumberMetrics: {change, currentValue, previousValue, buckets}
                return _count(value.get("currentValue")) if isinstance(value, dict) else _count(value)
            buys, sells = count_of("buys"), count_of("sells")
            txns[w] = {"buys": buys, "sells": sells,
                       "swaps": buys + sells if buys is not None and sells is not None else None}
    # Same derivation as the GMGN path: strictly from reported fields, never extrapolated.
    market_cap = _product(current, _num(token_data.get("circulating_supply")))
    fdv = _product(current, _num(token_data.get("total_supply")))
    return {"state": "ok" if main_pool else "no_pool", "pool": main_pool,
            "symbol": _text(token_data.get("symbol")), "gmgn_url": None, "provider": "Codex", "metric_scope": "token",
            "metrics": {"price_usd": current, "holder_count": None,
                        "market_cap_usd": market_cap, "fdv_usd": fdv,
                        "liquidity_usd": main_pool["liquidity_usd"] if main_pool else None,
                        "price_change": changes,
                        "volume_usd": volumes, "txns": txns}}


@dataclass
class _Entry:
    at: float
    value: dict | None
    error: MarketError | None = None
    retry_until: float = 0


@dataclass
class Market:
    client: httpx.AsyncClient | None = None
    api_key: str | None = field(default=None, repr=False)
    codex_key: str | None = field(default=None, repr=False)
    _cache: OrderedDict = field(default_factory=OrderedDict)
    _inflight: dict = field(default_factory=dict)
    _calls: deque = field(default_factory=deque)
    _codex_calls: deque = field(default_factory=deque)
    _backoff_until: float = 0
    _backoff_error: MarketError | None = None
    _codex_backoff_until: float = 0
    _codex_backoff_error: MarketError | None = None

    def __post_init__(self):
        # GMGN documents IPv4 only. Fixed origin and no redirects protect the server credential.
        self.client = self.client or httpx.AsyncClient(timeout=12, follow_redirects=False,
            transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"))
        if self.api_key is None:
            self.api_key = get_settings().gmgn_api_key.strip()
        if self.codex_key is None:
            self.codex_key = get_settings().codex_api_key.strip()

    def _remember(self, key, entry):
        self._cache[key] = entry
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_LIMIT:
            self._cache.popitem(last=False)

    def _answer(self, entry, stale=False):
        return {**entry.value, "stale": stale, "age_s": round(time.monotonic() - entry.at),
                **(entry.error.details() if stale and entry.error else {})}

    def _pause(self, error: MarketError, delay: float) -> MarketError:
        # Preserve the cause for every token, including cached/stale responses.
        error.retry_until = time.monotonic() + delay
        self._backoff_until, self._backoff_error = error.retry_until, error
        return error

    def _diagnostic(self, kind: str, response=None, envelope=None):
        # Never log provider messages, URLs, headers, tokens or exception text.
        code = (envelope or {}).get("code")
        code_text = str(code)
        safe_code = code_text if re.fullmatch(r"-?\d{1,6}", code_text) and code_text != self.api_key else "unreported"
        log.warning("gmgn_market_failure category=%s http_status=%s provider_code=%s response_format=%s",
                    kind, response.status_code if response is not None else "none", safe_code,
                    "json" if envelope is not None else "non_json")

    async def snapshot(self, network: str, token: str) -> dict:
        if network not in API_CHAIN:
            return {"state": "unsupported", "pool": None, "metrics": None}
        if not valid_address(network, token):
            raise MarketError("invalid_token", "The launch token address is not valid for this chain.")
        # DexScreener needs no configuration; only chains it doesn't index require a key.
        if not self.codex_key and not self.api_key and network not in DEX_CHAIN:
            return {"state": "not_configured", "pool": None, "metrics": None}
        key = (network, token if network == "solana" else token.lower())
        entry, now = self._cache.get(key), time.monotonic()
        if entry:
            self._cache.move_to_end(key)
            if entry.error and now < entry.retry_until:
                if entry.value and now - entry.at < STALE_MAX:
                    return self._answer(entry, stale=True)
                raise entry.error
            if not entry.error and now - entry.at < CACHE_TTL:
                return self._answer(entry)
        if key not in self._inflight:
            if len(self._inflight) >= 32:
                raise MarketError("busy", "Market data requests are busy. Try again shortly.",
                                  retry_until=now + ERROR_TTL)
            task = asyncio.create_task(self._load(key, network, token))
            self._inflight[key] = task
            def done(t):
                self._inflight.pop(key, None)
                if not t.cancelled():
                    t.exception()  # consume errors even if every waiting HTTP request was cancelled
            task.add_done_callback(done)
        return await asyncio.shield(self._inflight[key])

    async def _load(self, key, network, token):
        try:
            raw, provider = await self._fetch(network, token)
            if provider == "GMGN":
                if raw is None or raw == {}:
                    result = {"state": "no_pool", "pool": None, "metrics": None, "provider": provider}
                elif not isinstance(raw, dict):
                    raise MarketError("invalid_response", "GMGN returned an unexpected data format.")
                else:
                    result = _normalize(raw, network, token)
            elif provider == "Codex":
                if raw in (None, {}):
                    result = {"state": "no_pool", "pool": None, "metrics": None, "provider": provider}
                elif not isinstance(raw, dict):
                    raise MarketError("invalid_response", "Codex returned an unexpected data format.")
                else:
                    result = _normalize_codex(raw, network, token)
            else:
                if raw in (None, {}):
                    result = {"state": "no_pool", "pool": None, "metrics": None, "provider": "DexScreener"}
                elif not isinstance(raw, dict):
                    raise MarketError("invalid_response", "DexScreener returned an unexpected data format.")
                else:
                    candidates = raw.pop("_eligible_pairs", [raw.copy()])
                    normalized = [_normalize_dex(p, network, token) for p in candidates]
                    result = {**normalized[0], "pairs": [{"pool": p["pool"], "metrics": p["metrics"]} for p in normalized]}
            entry = _Entry(time.monotonic(), {**result, "fetched_at": time.time()})
            log.info("market_fetch provider=%s network=%s outcome=%s", provider, network, result.get("state"))
        except MarketError as exc:
            old = self._cache.get(key)
            if not exc.retry_until:
                exc.retry_until = time.monotonic() + ERROR_TTL
            entry = _Entry(old.at if old else time.monotonic(), old.value if old else None,
                           exc, exc.retry_until)
            self._remember(key, entry)
            log.info("market_fetch network=%s outcome=error kind=%s", network, exc.kind)
            if entry.value and time.monotonic() - entry.at < STALE_MAX:
                return self._answer(entry, stale=True)
            raise
        self._remember(key, entry)
        return self._answer(entry)

    async def _fetch(self, network, token):
        """(raw provider payload, provider name). A configured Codex key is primary — its coverage
        includes tokens DexScreener does not index — and on a Codex failure or empty answer the same
        data comes from DexScreener. Without a Codex key, a configured GMGN key stays primary (holder
        counts and the GMGN page) with the DexScreener denial fallback; with neither, DexScreener."""
        if self.codex_key and network in CODEX_NETWORK:
            codex_error = None
            try:
                raw = await self._get_codex(network, token)
            except MarketError as e:
                codex_error = e
            else:
                if raw:
                    return raw, "Codex"
            if network not in DEX_CHAIN:  # no keyless net exists for this chain
                if codex_error:
                    raise codex_error
                return {}, "Codex"
            try:
                return await self._get_dex(network, token), "DexScreener"
            except MarketError:
                if codex_error:
                    raise codex_error from None  # the Codex failure is the root cause; keep its guidance
                return {}, "Codex"  # both providers came up empty: a definitive no-pool answer
        if self.api_key:
            try:
                return await self._get(API_CHAIN[network], token), "GMGN"
            except MarketError as e:
                if e.kind != "access_denied" or network not in DEX_CHAIN:
                    raise
                try:
                    return await self._get_dex(network, token), "DexScreener"
                except MarketError:
                    raise e from None  # the GMGN denial is the root cause; keep its kind and guidance
        return await self._get_dex(network, token), "DexScreener"

    async def _get_dex(self, network, token):
        # No GMGN backoff here: the fallback must run even while GMGN is cooling down.
        now = time.monotonic()
        while self._calls and now - self._calls[0] >= 60:
            self._calls.popleft()
        if len(self._calls) >= get_settings().gmgn_requests_per_minute:
            raise MarketError("request_budget", "Helm's market-data request budget is reached. Updates will resume shortly.",
                              retry_until=self._calls[0] + 60)
        self._calls.append(now)
        try:
            response = await self.client.get(f"{DEXSCREENER_API}/tokens/v1/{DEX_CHAIN[network]}/{token}",
                headers={"Accept": "application/json", "User-Agent": "HelmTerminals/1.0"})
        except httpx.HTTPError:
            raise MarketError("unavailable", "DexScreener could not be reached. Please try again later.") from None
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code == 429:
            delay = max(60, _num(response.headers.get("retry-after")) or 0)
            raise MarketError("rate_limited", "DexScreener has paused requests due to its rate limit. Updates will resume after the cooldown.",
                              retry_until=time.monotonic() + delay)
        if response.status_code >= 300 or not isinstance(body, list):
            # DexScreener is keyless, so failures here are transient; no secrets can be echoed.
            raise MarketError("unavailable", "DexScreener did not return market data. Please try again later.")
        eligible = []
        seen = set()
        for pair in body:
            if _dex_best_pair([pair], network, token) and pair.get("pairAddress") not in seen:
                # Validate before sorting so malformed liquidity/addresses cannot poison the selected pool.
                _normalize_dex(pair, network, token)
                eligible.append(pair)
                seen.add(pair.get("pairAddress"))
        eligible.sort(key=lambda p: _num(p["liquidity"].get("usd")) or 0 if isinstance(p.get("liquidity"), dict) else 0, reverse=True)
        return {**eligible[0], "_eligible_pairs": eligible} if eligible else {}

    def _codex_pause(self, error: MarketError, delay: float) -> MarketError:
        # Separate cooldown from GMGN's: a Codex outage must not block the GMGN path.
        error.retry_until = time.monotonic() + delay
        self._codex_backoff_until, self._codex_backoff_error = error.retry_until, error
        return error

    def _codex_diagnostic(self, kind: str, response=None, envelope=None):
        # Never log provider messages, URLs, headers, tokens or exception text.
        errors = (envelope or {}).get("errors")
        log.warning("codex_market_failure category=%s http_status=%s graphql_errors=%s response_format=%s",
                    kind, response.status_code if response is not None else "none",
                    len(errors) if isinstance(errors, list) else "none",
                    "json" if envelope is not None else "non_json")

    async def _codex_post(self, query: str, variables: dict) -> dict:
        now = time.monotonic()
        if self._codex_backoff_error and now < self._codex_backoff_until:
            raise self._codex_backoff_error
        self._codex_backoff_error = None
        while self._codex_calls and now - self._codex_calls[0] >= 60:
            self._codex_calls.popleft()
        if len(self._codex_calls) >= get_settings().codex_requests_per_minute:
            raise MarketError("request_budget", "Helm's market-data request budget is reached. Updates will resume shortly.",
                              retry_until=self._codex_calls[0] + 60)
        self._codex_calls.append(now)
        try:
            response = await self.client.post(CODEX_API, json={"query": query, "variables": variables},
                headers={"Authorization": self.codex_key, "Content-Type": "application/json",
                         "Accept": "application/json", "User-Agent": "HelmTerminals/1.0"})
        except httpx.HTTPError:
            self._codex_diagnostic("unavailable")
            raise MarketError("unavailable", "Codex could not be reached. Please try again later.") from None
        try:
            body = response.json()
        except ValueError:
            body = None
        envelope = body if isinstance(body, dict) else {}
        if response.status_code == 429:
            delay = max(60, _num(response.headers.get("retry-after")) or 0)
            self._codex_diagnostic("rate_limited", response, envelope)
            raise self._codex_pause(MarketError("rate_limited", "Codex has paused requests due to its rate limit. Updates will resume after the cooldown."), delay)
        if response.status_code in (401, 403):
            self._codex_diagnostic("access_denied", response, envelope)
            raise self._codex_pause(MarketError("access_denied", "Codex denied market-data access. The operator needs to check the backend API key, its permissions, plan and any server IP restrictions."), 60)
        if response.status_code >= 300 or not isinstance(body, dict) or not isinstance(envelope.get("data"), dict):
            # Provider bodies can echo secrets or internal addresses; never return them to a user.
            self._codex_diagnostic("unavailable", response, envelope)
            raise MarketError("unavailable", "Codex did not return market data. Please try again later.")
        return envelope["data"]

    def _codex_best_result(self, results, network: str, token: str):
        """The token's most liquid pair on this chain, among results that consistently describe the token."""
        network_id = CODEX_NETWORK[network]
        best, best_liq = None, -1.0
        for r in results if isinstance(results, list) else []:
            if not isinstance(r, dict):
                continue
            entry, pair = r.get("token"), r.get("pair")
            if (not isinstance(entry, dict) or not isinstance(pair, dict)
                    or pair.get("networkId") != network_id
                    or not _same(entry.get("address"), token, network)
                    or not valid_address(network, pair.get("address") or "")):
                continue
            liq = _num(r.get("liquidity"))
            if (liq or 0) > best_liq:
                best, best_liq = r, liq or 0
        return best

    async def _get_codex(self, network, token):
        """Two POSTs: a batched overview (token supplies, pair discovery, token-wide trade counts),
        then the chosen pair's metadata for price and the documented price-change windows."""
        network_id = CODEX_NETWORK[network]
        data = await self._codex_post(_CODEX_OVERVIEW_QUERY, {"address": token, "networkId": network_id})
        token_data, listing = data.get("token"), data.get("listPairsWithMetadataForToken")
        stats = data.get("getDetailedTokenStats")
        if not isinstance(token_data, dict) and not (isinstance(listing, dict) and isinstance(listing.get("results"), list)):
            return {}  # Codex has neither metadata nor pairs indexed for this token
        if not isinstance(token_data, dict):
            raise MarketError("invalid_response", "Codex returned an unexpected data format.")
        if not _same(token_data.get("address"), token, network):
            raise MarketError("invalid_response", "Codex returned data for a different token.")
        info = token_data.get("info") if isinstance(token_data.get("info"), dict) else {}
        raw = {"token": {"address": token, "symbol": token_data.get("symbol"),
                         "circulating_supply": info.get("circulatingSupply"), "total_supply": info.get("totalSupply")},
               "pair": None, "meta": None, "stats": None}
        if isinstance(stats, dict):
            per_window = {}
            for w, code in CODEX_WINDOWS.items():
                entry = stats.get(f"stats_{code}")
                per_window[w] = entry.get("statsNonCurrency") if isinstance(entry, dict) else None
            raw["stats"] = per_window
        chosen = self._codex_best_result(listing.get("results") if isinstance(listing, dict) else None, network, token)
        if chosen is None:
            return raw  # token indexed, but no eligible pool: a definitive no-pool answer
        pair = chosen["pair"]
        pair_id = f"{pair['address']}:{network_id}"
        pair_data = await self._codex_post(_CODEX_PAIR_QUERY, {"pairId": pair_id})
        meta = pair_data.get("pairMetadata")
        if not isinstance(meta, dict):
            raise MarketError("invalid_response", "Codex returned an unexpected data format.")
        if not _same(meta.get("pairAddress"), pair["address"], network) or meta.get("networkId") != network_id:
            raise MarketError("invalid_response", "Codex returned metadata for a different pair.")
        sides = [s for s in (meta.get("token0"), meta.get("token1")) if isinstance(s, dict)]
        ours = [s for s in sides if _same(s.get("address"), token, network)]
        if not ours:
            raise MarketError("invalid_response", "Codex returned metadata for a different token.")
        backing = next((s for s in sides if not _same(s.get("address"), token, network)), None)
        exchange = chosen.get("exchange") if isinstance(chosen.get("exchange"), dict) else {}
        raw["pair"] = {"address": pair["address"], "exchange": exchange.get("name"),
                       "liquidity_usd": _num(chosen.get("liquidity")),
                       "quote_address": backing.get("address") if isinstance(backing, dict) else None,
                       "quote_symbol": backing.get("symbol") if isinstance(backing, dict) else None}
        raw["meta"] = {"price": ours[0].get("price"),
                       "price_change": {"m5": meta.get("priceChange5m"), "h1": meta.get("priceChange1"), "h24": meta.get("priceChange24")},
                       "volume": {"m5": meta.get("volume5m"), "h1": meta.get("volume1"), "h24": meta.get("volume24")},
                       "sides": [{"price": s.get("price"), "pooled": s.get("pooled")}
                                 for s in (meta.get("token0"), meta.get("token1")) if isinstance(s, dict)]}
        return raw

    async def _get(self, chain, token):
        now = time.monotonic()
        if self._backoff_error and now < self._backoff_until:
            raise self._backoff_error
        self._backoff_error = None
        while self._calls and now - self._calls[0] >= 60:
            self._calls.popleft()
        if len(self._calls) >= get_settings().gmgn_requests_per_minute:
            raise MarketError("request_budget", "Helm's GMGN request budget is reached. Updates will resume shortly.",
                              retry_until=self._calls[0] + 60)
        self._calls.append(now)
        try:
            response = await self.client.get(f"{GMGN_API}/v1/token/info",
                params={"chain": chain, "address": token, "timestamp": int(time.time()), "client_id": str(uuid4())},
                headers={"X-APIKEY": self.api_key, "Accept": "application/json", "User-Agent": "HelmTerminals/1.0"})
        except httpx.HTTPError:
            self._diagnostic("unavailable")
            raise MarketError("unavailable", "GMGN could not be reached. Please try again later.") from None
        try:
            body = response.json()
        except ValueError:
            body = None
        envelope = body if isinstance(body, dict) else {}
        if response.status_code == 429 or str(envelope.get("code")) == "429" or str(envelope.get("error")) in {"RATE_LIMIT_EXCEEDED", "RATE_LIMIT_BANNED"}:
            reset = max(_num(response.headers.get("x-ratelimit-reset")) or 0, _num(envelope.get("reset_at")) or 0)
            delay = max(60, reset - time.time(), _num(response.headers.get("retry-after")) or 0)
            self._diagnostic("rate_limited", response, body if isinstance(body, dict) else None)
            raise self._pause(MarketError("rate_limited", "GMGN has paused requests due to its rate limit. Updates will resume after the cooldown."), delay)
        if response.status_code in (401, 403) or str(envelope.get("code")) in {"401", "403"}:
            self._diagnostic("access_denied", response, body if isinstance(body, dict) else None)
            raise self._pause(MarketError("access_denied", "GMGN denied market-data access. The operator needs to check the backend API key, read permissions, plan and any server IP restrictions."), 60)
        if response.status_code >= 300 or not isinstance(body, dict) or body.get("code") != 0 or "data" not in body:
            # Provider bodies can echo secrets or internal addresses; never return them to a user.
            self._diagnostic("unavailable", response, body if isinstance(body, dict) else None)
            raise MarketError("unavailable", "GMGN did not return market data. Please try again later.")
        return body["data"]


_market: Market | None = None


def get_market() -> Market:
    global _market
    if _market is None:
        _market = Market()
    return _market
