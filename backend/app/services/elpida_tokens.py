"""Discover Elpida launches and verify their identity independently of the display feed."""
import asyncio
import json
import re
import time

from eth_abi import decode
from eth_abi.exceptions import DecodingError
from eth_utils import to_checksum_address
import httpx

from ..chains.base import AdapterError
from ..chains.elpida import ADDRESS, CID, LAUNCHED, METADATA_PUBLISHED, NETWORKS, SOCIALS, calldata, https_link
from ..chains.evm import EVM_CHAINS, EvmRPCError, EvmRPCUnavailable
from .token_meta import MAX_JSON_BYTES, _fetch

SITE = "https://elpida.fun"
SCANNER = SITE + "/api/public/scanner"
PINATA_GATEWAY = "https://gateway.pinata.cloud/ipfs/"
TX = re.compile(r"0x[0-9a-fA-F]{64}")
_cache: dict[tuple[str, int], tuple[float, list[dict]]] = {}
_locks = {network: asyncio.Lock() for network in NETWORKS}


def token_url(network: str, address: str) -> str:
    return f"{SITE}/#/token/{NETWORKS[network].slug}/{address.lower()}"


def parse_token(row, network: str) -> dict | None:
    if not isinstance(row, dict) or row.get("chainId") != EVM_CHAINS[network]["chain_id"]:
        return None
    address = row.get("address")
    if not isinstance(address, str) or not ADDRESS.fullmatch(address):
        return None
    if row.get("network", NETWORKS[network].slug) != NETWORKS[network].slug:
        return None
    name, symbol = row.get("name"), row.get("symbol")
    if not isinstance(name, str) or not isinstance(symbol, str) or not name or not symbol:
        return None
    links = row.get("links")
    return {"address": to_checksum_address(address), "network": network, "launchpad": f"elpida-{network}",
            "name": name[:64], "symbol": symbol[:10], "image": https_link(row.get("image")) or None,
            "description": row["description"][:1000] if isinstance(row.get("description"), str) else "",
            "metadata_uri": row.get("metadataURI") if isinstance(row.get("metadataURI"), str) else None,
            "socials": {k: href for k in SOCIALS if isinstance(links, dict) and (href := https_link(links.get(k)))},
            "launch_tx": row["launchTx"] if isinstance(row.get("launchTx"), str) and TX.fullmatch(row["launchTx"]) else None,
            "created_at": row["createdAt"] if type(row.get("createdAt")) is int and row["createdAt"] >= 0 else None,
            "url": token_url(network, address)}


def parse_feed(payload, network: str) -> list[dict]:
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("tokens"), list):
        raise AdapterError("Elpida's discovery feed returned an unexpected format.")
    tokens = {}
    for item in payload["tokens"][:500]:
        if row := parse_token(item, network):
            tokens.setdefault(row["address"], row)
    return list(tokens.values())


async def _public_json(url: str, params=None, max_bytes: int = 2_000_000):
    try:
        async with httpx.AsyncClient(timeout=8) as client, client.stream("GET", url, params=params) as response:
            if response.status_code == 404:
                return None
            response.raise_for_status()
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > max_bytes:
                    raise AdapterError("Elpida's discovery feed exceeded the response size limit.")
            return json.loads(raw)
    except (httpx.HTTPError, ValueError) as error:
        raise AdapterError("Elpida's discovery feed is temporarily unavailable.") from error


async def _scanner(path: str, params=None):
    return await _public_json(SCANNER + path, params)


def _launch_event(receipt: dict | None, deployments, token: str) -> dict | None:
    if not receipt or receipt.get("status") != "0x1":
        return None
    address_topic = "0x" + token[2:].lower().rjust(64, "0")
    allowed = {d.factory.lower(): d.start_block for d in deployments}
    for event in receipt.get("logs", []):
        factory = event.get("address", "").lower()
        topics = event.get("topics", [])
        if factory in allowed and len(topics) == 4 and topics[0].lower() == LAUNCHED and topics[2].lower() == address_topic:
            if int(receipt["blockNumber"], 16) >= allowed[factory]:
                return event
    return None


def _published_uri(receipt: dict, launch: dict) -> str | None:
    for log in receipt["logs"]:
        topics = log.get("topics", [])
        if (log.get("address", "").lower() == launch["address"].lower() and len(topics) == 2
                and topics[0].lower() == METADATA_PUBLISHED and topics[1].lower() == launch["topics"][2].lower()):
            try:
                return decode(["string"], bytes.fromhex(log["data"][2:]))[0]
            except (DecodingError, ValueError):
                return None
    return None


async def _recent_launch_events(adapter, deployment, limit: int) -> list[dict]:
    """Use factory counters to skip empty historical ranges, then read the actual logs."""
    call = {"to": deployment.factory, "data": "0x" + calldata("launches()").hex()}

    async def count(block: int) -> int:
        value = await adapter.rpc.call("eth_call", [call, hex(block)])
        return int(value, 16) if value != "0x" else 0

    head = await adapter.rpc.block_number()
    if head < deployment.start_block:
        return []
    latest = await count(head)
    threshold = max(0, latest - limit)

    async def scan(first, last, previous, total):
        if total <= max(threshold, previous):
            return []
        if last - first <= 1023:
            events = await adapter.rpc.call("eth_getLogs", [{"address": deployment.factory, "topics": [LAUNCHED],
                                                            "fromBlock": hex(first), "toBlock": hex(last)}])
            return list(reversed(events))
        mid = (first + last) // 2
        middle_count = await count(mid)
        newer = await scan(mid + 1, last, middle_count, total)
        older = await scan(first, mid, previous, middle_count)
        return newer + older

    return (await scan(deployment.start_block, head, 0, latest))[:limit]


async def _base_explorer_events(adapter, deployment, limit: int) -> list[dict]:
    response = await _public_json("https://base.blockscout.com/api", {
        "module": "logs", "action": "getLogs", "fromBlock": deployment.start_block, "toBlock": "latest",
        "address": deployment.factory, "topic0": LAUNCHED,
    })
    if not isinstance(response, dict) or not isinstance(response.get("result"), list):
        return []
    rows = []
    for log in reversed(response["result"][-limit:]):
        if not isinstance(log, dict):
            continue
        tx, topics = log.get("transactionHash"), log.get("topics")
        if not isinstance(tx, str) or not TX.fullmatch(tx) or not isinstance(topics, list) or len(topics) < 4:
            continue
        receipt = await adapter.rpc.receipt(tx)
        if verified := _launch_event(receipt, (deployment,), "0x" + topics[2][-40:]):
            rows.append({**verified, "transactionHash": tx})
    return rows


async def list_tokens(adapter, limit: int) -> list[dict]:
    key = (adapter.network, limit)
    async with _locks[adapter.network]:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        try:
            raw = await _scanner("/tokens", {"network": adapter.config.slug, "limit": limit})
            rows = parse_feed(raw, adapter.network)
        except AdapterError:
            rows = []
        deployments = (adapter.config.current, *adapter.config.previous)
        slots = asyncio.Semaphore(5)

        async def verified(row):
            if row["launch_tx"]:
                async with slots:
                    receipt = await adapter.rpc.receipt(row["launch_tx"])
                if event := _launch_event(receipt, deployments, row["address"]):
                    uri = _published_uri(receipt, event)
                    if row["metadata_uri"] is not None and row["metadata_uri"] != uri:
                        return {**row, "image": None, "socials": {}, "description": "", "metadata_uri": uri}
                    return {**row, "metadata_uri": uri}
            return None

        result = [row for row in await asyncio.gather(*(verified(row) for row in rows)) if row]
        known = {row["address"].lower() for row in result}

        async def from_events(deployment):
            try:
                events = await asyncio.wait_for(_recent_launch_events(adapter, deployment, limit), timeout=5)
            except (EvmRPCError, EvmRPCUnavailable, TimeoutError):
                events = []
            if not events and adapter.network == "base" and deployment in adapter.config.previous:
                try:
                    events = await _base_explorer_events(adapter, deployment, limit)
                except (AdapterError, EvmRPCError, EvmRPCUnavailable):
                    pass
            elif not events:
                return []
            found = []
            for event in events:
                topics = event.get("topics", [])
                if len(topics) != 4 or topics[0].lower() != LAUNCHED or event.get("removed"):
                    continue
                token = "0x" + topics[2][-40:]
                if token.lower() not in known:
                    known.add(token.lower())
                    try:
                        detail = await token_detail(adapter, token)
                        found.append({**detail, "launch_tx": event.get("transactionHash")})
                    except (AdapterError, EvmRPCError, EvmRPCUnavailable):
                        continue
            return found

        # The scanner can lag a newly confirmed launch or omit a historical deployment.
        if len(result) < limit:
            for deployment in deployments:
                try:
                    result += await asyncio.wait_for(from_events(deployment), timeout=25)
                except TimeoutError:
                    continue
                if len(result) >= limit:
                    break
        result = sorted(result, key=lambda row: row.get("created_at") or 0, reverse=True)[:limit]
        _cache[key] = (time.monotonic(), result)
        return result


async def token_detail(adapter, token: str) -> dict:
    if not ADDRESS.fullmatch(token):
        raise AdapterError("Enter a valid EVM token address.")
    token = to_checksum_address(token)
    deployment, quote, fee_bps = await adapter.token_pair(token)
    info, (uri,), launch = await asyncio.gather(
        adapter.rpc.erc20_meta(token),
        adapter._read(deployment.factory, "tokenMetadataURI(address)", ["string"], ["address"], [token]),
        adapter._read(deployment.hook, "launchOf(address)",
                      ["address", "address", "uint64", "uint16", "bool", "int24", "int24", "uint128", "uint128"],
                      ["address"], [token]),
    )
    try:
        raw = await _scanner(f"/token/{adapter.config.slug}/{token.lower()}")
        raw_row = raw.get("token", raw) if isinstance(raw, dict) else None
        display = parse_token(raw_row, adapter.network)
        if display and (display["address"] != token or display["metadata_uri"] != uri):
            display = None
    except AdapterError:
        display = None

    if not display:
        display = {"image": None, "description": "", "socials": {}, "launch_tx": None}
        if uri.startswith("ipfs://") and CID.fullmatch(uri[7:]):
            try:
                meta = await _public_json(PINATA_GATEWAY + uri[7:], max_bytes=MAX_JSON_BYTES)
            except AdapterError:
                meta = None
            if meta is None:
                response = await _fetch(None, uri, MAX_JSON_BYTES)
                if response:
                    try:
                        meta = json.loads(response[1])
                    except ValueError:
                        pass
            if isinstance(meta, dict) and meta.get("schema") == "elpida-token-v1":
                image = meta.get("image")
                if isinstance(image, str) and image.startswith("ipfs://") and CID.fullmatch(image[7:]):
                    display["image"] = PINATA_GATEWAY + image[7:]
                display["description"] = meta["description"][:1000] if isinstance(meta.get("description"), str) else ""
                display["socials"] = {k: href for k in SOCIALS if (href := https_link(meta.get(k)))}
    return {**display, "address": token, "network": adapter.network, "launchpad": adapter.id,
            "name": info["name"], "symbol": info["symbol"], "decimals": info["decimals"],
            "total_supply": str(info["total_supply"]), "quote": quote, "fee_bps": fee_bps,
            "graduated": launch[4], "created_at": launch[2] or None, "metadata_uri": uri,
            "url": token_url(adapter.network, token)}
