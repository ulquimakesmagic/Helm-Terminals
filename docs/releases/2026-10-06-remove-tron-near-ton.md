# Release note: TRON, NEAR and TON removed (6 October 2026)

Helm now supports Solana, Base, BNB Chain, Ethereum, Robinhood Chain and Arc. TRON, NEAR and TON are gone from the
product: no wallets, transfers, wallet linking (TronLink, NEAR Connect, TON Connect), launches (SunPump, Meme.Cooking)
or market panels on them.

## What happens to existing data

Nothing is deleted. Wallets, launches, jobs and transactions saved on `tron`, `near` or `ton` stay in the database
untouched, but Helm no longer lists, reads or counts them (the same rule already used for the retired testnets,
`chains.networks.offered`). Their pages return 404 and they are left out of the dashboard and portfolio. Admins still
see them in a user's admin detail view. No migration is needed.

**Funds still held in Helm-managed TRON or NEAR wallets can no longer be moved from Helm.** Their keys remain in
Turnkey under Helm's organisation (key names start `helm:<user id>:tron:` or `helm:<user id>:near:`), so an
organisation admin can recover them with Turnkey's own export, or by temporarily rolling back to the previous release
and withdrawing. Before deploying, check production for balances:

```sql
SELECT network, kind, count(*) FROM wallets WHERE network IN ('tron', 'near', 'ton') GROUP BY network, kind;
```

TON wallets were always the user's own (TON Connect) or watch-only, so no TON funds were ever in Helm's custody.

## Configuration

| Variable | Change |
|---|---|
| `EXTRA_NETWORKS` | Default is now `arc`. `tron`, `near` and `ton` are ignored if still listed. |
| `TRON_RPC_URL`, `TRONGRID_API_KEY`, `NEAR_RPC_URL`, `TON_RPC_URL`, `TONCENTER_API_KEY`, `PINATA_JWT` | Obsolete: remove. |
| `TON_PROOF_DOMAINS` | Still used (the sites an Arc wallet-ownership proof may name); name kept for existing deployments. |

## Code

Removed: `chains/tron.py`, `chains/near.py`, `chains/ton.py`, `routes/ton.py`, the SunPump and Meme.Cooking adapters,
TRON/NEAR key formats in both signers, the TON Connect component and manifest, the TronLink and NEAR Connect wallet
connectors, `@tonconnect/ui-react` and `@hot-labs/near-connect`, and the chain and launchpad logos. Wallet-proof
challenge helpers moved from `routes/ton.py` to `routes/external_wallets.py` unchanged (challenges issued before the
deploy still verify).

The frontend's main bundle is unchanged in size; the lazily loaded TON Connect chunk (about 450 kB) no longer exists.

Also fixed here: sweeping a wallet that holds stablecoins but not enough native coin for gas failed with "couldn't
read balances" instead of showing the gas warning.

## Rollback

Redeploy the previous commit. No data was changed, so the old release reads the same rows again.
