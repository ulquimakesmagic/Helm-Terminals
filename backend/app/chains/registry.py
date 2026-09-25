"""Launchpad adapter registry. To add a launchpad: write an adapter file, add one line to IMPLEMENTED."""

from ..config import get_settings
from .base import LaunchpadAdapter
from .pumpfun import PumpFunAdapter


def _pumpfun() -> LaunchpadAdapter:
    s = get_settings()
    return PumpFunAdapter(trade_url=s.pumpportal_trade_url, ipfs_url=s.pumpfun_ipfs_url)


IMPLEMENTED = {"pumpfun": _pumpfun}

# Roadmap from the spec (section 8). Shown in the UI as "planned" so the scope is visible, not hidden.
PLANNED = [
    ("letsbonk", "solana"), ("meteora-dbc", "solana"), ("bags", "solana"), ("printr", "solana"),
    ("surge", "solana"), ("anoncoin", "solana"), ("stonkfun", "solana"),
    ("clanker", "base"), ("bankr", "base"),
    ("four-meme", "bsc"), ("flap-sh", "bsc"),
    ("pons", "robinhood"), ("flap-sh-robinhood", "robinhood"),
    ("uniswap-v2", "ethereum"),
]

_cache: dict[str, LaunchpadAdapter] = {}


def get_adapter(launchpad_id: str) -> LaunchpadAdapter:
    if launchpad_id not in IMPLEMENTED:
        raise KeyError(launchpad_id)
    if launchpad_id not in _cache:
        _cache[launchpad_id] = IMPLEMENTED[launchpad_id]()
    return _cache[launchpad_id]


def list_launchpads() -> list[dict]:
    out = []
    for lid, factory in IMPLEMENTED.items():
        a = get_adapter(lid)
        out.append({"id": lid, "network": a.network, "quote_assets": a.quote_assets, "status": "available"})
    for lid, net in PLANNED:
        out.append({"id": lid, "network": net, "quote_assets": [], "status": "planned"})
    return out
