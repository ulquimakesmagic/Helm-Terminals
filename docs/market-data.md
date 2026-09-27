# Launch workspace market data

The workspace market view uses the CoinGecko API's on-chain DEX endpoints, powered by GeckoTerminal. Helm does not call or embed DEX Screener. Its documented pair API exposes current pair metrics but does not document a candle endpoint, and its published API terms restrict building a product whose primary purpose directly competes with DEX Screener. Helm therefore uses CoinGecko's documented token-pool lookup and pool OHLCV endpoints instead.

## Provider access and attribution

The CoinGecko Onchain API requires an API key. Configure the key only on the backend:

```text
COINGECKO_API_KEY=<licensed CoinGecko API key>
```

The backend defaults to `https://pro-api.coingecko.com/api/v3/onchain`; `COINGECKO_ONCHAIN_API_URL` can override that base URL for an approved compatible endpoint. Do not put the key in a `VITE_` variable, browser code, a checked-in file, or logs. With no key, the market view remains explicitly unavailable. Helm production use requires a CoinGecko plan whose license covers this commercial product and the on-chain endpoints. The UI includes the required “Data provided by CoinGecko” attribution link and names GeckoTerminal as the on-chain data source.

The provider documents its public GeckoTerminal API as beta, with a low and variable rate limit. Helm uses the licensed CoinGecko Onchain API instead, keeps token-pool and OHLCV responses in a short in-process cache, and refreshes the browser view no more often than every two minutes. Current upstream data may still be delayed or unavailable.

## Endpoints used

- `GET /onchain/networks` confirms the provider's current network IDs; `GET /onchain/networks/{network}/dexes` confirms DEX coverage for each chain.
- `GET /onchain/networks/{network}/tokens/{token_address}/pools` resolves pools using the Helm network and exact launch token address.
- `GET /onchain/networks/{network}/pools/{pool_address}/ohlcv/{timeframe}` retrieves documented OHLCV candles for the selected pool and exact token address.

The four chart ranges map to documented intervals: 1H uses 1-minute candles; 24H uses 5-minute candles; 7D uses hourly candles; 30D uses 4-hour candles. No synthetic candles are generated.

## Chain mapping and boundaries

Helm currently maps Solana to `solana`, Base (8453) to `base`, BNB Chain (56) to `bsc`, Ethereum (1) to `eth`, and Robinhood Chain (4663) to `robinhood`. CoinGecko's on-chain API identifies networks with provider slugs rather than numeric EVM chain IDs. Helm first checks its canonical EVM chain ID mapping, then confirms the provider slug appears in the provider's network index and that at least one DEX is listed for it. The provider network resource does not expose a numeric EVM chain ID, so this integration does not claim a provider-side numeric chain-ID check. If a slug or DEX coverage is absent, or token lookup returns no matching token address, the UI reports coverage unavailable or no indexed pool.

Third-party market data is informational. It does not modify or authorize Helm wallet balances, supply transparency, transaction history, quotes, or transaction execution. FDV and market cap are displayed only when separately returned by the provider; one is never calculated from the other.
