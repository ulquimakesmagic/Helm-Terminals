"""GMGN contract, ownership, isolation, missing data and shared quota regression tests."""
import asyncio
import copy
import time
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import get_sessionmaker
from app.main import app
from app.models import Launch, User
from app.services import market as mk
from app.services.market import Market, MarketError, get_market
from .conftest import signup

TOKEN = "So11111111111111111111111111111111111111112"
POOL = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2"
QUOTE = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def token_data(token=TOKEN):
    # Official token/info shape, independently checked against the public demo API.
    return {"address": token, "symbol": "WSOL", "holder_count": 120,
            "circulating_supply": "1000000", "total_supply": "1200000", "liquidity": "160000",
            "price": {"address": token, "price": "120", "price_24h": "100", "volume_24h": "50000",
                      "buys_24h": 120, "sells_24h": 0, "swaps_24h": 120},
            "pool": {"pool_address": POOL, "base_address": token, "quote_address": QUOTE,
                     "quote_symbol": "USDC", "exchange": "ray_v2", "liquidity": "150000"},
            "link": {"gmgn": f"https://gmgn.ai/sol/token/{token}"}}


class Provider:
    def __init__(self):
        self.data = token_data()
        self.status = 200
        self.code = 0
        self.headers = {}
        self.requests = []

    async def handle(self, request):
        self.requests.append(request)
        assert request.url.host == "openapi.gmgn.ai"
        assert request.url.path == "/v1/token/info" and request.method == "GET"
        assert request.headers["X-APIKEY"] == "test-only-secret"
        assert "X-Signature" not in request.headers
        assert abs(int(request.url.params["timestamp"]) - time.time()) < 10
        UUID(request.url.params["client_id"])
        await asyncio.sleep(0)
        return httpx.Response(self.status, json={"code": self.code, "data": self.data,
            "message": "test-only-secret: never echo this error"}, headers=self.headers)


@pytest.fixture
async def provider():
    p = Provider()
    async with httpx.AsyncClient(transport=httpx.MockTransport(p.handle)) as client:
        m = Market(client=client, api_key="test-only-secret")
        app.dependency_overrides[get_market] = lambda: m
        yield p, m
    app.dependency_overrides.pop(get_market, None)


async def launch(client, network="solana", mint=TOKEN, status="live", headers=None):
    h = headers or await signup(client, main=False)
    async with get_sessionmaker()() as db:
        uid = (await db.execute(select(User.id))).scalars().first()
        row = Launch(user_id=uid, name="Review", symbol="REV", launchpad="pumpfun", network=network,
                     status=status, mint_address=mint)
        db.add(row)
        await db.commit()
        return h, row.id


async def test_market_contract_and_no_other_provider(client, provider):
    p, m = provider
    h, lid = await launch(client)
    r = await client.get(f"/launches/{lid}/market", headers=h)
    body = r.json()
    assert body["state"] == "ok" and body["source"] == "GMGN"
    assert body["chart_chain"] == "sol" and body["token"] == TOKEN and "Indicative" in body["note"]
    assert body["pool"]["address"] == POOL and body["pool"]["liquidity_usd"] == 150000
    metrics = body["metrics"]
    assert metrics["price_usd"] == 120 and metrics["price_change"]["h24"] == pytest.approx(20)
    assert metrics["txns"]["h24"] == {"buys": 120, "sells": 0, "swaps": 120}
    assert metrics["volume_usd"]["h1"] is None and metrics["txns"]["h1"]["buys"] is None
    assert metrics["market_cap_usd"] == 120 * 1_000_000 and metrics["fdv_usd"] == 120 * 1_200_000
    assert metrics["liquidity_usd"] == 160000
    assert "test-only-secret" not in r.text
    await client.get(f"/launches/{lid}/market", headers=h)
    assert len(p.requests) == 1
    assert (await client.get(f"/launches/{lid}/market/candles?pool=x", headers=h)).status_code == 410
    assert len(p.requests) == 1
    assert (await client.get(f"/launches/{lid}/market")).status_code == 401
    other = await signup(client, email="other@example.com", main=False)
    assert (await client.get(f"/launches/{lid}/market", headers=other)).status_code == 404


async def test_missing_key_keeps_chart_and_unknown_chains_make_no_calls(client, provider):
    p, m = provider
    m.api_key = ""
    h, lid = await launch(client)
    r = (await client.get(f"/launches/{lid}/market", headers=h)).json()
    assert r["state"] == "not_configured" and r["chart_chain"] == "sol" and r["metrics"] is None
    for chain in ("tron", "near", "ton", "unknown"):
        assert (await m.snapshot(chain, "unimportant"))["state"] == "unsupported"
    assert not p.requests


async def test_new_token_draft_and_copied_source(client, provider):
    p, m = provider
    p.data = None
    h, lid = await launch(client)
    r = (await client.get(f"/launches/{lid}/market", headers=h)).json()
    assert r["state"] == "no_pool" and r["pool"] is None and r["metrics"] is None
    _, draft = await launch(client, mint=None, status="draft", headers=h)
    assert (await client.get(f"/launches/{draft}/market", headers=h)).json()["state"] == "no_token"
    async with get_sessionmaker()() as db:
        row = await db.get(Launch, draft)
        row.network = "robinhood"
        row.launchpad_params = {"cloned_from": {"network": "solana", "address": TOKEN, "copied": ["name"]}}
        await db.commit()
    r = (await client.get(f"/launches/{draft}/market", headers=h)).json()
    assert r["whose"] == "source" and r["network"] == "solana" and r["chart_chain"] == "sol"
    async with get_sessionmaker()() as db:
        row = await db.get(Launch, draft)
        row.status, row.mint_address = "live", "0x" + "ab" * 20
        await db.commit()
    m.api_key = ""
    r = (await client.get(f"/launches/{draft}/market", headers=h)).json()
    assert r["whose"] == "launch" and r["network"] == "robinhood" and r["chart_chain"] is None


async def test_stale_expiry_and_rate_limit_cooldown(provider):
    p, m = provider
    await m.snapshot("solana", TOKEN)
    entry = m._cache[("solana", TOKEN)]
    entry.at -= 120
    p.status = 429
    p.headers = {"x-ratelimit-reset": str(int(time.time()) + 180)}
    r = await m.snapshot("solana", TOKEN)
    assert r["stale"] and r["age_s"] >= 120 and r["metrics"]["price_usd"] == 120
    assert m._backoff_until - time.monotonic() > 170
    assert len(p.requests) == 2
    await m.snapshot("solana", TOKEN)
    assert len(p.requests) == 2
    entry = m._cache[("solana", TOKEN)]
    entry.at -= 300
    with pytest.raises(MarketError, match="rate limit"):
        await m.snapshot("solana", TOKEN)
    assert len(p.requests) == 2


@pytest.mark.parametrize("status,code", [(503, 0), (200, 500), (403, 0)])
async def test_provider_errors_are_sanitized_and_negatively_cached(provider, status, code):
    p, m = provider
    p.status, p.code = status, code
    for _ in range(2):
        with pytest.raises(MarketError) as caught:
            await m.snapshot("solana", TOKEN)
        assert "test-only-secret" not in str(caught.value)
    assert len(p.requests) == 1


async def test_requests_coalesce_and_budget_is_shared(provider, monkeypatch):
    p, m = provider
    monkeypatch.setattr(get_settings(), "gmgn_requests_per_minute", 1)
    results = await asyncio.gather(*(m.snapshot("solana", TOKEN) for _ in range(10)))
    assert all(r["state"] == "ok" for r in results) and len(p.requests) == 1
    with pytest.raises(MarketError) as error:
        await m.snapshot("solana", QUOTE)
    assert error.value.kind == "request_budget" and len(p.requests) == 1
    assert 0 < error.value.details()["retry_after_s"] <= 60


async def test_chain_mapping_address_validation_and_case_sensitive_cache(provider):
    p, m = provider
    for network, chain in mk.API_CHAIN.items():
        token = TOKEN if network == "solana" else "0x" + "aB" * 20
        p.data = token_data(token)
        p.data["pool"] = {}
        await m.snapshot(network, token)
        assert p.requests[-1].url.params["chain"] == chain
        assert p.requests[-1].url.params["address"] == token
    variant = TOKEN.replace("So", "so")
    p.data = token_data(variant)
    await m.snapshot("solana", variant)
    assert ("solana", TOKEN) in m._cache and ("solana", variant) in m._cache
    for network, token in [("base", TOKEN), ("solana", "../bad"), ("bsc", "0x123?api-key=bad")]:
        with pytest.raises(MarketError) as error:
            await m.snapshot(network, token)
        assert error.value.kind == "invalid_token"


@pytest.mark.parametrize("field,value", [("price", "NaN"), ("price", "Infinity"), ("price", -1), ("buys_24h", True), ("buys_24h", 2.5)])
def test_missing_and_malformed_numbers_remain_missing(field, value):
    raw = token_data()
    raw["price"][field] = value
    out = mk._normalize(raw, "solana", TOKEN)["metrics"]
    assert (out["price_usd"] if field == "price" else out["txns"]["h24"]["buys"]) is None


def test_supply_derivations_degrade_to_missing():
    raw = token_data()
    for field in ("circulating_supply", "total_supply"):
        data = copy.deepcopy(raw)
        data.pop(field)
        out = mk._normalize(data, "solana", TOKEN)["metrics"]
        assert out["market_cap_usd" if field == "circulating_supply" else "fdv_usd"] is None
    for bad in ("NaN", "Infinity", "-5", True, ""):
        out = mk._normalize({**raw, "circulating_supply": bad}, "solana", TOKEN)["metrics"]
        assert out["market_cap_usd"] is None
    assert mk._normalize({**raw, "liquidity": None}, "solana", TOKEN)["metrics"]["liquidity_usd"] is None


def test_mismatched_tokens_pools_and_links():
    raw = token_data()
    for mutate in (lambda r: r.update(address=QUOTE), lambda r: r["price"].update(address=QUOTE),
                   lambda r: r["pool"].update(base_address=QUOTE)):
        data = copy.deepcopy(raw)
        mutate(data)
        with pytest.raises(MarketError):
            mk._normalize(data, "solana", TOKEN)
    raw["link"]["gmgn"] = "https://gmgn.ai.evil.example/secret"
    assert mk._normalize(raw, "solana", TOKEN)["gmgn_url"] is None
    raw["pool"] = None
    assert mk._normalize(raw, "solana", TOKEN)["state"] == "no_pool"


@pytest.mark.parametrize("payload", [[], {"code": 0}, {"code": 0, "data": [1]}, {"code": 500, "error": {"details": "secret"}}])
async def test_malformed_envelopes_fail_without_leaking_or_crashing(payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        with pytest.raises(MarketError) as error:
            await Market(client=client, api_key="test-only").snapshot("solana", TOKEN)
        assert "secret" not in str(error.value)


async def test_cache_is_bounded(provider, monkeypatch):
    p, m = provider
    monkeypatch.setattr(mk, "CACHE_LIMIT", 2)
    for token in (TOKEN, POOL, QUOTE):
        p.data = token_data(token)
        await m.snapshot("solana", token)
    assert len(m._cache) == 2 and ("solana", TOKEN) not in m._cache


@pytest.mark.parametrize("status,code", [(401, 0), (403, 0), (200, 401), (200, "403")])
async def test_access_denial_survives_cooldown_across_tokens_and_recovers(client, provider, monkeypatch, caplog, status, code):
    p, m = provider
    clock = [time.monotonic()]
    monkeypatch.setattr(mk, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
    p.status, p.code = status, code
    h, lid = await launch(client)
    first = (await client.get(f"/launches/{lid}/market", headers=h)).json()
    assert first["error_kind"] == "access_denied" and first["retry_after_s"] == 60
    clock[0] += 20  # Beyond the old 15-second negative cache: the original regression.
    second = (await client.get(f"/launches/{lid}/market", headers=h)).json()
    assert second["error_kind"] == "access_denied" and second["retry_after_s"] == 40
    assert second["error"] == first["error"]
    with pytest.raises(MarketError) as caught:
        await m.snapshot("solana", QUOTE)
    assert caught.value.kind == "access_denied"
    assert caught.value.details()["retry_after_s"] == 40
    assert len(p.requests) == 1
    logs = [r for r in caplog.records if r.name == "app.services.market"]
    assert len(logs) == 1 and f"http_status={status}" in logs[0].message
    assert "test-only-secret" not in logs[0].message
    clock[0] += 41
    p.status, p.code = 200, 0
    recovered = (await client.get(f"/launches/{lid}/market", headers=h)).json()
    assert recovered["state"] == "ok" and "error_kind" not in recovered
    assert len(p.requests) == 2 and m._backoff_error is None


async def test_stale_access_error_keeps_cause_and_retry_until_success(provider, monkeypatch):
    p, m = provider
    clock = [time.monotonic()]
    monkeypatch.setattr(mk, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
    await m.snapshot("solana", TOKEN)
    clock[0] += 65
    p.status = 403
    stale = await m.snapshot("solana", TOKEN)
    assert stale["stale"] and stale["error_kind"] == "access_denied" and stale["retry_after_s"] == 60
    clock[0] += 20
    stale = await m.snapshot("solana", TOKEN)
    assert stale["metrics"]["price_usd"] == 120 and stale["retry_after_s"] == 40
    assert stale["error_kind"] == "access_denied" and len(p.requests) == 2
    clock[0] += 300
    with pytest.raises(MarketError) as caught:
        await m.snapshot("solana", TOKEN)
    assert caught.value.kind == "access_denied"  # Expired metrics are no longer exposed.


async def test_genuine_rate_limit_exposes_retry_delay_and_recovers(provider, monkeypatch):
    p, m = provider
    clock = [time.monotonic()]
    monkeypatch.setattr(mk, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
    p.status, p.headers = 429, {"retry-after": "120"}
    with pytest.raises(MarketError) as caught:
        await m.snapshot("solana", TOKEN)
    assert caught.value.kind == "rate_limited" and caught.value.details()["retry_after_s"] == 120
    clock[0] += 70
    with pytest.raises(MarketError) as caught:
        await m.snapshot("solana", QUOTE)
    assert caught.value.kind == "rate_limited" and caught.value.details()["retry_after_s"] == 50
    assert len(p.requests) == 1
    clock[0] += 51
    p.status = 200
    assert (await m.snapshot("solana", TOKEN))["state"] == "ok"
    assert len(p.requests) == 2


@pytest.mark.parametrize("payload", [None, {"code": 403, "message": "test-only-secret"},
    {"code": "test-only-secret", "error": "test-only-secret", "message": "private contents"}])
async def test_diagnostics_only_include_safe_fields(caplog, payload):
    response = httpx.Response(403, json=payload) if payload is not None else httpx.Response(403, text="private contents test-only-secret")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
        with pytest.raises(MarketError) as error:
            await Market(client=client, api_key="test-only-secret").snapshot("solana", TOKEN)
    assert error.value.kind == "access_denied"
    logs = [r.message for r in caplog.records if r.name == "app.services.market"]
    assert len(logs) == 1 and "http_status=403" in logs[0]
    assert f"response_format={'non_json' if payload is None else 'json'}" in logs[0]
    assert "test-only-secret" not in logs[0] and "private contents" not in logs[0]


@pytest.mark.parametrize("status,configured,exit_code", [(200, True, 0), (403, True, 1), (200, False, 1)])
async def test_operator_check_uses_one_read_only_call_and_never_prints_data(provider, monkeypatch, capsys, status, configured, exit_code):
    from app import market_check
    p, m = provider
    p.status = status
    if not configured:
        m.api_key = ""
    monkeypatch.setattr(market_check, "Market", lambda: m)
    assert await market_check.check("solana", TOKEN) == exit_code
    output = capsys.readouterr()
    assert "test-only-secret" not in output.out + output.err
    assert TOKEN not in output.out and "price" not in output.out
    assert len(p.requests) == (1 if configured else 0)
