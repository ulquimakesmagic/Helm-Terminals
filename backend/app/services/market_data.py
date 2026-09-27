"""Read-only, public on-chain market data for live launch workspaces.

All lookups are keyed by Helm's canonical network and the launch's exact token address. This module is
intentionally not imported by trade quote or execution services.
"""

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from ..chains.evm import EVM_CHAINS
from ..config import get_settings

SOURCE = "GeckoTerminal Public API"
SOURCE_URL = "https://www.geckoterminal.com"
GECKOTERMINAL_URL = "https://www.geckoterminal.com"
CACHE_TTL_SECONDS = 5 * 60
STALE_TTL_SECONDS = 15 * 60
NETWORK_CACHE_TTL_SECONDS = 12 * 60 * 60

# GeckoTerminal uses provider network IDs, not EVM chain IDs. Keep the numeric EVM chain IDs beside those
# IDs so Helm's mapping is explicit and testable; the provider's /networks endpoint confirms availability.
NETWORKS: dict[str, dict[str, Any]] = {
    "solana": {"provider_id": "solana", "chain_id": None, "native": "SOL"},
    "base": {"provider_id": "base", "chain_id": 8453, "native": "ETH"},
    "bsc": {"provider_id": "bsc", "chain_id": 56, "native": "BNB"},
    "ethereum": {"provider_id": "eth", "chain_id": 1, "native": "ETH"},
    "robinhood": {"provider_id": "robinhood", "chain_id": 4663, "native": "ETH"},
}

# Human-selected ranges map to documented OHLCV intervals and aggregates. Every request includes the exact
# launch token address, so a pair whose token is the quote asset still charts the correct token.
TIMEFRAMES: dict[str, dict[str, Any]] = {
    "1h": {"interval": "minute", "aggregate": 1, "limit": 60, "label": "1 hour"},
    "24h": {"interval": "minute", "aggregate": 5, "limit": 288, "label": "24 hours"},
    "7d": {"interval": "hour", "aggregate": 1, "limit": 168, "label": "7 days"},
    "30d": {"interval": "hour", "aggregate": 4, "limit": 180, "label": "30 days"},
}


@dataclass
class _CacheEntry:
    value: Any
    fetched_at: float


_cache: dict[tuple, _CacheEntry] = {}
_cache_locks: dict[tuple, asyncio.Lock] = {}


class MarketProviderError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _num(value: Any) -> float | None:
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def _same_address(network: str, left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    # Solana base58 addresses are case-sensitive; EVM addresses are not.
    return left == right if network == "solana" else left.lower() == right.lower()


def _resource_address(resource: dict | None, network_id: str) -> str | None:
    if not isinstance(resource, dict):
        return None
    attrs = resource.get("attributes") or {}
    address = attrs.get("address")
    if address:
        return str(address)
    # JSON:API relationship IDs use `<network>_<address>`.
    resource_id = str(resource.get("id") or "")
    prefix = f"{network_id}_"
    return resource_id[len(prefix):] if resource_id.startswith(prefix) else None


def _pct_for_token(value: Any, is_base: bool) -> float | None:
    pct = _num(value)
    if pct is None or is_base:
        return pct
    # Price changes are reported for the base token. Invert the relative change when the launch token
    # is the quote token; e.g. a +100% base move is a -50% quote move.
    ratio = 1 + pct / 100
    return (100 / ratio - 100) if ratio > 0 else None


def _pairs_from_payload(network: str, token_address: str, payload: dict) -> list[dict]:
    provider_id = NETWORKS[network]["provider_id"]
    resources: dict[tuple[str, str], dict] = {}
    for resource in payload.get("included") or []:
        if isinstance(resource, dict):
            resources[(str(resource.get("type") or ""), str(resource.get("id") or ""))] = resource

    pairs: list[dict] = []
    for pool in payload.get("data") or []:
        if not isinstance(pool, dict):
            continue
        attrs = pool.get("attributes") or {}
        rel = pool.get("relationships") or {}
        base_ref = ((rel.get("base_token") or {}).get("data") or {})
        quote_ref = ((rel.get("quote_token") or {}).get("data") or {})
        base_resource = resources.get(("token", str(base_ref.get("id") or "")))
        quote_resource = resources.get(("token", str(quote_ref.get("id") or "")))
        base_address = _resource_address(base_resource, provider_id) or _resource_address(base_ref, provider_id)
        quote_address = _resource_address(quote_resource, provider_id) or _resource_address(quote_ref, provider_id)
        is_base = _same_address(network, base_address, token_address)
        is_quote = _same_address(network, quote_address, token_address)
        # The token-address endpoint is chain+address scoped, but verify the returned pool resources too.
        if not (is_base or is_quote):
            continue

        target_resource = base_resource if is_base else quote_resource
        target_attrs = (target_resource or {}).get("attributes") or {}
        # "Quote asset" means the pool's quote-side token, independent of whether the launch is base or quote.
        quote_attrs = (quote_resource or {}).get("attributes") or {}
        dex_ref = ((rel.get("dex") or {}).get("data") or {})
        dex_resource = resources.get(("dex", str(dex_ref.get("id") or "")))
        dex_name = ((dex_resource or {}).get("attributes") or {}).get("name")
        if not dex_name:
            dex_name = str(dex_ref.get("id") or "").replace("_", " ").strip() or None

        addr = str(attrs.get("address") or "")
        if not addr:
            pool_id = str(pool.get("id") or "")
            prefix = f"{provider_id}_"
            addr = pool_id[len(prefix):] if pool_id.startswith(prefix) else ""
        if not addr:
            continue

        def price_field(field: str) -> float | None:
            return _num(attrs.get(("base_token_" if is_base else "quote_token_") + field))

        changes = attrs.get("price_change_percentage") or {}
        vols = attrs.get("volume_usd") or {}
        txns = attrs.get("transactions") or {}
        h24_txns = txns.get("h24") or {}
        buys_24h = _num(h24_txns.get("buys"))
        sells_24h = _num(h24_txns.get("sells"))
        txn_count = buys_24h + sells_24h if buys_24h is not None and sells_24h is not None else None
        base_symbol = ((base_resource or {}).get("attributes") or {}).get("symbol") or "Token"
        quote_symbol = quote_attrs.get("symbol") or "Unknown"
        quote_name = quote_attrs.get("name")
        quote_asset = f"{quote_symbol}{f' · {quote_name}' if quote_name and quote_name != quote_symbol else ''}"

        # Market cap / FDV may be provided on the token resource. Pool-level values describe the base
        # asset, so only use those as a fallback when the launch token is the base asset.
        fdv = _num(target_attrs.get("fdv_usd"))
        market_cap = _num(target_attrs.get("market_cap_usd"))
        if is_base:
            fdv = fdv if fdv is not None else _num(attrs.get("fdv_usd"))
            market_cap = market_cap if market_cap is not None else _num(attrs.get("market_cap_usd"))

        pairs.append({
            "pool_address": addr,
            "pool_name": attrs.get("name") or f"{base_symbol} / {quote_symbol}",
            "dex": str(dex_name) if dex_name else "Unknown DEX",
            "quote_asset": quote_asset,
            "quote_symbol": str(quote_symbol),
            "price_usd": price_field("price_usd"),
            "price_native": price_field("price_native_currency"),
            "price_change": {k: _pct_for_token(v, is_base) for k, v in changes.items()},
            "volume_usd_24h": _num(vols.get("h24")),
            "transactions_24h": txn_count,
            "buys_24h": buys_24h,
            "sells_24h": sells_24h,
            "liquidity_usd": _num(attrs.get("reserve_in_usd")),
            "fdv_usd": fdv,
            "market_cap_usd": market_cap,
            "pool_created_at": attrs.get("pool_created_at"),
            "pool_url": f"{GECKOTERMINAL_URL}/{provider_id}/pools/{quote(addr, safe='')}",
            "is_base_token": is_base,
        })

    # Prefer the deepest pool but keep all valid pools selectable.
    pairs.sort(key=lambda p: p["liquidity_usd"] if p["liquidity_usd"] is not None else -1, reverse=True)
    return pairs


def _validated_chain(network: str) -> tuple[dict | None, str | None]:
    config = NETWORKS.get(network)
    if not config:
        return None, "This chain is not supported by Helm's market-data mapping."
    if network in EVM_CHAINS and config["chain_id"] != EVM_CHAINS[network]["chain_id"]:
        return None, "Helm's chain ID mapping does not match the market-data configuration."
    if network != "solana" and network not in EVM_CHAINS:
        return None, "This chain is not supported by Helm's EVM configuration."
    return config, None


def clear_market_cache() -> None:
    """Reset the in-process cache; exposed for deterministic tests and administrative reloads."""
    _cache.clear()
    _cache_locks.clear()


async def _get_json(path: str, *, cache_key: tuple, ttl: int = CACHE_TTL_SECONDS,
                    client: httpx.AsyncClient | None = None, params: dict | None = None) -> tuple[dict, str, bool]:
    settings = get_settings()
    base_url = settings.geckoterminal_api_url.rstrip("/")
    key = (base_url, *cache_key)
    lock = _cache_locks.setdefault(key, asyncio.Lock())
    async with lock:
        entry = _cache.get(key)
        now = time.monotonic()
        if entry and now - entry.fetched_at < ttl:
            return entry.value, datetime.fromtimestamp(time.time() - (now - entry.fetched_at), timezone.utc).isoformat().replace("+00:00", "Z"), False

        # The public API needs no key. Pin a documented response version to reduce beta API surprises.
        headers = {"accept": "application/json;version=20230203"}
        owns_client = client is None
        http = client or httpx.AsyncClient(timeout=10)
        try:
            response = await http.get(f"{base_url}{path}", params=params, headers=headers)
            if response.status_code >= 400:
                raise MarketProviderError("Market data provider returned an error.", response.status_code)
            data = response.json()
            if not isinstance(data, dict):
                raise MarketProviderError("Market data provider returned an invalid response.")
            fetched = time.monotonic()
            _cache[key] = _CacheEntry(data, fetched)
            return data, _now_iso(), False
        except (httpx.HTTPError, ValueError) as exc:
            if entry and now - entry.fetched_at <= ttl + STALE_TTL_SECONDS:
                age = now - entry.fetched_at
                fetched_at = datetime.fromtimestamp(time.time() - age, timezone.utc).isoformat().replace("+00:00", "Z")
                return entry.value, fetched_at, True
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
            else:
                status = None
            raise MarketProviderError("Market data is temporarily unavailable.", status) from exc
        except MarketProviderError as exc:
            if entry and now - entry.fetched_at <= ttl + STALE_TTL_SECONDS:
                age = now - entry.fetched_at
                fetched_at = datetime.fromtimestamp(time.time() - age, timezone.utc).isoformat().replace("+00:00", "Z")
                return entry.value, fetched_at, True
            raise exc
        finally:
            if owns_client:
                await http.aclose()


def _disabled_or_unsupported(network: str) -> dict | None:
    config, error = _validated_chain(network)
    if error:
        return {"status": "unsupported", "reason": error, "source": SOURCE, "source_url": SOURCE_URL,
                "updated_at": None, "stale": False, "pairs": [], "selected_pool": None}
    return None


async def get_market_snapshot(network: str, token_address: str, pool_address: str | None = None,
                              client: httpx.AsyncClient | None = None) -> dict:
    unavailable = _disabled_or_unsupported(network)
    if unavailable:
        return unavailable
    config = NETWORKS[network]
    provider_id = config["provider_id"]
    try:
        network_data, _, network_stale = await _get_json("/networks", cache_key=("networks",),
                                                           ttl=NETWORK_CACHE_TTL_SECONDS, client=client)
        available_ids = {str(item.get("id") or "") for item in network_data.get("data") or []
                         if isinstance(item, dict)}
        if provider_id not in available_ids:
            return {"status": "unsupported", "reason": "This chain is not currently listed by the market-data provider.",
                    "source": SOURCE, "source_url": SOURCE_URL, "network_id": provider_id,
                    "chain_id": config["chain_id"], "updated_at": None, "stale": network_stale,
                    "pairs": [], "selected_pool": None}

        try:
            dex_data, _, dex_stale = await _get_json(f"/networks/{provider_id}/dexes",
                                                      cache_key=("dexes", provider_id),
                                                      ttl=NETWORK_CACHE_TTL_SECONDS, client=client)
        except MarketProviderError as exc:
            if exc.status_code != 404:
                raise
            dex_data, dex_stale = {"data": []}, False
        if not [item for item in dex_data.get("data") or [] if isinstance(item, dict) and item.get("id")]:
            return {"status": "unsupported", "reason": "The market-data provider currently reports no supported DEX coverage for this chain.",
                    "source": SOURCE, "source_url": SOURCE_URL, "network_id": provider_id,
                    "chain_id": config["chain_id"], "updated_at": None, "stale": network_stale or dex_stale,
                    "pairs": [], "selected_pool": None}

        token = quote(token_address, safe="")
        try:
            pair_data, updated, stale = await _get_json(f"/networks/{provider_id}/tokens/{token}/pools",
                                                         cache_key=("pairs", network, token_address), client=client,
                                                         params={"include": "base_token,quote_token,dex", "page": 1})
        except MarketProviderError as exc:
            if exc.status_code != 404:
                raise
            return {"status": "no_pool", "reason": "Market data will appear once an eligible pool is indexed.",
                    "source": SOURCE, "source_url": SOURCE_URL, "network_id": provider_id,
                    "chain_id": config["chain_id"], "updated_at": None, "stale": False,
                    "pairs": [], "selected_pool": None}
        pairs = _pairs_from_payload(network, token_address, pair_data)
        if not pairs:
            return {"status": "no_pool", "reason": "Market data will appear once an eligible pool is indexed.",
                    "source": SOURCE, "source_url": SOURCE_URL, "network_id": provider_id,
                    "chain_id": config["chain_id"], "updated_at": updated, "stale": stale,
                    "pairs": [], "selected_pool": None}
        selected = next((p for p in pairs if _same_address(network, p["pool_address"], pool_address)), None) if pool_address else None
        if pool_address and selected is None:
            return {"status": "invalid_pool", "reason": "That pool is not listed for this launch token and chain.",
                    "source": SOURCE, "source_url": SOURCE_URL, "network_id": provider_id,
                    "chain_id": config["chain_id"], "updated_at": updated, "stale": stale,
                    "pairs": pairs, "selected_pool": None}
        selected = selected or pairs[0]
        return {"status": "ok", "reason": None, "source": SOURCE, "source_url": SOURCE_URL,
                "network_id": provider_id, "chain_id": config["chain_id"], "updated_at": updated,
                "stale": stale or network_stale or dex_stale, "pairs": pairs, "selected_pool": selected}
    except MarketProviderError as exc:
        reason = "Market-data provider rate limit reached. Try again shortly." if exc.status_code == 429 else str(exc)
        return {"status": "unavailable", "reason": reason, "source": SOURCE, "source_url": SOURCE_URL,
                "network_id": provider_id, "chain_id": config["chain_id"], "updated_at": None,
                "stale": False, "pairs": [], "selected_pool": None}


async def get_pool_ohlcv(network: str, token_address: str, pool_address: str, timeframe: str,
                         client: httpx.AsyncClient | None = None) -> dict:
    unavailable = _disabled_or_unsupported(network)
    if unavailable:
        return {**unavailable, "timeframe": timeframe, "candles": []}
    if timeframe not in TIMEFRAMES:
        return {"status": "unsupported_timeframe", "reason": "Select one of the supported chart ranges.",
                "source": SOURCE, "source_url": SOURCE_URL, "timeframe": timeframe,
                "updated_at": None, "stale": False, "candles": []}
    snapshot = await get_market_snapshot(network, token_address, pool_address, client)
    if snapshot["status"] != "ok":
        return {**snapshot, "timeframe": timeframe, "candles": []}
    config = NETWORKS[network]
    spec = TIMEFRAMES[timeframe]
    pool = quote(pool_address, safe="")
    token = quote(token_address, safe="")
    path = f"/networks/{config['provider_id']}/pools/{pool}/ohlcv/{spec['interval']}"
    try:
        data, updated, stale = await _get_json(path, cache_key=("ohlcv", network, pool_address, token_address, timeframe),
                                                client=client, params={"aggregate": spec["aggregate"],
                                                                       "limit": spec["limit"], "token": token,
                                                                       "currency": "usd"})
        raw = (((data.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or [])
        candles = []
        for row in raw:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            ts = _num(row[0])
            values = [_num(v) for v in row[1:6]]
            if ts is None or any(v is None for v in values):
                continue
            open_, high, low, close, volume = values
            candles.append({"time": int(ts), "open": open_, "high": high, "low": low,
                            "close": close, "volume_usd": volume})
        candles.sort(key=lambda c: c["time"])
        return {"status": "ok" if candles else "no_history",
                "reason": None if candles else "Historical chart data is not available for this pool yet.",
                "source": SOURCE, "source_url": SOURCE_URL, "timeframe": timeframe,
                "updated_at": updated, "stale": stale, "candles": candles}
    except MarketProviderError as exc:
        reason = "Market-data provider rate limit reached. Try again shortly." if exc.status_code == 429 else str(exc)
        return {"status": "unavailable", "reason": reason, "source": SOURCE, "source_url": SOURCE_URL,
                "timeframe": timeframe, "updated_at": None, "stale": False, "candles": []}

