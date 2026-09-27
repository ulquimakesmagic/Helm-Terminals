# Launch workspace market data

The workspace market view reads third-party DEX market data from the GeckoTerminal Public API. Helm does not use DEX Screener: its documented pair API has no documented OHLCV endpoint, and its API terms restrict products whose primary purpose competes directly with DEX Screener.

## Provider access, limits, and attribution

The backend defaults to `https://api.geckoterminal.com/api/v2`; no API key is required. `GECKOTERMINAL_API_URL` is an optional backend-only override for the public API base URL. The frontend never calls the provider directly and no provider key should be placed in a `VITE_` variable.

The public API is in beta and may change. Its current endpoint documentation describes a variable limit of approximately 10 requests per minute; an older official FAQ states 30 requests per minute, so Helm should budget conservatively and handle rate-limit responses. Provider endpoints are cached for about one minute. Helm caches token-pool and OHLCV responses for five minutes, caches network/DEX coverage for twelve hours, and the browser refreshes no more often than every two minutes. The UI shows the time Helm last fetched the market data. There is no SLA for the public API.

The UI attributes market data to GeckoTerminal and links to its site. The API remains informational: its output never authorizes or feeds trade quotes or transaction execution. Before operating a commercial, user-facing product on the public API, review the current [GeckoTerminal API terms](https://www.geckoterminal.com/terms-conditions) and confirm the permitted use for Helm. Public, no-cost access is not an uptime commitment or an automatic commercial redistribution license.

## Endpoints used

- `GET /networks` checks whether the provider currently lists the mapped network.
- `GET /networks/{network}/dexes` checks whether the provider reports DEX coverage on that network.
- `GET /networks/{network}/tokens/{token_address}/pools` resolves pools using Helm's network and exact token address.
- `GET /networks/{network}/pools/{pool_address}/ohlcv/{timeframe}` retrieves documented OHLCV candles for the selected pool and exact token address.

The four chart ranges map to documented intervals: 1H uses 1-minute candles; 24H uses 5-minute candles; 7D uses hourly candles; 30D uses 4-hour candles. No synthetic candles are generated.

## Chain mapping and boundaries

Helm maps Solana to `solana`, Base (8453) to `base`, BNB Chain (56) to `bsc`, Ethereum (1) to `eth`, and Robinhood Chain (4663) to `robinhood`. GeckoTerminal identifies networks with provider slugs rather than numeric EVM chain IDs. Helm first checks its canonical EVM chain-ID mapping, then confirms the provider slug appears in the provider's network index and that at least one DEX is listed for it. The provider network resource does not expose a numeric EVM chain ID, so this integration does not claim a provider-side numeric chain-ID check. If a slug or DEX coverage is absent, or token lookup returns no matching token address, the UI reports coverage unavailable or no indexed pool.

Third-party market data is informational and separate from Helm wallet balances, supply transparency, transaction history, quotes, and confirmations. FDV and market cap are displayed only when separately returned by the provider; one is never calculated from the other.
