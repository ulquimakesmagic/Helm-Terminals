"""Display-only token market data from Codex (codex.io), Helm's single third-party market-data source.

Never used by execution, signing, quotes or confirmation. Wallet balances come from each chain's RPC, executed
trades from receipts, and execution prices from the launchpad or router that builds the transaction.

Contract (docs.codex.io GraphQL reference, checked 6 Oct 2026):
- `filterTokens(tokens: ["address:networkId"])` — price, market cap (`circulatingMarketCap`), FDV (`marketCap`,
  which Codex defines as price × total supply), liquidity, holders and per-window volume, change and trade counts
  (5m / 1h / 4h / 12h / 24h).
- `listPairsWithMetadataForToken` — the token's pools with liquidity, exchange and quote token.
- `getBars(symbol, from, to, resolution, countback, symbolType)` — OHLCV candles. Token-level bars aggregate
  the token's pools; pool-level bars chart one pool. At most 1,500 bars per request.
- Network IDs: Solana 1399811149, Ethereum 1, BNB Chain 56, Base 8453, Robinhood 4663, Arc 5042, TRON 728126428.
  NEAR and TON are not Codex networks, so Helm shows market data as unsupported there.

The API key stays on the server. Requests are cached, de-duplicated while in flight, budgeted per minute, retried
at most twice on transport errors and 5xx, and paused after a 429. Nothing is substituted: a value Codex does not
report is null, a token Codex has not indexed is `not_indexed`, and charts are drawn only from Codex's bars.
"""

import asyncio
import logging
import math
import random
import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from ..config import get_settings

CODEX_API = "https://graph.codex.io/graphql"
CODEX_NETWORK = {"solana": 1399811149, "ethereum": 1, "bsc": 56, "base": 8453,
                 "robinhood": 4663, "arc": 5042, "tron": 728126428}
# Helm window key -> Codex field suffix. Codex publishes these five windows; nothing else is shown.
WINDOWS = {"m5": "5m", "h1": "1", "h4": "4", "h12": "12", "h24": "24"}
# Candle resolution -> seconds per bar (Codex resolution strings).
RESOLUTIONS = {"1": 60, "5": 300, "15": 900, "60": 3600, "240": 14_400, "1D": 86_400}
MAX_BARS = 300
CACHE_TTL, STALE_MAX, ERROR_TTL, CACHE_LIMIT = 30, 300, 15, 512
UNKNOWN_CAP = 9_223_372_036_854_775_806  # Codex's "could not be computed" sentinel for market cap fields
log = logging.getLogger(__name__)

_SNAPSHOT_QUERY = """query HelmTokenMarket($ids: [String], $address: String!, $networkId: Int!) {
  filterTokens(tokens: $ids, limit: 1) {
    results {
      token { address networkId symbol name }
      priceUSD liquidity marketCap circulatingMarketCap holders isScam createdAt lastTransaction
      change5m change1 change4 change12 change24
      volume5m volume1 volume4 volume12 volume24
      buyCount5m buyCount1 buyCount4 buyCount12 buyCount24
      sellCount5m sellCount1 sellCount4 sellCount12 sellCount24
      txnCount5m txnCount1 txnCount4 txnCount12 txnCount24
    }
  }
  listPairsWithMetadataForToken(tokenAddress: $address, networkId: $networkId, limit: 10) {
    results {
      volume liquidity quoteToken
      token { address symbol networkId }
      backingToken { address symbol networkId }
      pair { address networkId }
      exchange { name }
    }
  }
}"""

_BARS_QUERY = """query HelmBars($symbol: String!, $from: Int!, $to: Int!, $resolution: String!, $countback: Int,
    $symbolType: SymbolType, $quoteToken: QuoteToken) {
  getBars(symbol: $symbol, from: $from, to: $to, resolution: $resolution, countback: $countback,
          currencyCode: "USD", removeLeadingNullValues: true, removeEmptyBars: true,
          symbolType: $symbolType, quoteToken: $quoteToken) {
    t o h l c volume s
  }
}"""

_PRICES_QUERY = """query HelmPrices($inputs: [GetPriceInput]) {
  getTokenPrices(inputs: $inputs) { address networkId priceUsd timestamp }
}"""


class MarketError(Exception):
    def __init__(self, kind: str, message: str, *, retry_until: float = 0) -> None:
        super().__init__(message)
        self.kind = kind
        self.retry_until = retry_until

    def details(self) -> dict:
        return {"error_kind": self.kind, "error": str(self),
                "retry_after_s": max(0, math.ceil(self.retry_until - time.monotonic()))}


def valid_address(network: str, token: str) -> bool:
    if not isinstance(token, str):
        return False
    if network == "solana":
        pattern = r"[1-9A-HJ-NP-Za-km-z]{32,44}"
    elif network == "tron":
        pattern = r"T[1-9A-HJ-NP-Za-km-z]{33}"
    else:
        pattern = r"0x[0-9a-fA-F]{40}"
    return re.fullmatch(pattern, token) is not None


def _key(network: str, address: str) -> str:
    return address if network in ("solana", "tron") else address.lower()


def _same(a, b: str, network: str) -> bool:
    return isinstance(a, str) and _key(network, a) == _key(network, b)


def _num(value, *, signed=False) -> float | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and (signed or result >= 0) else None


def _count(value) -> int | None:
    result = _num(value)
    return int(result) if result is not None and result.is_integer() else None


def _cap(value) -> float | None:
    result = _num(value)
    return None if result is None or result >= UNKNOWN_CAP or result == 0 else result


def _text(value, limit=100) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def _https(url) -> str | None:
    """Only plain https links without credentials (kept for provider links and their tests)."""
    if not isinstance(url, str) or len(url) > 500 or any(c.isspace() for c in url):
        return None
    try:
        p = urlsplit(url)
        return url if p.scheme == "https" and p.hostname and not p.username and not p.password else None
    except ValueError:
        return None


def _iso(epoch) -> str | None:
    value = _count(epoch)
    if value is None or value <= 0:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))


def normalize_snapshot(data: dict, network: str, token: str) -> dict:
    """Codex response -> Helm's published market shape. Only reported fields; nothing derived or filled."""
    network_id = CODEX_NETWORK[network]
    listing = data.get("filterTokens")
    results = listing.get("results") if isinstance(listing, dict) else None
    row = None
    for candidate in results if isinstance(results, list) else []:
        info = candidate.get("token") if isinstance(candidate, dict) else None
        if isinstance(info, dict) and _same(info.get("address"), token, network) and info.get("networkId") == network_id:
            row = candidate
            break
    pairs_raw = (data.get("listPairsWithMetadataForToken") or {}).get("results")
    pairs = []
    for p in pairs_raw if isinstance(pairs_raw, list) else []:
        if not isinstance(p, dict):
            continue
        pair, mine, backing = p.get("pair"), p.get("token"), p.get("backingToken")
        if (not isinstance(pair, dict) or pair.get("networkId") != network_id
                or not valid_address(network, pair.get("address") or "")):
            continue
        ours = next((t for t in (mine, backing) if isinstance(t, dict) and _same(t.get("address"), token, network)), None)
        if ours is None:
            continue
        quote = backing if ours is mine else mine
        pairs.append({
            "address": pair["address"], "dex": _text((p.get("exchange") or {}).get("name")) if isinstance(p.get("exchange"), dict) else None,
            "quote_symbol": _text((quote or {}).get("symbol")) if isinstance(quote, dict) else None,
            "quote_address": (quote or {}).get("address") if isinstance(quote, dict) and valid_address(network, (quote or {}).get("address") or "") else None,
            "liquidity_usd": _num(p.get("liquidity")), "volume_usd_24h": _num(p.get("volume")),
            "quote_side": p.get("quoteToken") if p.get("quoteToken") in ("token0", "token1") else None,
        })
    pairs.sort(key=lambda x: x["liquidity_usd"] or 0, reverse=True)
    if row is None and not pairs:
        return {"state": "not_indexed", "pool": None, "pairs": [], "metrics": None}
    metrics = None
    symbol = name = None
    flags = {}
    if row is not None:
        info = row["token"]
        symbol, name = _text(info.get("symbol"), 20), _text(info.get("name"), 60)
        flags = {"is_scam": bool(row.get("isScam"))}
        change = {}
        for w, suffix in WINDOWS.items():
            raw = _num(row.get(f"change{suffix}"), signed=True)
            change[w] = raw * 100 if raw is not None else None  # Codex reports a fraction (0.05 = 5%)
        txns = {}
        for w, suffix in WINDOWS.items():
            buys, sells = _count(row.get(f"buyCount{suffix}")), _count(row.get(f"sellCount{suffix}"))
            total = _count(row.get(f"txnCount{suffix}"))
            txns[w] = {"buys": buys, "sells": sells, "swaps": total}
        metrics = {
            "price_usd": _num(row.get("priceUSD")),
            "market_cap_usd": _cap(row.get("circulatingMarketCap")),  # circulating supply × price
            "fdv_usd": _cap(row.get("marketCap")),  # Codex: total supply × price (fully diluted)
            "liquidity_usd": _num(row.get("liquidity")),
            "holder_count": _count(row.get("holders")),
            "created_at": _iso(row.get("createdAt")),
            "last_trade_at": _iso(row.get("lastTransaction")),
            "price_change": change,
            "volume_usd": {w: _num(row.get(f"volume{suffix}")) for w, suffix in WINDOWS.items()},
            "txns": txns,
        }
    return {"state": "ok" if pairs else "no_pool", "pool": pairs[0] if pairs else None, "pairs": pairs,
            "symbol": symbol, "name": name, "flags": flags, "metric_scope": "token", "metrics": metrics}


def normalize_bars(data: dict, resolution: str) -> list[dict]:
    """getBars -> validated candles. A malformed bar is dropped, never repaired or invented."""
    bars = data.get("getBars")
    if not isinstance(bars, dict):
        return []
    columns = [bars.get(k) for k in ("t", "o", "h", "l", "c")]
    if not all(isinstance(c, list) for c in columns):
        return []
    volume = bars.get("volume") if isinstance(bars.get("volume"), list) else []
    n = min(len(c) for c in columns)
    out, last_t = [], None
    for i in range(n):
        t = _count(columns[0][i])
        o, h, l, c = (_num(columns[k][i]) for k in range(1, 5))
        if t is None or None in (o, h, l, c) or h < max(o, c, l) or l > min(o, c):
            continue
        if last_t is not None and t <= last_t:
            continue
        last_t = t
        out.append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": _num(volume[i]) if i < len(volume) else None})
    return out


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
    _cache: OrderedDict = field(default_factory=OrderedDict)
    _inflight: dict = field(default_factory=dict)
    _calls: deque = field(default_factory=deque)
    _backoff_until: float = 0
    _backoff_error: MarketError | None = None
    retry_delays: tuple = (0.4, 1.2)

    def __post_init__(self):
        # Fixed origin and no redirects protect the server credential.
        self.client = self.client or httpx.AsyncClient(timeout=12, follow_redirects=False)
        if self.api_key is None:
            self.api_key = get_settings().codex_api_key.strip()

    # --- transport -----------------------------------------------------------------------------------

    def _diagnostic(self, kind: str, status=None, errors=None):
        # Never log provider messages, URLs, headers, keys or token addresses.
        log.warning("codex_market_failure category=%s http_status=%s graphql_errors=%s", kind,
                    status if status is not None else "none", errors if errors is not None else "none")

    def _pause(self, error: MarketError, delay: float) -> MarketError:
        error.retry_until = time.monotonic() + delay
        self._backoff_until, self._backoff_error = error.retry_until, error
        return error

    async def query(self, document: str, variables: dict) -> dict:
        now = time.monotonic()
        if self._backoff_error and now < self._backoff_until:
            raise self._backoff_error
        self._backoff_error = None
        for attempt in range(len(self.retry_delays) + 1):
            now = time.monotonic()
            while self._calls and now - self._calls[0] >= 60:
                self._calls.popleft()
            if len(self._calls) >= get_settings().codex_requests_per_minute:
                raise MarketError("request_budget", "Helm's market-data request budget is reached. Updates resume shortly.",
                                  retry_until=self._calls[0] + 60)
            self._calls.append(now)
            try:
                response = await self.client.post(CODEX_API, json={"query": document, "variables": variables},
                    headers={"Authorization": self.api_key, "Content-Type": "application/json",
                             "Accept": "application/json", "User-Agent": "HelmTerminals/1.0"})
            except httpx.HTTPError:
                if attempt < len(self.retry_delays):
                    await asyncio.sleep(self.retry_delays[attempt] * (0.75 + random.random() / 2))
                    continue
                self._diagnostic("unavailable")
                raise MarketError("unavailable", "Codex could not be reached. Try again shortly.") from None
            if response.status_code >= 500 and attempt < len(self.retry_delays):
                await asyncio.sleep(self.retry_delays[attempt] * (0.75 + random.random() / 2))
                continue
            break
        try:
            body = response.json()
        except ValueError:
            body = None
        envelope = body if isinstance(body, dict) else {}
        errors = envelope.get("errors") if isinstance(envelope.get("errors"), list) else None
        if response.status_code == 429:
            delay = max(30, _num(response.headers.get("retry-after")) or 0)
            self._diagnostic("rate_limited", 429)
            raise self._pause(MarketError("rate_limited", "Codex paused requests for its rate limit. Updates resume after the cooldown."), delay)
        if response.status_code in (401, 403):
            self._diagnostic("access_denied", response.status_code)
            raise self._pause(MarketError("access_denied", "Codex refused Helm's market-data key. The operator needs to check CODEX_API_KEY and its plan."), 60)
        data = envelope.get("data")
        if response.status_code >= 300 or not isinstance(data, dict):
            self._diagnostic("unavailable", response.status_code, len(errors) if errors else None)
            raise MarketError("unavailable", "Codex did not return market data. Try again shortly.")
        if errors:
            # Partial data with errors: keep what was returned; absent fields stay null.
            self._diagnostic("partial", response.status_code, len(errors))
        return data

    # --- cache ---------------------------------------------------------------------------------------

    def _remember(self, key, entry):
        self._cache[key] = entry
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_LIMIT:
            self._cache.popitem(last=False)

    def _answer(self, entry, stale=False):
        return {**entry.value, "stale": stale, "age_s": round(time.monotonic() - entry.at),
                **(entry.error.details() if stale and entry.error else {})}

    async def _cached(self, key, ttl: float, loader) -> dict:
        entry, now = self._cache.get(key), time.monotonic()
        if entry:
            self._cache.move_to_end(key)
            if entry.error and now < entry.retry_until:
                if entry.value and now - entry.at < STALE_MAX:
                    return self._answer(entry, stale=True)
                raise entry.error
            if not entry.error and now - entry.at < ttl:
                return self._answer(entry)
        if key not in self._inflight:
            if len(self._inflight) >= 32:
                raise MarketError("busy", "Market data requests are busy. Try again shortly.", retry_until=now + ERROR_TTL)
            task = asyncio.create_task(self._load(key, loader))
            self._inflight[key] = task

            def done(t, key=key):
                self._inflight.pop(key, None)
                if not t.cancelled():
                    t.exception()  # consumed even if every waiting request was cancelled
            task.add_done_callback(done)
        return await asyncio.shield(self._inflight[key])

    async def _load(self, key, loader) -> dict:
        try:
            value = await loader()
        except MarketError as exc:
            old = self._cache.get(key)
            if not exc.retry_until:
                exc.retry_until = time.monotonic() + ERROR_TTL
            entry = _Entry(old.at if old else time.monotonic(), old.value if old else None, exc, exc.retry_until)
            self._remember(key, entry)
            if entry.value and time.monotonic() - entry.at < STALE_MAX:
                return self._answer(entry, stale=True)
            raise
        entry = _Entry(time.monotonic(), {**value, "provider": "Codex", "fetched_at": time.time()})
        self._remember(key, entry)
        return self._answer(entry)

    # --- public API ----------------------------------------------------------------------------------

    def availability(self, network: str) -> str | None:
        """None when Codex can serve this network, else the state to show."""
        if network not in CODEX_NETWORK:
            return "unsupported"
        if not self.api_key:
            return "not_configured"
        return None

    async def snapshot(self, network: str, token: str) -> dict:
        if (state := self.availability(network)):
            return {"state": state, "pool": None, "pairs": [], "metrics": None, "provider": "Codex"}
        if not valid_address(network, token):
            raise MarketError("invalid_token", "The token address is not valid for this chain.")
        network_id = CODEX_NETWORK[network]

        async def load():
            data = await self.query(_SNAPSHOT_QUERY, {"ids": [f"{token}:{network_id}"], "address": token,
                                                       "networkId": network_id})
            result = normalize_snapshot(data, network, token)
            log.info("market_fetch network=%s outcome=%s", network, result["state"])
            return result

        return await self._cached(("snapshot", network, _key(network, token)), CACHE_TTL, load)

    async def candles(self, network: str, token: str, resolution: str, pool: str | None = None,
                      quote_side: str | None = None) -> dict:
        if (state := self.availability(network)):
            return {"state": state, "bars": [], "provider": "Codex"}
        if not valid_address(network, token) or (pool is not None and not valid_address(network, pool)):
            raise MarketError("invalid_token", "The token or pool address is not valid for this chain.")
        if resolution not in RESOLUTIONS:
            raise MarketError("invalid_resolution", "Unsupported chart timeframe.")
        network_id = CODEX_NETWORK[network]
        step = RESOLUTIONS[resolution]
        ttl = min(120, max(15, step // 4))

        async def load():
            now = int(time.time())
            variables = {"symbol": f"{pool or token}:{network_id}", "from": now - step * MAX_BARS, "to": now,
                         "resolution": resolution, "countback": MAX_BARS, "symbolType": "POOL" if pool else "TOKEN",
                         "quoteToken": quote_side if pool and quote_side in ("token0", "token1") else None}
            data = await self.query(_BARS_QUERY, variables)
            bars = normalize_bars(data, resolution)
            return {"state": "ok" if bars else "no_bars", "bars": bars, "resolution": resolution,
                    "bar_seconds": step, "currency": "USD", "scope": "pool" if pool else "token",
                    "pool": pool, "last_bar_at": bars[-1]["t"] if bars else None}

        key = ("bars", network, _key(network, token), _key(network, pool) if pool else None, resolution)
        return await self._cached(key, ttl, load)

    async def prices(self, inputs: list[tuple[str, str, int | None]]) -> dict:
        """USD prices for (network, address, timestamp|None) tuples, keyed by the same tuple.

        A price Codex reports for a different time than asked (more than a day earlier) is left out, never used
        as a stand-in for the requested time."""
        if not self.api_key:
            return {}
        wanted = [(n, a, ts) for n, a, ts in inputs if n in CODEX_NETWORK]
        out = {}
        for i in range(0, len(wanted), 25):  # Codex reads at most 25 inputs per request
            batch = wanted[i:i + 25]
            body = [{"address": a, "networkId": CODEX_NETWORK[n], **({"timestamp": ts} if ts else {})} for n, a, ts in batch]

            async def load(body=body):
                data = await self.query(_PRICES_QUERY, {"inputs": body})
                return {"rows": data.get("getTokenPrices") if isinstance(data.get("getTokenPrices"), list) else []}

            key = ("prices", tuple((n, _key(n, a), ts) for n, a, ts in batch))
            ttl = 60 if any(ts is None for _, _, ts in batch) else 86_400
            try:
                rows = (await self._cached(key, ttl, load))["rows"]
            except MarketError:
                continue
            for (n, a, ts), row in zip(batch, rows):
                if not isinstance(row, dict) or not _same(row.get("address"), a, n):
                    continue
                price = _num(row.get("priceUsd"))
                stamp = _count(row.get("timestamp"))
                if price is None or price <= 0:
                    continue
                if ts and (stamp is None or ts - stamp > 86_400 or stamp > ts + 3_600):
                    continue
                out[(n, a, ts)] = price
        return out


_market: Market | None = None


def get_market() -> Market:
    global _market
    if _market is None:
        _market = Market()
    return _market
