"""Chain-neutral balance helpers, so features like automations, disperse and the portfolio work the same on
Solana and every EVM chain. Amounts come back in whole coins/tokens (floats) unless a name says `raw`."""

from ..chains.evm import EVM_CHAINS, WEI
from ..chains.solana import LAMPORTS_PER_SOL

TRADE_GAS = 400_000  # gas budget for one launchpad/DEX trade (matches the manual trade preflight)
SOL_FEE_RESERVE = 0.01  # SOL kept aside per Solana transaction (priority fees, ATA rent headroom)


def native_coin(network: str) -> str:
    return "SOL" if network == "solana" else EVM_CHAINS[network]["native"]


def chain_name(network: str) -> str:
    return "Solana" if network == "solana" else EVM_CHAINS[network]["name"]


async def native_balance(ex, network: str, address: str) -> float:
    if network == "solana":
        return await ex.rpc.get_balance(address) / LAMPORTS_PER_SOL
    return await ex.evm(network).rpc.get_balance(address) / WEI


async def token_balance_raw(ex, network: str, token: str, address: str) -> int:
    if network == "solana":
        return int(await ex.rpc.get_token_balance(address, token))
    return int(await ex.evm(network).rpc.erc20_balance(token, address))


async def token_decimals(ex, network: str, token: str) -> int:
    if network == "solana":
        _, decimals = await ex.rpc.get_token_supply(token)
        return decimals
    return int((await ex.evm(network).rpc.erc20_meta(token))["decimals"])


async def fee_reserve(ex, network: str, transactions: int, gas_each: int = TRADE_GAS) -> float:
    """Native coin to keep aside for `transactions` sends at today's worst-case gas price."""
    if transactions <= 0:
        return 0.0
    if network == "solana":
        return transactions * SOL_FEE_RESERVE
    return transactions * await ex.evm(network).max_gas_cost(gas_each) / WEI


# The dollar stablecoin Helm keeps balances in on each chain. Robinhood Chain has no native USDC; its
# stablecoin is Paxos' USDG (docs.robinhood.com/chain/contracts). Decimals are read on-chain (USDC on BNB Chain
# uses 18, the rest 6).
STABLES: dict[str, tuple[str, str]] = {
    "solana": ("USDC", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"),
    "base": ("USDC", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"),
    "ethereum": ("USDC", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"),
    "bsc": ("USDC", "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"),
    "robinhood": ("USDG", "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"),
}


def stable_for(network: str) -> tuple[str, str]:
    """(symbol, token address) of the chain's dollar stablecoin."""
    return STABLES[network]
