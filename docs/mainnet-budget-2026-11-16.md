# Mainnet funding budget — 16 November 2026

Only Pons currently needs a separately deployed Helm factory. Do not fund a
different factory deployment for every launchpad: the other current setup states
mainly require protocol integration, reward collection or provider access.

| Purpose | Planning amount | What it covers |
|---|---:|---|
| Pons V2 factory transaction | Approximately **$0.03** at this quote | 803,157 gas × 20,044,000 wei/gas = 0.000016098478908 ETH on Robinhood Chain, chain ID 4663 |
| Pons operator-wallet reserve | **$10–20 equivalent in ETH** | A practical funding allowance for refreshed gas prices, verification transactions and retries; this is not the predicted deployment fee |
| Separate multichain validation reserve | **$50–100 total**, allocated to chains actually tested | A planning allowance for small rehearsal launches/transfers/trades; not a guarantee of the full cost of every upstream launch configuration |

The dollar conversion uses an ETH price near $1,800. The
[Coinbase ETH price page](https://www.coinbase.com/price/ethereum) and the RPC quote
were checked for this review. Both change; rerun the preparation command immediately
before funding/signing. Keep the operator reserve on **Robinhood Chain**, since
Ethereum mainnet or testnet ETH does not pay Robinhood Chain gas directly.

The amounts above exclude exchange withdrawal minimums, bridging/on-ramp fees,
liquidity or initial buys, launchpad charges, stock-token purchase amounts,
hosting/RPC/signing subscriptions, partner API pricing and independent contract
review. Liquidity remains capital at risk, not infrastructure deployment gas.
No reliable fixed total can be quoted for integrations whose contracts or provider
agreements are not yet finalized.

No deployment was signed or broadcast. Follow
[the mainnet setup guide](mainnet-integration-setup.md) to regenerate the unsigned
Pons packet, deploy later from your own wallet and verify its exact runtime.

## Launch-flow version gaps

The current native Clanker form is V5, with chain-specific registered quotes,
separate buy/sell tax, optional holder dividends and burn allocation, mutable
metadata controls and reward recipients. The new
[Clanker V5 adapter](clanker-v5-integration.md) implements that version on Base,
BNB Chain and Robinhood Chain while preserving V4 token management. It uses the
existing upstream factories, with no new Helm factory deployment fee per chain.
Funded production lifecycle rehearsals remain separate from unsigned simulation.

Bags' current custom-quote route starts on DBC with Fee Share V2 and a staged
transaction plan, rather than the older direct DAMM-only path. Enabling quote
selection without implementing that execution/claim path does not complete the
integration.

Primary references: [Clanker V5 contracts](https://github.com/clanker-devco/DOCS/blob/main/references/core-contracts/v5.md),
[Clanker native form](https://www.clanker.world/deploy),
[Bags custom-quote flow](https://docs.bags.fm/how-to-guides/launch-token-non-sol-quote).
