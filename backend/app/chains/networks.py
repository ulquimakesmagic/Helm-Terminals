"""TRON, NEAR, Arc and TON: network registry and capability matrix.

These networks sit beside Helm's original chains (Solana, Base, BNB Chain, Ethereum, Robinhood Chain) and are
enabled by EXTRA_NETWORKS (default: the TRON, NEAR, Arc and TON mainnets). ALLOW_MAINNET_EXTRA_NETWORKS=false switches every mainnet
off at once. Testnets and mainnets are separate network ids, and users still pick a network explicitly.

Every value here comes from the official documentation listed in SOURCES (consulted 2026-09-28).
"""

from dataclasses import dataclass, field

from ..config import get_settings


@dataclass(frozen=True)
class ExtraNetwork:
    id: str
    family: str  # tron | near | evm | ton
    name: str
    testnet: bool
    native: str
    decimals: int  # of the native asset
    default_rpc: str
    explorer: dict = field(default_factory=dict)  # account / tx / token URL prefixes
    chain_id: int | None = None  # EVM chain id (Arc)
    network_id: str | None = None  # TON Connect network id (TON: -239 mainnet, -3 testnet); not an EVM chain id
    notes: str = ""


EXTRA_NETWORKS: dict[str, ExtraNetwork] = {
    "tron": ExtraNetwork(
        "tron", "tron", "TRON", False, "TRX", 6, "https://api.trongrid.io",
        {"account": "https://tronscan.org/address/", "tx": "https://tronscan.org/transaction/",
         "token": "https://tronscan.org/token20/"},
        notes="Fees are paid in resources: bandwidth (600 free a day) and energy for contracts, else TRX is burned."),
    "tron-nile": ExtraNetwork(
        "tron-nile", "tron", "TRON Nile testnet", True, "TRX", 6, "https://nile.trongrid.io",
        {"account": "https://nile.tronscan.org/address/", "tx": "https://nile.tronscan.org/transaction/",
         "token": "https://nile.tronscan.org/token20/"}),
    "near": ExtraNetwork(
        "near", "near", "NEAR", False, "NEAR", 24, "https://free.rpc.fastnear.com",
        {"account": "https://nearblocks.io/address/", "tx": "https://nearblocks.io/txns/",
         "token": "https://nearblocks.io/token/"},
        notes="Accounts are names (alice.near) or 64-hex implicit accounts; tokens need a storage deposit."),
    "near-testnet": ExtraNetwork(
        "near-testnet", "near", "NEAR testnet", True, "NEAR", 24, "https://test.rpc.fastnear.com",
        {"account": "https://testnet.nearblocks.io/address/", "tx": "https://testnet.nearblocks.io/txns/",
         "token": "https://testnet.nearblocks.io/token/"}),
    "arc": ExtraNetwork(
        "arc", "evm", "Arc", False, "USDC", 18, "https://rpc.mainnet.arc.io",
        {"account": "https://explorer.arc.io/address/", "tx": "https://explorer.arc.io/tx/",
         "token": "https://explorer.arc.io/token/"}, chain_id=5042,
        notes="Gas is paid in USDC. Native USDC has 18 decimals; its ERC-20 interface (0x3600…) shows the same "
              "balance with 6 decimals, so it is never counted twice."),
    "arc-testnet": ExtraNetwork(
        "arc-testnet", "evm", "Arc testnet", True, "USDC", 18, "https://rpc.testnet.arc.io",
        {"account": "https://explorer.testnet.arc.io/address/", "tx": "https://explorer.testnet.arc.io/tx/",
         "token": "https://explorer.testnet.arc.io/token/"}, chain_id=5042002),
    "ton": ExtraNetwork(
        "ton", "ton", "TON", False, "TON", 9, "https://toncenter.com",
        {"account": "https://tonviewer.com/", "tx": "https://tonviewer.com/transaction/",
         "token": "https://tonviewer.com/"}, network_id="-239",
        notes="Your own wallet signs through TON Connect; Helm never holds TON keys. Jettons move through the "
              "sender's jetton wallet with 0.05 TON attached (the unused part comes back)."),
    "ton-testnet": ExtraNetwork(
        "ton-testnet", "ton", "TON testnet", True, "TON", 9, "https://testnet.toncenter.com",
        {"account": "https://testnet.tonviewer.com/", "tx": "https://testnet.tonviewer.com/transaction/",
         "token": "https://testnet.tonviewer.com/"}, network_id="-3"),
}

# Known token contracts from the chains' official docs, matched by chain and address, never by ticker.
KNOWN_TOKENS: dict[str, list[dict]] = {
    # Arc: the USDC ERC-20 interface is the native balance again, so it is not listed as a separate token.
    "arc": [{"address": "0xbEf5f6d51CB62b58e6A8f77868681825C6fe21c1", "symbol": "EURC", "decimals": 6}],
    "arc-testnet": [{"address": "0x89B50855Aa3bE2F677cD6303Cec089B5F319D72a", "symbol": "EURC", "decimals": 6}],
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
        "launches": "no: no launchpad with documented contracts or API on TRON (SunPump publishes no developer docs)",
        "swaps": "no: not in this release (SunSwap documents contracts, but Helm has no audited route for it yet)",
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
        "launches": "no: no launchpad with documented contracts or API integrated",
        "swaps": "no: not in this release",
        "market_data": "no: no provider coverage verified for Helm's market views on NEAR yet",
        "cross_chain": "no: Helm's route provider isn't wired for NEAR",
    },
    "evm": {  # Arc
        "wallets": "yes: Helm-managed EVM keys (a separate key per Arc network) and watch-only addresses",
        "balances": "yes: USDC (native, 18 decimals) and ERC-20 tokens such as EURC",
        "token_metadata": "yes: ERC-20 name, symbol, decimals",
        "history": "partial: Helm's own transfers only; no official Arc history API is documented",
        "transfers": "yes: USDC and ERC-20, gas paid in USDC; final at one confirmation (deterministic finality)",
        "launches": "no: no launchpad with documented contracts on Arc",
        "swaps": "no: Arc's docs name no DEX contracts; App Kit swaps cover only USDC/EURC/cirBTC",
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
    """Extra networks switched on for this server (EXTRA_NETWORKS), mainnets only with the explicit opt-in."""
    s = get_settings()
    wanted = [n.strip() for n in (s.extra_networks or "").split(",") if n.strip()]
    out = []
    for n in wanted:
        net = EXTRA_NETWORKS.get(n)
        if net and (net.testnet or s.allow_mainnet_extra_networks):
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


def rpc_urls(network: str) -> str:
    """The configured endpoint(s) for a network, else its public default. Env: {NETWORK}_RPC_URL."""
    s = get_settings()
    return getattr(s, f"{network.replace('-', '_')}_rpc_url", "") or EXTRA_NETWORKS[network].default_rpc
