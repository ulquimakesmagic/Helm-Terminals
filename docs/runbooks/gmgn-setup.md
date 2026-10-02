# GMGN market data — setup and troubleshooting

Helm's launch terminal (price, market cap, liquidity, volume, swaps, holders) reads one source:
GMGN's OpenAPI `GET /v1/token/info`, through `backend/app/services/market.py`. The request shape
is verified against GMGN's own client (`gmgn-cli`): `X-APIKEY` header plus `timestamp` (Unix
seconds) and `client_id` (fresh UUID) query params. Market-data routes need **no signature** —
that is GMGN's "exist auth" tier. Swap/order routes would additionally sign with Ed25519, but
Helm never calls those (its swaps go through Jupiter).

## Wire it up (operator)

1. **Create the key.** At <https://gmgn.ai/ai>, generate an Ed25519 key pair (their site walks
   you through it — OpenSSL or their desktop generator) and upload the **public** key to issue
   an API key. This asymmetric pairing is how the Agent/OpenAPI plan is provisioned; a plan
   purchase alone does not create a usable key.
2. **Set the key.** In `backend/.env`:
   ```
   GMGN_API_KEY=<the issued API key>
   ```
   Restart the backend. The Ed25519 **private** key stays out of Helm entirely — it signs only
   GMGN's own swap routes, which Helm does not use.
3. **Verify.** One read-only call, prints status only, never credentials:
   ```
   python -m app.market_check --network solana --token So11111111111111111111111111111111111111112
   ```
   `"state": "ok"` (or `"no_pool"` for a fresh token) means done. Data then appears in each
   launch desk's GMGN panel and refreshes every 60 s.

## What the panel shows

| Field | Source |
|---|---|
| Price, 24h change, volume, buys/sells | `price` object |
| Market cap | price × `circulating_supply` — GMGN's own definition |
| Liquidity | biggest-pool liquidity (`pool.liquidity`, falling back to top-level `liquidity`) |
| Holders | `holder_count` |

All display-only: cached 60 s, never fed into previews, quotes or execution.

## Troubleshooting

| Symptom | Cause → fix |
|---|---|
| Panel says "GMGN market metrics need setup" (`not_configured`) | `GMGN_API_KEY` missing/empty in `backend/.env` → set it and restart. |
| "GMGN access needs attention" (`access_denied`) | Key invalid/expired, plan inactive, or IP not allowed. GMGN accepts **IPv4 only** — if the server egresses via IPv6, disable it for the backend. Helm pins IPv4 client-side, but the network path must allow it. |
| "paused requests due to its rate limit" (`rate_limited`) | Plan quota hit. Helm caches 60 s and backs off using GMGN's reset header; if it recurs, upgrade the plan or lower `GMGN_REQUESTS_PER_MINUTE`. |
| "Helm's GMGN request budget is reached" (`request_budget`) | Helm's own shared budget (`GMGN_REQUESTS_PER_MINUTE`, default 30/min) — raise within plan quota. |
| "No indexed main pool yet" (`no_pool`) | Normal for brand-new tokens; fills in once GMGN indexes a pool. Chart still works. |

Buying a plan but never generating/uploading the Ed25519 public key at gmgn.ai/ai is the most
common "I paid but nothing works" cause: no API key exists to put in `.env`, so Helm reports
`not_configured` and shows no metrics.
