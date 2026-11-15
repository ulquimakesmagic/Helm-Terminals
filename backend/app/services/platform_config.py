"""Effective fee settings and treasury addresses.

Rates come from the fixed pricing policy. Legacy database/environment rates are
reported for audit but cannot override it. Treasuries remain configurable, cached
for CACHE_S, and refreshed immediately after an admin change. Fee-bearing actions
fail closed when their network's treasury is missing.
"""

import math
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.evm import EVM_CHAINS
from ..config import get_settings
from ..models import PlatformSetting, TreasuryAddress

CACHE_S = 30.0

# Safe bounds for what an admin may set, in basis points.
BOUNDS = {
    "trade_fee_bps_solana": (250, 250),
    "trade_fee_bps_evm_dex": (250, 250),
    "route_fee_bps": (0, 0),
    "creator_share_bps": (250, 250),
}

FEE_PATHS = [
    {"id": "launch_fee", "label": "Free launches", "setting": None,
     "networks": ["solana", *EVM_CHAINS, "tron", "near", "arc"], "supported": True, "lands": None,
     "how": "Launches are free for everyone. Network and third-party launchpad costs are separate."},
    {"id": "trade_fee_solana", "label": "2.5% Solana trading fee", "setting": "trade_fee_bps_solana",
     "networks": ["solana"], "supported": True, "lands": "received", "how": "2.5% of trade notional; referral share is 30% of the fee."},
    {"id": "trade_fee_evm_dex", "label": "2.5% EVM trading fee", "setting": "trade_fee_bps_evm_dex",
     "networks": list(EVM_CHAINS), "supported": True, "lands": "received", "how": "2.5% of trade notional, including bonding curves; referral share is 30% of the fee."},
    {"id": "creator_rewards", "label": "2.5% creator-reward share", "setting": "creator_share_bps",
     "networks": ["solana", "base", "robinhood"], "supported": True, "lands": "configured", "how": "2.5% of creator rewards on new Helm launches. Collection readiness is launchpad-specific; historical immutable splits retain their beneficiaries."},
]

# Fixed in HelmPonsRewards / HelmRewardsSplitter (HELM_BPS = 600). Existing splitters' addresses derive from that
# bytecode, so a different rate needs a new contract version and factory, not a settings change.
PONS_SHARE_BPS = 600

CREATOR_SHARE_LAUNCHPADS = {
    "pumpfun": "solana", "bags": "solana", "clanker": "base", "clanker-robinhood": "robinhood",
    "pons": "robinhood",
}


@dataclass
class Snapshot:
    at: float = -1.0  # monotonic time of the load; -1 = never loaded
    settings: dict = field(default_factory=dict)  # key -> {"value": int, "source": "admin"|"env"}
    treasury: dict = field(default_factory=dict)  # network -> {"address", "label", "source"}


_snap = Snapshot()


def _env_defaults() -> dict:
    from .pricing import TRADE_BPS, CREATOR_SHARE_BPS
    return {"trade_fee_bps_solana": TRADE_BPS, "trade_fee_bps_evm_dex": TRADE_BPS,
            "route_fee_bps": 0, "creator_share_bps": CREATOR_SHARE_BPS}


async def refresh(db: AsyncSession, force: bool = False) -> Snapshot:
    global _snap
    if not force and not stale():
        return _snap
    env = _env_defaults()
    rows = {r.key: r for r in (await db.execute(select(PlatformSetting))).scalars()}
    settings = {}
    for key, default in env.items():
        r = rows.get(key)
        settings[key] = {"value": int(default), "source": "policy",
                         "previous_setting": int(r.value.get("bps", 0)) if r else None}
    s = get_settings()
    treasury = {"solana": {"address": s.platform_fee_wallet, "label": "", "source": "env"} if s.platform_fee_wallet else None,
                "evm": {"address": s.platform_fee_wallet_evm, "label": "", "source": "env"} if s.platform_fee_wallet_evm else None}
    for t in (await db.execute(select(TreasuryAddress))).scalars():
        treasury[t.network] = {"address": t.address, "label": t.label, "source": "admin",
                               "updated_at": t.updated_at.isoformat()}
    _snap = Snapshot(time.monotonic(), settings, {k: v for k, v in treasury.items() if v})
    return _snap


def stale() -> bool:
    return _snap.at < 0 or time.monotonic() - _snap.at >= CACHE_S


def current() -> Snapshot:
    """The last loaded snapshot (call refresh() first in async code). Before any load, env values."""
    if _snap.at < 0:
        s = get_settings()
        return Snapshot(-1.0, {k: {"value": int(v), "source": "env"} for k, v in _env_defaults().items()},
                        {k: {"address": a, "label": "", "source": "env"} for k, a in
                         (("solana", s.platform_fee_wallet), ("evm", s.platform_fee_wallet_evm)) if a})
    return _snap


def bps(key: str) -> int:
    return _env_defaults().get(key, 0)


def treasury_for(network: str) -> str:
    """The treasury address for a network: its own entry, else the shared EVM entry for EVM chains. '' if none."""
    t = current().treasury
    if network in t:
        return t[network]["address"]
    if (network in EVM_CHAINS or network == "arc") and "evm" in t:
        return t["evm"]["address"]
    return ""


def creator_share(launchpad: str) -> dict:
    """Policy and verified collection capability; never silently waive a missing split."""
    from .pricing import CREATOR_SHARE_BPS
    network = CREATOR_SHARE_LAUNCHPADS.get(launchpad)
    recipient = treasury_for(network) if network else ""
    native = launchpad in {"pumpfun", "bags", "clanker", "clanker-robinhood"}
    if launchpad == "pons":
        native = bool(get_settings().pons_rewards_factory_v2)
    supported = native and bool(recipient)
    return {"bps": CREATOR_SHARE_BPS, "recipient": recipient, "supported": supported,
            "collection": "native_split" if supported else "integration_required",
            "note": ("2.5% of creator rewards to Helm; 97.5% to the creator" if supported else
                     "2.5% creator-reward policy; collection is not ready for this launchpad or treasury")}


def reset_cache() -> None:
    global _snap
    _snap = Snapshot()
