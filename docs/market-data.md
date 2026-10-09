# Market data: Codex integration and coverage report

Updated 6 October 2026 (audit HT-06). **Codex (codex.io) is Helm's only source of third-party DEX and token-market
data.** GMGN's API and DEX Screener were removed, and portfolio/dashboard USD prices moved from DefiLlama to Codex.
There is no fallback provider for data: when Codex cannot answer, Helm says so.

**Charts (changed later on 6 October 2026):** on Solana, Ethereum and BNB Chain the workspace chart is GMGN's public
chart embed (`https://www.gmgn.cc/kline/{sol|eth|bsc}/{token}?theme=dark&interval=…`, intervals 5m/15m/1H/4H/1D),
the only chains and parameters in GMGN's embed guide. It needs no API key; the visitor's browser loads it straight
from GMGN (sandboxed iframe, no referrer). On every other Codex chain the chart is Helm's own Codex candle chart.
Every figure outside the chart (price, change, market cap, FDV, liquidity, volume, holders, pools, activity,
portfolio values) still comes from Codex only. The privacy policy names GMGN as a third party the browser contacts.

## What is and isn't market data

| Data | Source | Notes |
|---|---|---|
| Token price, market cap, FDV, liquidity, volume, buy/sell/trade counts, holders, pools, candles | **Codex** | Display only; never used for previews, quotes or execution |
| Coin prices behind portfolio and net-flow USD values | **Codex** `getTokenPrices` (wrapped native tokens) | SOL via WSOL, ETH via mainnet WETH (also used for Base/Robinhood ETH), BNB via WBNB, TRX via WTRX |
| Wallet balances | Each chain's RPC | Unchanged |
| Confirmed transactions, fees received | Chain receipts / provider settlement | Unchanged |
| Launchpad state (curves, eligibility, rewards) | Launchpad contracts and APIs | Unchanged |
| Execution quotes | Jupiter, KyberSwap, Relay, launchpad builders | Unchanged; bound to the reviewed minimum (HT-03) |
| Token symbol lookup for Solana swaps | Jupiter token list | Part of the execution provider, not market data |

## Interfaces used (docs.codex.io, checked 6 Oct 2026)

- `filterTokens(tokens: ["address:networkId"], limit: 1)`: `priceUSD`, `circulatingMarketCap` (shown as **market cap**),
  `marketCap` (Codex defines this as price × total supply; shown as **FDV**), `liquidity`, `holders`, `createdAt`,
  `lastTransaction`, `isScam`, and per-window `change*`, `volume*`, `buyCount*`, `sellCount*`, `txnCount*` for
  5m / 1h / 4h / 12h / 24h. `9223372036854775806` ("could not be computed") is treated as missing.
- `listPairsWithMetadataForToken`: pools, exchange, quote token and liquidity (sorted by liquidity).
- `getBars`: OHLCV candles in USD, `removeLeadingNullValues` and `removeEmptyBars` on, `symbolType: TOKEN` (aggregated
  across pools; Codex's recommended mode for launchpad tokens) or `POOL` for a pool the user selects from Codex's own
  list. Resolutions 1m, 5m, 15m, 1H, 4H, 1D; 300 bars per request (Codex maximum 1,500).
- `getTokenPrices`: up to 25 inputs per request; historical prices use `timestamp`, and a returned price more than a
  day older (or an hour newer) than requested is discarded rather than used for that day.
- Authentication: `Authorization: <CODEX_API_KEY>` from the server only. The key never reaches the browser.

Not verified against a live Codex account (no key was available in the audit environment): exact field availability
per plan, holder-count availability (Codex documents its `holders` query as Growth/Enterprise; `filterTokens.holders`
may be null on lower plans and is then shown as "—"), and TRON address formatting. The integration was tested
against recorded response shapes from the documentation.

## Network coverage

| Helm chain | Codex networkId | Market panel | Chart |
|---|---|---|---|
| Solana | 1399811149 | Yes | GMGN embed |
| Ethereum | 1 | Yes | GMGN embed |
| BNB Chain | 56 | Yes | GMGN embed |
| Base | 8453 | Yes (previously metrics only) | Yes (new) |
| Robinhood Chain | 4663 | Yes | Yes (new) |
| Arc | 5042 | Yes | Yes (new) |
| TRON | 728126428 | Yes (new) | Yes (new) |
| NEAR | — | Unsupported | Unsupported |
| TON | — | Unsupported | Unsupported |

## Honest states

`not_configured` (no key), `unsupported` (NEAR, TON), `no_token` (draft), `not_indexed` (Codex doesn't know the
token yet), `no_pool`, `no_bars` (no trades in the range; never filled), `stale` (last good response shown with a
label after a failure, up to 5 minutes), `error` (`unavailable`, `rate_limited` with retry time, `access_denied`
naming `CODEX_API_KEY`, `request_budget`, `invalid_response`, `unknown_pool`, `invalid_resolution`). Missing values
are null and shown as "—", never zero. Figures older than five minutes are hidden in the panel.

## Reliability controls

Per-token cache (snapshot 30 s; candles 15–120 s by resolution; historical prices 24 h), stale-on-error up to
5 minutes, in-flight de-duplication across users, a per-minute request budget (`CODEX_REQUESTS_PER_MINUTE`, default
120), at most two retries with jitter on transport errors and 5xx, a cooldown honouring `Retry-After` on 429, and a
60-second pause after 401/403. Logs carry only category, HTTP status and error count — never URLs, keys, provider
messages or token addresses.

## Features lost or changed

- **GMGN chart** is back on Solana, Ethereum and BNB Chain (with GMGN's own indicators and drawing tools). Other
  chains use Helm's Codex candle chart (keyboard and touch inspection, data table) without indicators or drawing
  tools. GMGN picks its own pool and update timing, so its chart can differ from the Codex figures beside it.
- **6-hour window** replaced by Codex's 4h and 12h windows.
- **NEAR and TON USD valuation** in the portfolio is unavailable (not Codex networks); amounts still show.
- **DexScreener pair links** removed. Pool rows link to the chain explorer.
- **Holder counts** depend on the Codex plan.

## Setup and check

Set `CODEX_API_KEY` (server secret) and optionally `CODEX_REQUESTS_PER_MINUTE`. `GMGN_API_KEY` and
`GMGN_REQUESTS_PER_MINUTE` are obsolete and can be deleted. Check connectivity without printing data:

```bash
cd backend && python -m app.market_check --network solana --token <token address>
```

Exit code 0 means Codex answered (`ok`, `no_pool` or `not_indexed`).
