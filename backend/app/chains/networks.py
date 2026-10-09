"""TRON, NEAR, Arc and TON: network registry and capability matrix.

These networks sit beside Helm's original chains (Solana, Base, BNB Chain, Ethereum, Robinhood Chain) and are
enabled by EXTRA_NETWORKS (default: the TRON, NEAR, Arc and TON mainnets). ALLOW_MAINNET_EXTRA_NETWORKS=false switches every mainnet
off at once. Only mainnets are offered; Helm has no testnet mode for these networks.

Every value here comes from the official documentation listed in SOURCES (consulted 2026-09-28).
"""

from dataclasses import dataclass, field

from ..config import get_settings


@dataclass(frozen=True)
class ExtraNetwork:
    id: str
    family: str  # tron | near | evm | ton
    name: str
    native: str
    decimals: int  # of the native asset
    default_rpc: str
    explorer: dict = field(default_factory=dict)  # account / tx / token URL prefixes
    chain_id: int | None = None  # EVM chain id (Arc)
    network_id: str | None = None  # TON Connect network id (-239, TON mainnet); not an EVM chain id
    notes: str = ""


EXTRA_NETWORKS: dict[str, ExtraNetwork] = {
    "tron": ExtraNetwork(
        "tron", "tron", "TRON", "TRX", 6, "https://api.trongrid.io",
        {"account": "https://tronscan.org/address/", "tx": "https://tronscan.org/transaction/",
         "token": "https://tronscan.org/token20/"},
        notes="Fees are paid in resources: bandwidth (600 free a day) and energy for contracts, else TRX is burned."),
    "near": ExtraNetwork(
        "near", "near", "NEAR", "NEAR", 24, "https://free.rpc.fastnear.com",
        {"account": "https://nearblocks.io/address/", "tx": "https://nearblocks.io/txns/",
         "token": "https://nearblocks.io/token/"},
        notes="Accounts are names (alice.near) or 64-hex implicit accounts; tokens need a storage deposit."),
    "arc": ExtraNetwork(
        "arc", "evm", "Arc", "USDC", 18, "https://rpc.mainnet.arc.io",
        {"account": "https://explorer.arc.io/address/", "tx": "https://explorer.arc.io/tx/",
         "token": "https://explorer.arc.io/token/"}, chain_id=5042,
        notes="Gas is paid in USDC. Native USDC has 18 decimals; its ERC-20 interface (0x3600…) shows the same "
              "balance with 6 decimals, so it is never counted twice."),
    "ton": ExtraNetwork(
        "ton", "ton", "TON", "TON", 9, "https://toncenter.com",
        {"account": "https://tonviewer.com/", "tx": "https://tonviewer.com/transaction/",
         "token": "https://tonviewer.com/"}, network_id="-239",
        notes="Your own wallet signs through TON Connect; Helm never holds TON keys. Jettons move through the "
              "sender's jetton wallet with 0.05 TON attached (the unused part comes back)."),
}

# Known token contracts from the chains' official docs, matched by chain and address, never by ticker.
KNOWN_TOKENS: dict[str, list[dict]] = {
    # Arc: the USDC ERC-20 interface is the native balance again, so it is not listed as a separate token.
    "arc": [{"address": "0xbEf5f6d51CB62b58e6A8f77868681825C6fe21c1", "symbol": "EURC", "decimals": 6}],
}
ARC_USDC_INTERFACE = "0x3600000000000000000000000000000000000000"

SOURCES = {
    "tron": [
        "https://developers.tron.network/docs/networks",
        "https://developers.tron.network/docs/account",
        "https://developers.tron.network/docs/api-signature-and-broadcast-flow",
        "https://developers.tron.network/docs/tron-protocol-transaction",
        "https://developers.tron.network/docs/resource-model",
        "https://developers.tron.network/docs/paying-for-resources",
        "https://developers.tron.network/docs/set-feelimit",
        "https://developers.tron.network/docs/trc20-contract-interaction",
        "https://developers.tron.network/reference/gettransactioninfobyid-1",
        "https://developers.tron.network/reference/rate-limits",
        "https://developers.tron.network/docs/tronlink-integration",
    ],
    "near": [
        "https://docs.near.org/api/rpc/providers",
        "https://docs.near.org/api/rpc/transactions",
        "https://docs.near.org/api/rpc/access-keys",
        "https://nomicon.io/DataStructures/Account",
        "https://nomicon.io/RuntimeSpec/Transactions",
        "https://nomicon.io/RuntimeSpec/Actions",
        "https://docs.near.org/integrations/implicit-accounts",
        "https://docs.near.org/protocol/transactions/gas",
        "https://docs.near.org/protocol/storage/storage-staking",
        "https://docs.near.org/primitives/ft",
        "https://docs.near.org/tools/near-connect.md",
    ],
    "evm": [
        "https://docs.arc.io/integrate/connect-to-arc",
        "https://docs.arc.io/integrate/wallets/add-arc-to-a-wallet",
        "https://docs.arc.io/arc/references/evm-differences",
        "https://docs.arc.io/arc/references/contract-addresses",
        "https://docs.arc.io/arc/references/gas-and-fees.md",
        "https://docs.arc.io/arc/concepts/deterministic-finality.md",
    ],
    "ton": [
        "https://github.com/ton-blockchain/ton-connect/blob/main/spec/connect.md",
        "https://github.com/ton-blockchain/ton-connect/blob/main/spec/rpc.md",
        "https://github.com/ton-blockchain/ton-connect/blob/main/spec/manifest.md",
        "https://docs.ton.org/applications/ton-connect/how-to/ton-proof",
        "https://docs.ton.org/applications/ton-connect/how-to/send-transaction",
        "https://docs.ton.org/applications/ton-connect/how-to/message-lookup",
        "https://github.com/ton-blockchain/TEPs/blob/master/text/0002-address.md",
        "https://github.com/ton-blockchain/TEPs/blob/master/text/0074-jettons-standard.md",
        "https://github.com/ton-blockchain/TEPs/blob/master/text/0467-normalized-message-hash.md",
        "https://docs.ton.org/foundations/addresses/formats",
        "https://docs.ton.org/foundations/status",
        "https://docs.ton.org/foundations/fees",
        "https://docs.ton.org/api/v3/overview",
        "https://docs.ton.org/api/rate-limit",
    ],
}

# What Helm does on each family. "yes" = implemented on a documented path; anything else says why not.
CAPABILITIES = {
    "tron": {
        "wallets": "yes: Helm-managed keys (secp256k1, TRON address format) and watch-only addresses",
        "balances": "yes: TRX and TRC-20 by contract address",
        "token_metadata": "yes: TRC-20 name, symbol, decimals read from the contract",
        "history": "yes: TronGrid account history",
        "transfers": "yes: TRX and TRC-20, with bandwidth, energy and account-activation costs in the preview; "
                     "final once solidified",
        "launches": "yes: SunPump (name and symbol on-chain; SunPump's own site doesn't show an image for them)",
        "swaps": "partial: buys and sells on SunPump's bonding curve; SunSwap (after graduation) isn't routed",
        "market_data": "no: no provider coverage verified for Helm's market views on TRON yet",
        "cross_chain": "no: Helm's route provider (Relay) isn't wired for TRON",
    },
    "near": {
        "wallets": "yes: Helm-managed ed25519 keys as implicit accounts, and watch-only named or implicit accounts",
        "balances": "yes: NEAR (available, after storage) and NEP-141 tokens by contract account",
        "token_metadata": "yes: NEP-148 ft_metadata",
        "history": "partial: Helm's own transfers only; NEAR has no official account-history API",
        "transfers": "yes: NEAR and NEP-141 (with the recipient's storage registration when needed); final "
                     "execution awaited",
        "launches": "yes: Meme.Cooking fair-launch auctions (needs PINATA_JWT for the image)",
        "swaps": "partial: deposit into and withdraw from Meme.Cooking auctions; Ref Finance isn't routed",
        "market_data": "no: no provider coverage verified for Helm's market views on NEAR yet",
        "cross_chain": "no: Helm's route provider isn't wired for NEAR",
    },
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
    "ton": {
        "wallets": "yes: your own wallet via TON Connect (bound with a signed ton_proof) and watch-only addresses; "
                   "Helm-managed TON keys aren't offered",
        "balances": "yes: TON and Jettons by master address (toncenter index)",
        "token_metadata": "yes: Jetton name, symbol, decimals from the index (decimals default to 9)",
        "history": "yes: toncenter account transactions",
        "transfers": "yes: TON and Jettons, approved and signed in your wallet; followed by message hash until "
                     "final",
        "launches": "no: no TON launchpad integrated with verified contracts and terms",
        "swaps": "no: no TON DEX route integrated in this release",
        "market_data": "no: no provider coverage verified for Helm's market views on TON yet",
        "cross_chain": "no: Helm's route provider isn't wired for TON",
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


# Keyless public endpoints that answer the same API, cycled when the primary rate-limits the host
# (balance reads fan out across every wallet at once). NearClient rotates across the whole list.
_FALLBACK_RPCS: dict[str, list[str]] = {
    "near": ["https://rpc.mainnet.near.org", "https://1rpc.io/near"],
}


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
    """Whether Helm still offers this network. Wallets saved on a network Helm no longer offers (the TRON, NEAR, Arc
    and TON testnets were retired) stay in the database untouched but aren't listed or read."""
    from .evm import EVM_CHAINS

    return network == "solana" or network in EVM_CHAINS or network in EXTRA_NETWORKS
