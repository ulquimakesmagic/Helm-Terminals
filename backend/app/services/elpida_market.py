"""Read the Elpida pool directly for price, fixed-supply market cap and trading volume."""
import asyncio
from collections import OrderedDict
from decimal import Decimal
import time

from eth_abi import decode, encode
from eth_utils import keccak

from ..chains.base import AdapterError
from ..chains.elpida import ZERO
from ..chains.evm import EvmRPCError, EvmRPCUnavailable
from .market import WINDOWS

SWAP = "0x" + keccak(text="Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)").hex()
_cache: OrderedDict[tuple[str, str], tuple[float, dict]] = OrderedDict()


async def _timestamp(rpc, number) -> int:
    block = await rpc.call("eth_getBlockByNumber", [number, False])
    return int(block["timestamp"], 16)


async def _volume_24h(rpc, manager: str, pool_id: str, deployed_at: int) -> tuple[int, int, int]:
    head = await rpc.block_number()
    since = await _timestamp(rpc, hex(head)) - 86_400
    lower, upper = deployed_at, head
    while lower < upper:
        mid = (lower + upper) // 2
        if await _timestamp(rpc, hex(mid)) < since:
            lower = mid + 1
        else:
            upper = mid

    slots = asyncio.Semaphore(4)

    async def logs(start, end):
        request = {"address": manager, "topics": [SWAP, pool_id], "fromBlock": hex(start), "toBlock": hex(end)}
        try:
            async with slots:
                return await rpc.call("eth_getLogs", [request])
        except EvmRPCError:
            # Providers can cap the block range on log queries. Fail with an unknown volume if even
            # a short range cannot be read. Never present a partial window as a complete total.
            if end - start <= 2048:
                raise
            mid = (start + end) // 2
            first, second = await asyncio.gather(logs(start, mid), logs(mid + 1, end))
            return first + second

    amounts = [decode(["int128", "int128", "uint160", "uint128", "int24", "uint24"],
                      bytes.fromhex(event["data"][2:]))[0] for event in await logs(lower, head)]
    return sum(map(abs, amounts)), sum(amount < 0 for amount in amounts), sum(amount > 0 for amount in amounts)


async def snapshot(adapter, token: str) -> dict:
    cache_key = adapter.network, token.lower()
    cached = _cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < 60:
        return cached[1]
    deployment, key = await adapter.pool_key(token)
    pool = keccak(encode(["(address,address,uint24,int24,address)"], [key]))
    # Uniswap v4 StateLibrary.getSlot0: PoolManager stores its pool mapping at slot 6.
    slot = keccak(encode(["bytes32", "uint256"], [pool, 6]))
    rpc = adapter.rpc
    pool_id = "0x" + pool.hex()
    quote, meta, (state,) = await asyncio.gather(
        adapter.asset_info(key[0]), rpc.erc20_meta(token),
        adapter._read(adapter.config.pool_manager, "extsload(bytes32)", ["bytes32"], ["bytes32"], [slot]),
    )
    sqrt_price = int.from_bytes(state, "big") & ((1 << 160) - 1)
    if not sqrt_price:
        raise AdapterError("Elpida's pool did not return a price.")
    raw_price = Decimal(2**192) / sqrt_price**2
    spot = raw_price.scaleb(meta["decimals"] - quote["decimals"])
    if key[0].lower() == adapter.config.stable.lower():
        usd_per_quote = Decimal(1)
    elif key[0] == ZERO:
        try:
            # The $10 fee read checks the same oracle's freshness in the verified factory.
            await adapter._read(deployment.factory, "launchFee()", ["uint256"])
            value, _, decimals = await adapter._read(deployment.factory, "price()", ["uint256", "uint256", "uint8"])
            usd_per_quote = Decimal(value).scaleb(-decimals)
        except AdapterError:
            usd_per_quote = None
    else:
        usd_per_quote = None

    price_usd = spot * usd_per_quote if usd_per_quote is not None else None
    cap_usd = price_usd * Decimal(meta["total_supply"]).scaleb(-meta["decimals"]) if price_usd is not None else None
    try:
        volume, buys, sells = await asyncio.wait_for(
            _volume_24h(rpc, adapter.config.pool_manager, pool_id, deployment.start_block), timeout=12)
    except (EvmRPCError, EvmRPCUnavailable, TimeoutError, ValueError, KeyError):
        volume = buys = sells = None
    volume_quote = Decimal(volume).scaleb(-quote["decimals"]) if volume is not None else None
    volume_usd = volume_quote * usd_per_quote if volume_quote is not None and usd_per_quote is not None else None
    pair = {"address": pool_id, "dex": "Uniswap v4 · Elpida", "quote_symbol": quote["asset"],
            "quote_address": quote["token"], "liquidity_usd": None, "volume_usd_24h": float(volume_usd) if volume_usd is not None else None}
    metrics = {"price_usd": float(price_usd) if price_usd is not None else None,
               "price_quote": float(spot), "quote_symbol": quote["asset"],
               "market_cap_usd": float(cap_usd) if cap_usd is not None else None,
               "fdv_usd": None, "liquidity_usd": None, "holder_count": None,
               "price_change": dict.fromkeys(WINDOWS),
               "volume_usd": {**dict.fromkeys(WINDOWS), "h24": pair["volume_usd_24h"]},
               "volume_quote_24h": float(volume_quote) if volume_quote is not None else None,
               "txns": {key: {"buys": buys if key == "h24" else None, "sells": sells if key == "h24" else None,
                              "swaps": buys + sells if key == "h24" and buys is not None else None} for key in WINDOWS}}
    result = {"state": "ok", "provider": "Elpida pool", "fetched_at": int(time.time()),
              "pool": pair, "pairs": [pair], "metrics": metrics, "symbol": meta["symbol"], "name": meta["name"],
              "note": "Pool market cap uses the fixed supply. Stablecoin USD values use the dollar peg."}
    _cache[cache_key] = time.monotonic(), result
    _cache.move_to_end(cache_key)
    if len(_cache) > 256:
        _cache.popitem(last=False)
    return result
