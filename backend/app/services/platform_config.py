"""Effective fee settings and treasury addresses.

Each value comes from an admin setting in the database when one has been saved, otherwise from the environment
(where Helm's fees were configured before the admin dashboard existed), so deploying this changes no live rate.
Values are cached in the process for a short time; admin changes refresh the cache immediately on this process and
within CACHE_S on any other.

Unsupported or unconfigured fee paths are off: a path is active only when its rate is above zero, a treasury address
is set for its network, and the provider supports taking the fee (see FEE_PATHS).
"""

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
    "trade_fee_bps_solana": (0, 200),  # Helm fee on Solana buys and sells: at most 2%
    "trade_fee_bps_evm_dex": (0, 200),  # Helm fee on EVM trades routed through KyberSwap: at most 2%
    "route_fee_bps": (0, 100),  # Helm fee on USDC routes and bridges (Relay app fee): at most 1%
    "creator_share_bps": (0, 1000),  # Helm's share of a new launch's creator rewards: at most 10%
}

# Every way Helm can earn, where it lands, and whether it's available. Used for the admin support matrix and to
# refuse enabling paths that don't exist.
FEE_PATHS = [
    {"id": "trade_fee_solana", "label": "Trading fee: Solana launchpads", "setting": "trade_fee_bps_solana",
     "networks": ["solana"], "supported": True, "lands": "received",
     "how": "Added as a transfer inside each confirmed buy or sell; a failed trade pays nothing."},
    {"id": "trade_fee_evm_dex", "label": "Trading fee: EVM trades routed through a DEX", "setting": "trade_fee_bps_evm_dex",
     "networks": list(EVM_CHAINS), "supported": True, "lands": "received",
     "how": "KyberSwap's router sends the fee to the treasury in the same swap transaction."},
    {"id": "trade_fee_evm_curve", "label": "Trading fee: EVM bonding-curve trades", "setting": None,
     "networks": list(EVM_CHAINS), "supported": False, "lands": None,
     "how": "Not available: Four.meme, Flap and Pons curves have no fee hook, and Helm doesn't add a separate fee "
            "transfer on EVM."},
    {"id": "route_fee", "label": "Cross-chain route fee (Relay)", "setting": "route_fee_bps",
     "networks": ["evm"], "supported": True, "lands": "claimable",
     "how": "A Relay app fee: Relay holds it as a balance for the treasury address until it's claimed at relay.link, "
            "so it counts as claimable, not received."},
    {"id": "creator_share_bags", "label": "Creator-reward share: Bags", "setting": "creator_share_bps",
     "networks": ["solana"], "supported": True, "lands": "claimable",
     "how": "Set in the launch's Bags fee-share config (treasury as a second claimer); claimed on Bags."},
    {"id": "creator_share_clanker", "label": "Creator-reward share: Clanker", "setting": "creator_share_bps",
     "networks": ["base", "robinhood"], "supported": True, "lands": "claimable",
     "how": "The treasury is a reward recipient in Clanker's LP locker; claimed from Clanker's fee locker."},
    {"id": "creator_share_pumpfun", "label": "Creator-reward share: Pump.fun", "setting": "creator_share_bps",
     "networks": ["solana"], "supported": True, "lands": "claimable",
     "how": "After the token confirms, Helm creates pump.fun's native one-time sharing config. Creator and treasury "
            "are separate shareholders; either party can trigger the permissionless distribution."},
    {"id": "creator_share_pons", "label": "Creator-reward share: Pons", "setting": None,
     "networks": ["robinhood"], "supported": True, "lands": "configured",
     "how": "Fixed 6% via a separate immutable reward splitter for each new native-ETH launch. Disabled until "
            "PONS_REWARDS_ENABLED and a verified PONS_REWARDS_FACTORY are configured. Configuration is not income; "
            "only confirmed treasury payouts through Helm enter received revenue. Legacy claims stay unsplit."},
    {"id": "creator_share_meteora", "label": "Creator-reward share: Meteora DBC", "setting": None,
     "networks": ["solana"], "supported": False, "lands": None,
     "how": "A split requires a Helm-owned Meteora DBC configuration with explicit partner and creator fee "
            "claimers. The current Jupiter Studio route does not expose that split."},
    {"id": "creator_share_other", "label": "Creator-reward share: other launchpads", "setting": None,
     "networks": [], "supported": False, "lands": None,
     "how": "No equivalent native split is integrated for LetsBonk, Four.meme, Flap, Tolly "
            "or Uniswap v2. Creator taxes, LP ownership and auction proceeds are different economic "
            "rights and are not relabelled as creator rewards."},
    {"id": "partner_flap", "label": "Flap launchpad commission (tax tokens)", "setting": None,
     "networks": ["bsc", "robinhood", "base"], "supported": True, "lands": "configured",
     "how": "The treasury is set as the commission receiver on new Flap tax tokens, using Flap's protocol-calculated "
            "commission. A configured recipient is not proof of accrued or received revenue; "
            "payouts require on-chain reconciliation. This is not Helm's 6% creator share."},
    {"id": "partner_bags", "label": "Bags partner share", "setting": None,
     "networks": ["solana"], "supported": True, "lands": "claimable",
     "how": "Configured with BAGS_PARTNER_WALLET / BAGS_PARTNER_CONFIG; paid by Bags, not directly verifiable here."},
]

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
    s = get_settings()
    return {
        "trade_fee_bps_solana": s.platform_fee_bps,
        "trade_fee_bps_evm_dex": s.platform_fee_bps,
        "route_fee_bps": s.route_fee_bps,
        # Before the dashboard, only Clanker had a platform share; keep that as the default.
        "creator_share_bps": s.clanker_platform_reward_bps,
    }


async def refresh(db: AsyncSession, force: bool = False) -> Snapshot:
    global _snap
    if not force and not stale():
        return _snap
    env = _env_defaults()
    rows = {r.key: r for r in (await db.execute(select(PlatformSetting))).scalars()}
    settings = {}
    for key, default in env.items():
        r = rows.get(key)
        settings[key] = ({"value": int(r.value.get("bps", 0)), "source": "admin", "updated_at": r.updated_at.isoformat()}
                         if r else {"value": int(default), "source": "env"})
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
    return current().settings.get(key, {}).get("value", 0)


def treasury_for(network: str) -> str:
    """The treasury address for a network: its own entry, else the shared EVM entry for EVM chains. '' if none."""
    t = current().treasury
    if network in t:
        return t[network]["address"]
    if network in EVM_CHAINS and "evm" in t:
        return t["evm"]["address"]
    return ""


def creator_share(launchpad: str) -> dict:
    """Helm's share of creator rewards for a new launch on this launchpad: {bps, recipient, supported, note}."""
    network = CREATOR_SHARE_LAUNCHPADS.get(launchpad)
    if network is None:
        return {"bps": 0, "recipient": "", "supported": False,
                "note": "Helm takes no share of creator rewards on this launchpad"}
    recipient = treasury_for(network)
    if launchpad == "pons":
        s = get_settings()
        # An explicit enable must fail closed at preflight if its factory/treasury is missing, not silently
        # create a launch with no revenue split. Actual addresses and bytecode are checked before confirmation.
        active = s.pons_rewards_enabled
        return {"bps": 600 if active else 0, "recipient": recipient if active else "", "supported": True,
                "note": ("Fixed split: 94% of native creator rewards to you; 6% to Helm, including creator tax. "
                         "Recipients cannot be changed through Helm. Pons retains its timelocked override."
                         if active else "Pons reward splitting is not enabled; rewards go to your dev wallet")}
    setting = current().settings.get("creator_share_bps", {})
    if setting.get("source") != "admin" and not launchpad.startswith("clanker"):
        # Before the dashboard only Clanker had a share (CLANKER_PLATFORM_REWARD_BPS); other launchpads stay at 0
        # until an admin sets the share, so deploying this doesn't change anyone's rates.
        return {"bps": 0, "recipient": "", "supported": True,
                "note": "Helm takes no share of your creator rewards"}
    share = min(max(bps("creator_share_bps"), 0), 1000) if recipient else 0
    return {"bps": share, "recipient": recipient, "supported": True,
            "note": (f"{share / 100:g}% of your creator rewards go to Helm's treasury; you keep {100 - share / 100:g}%"
                     if share else "Helm takes no share of your creator rewards")}


def reset_cache() -> None:
    global _snap
    _snap = Snapshot()
