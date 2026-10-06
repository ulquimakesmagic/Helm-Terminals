"""Arc: extra-network registry and capability matrix.

Arc sits beside Helm's original chains (Solana, Base, BNB Chain, Ethereum, Robinhood Chain) and is enabled by
EXTRA_NETWORKS (default: Arc mainnet). ALLOW_MAINNET_EXTRA_NETWORKS=false switches it off. Only mainnet is offered.
TRON, NEAR and TON were removed on 6 October 2026; wallets saved on them stay in the database but aren't listed or
read (see offered()).

Every value here comes from the official documentation listed in SOURCES (consulted 2026-09-28).
"""

from dataclasses import dataclass, field

from ..config import get_settings


@dataclass(frozen=True)
class ExtraNetwork:
    id: str
    family: str  # evm
    name: str
    native: str
    decimals: int  # of the native asset
    default_rpc: str
    explorer: dict = field(default_factory=dict)  # account / tx / token URL prefixes
    chain_id: int | None = None  # EVM chain id (Arc)
    notes: str = ""


EXTRA_NETWORKS: dict[str, ExtraNetwork] = {
    "arc": ExtraNetwork(
        "arc", "evm", "Arc", "USDC", 18, "https://rpc.mainnet.arc.io",
        {"account": "https://explorer.arc.io/address/", "tx": "https://explorer.arc.io/tx/",
         "token": "https://explorer.arc.io/token/"}, chain_id=5042,
        notes="Gas is paid in USDC. Native USDC has 18 decimals; its ERC-20 interface (0x3600…) shows the same "
              "balance with 6 decimals, so it is never counted twice."),
}

# Known token contracts from the chains' official docs, matched by chain and address, never by ticker.
KNOWN_TOKENS: dict[str, list[dict]] = {
    # Arc: the USDC ERC-20 interface is the native balance again, so it is not listed as a separate token.
    "arc": [{"address": "0xbEf5f6d51CB62b58e6A8f77868681825C6fe21c1", "symbol": "EURC", "decimals": 6}],
}
ARC_USDC_INTERFACE = "0x3600000000000000000000000000000000000000"

SOURCES = {
    "evm": [
        "https://docs.arc.io/integrate/connect-to-arc",
        "https://docs.arc.io/integrate/wallets/add-arc-to-a-wallet",
        "https://docs.arc.io/arc/references/evm-differences",
        "https://docs.arc.io/arc/references/contract-addresses",
        "https://docs.arc.io/arc/references/gas-and-fees.md",
        "https://docs.arc.io/arc/concepts/deterministic-finality.md",
    ],
}

# What Helm does on each family. "yes" = implemented on a documented path; anything else says why not.
CAPABILITIES = {
    "evm": {  # Arc
        "wallets": "yes: Helm-managed EVM keys and watch-only addresses",
        "balances": "yes: USDC (native, 18 decimals) and ERC-20 tokens such as EURC",
        "token_metadata": "yes: ERC-20 name, symbol, decimals",
        "history": "partial: Helm's own transfers only; no official Arc history API is documented",
        "transfers": "yes: USDC and ERC-20, gas paid in USDC; final at one confirmation (deterministic finality)",
        "launches": "yes: Tolly (Uniswap V3 token/USDC pool with locked liquidity; verified source)",
        "swaps": "partial: buys and sells of Tolly tokens through Arc's Uniswap V3 SwapRouter02",
        "market_data": "no: no provider coverage verified for Arc",
        "cross_chain": "no: not wired in this release",
    },
}


def extra_enabled() -> list[str]:
    """Extra networks switched on for this server (EXTRA_NETWORKS), unless ALLOW_MAINNET_EXTRA_NETWORKS=false."""
    s = get_settings()
    wanted = [n.strip() for n in (s.extra_networks or "").split(",") if n.strip()]
    out = []
    for n in wanted:
        net = EXTRA_NETWORKS.get(n)
        if net and s.allow_mainnet_extra_networks:
            out.append(n)
    return out


def is_extra(network: str) -> bool:
    return network in EXTRA_NETWORKS


def require_enabled(network: str) -> ExtraNetwork:
    from fastapi import HTTPException

    net = EXTRA_NETWORKS.get(network)
    if not net:
        raise HTTPException(404, f"unknown network {network}")
    if network not in extra_enabled():
        raise HTTPException(409, f"{net.name} isn't enabled on this server")
    return net


# Keyless public endpoints that answer the same API, cycled when the primary rate-limits the host.
_FALLBACK_RPCS: dict[str, list[str]] = {}


def rpc_urls(network: str) -> str:
    """The configured endpoint(s) for a network, else its public default(s). Env: {NETWORK}_RPC_URL."""
    s = get_settings()
    configured = getattr(s, f"{network.replace('-', '_')}_rpc_url", "")
    if configured:
        return configured
    default = EXTRA_NETWORKS[network].default_rpc
    fallbacks = _FALLBACK_RPCS.get(network)
    return f"{default},{','.join(fallbacks)}" if fallbacks else default


def offered(network: str) -> bool:
    """Whether Helm still offers this network. Wallets saved on a network Helm no longer offers (TRON, NEAR, TON, and
    the retired testnets) stay in the database untouched but aren't listed or read."""
    from .evm import EVM_CHAINS

    return network == "solana" or network in EVM_CHAINS or network in EXTRA_NETWORKS
