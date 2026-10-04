import httpx
import pytest

from app.services.market import Market, MarketError, _normalize_dex

TOKEN, QUOTE, POOL, SECOND = ["0x"+c*40 for c in "abcd"]


def pair(**changes):
    return {**dict(chainId="base", pairAddress=POOL, dexId="test", baseToken={"address": TOKEN, "symbol": "TEST"},
        quoteToken={"address": QUOTE, "symbol": "ETH"}, liquidity={"usd": 17}, priceUsd="2", priceNative=".01",
        fdv=1000, marketCap=None, txns={"h24": {"buys": 3, "sells": 4}}), **changes}


async def test_multiple_pools_are_chain_and_address_bound_and_sorted():
    payload = [pair(chainId="ethereum", liquidity={"usd":99999}), pair(), pair(),
               pair(pairAddress=SECOND, liquidity={"usd":90}), pair(baseToken={"address": SECOND})]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        result = await Market(client=client, api_key="").snapshot("base", TOKEN)
    assert [p["pool"]["address"] for p in result["pairs"]] == [SECOND, POOL]
    assert result["metric_scope"] == "pair" and result["provider"] == "DexScreener"
    assert result["metrics"]["market_cap_usd"] is None and result["metrics"]["fdv_usd"] == 1000


def test_quote_side_does_not_mislabel_base_token_price_or_market_cap():
    data = _normalize_dex(pair(), "base", QUOTE)
    assert data["pool"]["orientation"] == "quote" and data["pool"]["quote_address"] == TOKEN
    assert data["metrics"]["price_usd"] is None and data["metrics"]["price_native"] is None
    assert data["metrics"]["fdv_usd"] is None
    assert data["metrics"]["txns"]["h24"]["swaps"] == 7


@pytest.mark.parametrize("changes", [{"quoteToken": "broken"}, {"txns": [1]}, {"txns": {"h24": [1]}},
    {"priceChange": [1]}, {"volume": "broken"}])
def test_malformed_provider_shapes_fail_honestly(changes):
    with pytest.raises(MarketError): _normalize_dex(pair(**changes), "base", TOKEN)
