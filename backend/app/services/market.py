"""GMGN-only display data. Never used by execution, signing or confirmation services.

Contract: GMGNAI/gmgn-skills OpenApiClient and token field reference. No website scraping.
Candles are rendered by GMGN's hosted embed, never constructed from market snapshots.
Market cap = price × circulating_supply and FDV = price × total_supply, GMGN's own
definitions, derived only from reported fields.
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
# OpenAPI token commands support more chains than the separately documented embed.
API_CHAIN = {"solana": "sol", "bsc": "bsc", "base": "base", "ethereum": "eth",
             "robinhood": "robinhood", "arc": "arc"}
CHART_CHAIN = {"solana": "sol", "bsc": "bsc", "ethereum": "eth"}
CACHE_TTL, STALE_MAX, ERROR_TTL, CACHE_LIMIT = 60, 300, 15, 256
WINDOWS = {"m5": "5m", "h1": "1h", "h6": "6h", "h24": "24h"}
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
            "symbol": _text(raw.get("symbol")), "gmgn_url": link,
            "metrics": {"price_usd": current, "holder_count": _count(raw.get("holder_count")),
                        "market_cap_usd": market_cap, "fdv_usd": fdv,
                        "liquidity_usd": _num(raw.get("liquidity")),
                        "price_change": changes,
                        "volume_usd": {w: _num(price.get(f"volume_{k}")) for w, k in WINDOWS.items()},
                        "txns": {w: {"buys": _count(price.get(f"buys_{k}")), "sells": _count(price.get(f"sells_{k}")),
                                     "swaps": _count(price.get(f"swaps_{k}"))} for w, k in WINDOWS.items()}}}


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

    def __post_init__(self):
        # GMGN documents IPv4 only. Fixed origin and no redirects protect the server credential.
        self.client = self.client or httpx.AsyncClient(timeout=12, follow_redirects=False,
            transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"))
        if self.api_key is None:
            self.api_key = get_settings().gmgn_api_key.strip()

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
        if not self.api_key:
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
            raw = await self._get(API_CHAIN[network], token)
            if raw is None or raw == {}:
                result = {"state": "no_pool", "pool": None, "metrics": None}
            elif not isinstance(raw, dict):
                raise MarketError("invalid_response", "GMGN returned an unexpected data format.")
            else:
                result = _normalize(raw, network, token)
            entry = _Entry(time.monotonic(), {**result, "fetched_at": time.time()})
        except MarketError as exc:
            old = self._cache.get(key)
            if not exc.retry_until:
                exc.retry_until = time.monotonic() + ERROR_TTL
            entry = _Entry(old.at if old else time.monotonic(), old.value if old else None,
                           exc, exc.retry_until)
            self._remember(key, entry)
            if entry.value and time.monotonic() - entry.at < STALE_MAX:
                return self._answer(entry, stale=True)
            raise
        self._remember(key, entry)
        return self._answer(entry)

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
