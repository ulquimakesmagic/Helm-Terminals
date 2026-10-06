"""Codex-only market data: contract, honest states, ownership, caching, retries and candles (audit HT-06)."""
import asyncio
import json
import time

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.main import app
from app.models import Launch, User
from app.services import market as mk
from app.services.market import Market, MarketError, get_market, normalize_bars, normalize_snapshot
from .conftest import TEST_OPENAPI, signup

TOKEN = "So11111111111111111111111111111111111111112"
POOL = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2"
POOL2 = "8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj"
QUOTE = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
NET = 1399811149


def token_row(token=TOKEN, network_id=NET, **over):
    row = {"token": {"address": token, "networkId": network_id, "symbol": "WSOL", "name": "Wrapped SOL"},
           "priceUSD": "120.5", "liquidity": "150000", "marketCap": "144600000", "circulatingMarketCap": "120500000",
           "holders": 4321, "isScam": False, "createdAt": 1_700_000_000, "lastTransaction": 1_759_700_000,
           "change5m": "0.005", "change1": "-0.012", "change4": "0.02", "change12": "0.03", "change24": "0.2",
           "volume5m": "100", "volume1": "3000", "volume4": "9000", "volume12": "20000", "volume24": "50000",
           "buyCount5m": 1, "buyCount1": 10, "buyCount4": 30, "buyCount12": 60, "buyCount24": 120,
           "sellCount5m": 0, "sellCount1": 5, "sellCount4": 20, "sellCount12": 40, "sellCount24": 80,
           "txnCount5m": 1, "txnCount1": 15, "txnCount4": 50, "txnCount12": 100, "txnCount24": 200}
    row.update(over)
    return row


def pair(address=POOL, liquidity="150000", network_id=NET, token=TOKEN, ours="token"):
    mine = {"address": token, "symbol": "WSOL", "networkId": network_id}
    other = {"address": QUOTE, "symbol": "USDC", "networkId": network_id}
    return {"volume": "50000", "liquidity": liquidity, "quoteToken": "token1",
            "token": mine if ours == "token" else other, "backingToken": other if ours == "token" else mine,
            "pair": {"address": address, "networkId": network_id}, "exchange": {"name": "Raydium"}}


def snapshot_body(rows=None, pairs=None):
    return {"data": {"filterTokens": {"results": [token_row()] if rows is None else rows},
                     "listPairsWithMetadataForToken": {"results": [pair()] if pairs is None else pairs}}}


def bars_body(n=3, start=1_759_000_000, step=900):
    t = [start + i * step for i in range(n)]
    return {"data": {"getBars": {"t": t, "o": [1.0] * n, "h": [1.2] * n, "l": [0.9] * n, "c": [1.1] * n,
                                 "volume": ["10"] * n, "s": "ok"}}}


class Codex:
    """A fake Codex GraphQL endpoint that records every request and refuses anything else."""

    def __init__(self):
        self.requests: list[dict] = []
        self.status = 200
        self.snapshot = snapshot_body()
        self.bars = bars_body()
        self.prices = None
        self.headers = {}
        self.fail_transport = 0

    async def handle(self, request: httpx.Request):
        assert request.url.host == "graph.codex.io" and request.url.path == "/graphql" and request.method == "POST"
        assert request.headers["Authorization"] == TEST_OPENAPI
        body = json.loads(request.content)
        self.requests.append(body)
        await asyncio.sleep(0)
        if self.fail_transport:
            self.fail_transport -= 1
            raise httpx.ConnectError("boom")
        if self.status != 200:
            return httpx.Response(self.status, json={"errors": [{"message": "test-only-secret: never echo"}]},
                                  headers=self.headers)
        if "getBars" in body["query"]:
            return httpx.Response(200, json=self.bars)
        if "getTokenPrices" in body["query"]:
            return httpx.Response(200, json=self.prices)
        return httpx.Response(200, json=self.snapshot)


@pytest.fixture
async def codex():
    c = Codex()
    async with httpx.AsyncClient(transport=httpx.MockTransport(c.handle)) as client:
        m = Market(client=client, api_key=TEST_OPENAPI, retry_delays=(0, 0))
        app.dependency_overrides[get_market] = lambda: m
        yield c, m
    app.dependency_overrides.pop(get_market, None)


async def launch(client, network="solana", mint=TOKEN, status="live", email="m@example.com"):
    h = await signup(client, email, main=False)
    async with get_sessionmaker()() as db:
        uid = (await db.execute(select(User.id).where(User.email == email))).scalar_one()
        row = Launch(user_id=uid, name="Review", symbol="REV", launchpad="pumpfun", network=network,
                     status=status, mint_address=mint)
        db.add(row)
        await db.commit()
        return h, row.id


# --- normalisation ----------------------------------------------------------------------------------------

def test_snapshot_maps_codex_fields_without_derivation():
    out = normalize_snapshot(snapshot_body()["data"], "solana", TOKEN)
    m = out["metrics"]
    assert out["state"] == "ok" and out["metric_scope"] == "token" and out["symbol"] == "WSOL"
    assert m["price_usd"] == 120.5 and m["holder_count"] == 4321
    assert m["market_cap_usd"] == 120_500_000 and m["fdv_usd"] == 144_600_000  # circulating vs fully diluted
    assert m["price_change"]["h24"] == pytest.approx(20.0) and m["price_change"]["h1"] == pytest.approx(-1.2)
    assert m["txns"]["h24"] == {"buys": 120, "sells": 80, "swaps": 200} and m["volume_usd"]["h4"] == 9000
    assert set(m["price_change"]) == {"m5", "h1", "h4", "h12", "h24"}  # only windows Codex publishes
    assert out["pool"]["address"] == POOL and out["pool"]["quote_symbol"] == "USDC"


@pytest.mark.parametrize("field,value", [("priceUSD", None), ("priceUSD", "NaN"), ("marketCap", "9223372036854775806"),
                                         ("holders", "12.5"), ("change24", ""), ("volume24", "-4")])
def test_missing_or_malformed_values_stay_missing(field, value):
    out = normalize_snapshot(snapshot_body(rows=[token_row(**{field: value})])["data"], "solana", TOKEN)
    m = out["metrics"]
    flat = {"priceUSD": m["price_usd"], "marketCap": m["fdv_usd"], "holders": m["holder_count"],
            "change24": m["price_change"]["h24"], "volume24": m["volume_usd"]["h24"]}
    assert flat[field] is None  # never zero, never a neighbour's value


def test_wrong_token_network_and_pairs_are_ignored():
    rows = [token_row(token=QUOTE), token_row(network_id=1)]
    pairs = [pair(network_id=1), pair(token=QUOTE), pair(address="not-an-address")]
    assert normalize_snapshot(snapshot_body(rows=rows, pairs=pairs)["data"], "solana", TOKEN)["state"] == "not_indexed"


def test_token_without_pool_and_pool_ordering():
    assert normalize_snapshot(snapshot_body(pairs=[])["data"], "solana", TOKEN)["state"] == "no_pool"
    out = normalize_snapshot(snapshot_body(pairs=[pair(POOL, "10"), pair(POOL2, "900", ours="backing")])["data"],
                             "solana", TOKEN)
    assert [p["address"] for p in out["pairs"]] == [POOL2, POOL] and out["pairs"][0]["quote_symbol"] == "USDC"


def test_bars_are_validated_not_repaired():
    body = bars_body(4)["data"]
    body["getBars"]["h"][1] = 0.5  # high below open/close: dropped
    body["getBars"]["t"][3] = body["getBars"]["t"][2]  # out of order: dropped
    bars = normalize_bars(body, "15")
    assert [b["t"] for b in bars] == [body["getBars"]["t"][0], body["getBars"]["t"][2]]
    assert normalize_bars({"getBars": None}, "15") == []


# --- route behaviour --------------------------------------------------------------------------------------

async def test_market_route_serves_codex_only(client, codex):
    c, m = codex
    h, lid = await launch(client)
    r = (await client.get(f"/launches/{lid}/market", headers=h)).json()
    assert r["state"] == "ok" and r["provider"] == "Codex" and r["metrics"]["price_usd"] == 120.5
    assert r["chart"]["available"] and "15" in r["chart"]["resolutions"]
    assert len(c.requests) == 1 and c.requests[0]["variables"]["ids"] == [f"{TOKEN}:{NET}"]


async def test_other_users_cannot_read_a_launch_market(client, codex):
    _, lid = await launch(client)
    other = await signup(client, "o@example.com", main=False)
    assert (await client.get(f"/launches/{lid}/market", headers=other)).status_code == 404
    assert (await client.get(f"/launches/{lid}/market/candles", headers=other)).status_code == 404


async def test_unsupported_networks_and_missing_key_make_no_calls(client, codex):
    c, m = codex
    h, lid = await launch(client, network="near", mint="token.near")
    assert (await client.get(f"/launches/{lid}/market", headers=h)).json()["state"] == "unsupported"
    m.api_key = ""
    h2, lid2 = await launch(client, email="k@example.com")
    assert (await client.get(f"/launches/{lid2}/market", headers=h2)).json()["state"] == "not_configured"
    assert c.requests == []


async def test_provider_errors_are_sanitised_and_cooled_down(client, codex):
    c, m = codex
    c.status, c.headers = 429, {"retry-after": "90"}
    h, lid = await launch(client)
    r = (await client.get(f"/launches/{lid}/market", headers=h)).json()
    assert r["state"] == "error" and r["error_kind"] == "rate_limited" and r["retry_after_s"] >= 89
    assert "test-only-secret" not in json.dumps(r)
    calls = len(c.requests)
    await client.get(f"/launches/{lid}/market", headers=h)
    assert len(c.requests) == calls  # paused, not hammered


async def test_access_denied_names_the_operator_fix(codex):
    c, m = codex
    c.status = 403
    with pytest.raises(MarketError) as e:
        await m.snapshot("solana", TOKEN)
    assert e.value.kind == "access_denied" and "CODEX_API_KEY" in str(e.value)


async def test_concurrent_requests_coalesce_and_cache(codex):
    c, m = codex
    results = await asyncio.gather(*(m.snapshot("solana", TOKEN) for _ in range(5)))
    assert len(c.requests) == 1 and all(r["state"] == "ok" for r in results)
    await m.snapshot("solana", TOKEN)
    assert len(c.requests) == 1


async def test_stale_data_is_labelled_when_a_refresh_fails(codex, monkeypatch):
    c, m = codex
    await m.snapshot("solana", TOKEN)
    monkeypatch.setattr(mk, "CACHE_TTL", 0)
    c.status = 503
    r = await m.snapshot("solana", TOKEN)
    assert r["stale"] and r["error_kind"] == "unavailable" and r["metrics"]["price_usd"] == 120.5


async def test_transport_retries_are_bounded(codex):
    c, m = codex
    c.fail_transport = 2
    assert (await m.snapshot("solana", TOKEN))["state"] == "ok" and len(c.requests) == 3
    c.fail_transport = 5
    with pytest.raises(MarketError):
        await m.snapshot("solana", QUOTE)
    assert len(c.requests) == 6  # three attempts per lookup, then stop


async def test_request_budget_is_enforced(codex, monkeypatch):
    from app.config import get_settings

    c, m = codex
    monkeypatch.setattr(get_settings(), "codex_requests_per_minute", 1)
    await m.snapshot("solana", TOKEN)
    with pytest.raises(MarketError) as e:
        await m.snapshot("solana", QUOTE)
    assert e.value.kind == "request_budget"


async def test_candles_are_token_level_by_default_and_pool_level_on_request(client, codex):
    c, m = codex
    h, lid = await launch(client)
    r = (await client.get(f"/launches/{lid}/market/candles?resolution=15", headers=h)).json()
    assert r["state"] == "ok" and r["scope"] == "token" and len(r["bars"]) == 3 and r["currency"] == "USD"
    q = c.requests[-1]["variables"]
    assert q["symbol"] == f"{TOKEN}:{NET}" and q["symbolType"] == "TOKEN" and q["resolution"] == "15"
    r = (await client.get(f"/launches/{lid}/market/candles?resolution=60&pool={POOL}", headers=h)).json()
    assert r["scope"] == "pool" and c.requests[-1]["variables"]["symbolType"] == "POOL"
    assert c.requests[-1]["variables"]["quoteToken"] == "token1"


async def test_candles_refuse_unknown_pools_and_resolutions(client, codex):
    c, m = codex
    h, lid = await launch(client)
    r = (await client.get(f"/launches/{lid}/market/candles?pool={POOL2}", headers=h)).json()
    assert r["state"] == "error" and r["error_kind"] == "unknown_pool"
    r = (await client.get(f"/launches/{lid}/market/candles?resolution=7", headers=h)).json()
    assert r["state"] == "error" and r["error_kind"] == "invalid_resolution"


async def test_no_bars_is_reported_not_invented(client, codex):
    c, m = codex
    c.bars = {"data": {"getBars": {"t": [], "o": [], "h": [], "l": [], "c": [], "volume": [], "s": "no_data"}}}
    h, lid = await launch(client)
    r = (await client.get(f"/launches/{lid}/market/candles", headers=h)).json()
    assert r["state"] == "no_bars" and r["bars"] == []


async def test_draft_without_token_has_no_market(client, codex):
    c, m = codex
    h, lid = await launch(client, mint=None, status="draft")
    assert (await client.get(f"/launches/{lid}/market", headers=h)).json()["state"] == "no_token"
    assert (await client.get(f"/launches/{lid}/market/candles", headers=h)).json()["state"] == "no_token"
    assert c.requests == []


# --- prices -----------------------------------------------------------------------------------------------

async def test_prices_reject_quotes_from_the_wrong_time(codex, monkeypatch):
    c, m = codex
    monkeypatch.undo()  # the suite's offline fixture stubs prices(); exercise the real one here
    asked = 1_759_000_000
    c.prices = {"data": {"getTokenPrices": [
        {"address": TOKEN, "networkId": NET, "priceUsd": 150.0, "timestamp": asked - 60},
        {"address": TOKEN, "networkId": NET, "priceUsd": 140.0, "timestamp": asked - 5 * 86_400}]}}
    out = await Market.prices(m, [("solana", TOKEN, asked), ("solana", TOKEN, asked + 1)])
    assert out == {("solana", TOKEN, asked): 150.0}


async def test_daily_prices_leave_unpriced_days_missing(monkeypatch):
    from app.services import portfolio as pf

    class Fake:
        async def prices(self, inputs):
            return {inputs[0]: 100.0}  # only the first day is priced

    out = await pf.daily_prices("SOL", ["2026-09-01", "2026-09-02", "2026-09-03"], Fake())
    assert out == {"2026-09-01": 100.0}  # no nearest-day substitution


async def test_operator_check_never_prints_data(codex, monkeypatch, capsys):
    from app import market_check

    c, m = codex
    monkeypatch.setattr(market_check, "Market", lambda: m)
    assert await market_check.check("solana", TOKEN) == 0
    output = capsys.readouterr().out
    assert TOKEN not in output and "price" not in output and "test-only-secret" not in output
