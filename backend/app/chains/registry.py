"""Launchpad adapter registry. To add a launchpad: write an adapter file, add one line to IMPLEMENTED."""

from ..config import get_settings
from .bags import BagsAdapter
from .evm import shared_evm
from .flap import FlapAdapter
from .clanker import ClankerAdapter
from .fourmeme import FourMemeAdapter
from .meteora_dbc import MeteoraDbcAdapter
from .pons import PonsAdapter
from .printr import PrintrAdapter
from .uniswap_v2 import UniswapV2Adapter
from .base import LaunchpadAdapter
from .letsbonk import LetsBonkAdapter
from .pumpfun import PumpFunAdapter
from .solana import shared_rpc
from .extra_pads import MemeCooking, Pinata, SunPump, Tolly


def _pumpfun() -> LaunchpadAdapter:
    s = get_settings()
    return PumpFunAdapter(trade_url=s.pumpportal_trade_url, ipfs_url=s.pumpfun_ipfs_url,
                          agent_api_url=s.pumpfun_agent_api_url)


def _letsbonk() -> LaunchpadAdapter:
    s = get_settings()
    return LetsBonkAdapter(trade_url=s.pumpportal_trade_url, storage_url=s.letsbonk_storage_url)


def _bags() -> LaunchpadAdapter:
    s = get_settings()
    return BagsAdapter(api_key=s.bags_api_key, api_url=s.bags_api_url, rpc=shared_rpc(s.solana_rpc_url),
                       partner_wallet="", partner_config="")


def _flap(launchpad_id: str, network: str):
    def make():
        s = get_settings()
        # The commission receiver comes from the live treasury settings (see platform_config).
        return FlapAdapter(launchpad_id, network, shared_evm(network), upload_url=s.flap_upload_url)
    return make


def _clanker(launchpad_id: str, network: str):
    def make():
        # Helm's reward share and its recipient come from the live platform settings (see platform_config).
        return ClankerAdapter(launchpad_id, network, shared_evm(network))
    return make


IMPLEMENTED = {"pumpfun": _pumpfun, "letsbonk": _letsbonk, "bags": _bags,
               "flap-sh": _flap("flap-sh", "bsc"), "flap-sh-robinhood": _flap("flap-sh-robinhood", "robinhood"),
               "flap-sh-base": _flap("flap-sh-base", "base"),
               "pons": lambda: PonsAdapter(shared_evm("robinhood")),
               "uniswap-v2": lambda: UniswapV2Adapter(shared_evm("ethereum")),
               "four-meme": lambda: FourMemeAdapter(shared_evm("bsc")),
               "meteora-dbc": lambda: MeteoraDbcAdapter(get_settings().jupiter_api_key,
                                                        shared_rpc(get_settings().solana_rpc_url),
                                                        get_settings().jupiter_api_url or "https://api.jup.ag"),
               "printr": lambda: PrintrAdapter(
                   get_settings().printr_api_url, get_settings().printr_api_key,
                   shared_rpc(get_settings().solana_rpc_url),
                   MeteoraDbcAdapter(get_settings().jupiter_api_key, shared_rpc(get_settings().solana_rpc_url),
                                     get_settings().jupiter_api_url or "https://api.jup.ag")
                   if get_settings().jupiter_api_key else None),
               "clanker": _clanker("clanker", "base"), "clanker-robinhood": _clanker("clanker-robinhood", "robinhood"),
               # TRON, NEAR and Arc (see chains/extra_pads.py and services/extra_launch.py)
               "sunpump": SunPump, "tolly": Tolly,
               "meme-cooking": lambda: MemeCooking(Pinata(get_settings().pinata_jwt) if get_settings().pinata_jwt
                                                   else None)}


def is_extra(adapter) -> bool:
    return getattr(adapter, "kind", "") == "extra"


def is_evm(adapter) -> bool:
    return getattr(adapter, "kind", "solana") == "evm"


def is_evm_network(network: str) -> bool:
    """Whether a network key belongs to one of the EVM JSON-RPC chains."""
    from .evm import EVM_CHAINS
    from .networks import EXTRA_NETWORKS

    extra = EXTRA_NETWORKS.get(network)
    return network in EVM_CHAINS or getattr(extra, "family", None) == "evm"


def needs_setup(launchpad_id: str) -> str | None:
    """Why an implemented launchpad can't be used on this server yet, or None if it can."""
    if launchpad_id == "pons" and not get_settings().pons_rewards_factory_v2:
        return "Pons 2.5% creator rewards need a separately verified PONS_REWARDS_FACTORY_V2"
    if launchpad_id == "bags" and not get_settings().bags_api_key:
        return "Bags needs an API key (BAGS_API_KEY) on the server"
    if launchpad_id in ("sunpump", "meme-cooking", "tolly"):
        from .networks import EXTRA_NETWORKS, extra_enabled

        net = get_adapter(launchpad_id).network
        if net not in extra_enabled():
            return f"{EXTRA_NETWORKS[net].name} isn't enabled on this server (EXTRA_NETWORKS)"
        if launchpad_id == "meme-cooking" and not get_settings().pinata_jwt:
            return "Meme.Cooking launches pin the image to IPFS, which needs a Pinata key (PINATA_JWT)"
        if launchpad_id == "tolly" and not get_settings().public_api_url:
            return "Tolly stores the token image as a link, which needs PUBLIC_API_URL on the server"
    if launchpad_id == "meteora-dbc" and not get_settings().jupiter_api_key:
        return "Meteora DBC launches go through Jupiter Studio, which needs a Jupiter API key (JUPITER_API_KEY)"
    return None

# Roadmap from the spec (section 8). Shown in the UI as "planned" so the scope is visible, not hidden.
PLANNED = [
    ("surge", "solana"), ("anoncoin", "solana"), ("stonkfun", "solana"),
    ("bankr", "base"),
    # Elpida is a development prototype. Enable only after deployed program/contract verification.
    ("elpida-solana", "solana"), ("elpida-ethereum", "ethereum"), ("elpida-base", "base"),
    ("elpida-bsc", "bsc"), ("elpida-robinhood", "robinhood"),
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
        from ..services.capabilities import launchpad_capabilities
        status = "setup_required" if reason else "available"
        capabilities = launchpad_capabilities(lid, a.network, status)
        if not reason and not capabilities["creator_reward_collection_ready"]:
            reason = capabilities["creator_reward_note"]
            status = "setup_required"
        out.append({"id": lid, "network": a.network, "quote_assets": a.quote_assets,
                    "status": status, "capabilities": capabilities, **({"reason": reason} if reason else {})})
    for lid, net in PLANNED:
        out.append({"id": lid, "network": net, "quote_assets": [], "status": "planned",
                    "capabilities": launchpad_capabilities(lid, net, "planned")})
    return out
