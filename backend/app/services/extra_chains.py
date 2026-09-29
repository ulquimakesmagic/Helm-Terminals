"""Balances, token metadata, transfers and history for TRON, NEAR and Arc (see chains/networks.py)."""

import asyncio
from decimal import Decimal

from ..chains.base import AdapterError
from ..chains.networks import EXTRA_NETWORKS, rpc_urls
from ..config import get_settings
from ..keyring import parse_list

_clients: dict[str, object] = {}  # network -> client (tests inject fakes here)


def tron_client(network: str):
    from ..chains.tron import TronClient

    if network not in _clients:
        s = get_settings()
        _clients[network] = TronClient(parse_list(rpc_urls(network))[0], s.trongrid_api_key,
                                       timeout_s=s.extra_rpc_timeout_s)
    return _clients[network]


def near_client(network: str):
    from ..chains.near import NearClient

    if network not in _clients:
        _clients[network] = NearClient(parse_list(rpc_urls(network)), timeout_s=get_settings().extra_rpc_timeout_s)
    return _clients[network]


def ton_client(network: str):
    from ..chains.ton import Toncenter

    if network not in _clients:
        s = get_settings()
        _clients[network] = Toncenter(parse_list(rpc_urls(network))[0], s.toncenter_api_key,
                                      timeout_s=s.extra_rpc_timeout_s)
    return _clients[network]


def ton_address(network: str, addr: str, wallet: bool = True) -> str:
    """The one stored/displayed form of a TON address on a network: user-friendly base64url, non-bounceable for
    wallets (so an undeployed wallet still receives), bounceable for contracts (mainnet form)."""
    from ..chains.ton import parse_address

    return parse_address(addr).friendly(bounceable=not wallet, testnet=False)


def evm_rpc(ex, network: str):
    return ex.evm(network).rpc


def human(raw: int, decimals: int) -> str:
    """Exact decimal string of a base-unit amount."""
    q = Decimal(raw) / (Decimal(10) ** decimals)
    return format(q.normalize(), "f") if q else "0"


async def native_balance(ex, network: str, address: str) -> int:
    """Spendable native balance in base units (NEAR: after the storage its account keeps staked)."""
    fam = EXTRA_NETWORKS[network].family
    if fam == "tron":
        return await tron_client(network).balance(address)
    if fam == "near":
        from ..chains.near import available_balance

        return available_balance(await near_client(network).account(address))
    if fam == "ton":
        return (await ton_client(network).account(address))["balance"]
    return await evm_rpc(ex, network).get_balance(address)


async def token_metadata(ex, network: str, token: str, reader: str | None = None) -> dict:
    fam = EXTRA_NETWORKS[network].family
    if fam == "tron":
        return await tron_client(network).trc20_metadata(token, reader or token)
    if fam == "near":
        m = await near_client(network).view(token, "ft_metadata", {})
        if not isinstance(m, dict) or "decimals" not in m:
            raise AdapterError("that account doesn't answer like a NEP-141 token")
        return {"name": m.get("name"), "symbol": m.get("symbol"), "decimals": int(m["decimals"])}
    if fam == "ton":
        m = await ton_client(network).jetton_master(token)
        return {"name": m["name"], "symbol": m["symbol"], "decimals": m["decimals"]}
    rpc = evm_rpc(ex, network)
    from ..chains.networks import ARC_USDC_INTERFACE

    if token.lower() == ARC_USDC_INTERFACE:
        raise AdapterError("that's the USDC ERC-20 interface: the same balance as native USDC, shown as the "
                           "wallet's USDC balance instead")
    meta = await rpc.erc20_meta(token)
    return {"name": meta.get("name"), "symbol": meta.get("symbol"), "decimals": int(meta["decimals"])}


async def token_balance(ex, network: str, token: str, holder: str) -> int:
    fam = EXTRA_NETWORKS[network].family
    if fam == "tron":
        return await tron_client(network).trc20_balance(token, holder)
    if fam == "near":
        return int(await near_client(network).view(token, "ft_balance_of", {"account_id": holder}) or 0)
    if fam == "ton":
        w = await ton_client(network).jetton_wallet(holder, token)
        return w["balance"] if w else 0
    return await evm_rpc(ex, network).erc20_balance(token, holder)


async def gather_safe(*coros):
    """Results or the exception, per call, so one failing read doesn't hide the rest."""
    return await asyncio.gather(*coros, return_exceptions=True)
