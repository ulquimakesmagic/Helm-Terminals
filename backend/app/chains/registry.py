"""Launchpad adapter registry. To add a launchpad: write an adapter file, add one line to IMPLEMENTED."""

from ..config import get_settings
from .bags import BagsAdapter
from .evm import shared_evm
from .flap import FlapAdapter
from .base import LaunchpadAdapter
from .letsbonk import LetsBonkAdapter
from .pumpfun import PumpFunAdapter
from .solana import shared_rpc


def _pumpfun() -> LaunchpadAdapter:
    s = get_settings()
    return PumpFunAdapter(trade_url=s.pumpportal_trade_url, ipfs_url=s.pumpfun_ipfs_url)


def _letsbonk() -> LaunchpadAdapter:
    s = get_settings()
    return LetsBonkAdapter(trade_url=s.pumpportal_trade_url, storage_url=s.letsbonk_storage_url)


def _bags() -> LaunchpadAdapter:
    s = get_settings()
    return BagsAdapter(api_key=s.bags_api_key, api_url=s.bags_api_url, rpc=shared_rpc(s.solana_rpc_url),
                       partner_wallet=s.bags_partner_wallet, partner_config=s.bags_partner_config)


def _flap(launchpad_id: str, network: str):
    def make():
        s = get_settings()
        return FlapAdapter(launchpad_id, network, shared_evm(network), upload_url=s.flap_upload_url,
                           commission_receiver=s.platform_fee_wallet_evm)
    return make


IMPLEMENTED = {"pumpfun": _pumpfun, "letsbonk": _letsbonk, "bags": _bags,
               "flap-sh": _flap("flap-sh", "bsc"), "flap-sh-robinhood": _flap("flap-sh-robinhood", "robinhood")}


def is_evm(adapter) -> bool:
    return getattr(adapter, "kind", "solana") == "evm"


def needs_setup(launchpad_id: str) -> str | None:
    """Why an implemented launchpad can't be used on this server yet, or None if it can."""
    if launchpad_id == "bags" and not get_settings().bags_api_key:
        return "Bags needs an API key (BAGS_API_KEY) on the server"
    return None

# Roadmap from the spec (section 8). Shown in the UI as "planned" so the scope is visible, not hidden.
PLANNED = [
    ("meteora-dbc", "solana"), ("printr", "solana"),
    ("surge", "solana"), ("anoncoin", "solana"), ("stonkfun", "solana"),
    ("clanker", "base"), ("bankr", "base"),
    ("four-meme", "bsc"),
    ("pons", "robinhood"),
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
        reason = needs_setup(lid)
        out.append({"id": lid, "network": a.network, "quote_assets": a.quote_assets,
                    "status": "setup_required" if reason else "available", **({"reason": reason} if reason else {})})
    for lid, net in PLANNED:
        out.append({"id": lid, "network": net, "quote_assets": [], "status": "planned"})
    return out
