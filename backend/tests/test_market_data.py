import httpx
import pytest

from app.chains.evm import EVM_CHAINS
from app.config import Settings, get_settings
from app.services import market_data as market
from .conftest import signup

TOKEN = "TokenAddressABC"
OTHER = "OtherTokenXYZ"
POOL_A = "PoolWithLowerLiquidity"
POOL_B = "PoolWithHigherLiquidity"


def _provider_payload():
    def pool(pool_address, base_address, quote_address, base_symbol, quote_symbol, liquidity, base_price, quote_price,
             pct, base_fdv, quote_fdv):
        base_id = f"solana_{base_address}"
        quote_id = f"solana_{quote_address}"
        return {
            "data": [{
                "id": f"solana_{pool_address}", "type": "pool",
                "attributes": {
                    "address": pool_address, "name": f"{base_symbol} / {quote_symbol}",
                    "base_token_price_usd": str(base_price), "quote_token_price_usd": str(quote_price),
                    "base_token_price_native_currency": "0.001", "quote_token_price_native_currency": "0.0000001",
                    "price_change_percentage": {"h24": str(pct)},
                    "volume_usd": {"h24": "1200"}, "transactions": {"h24": {"buys": 10, "sells": 5}},
                    "reserve_in_usd": str(liquidity), "fdv_usd": str(base_fdv), "market_cap_usd": None,
                    "pool_created_at": "2026-09-26T12:00:00Z",
                },
                "relationships": {"base_token": {"data": {"id": base_id}},
                                  "quote_token": {"data": {"id": quote_id}},
                                  "dex": {"data": {"id": "solana_orca"}}},
            }],
            "included": [
                {"id": base_id, "type": "token", "attributes": {"address": base_address, "symbol": base_symbol,
                                                                       "name": base_symbol, "fdv_usd": str(base_fdv)}},
                {"id": quote_id, "type": "token", "attributes": {"address": quote_address, "symbol": quote_symbol,
                                                                        "name": quote_symbol, "fdv_usd": str(quote_fdv),
                                                                        "market_cap_usd": "900"}},
                {"id": "solana_orca", "type": "dex", "attributes": {"name": "Orca"}},
            ],
        }

    # The first result places the launch token as base; the deeper pool places it as quote.
    first = pool(POOL_A, TOKEN, OTHER, "TST", "SOL", 1000, 0.002, 150, 20, 2000, 150000)
    second = pool(POOL_B, OTHER, TOKEN, "SOL", "TST", 5000, 150, 0.002, 20, 150000, 2000)
    ticker_collision = pool("UnrelatedTickerPool", "SameTickerDifferentAddress", OTHER, "TST", "SOL",
                            9000, 0.002, 150, 20, 2000, 150000)
    return {"data": first["data"] + second["data"] + ticker_collision["data"],
            "included": first["included"] + second["included"] + ticker_collision["included"]}


def _settings_url(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "geckoterminal_api_url", "https://provider.test/api/v2")


@pytest.fixture(autouse=True)
def clear_cache():
    market.clear_market_cache()
    yield
    market.clear_market_cache()


def test_default_provider_is_the_keyless_public_api():
    assert Settings(_env_file=None).geckoterminal_api_url == "https://api.geckoterminal.com/api/v2"


def test_network_mappings_match_helm_evm_chain_ids():
    assert market.NETWORKS["base"]["chain_id"] == EVM_CHAINS["base"]["chain_id"] == 8453
    assert market.NETWORKS["bsc"]["chain_id"] == EVM_CHAINS["bsc"]["chain_id"] == 56
    assert market.NETWORKS["ethereum"]["chain_id"] == EVM_CHAINS["ethereum"]["chain_id"] == 1
    assert market.NETWORKS["robinhood"]["chain_id"] == EVM_CHAINS["robinhood"]["chain_id"] == 4663
    assert market.NETWORKS["ethereum"]["provider_id"] == "eth"


@pytest.mark.asyncio
async def test_snapshot_resolves_by_network_and_exact_token_and_selects_deepest_pool(monkeypatch):
    _settings_url(monkeypatch)
    seen = []

    def handler(req):
        seen.append(req.url)
        assert req.headers.get("accept") == "application/json;version=20230203"
        assert req.headers.get("x-cg-pro-api-key") is None
        if req.url.path.endswith("/networks"):
            return httpx.Response(200, json={"data": [{"id": "solana"}, {"id": "base"}]})
        if req.url.path.endswith("/dexes"):
            assert req.url.path.endswith("/networks/solana/dexes")
            return httpx.Response(200, json={"data": [{"id": "solana_orca"}]})
        assert req.url.path.endswith(f"/networks/solana/tokens/{TOKEN}/pools")
        assert req.url.params.get("include") == "base_token,quote_token,dex"
        return httpx.Response(200, json=_provider_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await market.get_market_snapshot("solana", TOKEN, client=client)

    assert result["status"] == "ok"
    assert result["network_id"] == "solana"
    assert result["selected_pool"]["pool_address"] == POOL_B
    assert len(result["pairs"]) == 2
    quote_pool = result["selected_pool"]
    assert quote_pool["quote_symbol"] == "TST"
    assert quote_pool["price_usd"] == 0.002
    # The base asset gained 20%, so the inverse quote-asset price fell by 16.67%.
    assert quote_pool["price_change"]["h24"] == pytest.approx(-16.6666667)
    assert quote_pool["fdv_usd"] == 2000
    assert quote_pool["market_cap_usd"] == 900
    assert quote_pool["liquidity_usd"] == 5000
    assert quote_pool["transactions_24h"] == 15
    assert len(seen) == 3


@pytest.mark.asyncio
async def test_empty_pool_index_and_provider_coverage_are_distinguished(monkeypatch):
    _settings_url(monkeypatch)

    def empty_handler(req):
        if req.url.path.endswith("/networks"):
            return httpx.Response(200, json={"data": [{"id": "solana"}]})
        if req.url.path.endswith("/dexes"):
            return httpx.Response(200, json={"data": [{"id": "solana_orca"}]})
        return httpx.Response(200, json={"data": [], "included": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(empty_handler)) as client:
        none = await market.get_market_snapshot("solana", TOKEN, client=client)
    assert none["status"] == "no_pool"
    assert "indexed" in none["reason"]

    def unsupported_handler(req):
        return httpx.Response(200, json={"data": [{"id": "solana"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(unsupported_handler)) as client:
        unsupported = await market.get_market_snapshot("robinhood", "0x" + "12" * 20, client=client)
    assert unsupported["status"] == "unsupported"
    assert unsupported["network_id"] == "robinhood"
    market.clear_market_cache()

    def no_dex_handler(req):
        if req.url.path.endswith("/networks"):
            return httpx.Response(200, json={"data": [{"id": "robinhood"}]})
        return httpx.Response(200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_dex_handler)) as client:
        no_dex = await market.get_market_snapshot("robinhood", "0x" + "12" * 20, client=client)
    assert no_dex["status"] == "unsupported"
    assert "DEX coverage" in no_dex["reason"]


@pytest.mark.asyncio
async def test_documented_ohlcv_uses_pool_and_exact_token_address(monkeypatch):
    _settings_url(monkeypatch)
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.path.endswith("/networks"):
            return httpx.Response(200, json={"data": [{"id": "solana"}]})
        if req.url.path.endswith("/dexes"):
            return httpx.Response(200, json={"data": [{"id": "solana_orca"}]})
        if req.url.path.endswith("/tokens/TokenAddressABC/pools"):
            return httpx.Response(200, json={"data": [{
                "id": f"solana_{POOL_A}", "type": "pool", "attributes": {
                    "address": POOL_A, "name": "TST / SOL", "reserve_in_usd": "100",
                    "base_token_price_usd": "1", "base_token_price_native_currency": "0.01",
                    "quote_token_price_usd": "100", "quote_token_price_native_currency": "1",
                }, "relationships": {"base_token": {"data": {"id": f"solana_{TOKEN}"}},
                                    "quote_token": {"data": {"id": f"solana_{OTHER}"}},
                                    "dex": {"data": {"id": "solana_orca"}}},
            }], "included": [
                {"id": f"solana_{TOKEN}", "type": "token", "attributes": {"address": TOKEN, "symbol": "TST"}},
                {"id": f"solana_{OTHER}", "type": "token", "attributes": {"address": OTHER, "symbol": "SOL"}},
                {"id": "solana_orca", "type": "dex", "attributes": {"name": "Orca"}},
            ]})
        return httpx.Response(200, json={"data": {"attributes": {"ohlcv_list": [
            [1000, "1", "2", "0.5", "1.5", "30"], [1060, "1.5", "2.5", "1", "2", "45"]
        ]}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await market.get_pool_ohlcv("solana", TOKEN, POOL_A, "1h", client=client)

    assert result["status"] == "ok"
    assert result["candles"][0] == {"time": 1000, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
                                     "volume_usd": 30.0}
    request = seen[-1]
    assert request.url.path.endswith(f"/networks/solana/pools/{POOL_A}/ohlcv/minute")
    assert request.url.params.get("token") == TOKEN
    assert request.url.params.get("aggregate") == "1"
    assert request.url.params.get("currency") == "usd"


@pytest.mark.asyncio
async def test_public_api_needs_no_key_and_rate_limit_is_graceful(monkeypatch):
    _settings_url(monkeypatch)

    def public_handler(req):
        assert req.headers.get("accept") == "application/json;version=20230203"
        assert req.headers.get("x-cg-pro-api-key") is None
        if req.url.path.endswith("/networks"):
            return httpx.Response(200, json={"data": [{"id": "base"}]})
        if req.url.path.endswith("/dexes"):
            return httpx.Response(200, json={"data": [{"id": "base_uniswap_v3"}]})
        return httpx.Response(200, json={"data": [], "included": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(public_handler)) as client:
        keyless = await market.get_market_snapshot("base", "0x" + "12" * 20, client=client)
    assert keyless["status"] == "no_pool"

    market.clear_market_cache()

    def handler(req):
        if req.url.path.endswith("/networks"):
            return httpx.Response(200, json={"data": [{"id": "base"}]})
        if req.url.path.endswith("/dexes"):
            return httpx.Response(200, json={"data": [{"id": "base_uniswap_v3"}]})
        return httpx.Response(429, json={"error": "rate limit"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        limited = await market.get_market_snapshot("base", "0x" + "12" * 20, client=client)
    assert limited["status"] == "unavailable"
    assert "rate limit" in limited["reason"].lower()


@pytest.mark.asyncio
async def test_cached_snapshot_is_marked_stale_during_provider_failure(monkeypatch):
    _settings_url(monkeypatch)

    def good_handler(req):
        if req.url.path.endswith("/networks"):
            return httpx.Response(200, json={"data": [{"id": "solana"}]})
        if req.url.path.endswith("/dexes"):
            return httpx.Response(200, json={"data": [{"id": "solana_orca"}]})
        return httpx.Response(200, json=_provider_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(good_handler)) as client:
        initial = await market.get_market_snapshot("solana", TOKEN, client=client)
    assert initial["status"] == "ok" and initial["stale"] is False
    pair_entry = next(entry for key, entry in market._cache.items() if "pairs" in key)
    pair_entry.fetched_at -= market.CACHE_TTL_SECONDS + 1

    def failed_handler(req):
        return httpx.Response(503, json={"error": "temporarily unavailable"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(failed_handler)) as client:
        stale = await market.get_market_snapshot("solana", TOKEN, client=client)
    assert stale["status"] == "ok"
    assert stale["stale"] is True
    assert stale["selected_pool"]["pool_address"] == POOL_B


@pytest.mark.asyncio
async def test_market_route_requires_launch_owner_and_returns_pending_for_draft(client):
    owner = await signup(client, "market-owner@example.com")
    stranger = await signup(client, "market-stranger@example.com")
    launch = (await client.post("/launches", json={"name": "Market test", "symbol": "MKT"}, headers=owner)).json()

    assert (await client.get(f"/launches/{launch['id']}/market")).status_code == 401
    assert (await client.get(f"/launches/{launch['id']}/market", headers=stranger)).status_code == 404
    own = await client.get(f"/launches/{launch['id']}/market", headers=owner)
    assert own.status_code == 200
    assert own.json()["status"] == "pending"

