# Release note: TRON, NEAR and TON restored (10 October 2026)

Reverses `2026-10-06-remove-tron-near-ton.md`. Helm again supports Solana, Base, BNB Chain, Ethereum, Robinhood
Chain, Arc, TRON, NEAR and TON: Helm-managed and watch-only wallets, transfers, wallet linking (TronLink, NEAR Connect,
TON Connect), SunPump and Meme.Cooking launches, and their market panels.

## Data
Nothing was deleted when they were removed, so wallets, launches, jobs and transactions saved on `tron`, `near` and
`ton` before 6 October reappear as they were. No migration.

## Configuration
| Variable | Change |
|---|---|
| `EXTRA_NETWORKS` | Default back to `tron,near,arc,ton`. **Production must list them** if it was set to `arc` after the removal. |
| `TRON_RPC_URL`, `TRONGRID_API_KEY`, `NEAR_RPC_URL`, `TON_RPC_URL`, `TONCENTER_API_KEY`, `PINATA_JWT` | Used again. Restore them if they were deleted from Railway (public defaults work for RPC; Meme.Cooking images need `PINATA_JWT`). |

## Kept from later releases
USDC withdrawals and the Solana fee payer, route providers (Relay, deBridge off by default), and the $10 / 1% / 3% fee
model all stay. Dialogs still don't repeat "mainnet" (every network Helm offers is mainnet).

## Fees on these chains: none yet
TRON, NEAR, TON and Arc launches and trades run through Helm's separate extra-chain path, which charges **no** Helm
fee: no launch fee, no trading fee, no creator-reward share. Collecting them there needs per-chain fee transfers,
treasury addresses for each chain and a price source for TRX, NEAR and TON. Not built in this release.

## Rollback
Redeploy the previous commit. No data changes.
