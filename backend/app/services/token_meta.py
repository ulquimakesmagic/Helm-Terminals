"""Copy an existing token's public metadata (name, ticker, description, image, socials) for a relaunch draft.

Solana: the DAS `getAsset` record plus the token's off-chain metadata JSON (where the image and socials usually live).
EVM: the ERC-20 name and symbol (no standard on-chain description, image or socials).

Off-chain URLs come from token metadata anyone can write, so fetching them is fenced: https only (ipfs:// goes
through a public gateway), public addresses only (no loopback, private or link-local hosts), no redirects to
elsewhere, short timeouts and size caps. A metadata or image fetch that fails just leaves that field empty.
"""

import asyncio
import ipaddress
import json
import socket
import ssl
from urllib.parse import urlsplit

import aiohttp
import httpx

MAX_JSON_BYTES = 256 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
IPFS_GATEWAY = "https://ipfs.io/ipfs/"


def _https(url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if url.startswith("ipfs://"):
        return IPFS_GATEWAY + url.removeprefix("ipfs://").removeprefix("ipfs/")
    return url if url.startswith("https://") else None


async def _public_addresses(url: str) -> tuple[str, int, list[tuple[str, int]]] | None:
    parts = urlsplit(url)
    host = parts.hostname
    try:
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or not host or port not in (None, 443):
        return None
    try:
        infos = await asyncio.get_running_loop().run_in_executor(
            None, lambda: socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        )
    except OSError:
        return None
    addresses = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except (IndexError, ValueError):
            return None
        # is_global also excludes shared/CGNAT space (100.64.0.0/10), which Railway's internal network uses.
        if not ip.is_global or ip.is_multicast:
            return None
        pair = (str(ip), socket.AF_INET6 if ip.version == 6 else socket.AF_INET)
        if pair not in addresses:
            addresses.append(pair)
    return (host, 443, addresses) if addresses else None


async def _public_host(url: str) -> bool:
    """Compatibility helper for tests and policy checks; production requests use the returned pinned addresses."""
    return await _public_addresses(url) is not None


class _PinnedResolver(aiohttp.abc.AbstractResolver):
    """Resolve only the hostname and IPs already checked by `_public_addresses`; never perform a second DNS lookup."""

    def __init__(self, host: str, addresses: list[tuple[str, int]]) -> None:
        self.host = host.rstrip(".").lower()
        self.addresses = addresses

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
        if host.rstrip(".").lower() != self.host or port != 443:
            raise OSError("metadata request attempted to resolve an unvalidated host")
        return [{"hostname": host, "host": ip, "port": port, "family": ip_family, "proto": 0,
                 "flags": socket.AI_NUMERICHOST}
                for ip, ip_family in self.addresses if family in (socket.AF_UNSPEC, ip_family)]

    async def close(self) -> None:
        return None


async def _read_aiohttp(session: aiohttp.ClientSession, url: str, cap: int) -> tuple[str, bytes] | None:
    async with session.get(url, allow_redirects=False) as response:
        if response.status != 200:
            return None
        body = bytearray()
        async for chunk in response.content.iter_chunked(64 * 1024):
            body.extend(chunk)
            if len(body) > cap:
                return None
        return response.headers.get("content-type", "").split(";")[0].strip().lower(), bytes(body)


async def _fetch(client: httpx.AsyncClient | None, url: str | None, cap: int) -> tuple[str, bytes] | None:
    """(content type, body) of a public https URL, or None when it's not allowed, fails or is too big."""
    url = _https(url)
    if not url:
        return None
    try:
        if client is not None:
            # An injected HTTPX client is used only by deterministic MockTransport tests. Production calls leave it
            # unset and use the pinned aiohttp resolver below.
            if not await _public_host(url):
                return None
            async with client.stream("GET", url, follow_redirects=False, timeout=8) as r:
                if r.status_code != 200:
                    return None
                body = bytearray()
                async for chunk in r.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > cap:
                        return None
                return r.headers.get("content-type", "").split(";")[0].strip().lower(), bytes(body)

        pinned = await _public_addresses(url)
        if pinned is None:
            return None
        host, _, addresses = pinned
        timeout = aiohttp.ClientTimeout(total=8, connect=4, sock_read=4)
        connector = aiohttp.TCPConnector(resolver=_PinnedResolver(host, addresses), use_dns_cache=False,
                                         ssl=ssl.create_default_context())
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=False,
                                         auto_decompress=True) as session:
            return await _read_aiohttp(session, url, cap)
    except (aiohttp.ClientError, asyncio.TimeoutError, httpx.HTTPError, OSError, ValueError):
        return None


def _social(meta: dict, *keys: str) -> str | None:
    for src in (meta, meta.get("extensions") or {}, meta.get("properties") or {}):
        for k in keys:
            v = src.get(k)
            if isinstance(v, str) and v.startswith(("https://", "http://")):
                return v[:200]
    return None


async def source_metadata(ex, network: str, address: str, client: httpx.AsyncClient | None = None) -> dict:
    """{'name','symbol','description','image': bytes|None,'socials': {...},'copied': [...]} for a relaunch draft."""
    out = {"name": None, "symbol": None, "description": "", "image": None, "socials": {}, "copied": []}
    if network != "solana":
        meta = await ex.evm(network).rpc.erc20_meta(address)
        out["name"], out["symbol"] = meta.get("name") or None, meta.get("symbol") or None
        out["copied"] = [k for k in ("name", "symbol") if out[k]]
        return out
    asset = await ex.rpc.get_asset(address)
    content = asset.get("content") or {}
    md = content.get("metadata") or {}
    out["name"], out["symbol"] = md.get("name") or None, md.get("symbol") or None
    out["description"] = (md.get("description") or "")[:500]
    image_url = (content.get("links") or {}).get("image")
    r = await _fetch(client, content.get("json_uri"), MAX_JSON_BYTES)
    if r is not None:
        try:
            j = json.loads(r[1])
        except ValueError:
            j = {}
        if isinstance(j, dict):
            out["name"] = out["name"] or j.get("name")
            out["symbol"] = out["symbol"] or j.get("symbol")
            out["description"] = out["description"] or (j.get("description") or "")[:500]
            image_url = image_url or j.get("image")
            socials = {"twitter": _social(j, "twitter", "x"), "telegram": _social(j, "telegram"),
                       "website": _social(j, "website", "external_url")}
            out["socials"] = {k: v for k, v in socials.items() if v}
    img = await _fetch(client, image_url, MAX_IMAGE_BYTES)
    if img is not None and img[0] in IMAGE_TYPES:
        out["image"] = img[1]
    out["copied"] = [k for k in ("name", "symbol", "description", "image") if out[k]] + list(out["socials"])
    return out
