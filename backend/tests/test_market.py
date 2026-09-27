"""Market view: pools resolved by chain + token address, honest missing values, caching, stale fallback and throttling."""

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.main import app
from app.models import Launch, User
from app.services import market as mk
from app.services.market import Market, get_market

from .conftest import signup

TOKEN = "HeLmCaT1111111111111111111111111111111111pump"
OTHER = "0therToken111111111111111111111111111111111"
SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def pair(addr, base, quote, liq, **kw):
    return {"chainId": kw.get("chain", "solana"), "dexId": kw.get("dex", "pumpswap"), "url": f"https://dexscreener.com/solana/{addr}",
            "pairAddress": addr, "baseToken": {"address": base, "symbol": "HCAT" if base == TOKEN else "SOL", "name": "x"},
            "quoteToken": {"address": quote, "symbol": "SOL" if quote == SOL else ("USDC" if quote == USDC else "HCAT"), "name": "y"},
            "priceNative": "0.0000012", "priceUsd": "0.00018",
            "txns": {"m5": {"buys": 3, "sells": 1}, "h24": {"buys": 120, "sells": 80}},
            "volume": {"h24": 5400.5, "m5": 12}, "priceChange": {"h24": -4.2},
            "liquidity": {"usd": liq} if liq is not None else {},
            "fdv": 180000, **({"marketCap": kw["mcap"]} if "mcap" in kw else {}), "pairCreatedAt": 1790000000000}


class Providers:
    def __init__(self):
        self.pairs: object = [
            pair("POOLsmall", TOKEN, USDC, 900.0, dex="meteora"),
            pair("POOLbig", TOKEN, SOL, 25000.0, mcap=175000),
            pair("POOLquote", SOL, TOKEN, 400.0, dex="raydium"),  # our token on the quote side
            pair("POOLother", OTHER, SOL, 99999.0),  # a different token: never shown
            pair("POOLevm", TOKEN, SOL, 50000.0, chain="base"),  # another chain: never shown
        ]
        self.pairs_status = 200
        self.gecko_status = 200
        self.calls = {"ds": 0, "gt": 0}
        self.seen_params: list = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        if req.url.host == "api.dexscreener.com":
            self.calls["ds"] += 1
            assert req.url.path == f"/token-pairs/v1/solana/{TOKEN}"
            if self.pairs_status != 200:
                return httpx.Response(self.pairs_status, json={}, headers={"retry-after": "30"})
            return httpx.Response(200, json=self.pairs)
        if req.url.host == "api.geckoterminal.com":
            self.calls["gt"] += 1
            self.seen_params.append((req.url.path, dict(req.url.params)))
            if self.gecko_status != 200:
                return httpx.Response(self.gecko_status, json={})
            return httpx.Response(200, json={"data": {"attributes": {"ohlcv_list": [
                [1790003600, 1.1, 1.3, 1.0, 1.2, 500.0],  # newest first, as the provider returns it
                [1790000000, 1.0, 1.2, 0.9, 1.1, 300.0],
                [1789996400, "bad", None, 1, 1, 1],  # malformed rows are dropped, not filled
            ]}}})
        raise AssertionError(req.url)


async def _launch(client, network="solana", mint=TOKEN, status="live", h=None):
    h = h or await signup(client)
    async with get_sessionmaker()() as db:
        uid = (await db.execute(select(User.id))).scalars().first()
        l = Launch(user_id=uid, name="Helm Cat", symbol="HCAT", launchpad="pumpfun", network=network, status=status,
                   mint_address=mint)
        db.add(l)
        await db.commit()
        return h, l.id


def _use(prov: Providers):
    m = Market(client=httpx.AsyncClient(transport=httpx.MockTransport(prov.handler)))
    app.dependency_overrides[get_market] = lambda: m
    return m


async def test_pools_resolved_by_chain_and_address_with_honest_fields(client):
    prov = Providers()
    _use(prov)
    try:
        h, lid = await _launch(client)
        r = (await client.get(f"/launches/{lid}/market", headers=h)).json()
        assert r["state"] == "ok" and r["source"] == "DEX Screener" and "Indicative" in r["note"]
        addrs = [p["pair_address"] for p in r["pairs"]]
        assert addrs == ["POOLbig", "POOLsmall", "POOLquote"]  # this token, this chain only; largest liquidity first
        big, small, quote = r["pairs"]
        assert big["price_usd"] == 0.00018 and big["market_cap"] == 175000 and big["fdv"] == 180000
        assert small["market_cap"] is None and small["fdv"] == 180000  # never inferred from FDV
        assert big["price_change"]["h1"] is None and big["volume_usd"]["h6"] is None  # absent, not zero
        assert big["txns"]["h24"] == {"buys": 120, "sells": 80} and "h1" not in big["txns"]
        assert quote["token_side"] == "quote" and quote["price_usd"] is None and quote["fdv"] is None  # base token's numbers withheld
        assert r["explorer_url"].endswith(TOKEN)
        # cached: a second look within the TTL doesn't call the provider again
        await client.get(f"/launches/{lid}/market", headers=h)
        assert prov.calls["ds"] == 1
    finally:
        app.dependency_overrides.pop(get_market, None)


async def test_candles_for_a_listed_pool_only_sorted_and_throttled(client, monkeypatch):
    prov = Providers()
    m = _use(prov)
    try:
        h, lid = await _launch(client)
        r = (await client.get(f"/launches/{lid}/market/candles?pool=POOLbig&tf=1h", headers=h)).json()
        assert r["state"] == "ok" and r["source"] == "GeckoTerminal"
        assert [c["t"] for c in r["candles"]] == [1790000000, 1790003600]  # oldest first; the bad row dropped
        path, params = prov.seen_params[-1]
        assert path == "/api/v2/networks/solana/pools/POOLbig/ohlcv/hour"
        assert params == {"aggregate": "1", "limit": "200", "currency": "usd", "token": "base"}
        # our token is the quote side of this pool: candles are asked for the quote token
        await client.get(f"/launches/{lid}/market/candles?pool=POOLquote&tf=15m", headers=h)
        assert prov.seen_params[-1][1]["token"] == "quote" and prov.seen_params[-1][0].endswith("/ohlcv/minute")
        # a pool that doesn't trade this token is refused, as is an unknown timeframe
        assert (await client.get(f"/launches/{lid}/market/candles?pool=POOLother&tf=1h", headers=h)).status_code == 404
        assert (await client.get(f"/launches/{lid}/market/candles?pool=POOLbig&tf=3m", headers=h)).status_code == 400
        # throttle: past the per-minute budget, uncached requests wait instead of hitting the provider
        monkeypatch.setattr(mk, "GECKO_PER_MIN", 2)
        r = (await client.get(f"/launches/{lid}/market/candles?pool=POOLsmall&tf=1d", headers=h)).json()
        assert r["state"] == "error" and r["error_kind"] == "rate_limited" and prov.calls["gt"] == 2
        # a pool GeckoTerminal hasn't indexed yet
        m._gecko_calls.clear()
        prov.gecko_status = 404
        r = (await client.get(f"/launches/{lid}/market/candles?pool=POOLsmall&tf=4h", headers=h)).json()
        assert r["state"] == "no_candles" and "indexed" in r["reason"]
    finally:
        app.dependency_overrides.pop(get_market, None)


async def test_no_pool_unsupported_no_token_and_provider_failures(client, monkeypatch):
    prov = Providers()
    m = _use(prov)
    try:
        h, lid = await _launch(client)
        # provider down before anything was cached: an error state, not zeros
        prov.pairs_status = 503
        r = (await client.get(f"/launches/{lid}/market", headers=h)).json()
        assert r["state"] == "error" and r["pairs"] == [] and "HTTP 503" in r["error"]
        # works, then rate-limited: the last good answer is served and marked stale
        prov.pairs_status = 200
        assert (await client.get(f"/launches/{lid}/market", headers=h)).json()["state"] == "ok"
        m._cache[("pairs", "solana", TOKEN.lower())].at -= 120  # past the TTL
        prov.pairs_status = 429
        r = (await client.get(f"/launches/{lid}/market", headers=h)).json()
        assert r["state"] == "ok" and r["stale"] is True and "rate limit" in r["error"] and r["age_s"] >= 120
        calls = prov.calls["ds"]
        m._cache.clear()
        r = (await client.get(f"/launches/{lid}/market", headers=h)).json()  # still backing off: no new call
        assert r["state"] == "error" and r["error_kind"] == "rate_limited" and prov.calls["ds"] == calls
        # a freshly launched token with no indexed pool yet
        m._backoff_until.clear()
        prov.pairs_status = 200
        prov.pairs = []
        r = (await client.get(f"/launches/{lid}/market", headers=h)).json()
        assert r["state"] == "no_pool" and r["pairs"] == []
        # a draft has no token to look up
        _, draft = await _launch(client, mint=None, status="draft", h=h)
        assert (await client.get(f"/launches/{draft}/market", headers=h)).json()["state"] == "no_token"
        # a chain the provider doesn't cover
        monkeypatch.setitem(mk.DEXSCREENER_CHAIN, "solana", None)
        m._cache.clear()
        r = (await client.get(f"/launches/{lid}/market", headers=h)).json()
        assert r["state"] == "unsupported"
    finally:
        app.dependency_overrides.pop(get_market, None)
