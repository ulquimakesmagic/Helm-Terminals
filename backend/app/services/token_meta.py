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
from urllib.parse import urlsplit

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


async def _public_host(url: str) -> bool:
    host = urlsplit(url).hostname
    if not host:
        return False
    try:
        infos = await asyncio.get_running_loop().run_in_executor(None, lambda: socket.getaddrinfo(host, 443))
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


async def _fetch(client: httpx.AsyncClient, url: str | None, cap: int) -> tuple[str, bytes] | None:
    """(content type, body) of a public https URL, or None when it's not allowed, fails or is too big."""
    url = _https(url)
    if not url or not await _public_host(url):
        return None
    try:
        async with client.stream("GET", url, follow_redirects=False, timeout=8) as r:
            if r.status_code != 200:
                return None
            body = b""
            async for chunk in r.aiter_bytes():
                body += chunk
                if len(body) > cap:
                    return None
            return r.headers.get("content-type", "").split(";")[0].strip().lower(), body
    except (httpx.HTTPError, ValueError):
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
    own = client is None
    client = client or httpx.AsyncClient()
    try:
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
    finally:
        if own:
            await client.aclose()
    out["copied"] = [k for k in ("name", "symbol", "description", "image") if out[k]] + list(out["socials"])
    return out
