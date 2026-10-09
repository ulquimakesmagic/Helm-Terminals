"""Balances, token metadata, transfers and history for Arc (see chains/networks.py)."""

import asyncio
from decimal import Decimal

from ..chains.base import AdapterError

_clients: dict[str, object] = {}  # network -> client (tests inject fakes here)


def evm_rpc(ex, network: str):
    return ex.evm(network).rpc


def human(raw: int, decimals: int) -> str:
    """Exact decimal string of a base-unit amount."""
    q = Decimal(raw) / (Decimal(10) ** decimals)
    return format(q.normalize(), "f") if q else "0"


async def native_balance(ex, network: str, address: str) -> int:
    """Spendable native balance in base units."""
    return await evm_rpc(ex, network).get_balance(address)


async def token_metadata(ex, network: str, token: str, reader: str | None = None) -> dict:
    rpc = evm_rpc(ex, network)
    from ..chains.networks import ARC_USDC_INTERFACE

    if token.lower() == ARC_USDC_INTERFACE:
        raise AdapterError("that's the USDC ERC-20 interface: the same balance as native USDC, shown as the "
                           "wallet's USDC balance instead")
    meta = await rpc.erc20_meta(token)
    return {"name": meta.get("name"), "symbol": meta.get("symbol"), "decimals": int(meta["decimals"])}


async def token_balance(ex, network: str, token: str, holder: str) -> int:
    return await evm_rpc(ex, network).erc20_balance(token, holder)


async def gather_safe(*coros):
    """Results or the exception, per call, so one failing read doesn't hide the rest."""
    return await asyncio.gather(*coros, return_exceptions=True)
