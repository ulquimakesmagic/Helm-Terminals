# Launch terminal market data

The terminal view of a live launch shows indicative, third-party market data next to the launch's own Helm data
(wallets, supply, transactions, operations). The two are kept apart: market data is labelled as such, and it never
feeds quotes, previews, confirmations or anything that executes.

## Providers and endpoints

Both are documented public APIs called from the backend only (the browser never calls a provider, and no provider
key lives in a `VITE_` variable). Nothing is scraped and no undocumented embed is used.

| What | Provider | Endpoint |
|---|---|---|
| Pools that trade the token, with DEX, pair address, quote asset, liquidity, price, price change, volume, buy/sell counts, FDV and market cap | DEX Screener | `GET https://api.dexscreener.com/token-pairs/v1/{chainId}/{tokenAddress}` |
| Price candles (OHLCV) for one pool | GeckoTerminal | `GET {GECKOTERMINAL_API_URL}/networks/{network}/pools/{pool}/ohlcv/{timeframe}` |

DEX Screener documents no candle endpoint and no chart embed with parameters, so it isn't used for chart history.
`GECKOTERMINAL_API_URL` defaults to `https://api.geckoterminal.com/api/v2`; no key is needed.

Helm routes:

- `GET /launches/{id}/market` returns the token's pools (largest liquidity first) and a `state`: `ok`, `no_pool`
  (not indexed yet), `no_token` (a draft), `unsupported` (a chain the provider doesn't cover) or `error`.
- `GET /launches/{id}/market/candles?pool=&tf=` returns candles for one of that token's own pools (any other pool is
  refused with 404). Timeframes: `5m`, `15m`, `1h`, `4h`, `1d`, which map to GeckoTerminal's documented
  `minute/5`, `minute/15`, `hour/1`, `hour/4` and `day/1`. Up to 200 candles.

## Chain mapping

| Helm | DEX Screener | GeckoTerminal |
|---|---|---|
| Solana | `solana` | `solana` |
| Base (8453) | `base` | `base` |
| BNB Chain (56) | `bsc` | `bsc` |
| Ethereum (1) | `ethereum` | `eth` |
| Robinhood Chain (4663) | `robinhood` | `robinhood` |

Pools are resolved from the launch's own chain and token address, never by ticker. A pool is kept only when its
chain matches and the token is its base or quote token (Solana addresses compared case-sensitively).

## Honesty rules

- A missing number stays missing ("—"); it never becomes zero, and FDV and market cap are shown only as each is
  returned. One is never calculated from the other.
- When the launch token is the pool's quote token, DEX Screener's price, FDV and market cap describe the other token,
  so they're withheld and the view says so; candles are requested for the quote side.
- Malformed candle rows are dropped, never filled in. No synthetic candles.

## Limits, caching and failure

GeckoTerminal's public tier allows 30 calls a minute per its FAQ and about 10 per its product page, with no SLA.
Helm keeps one shared cache and budget per server: pools are reused for 30 seconds, candles for 60, and uncached
candle requests are capped at 8 a minute. A provider's 429 starts a back-off (its `Retry-After`, else 60 seconds).
When a provider fails, the last good answer up to 30 minutes old is shown and marked stale with its age; past that,
the view shows an error with a retry.

## Terms

DEX Screener's API terms restrict products whose *primary purpose* competes with DEX Screener; Helm's is launching
and managing tokens, and the market view is a supporting panel with attribution. GeckoTerminal's public API terms
should be reviewed before relying on it commercially. Both providers are credited in the view, with links.

## Not covered

- No live trades feed (neither provider documents one in these APIs), so the terminal shows buy/sell counts per
  window instead of individual trades.
- A freshly created token shows "no pool yet" until the providers index it.
