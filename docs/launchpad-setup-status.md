# Launchpads not yet configured: what each needs (researched 2026-10-10)

Live API (`/launchpads`, 10 Oct 2026): Pons is `setup_required`; Printr, Surge, Anoncoin, Stonkfun and Bankr are
`planned`. After the TRON/NEAR/TON restore, SunPump and Meme.Cooking depend on server config, and GasPump (TON) is
`planned`. Findings are from official docs where available; anything unverified is marked.

| Launchpad | Status | What a server needs | Fits Helm's own-key model? | Recommendation |
|---|---|---|---|---|
| Pons (Robinhood) | integrated, paused | Deploy Helm's reward-splitter factory after independent review; then `PONS_REWARDS_ENABLED=true`, `PONS_REWARDS_FACTORY=<address>` | yes | Contract deploy is the blocker (needs review and your sign-off) |
| SunPump (TRON) | integrated | `EXTRA_NETWORKS` includes `tron`; `TRONGRID_API_KEY` recommended | yes | Ready once enabled; still active on-chain |
| Meme.Cooking (NEAR) | integrated | `EXTRA_NETWORKS` includes `near`; `PINATA_JWT` (free Pinata plan) | yes | Low activity (~$12K/30d); optional |
| Printr (Solana) | **integrated (10 Oct)** | Nothing required; optional `PRINTR_API_KEY`. Trades need `JUPITER_API_KEY` | yes | Rehearse one small launch; claims on app.printr.money |
| Stonkfun (Solana) | planned | No key. Raydium LaunchLab SDK + Stonkfun pricing/pairs API; `PINATA_JWT` for metadata | yes | Good candidate; trades go through Raydium/Jupiter |
| Bankr (Base / Robinhood) | planned | `BANKR_API_KEY` or partner key | **no**: deploys from Bankr's own wallet | Only if custodial deploys are acceptable |
| Anoncoin (Solana) | planned | `ANONCOIN_API_KEY` + fee-payer whitelisting | partly | Wait: create endpoint documented as not live yet |
| Surge | planned | No official production API found | no (custodial wallets in the only API seen) | Blocked |
| GasPump (TON) | removed from the roadmap (10 Oct) | Token creation not documented; listed as deprecated by DefiLlama | — | — |

Sources: Printr — theblock.co/post/397357, docs.cdp.coinbase.com/coinbase-for-agents/wallet-mcp/plugins/printr.md,
github.com/PrintrFi/printr-mcp; Stonkfun — stonkfun.xyz/developers; Bankr — docs.bankr.bot/token-launching/overview,
docs.bankr.bot/partnership/overview; Anoncoin — docs.anoncoin.it/apis/create-token.md; Surge — surge.xyz;
GasPump — defillama.com/protocol/gaspump; Pinata — pinata.cloud/pricing; SunPump — tronscan contract
TTfvyrAz86hbZk5iDpKD78pqLGgi8C7AAw; Meme.Cooking — defillama.com/protocol/meme-cooking.
