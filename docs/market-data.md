# GMGN market terminal

Helm's launch workspace uses **GMGN only** for its token chart and market metrics. The former custom candle renderer,
DEX Screener integration and GeckoTerminal integration have been removed. There is no alternate market provider.
Helm wallet balances, supply transparency, activity, previews, fees, signing and trade execution remain independent.

## Interfaces and setup

- Chart: `https://www.gmgn.cc/kline/{chain}/{token}?theme=dark&interval={interval}`. Only the parameters in
  [GMGN's embed guide](https://docs.gmgn.ai/index/cooperation-api-integrate-gmgn-price-chart) are used.
  Intervals: `5`, `15`, `60`, `240`, `1D`. The chart requires no Helm API credential.
- Metrics: backend-only `GET https://openapi.gmgn.ai/v1/token/info`, with `chain`, `address`, Unix-seconds
  `timestamp` and a fresh UUID `client_id`; authentication is the `X-APIKEY` header. No private key or signed trade
  calls are needed. The response supplies both token activity and main-pool details, avoiding a duplicate pool request.
- Helm: authenticated `GET /launches/{id}/market`. Ownership is checked before provider access. Copied drafts resolve
  to the original token; after deployment the launch's own chain/address takes over. Symbols never select tokens.
- The retired `/market/candles` route returns 410 for owned launches, advising old clients to reload.

Set backend `GMGN_API_KEY` to your own key from [GMGN API access](https://gmgn.ai/ai). Do not use GMGN's public demo
key in production. Never put this secret in a `VITE_` variable, logs or the browser. No frontend environment variable
is required. Optional `GMGN_REQUESTS_PER_MINUTE=30` caps this server's shared budget (range 1–300); choose a value
within your plan. GMGN requires IPv4 egress and a synchronized server clock. `GECKOTERMINAL_API_URL` is obsolete.

## Coverage

| Helm chain | GMGN token API ID | Documented chart embed |
|---|---|---|
| Solana | `sol` | `sol` |
| BNB Chain | `bsc` | `bsc` |
| Ethereum | `eth` | `eth` |
| Base | `base` | Not documented; unavailable |
| Robinhood | `robinhood` | Not documented; unavailable |
| Arc | `arc` | Not documented; unavailable |
| TRON, NEAR, TON | Not supported by the published token command | Unavailable |

API coverage follows the official repository's current token command and validation list, which is newer/broader
than the introductory Agent API page. An allowed chain is not a guarantee that any particular launchpad/pool is indexed.
Do not extend iframe paths by analogy with API chain IDs. Runtime indexing and plan availability can differ.

## Meaning of the numbers

The main pool is selected by GMGN; its address, DEX, quote asset and liquidity are labelled as such. The published
token-info response exposes one main pool, not a list of all pairs, so Helm does not invent a pool selector.
The iframe is token-based and its pool routing cannot be controlled through documented parameters.

USD price, volume, swaps, buys, sells and holder count use documented token fields. They are **token-wide**, not
main-pool trade counts. Percentage change is calculated only from GMGN's current price and the documented starting
price of the window. No candle history is reconstructed. Missing, malformed, nonfinite or negative amounts remain
missing, while actual zero is preserved. FDV, native price and market cap are not provided as direct fields by the
documented response used here and are omitted rather than inferred from each other or from supply.

## Failure and freshness

- Missing key: setup notice for metrics; the independently loaded chart can still work.
- No indexed main pool: explanation and explorer link; an independently indexed chart may still be available.
- Unsupported chains: explicit unavailable state, with Helm trading and wallet controls preserved.
- API errors: sanitized messages, never raw provider bodies. Old successful metrics may be shown for at most five
  minutes, always marked stale. Browser errors and elapsed time also mark retained metrics stale; expired values hide.
- Embed failures: loading/slow states, manual reload and a direct GMGN chart link. An iframe `load` event does **not**
  prove that candles rendered or are current. Helm cannot inspect or verify the cross-origin chart's freshness.
- API snapshots cache for 60 seconds; concurrent identical requests coalesce. Cache is bounded to 256 entries.
  Failures are cached briefly. Polling occurs once a minute in visible browser tabs and stops for setup/unsupported states.
  GMGN 429 cooldowns respect `X-RateLimit-Reset`, `reset_at` and numeric `Retry-After`, with at least 60 seconds of rest.
  Access failures (HTTP/API 401 or 403) retain `access_denied` throughout their 60-second cooldown, including across
  tokens and stale-cache responses. They are never reclassified as rate limits. Helm's own quota is `request_budget`.
  Error responses include `retry_after_s`; the terminal shows a countdown and skips manual/automatic refreshes until
  that delay has elapsed. The visible-tab polling interval remains one minute.
  The existing production single-process constraint makes this budget shared by the deployment; scale-out would need
  a shared cache and quota coordinator. The third-party iframe manages its own traffic.

## Diagnosing production access

After setting or replacing `GMGN_API_KEY`, restart/redeploy the **backend**: the running market client retains its
startup key. Confirm the key's read permissions and entitlement for `https://openapi.gmgn.ai`; if an IP allowlist is
configured, it must include the deployed backend's outgoing IPv4 address, not the operator's laptop address. Server
time must be synchronized (GMGN validates request timestamps within five seconds).

From the deployed backend's `backend` working directory, using its existing environment, run one read-only check:

```sh
python -m app.market_check --network solana --token FDa5EVK4bJJy3dDJpvgXcDGfm9ymw8NZBt6BsSZ7Pk4F
```

This performs at most one token-info request, makes no trades and prints only state, sanitized error text and retry
delay. It exits 0 for a successful provider lookup (`ok` or `no_pool`) and 1 for failure or missing configuration.
Do not put keys on the command line. Avoid repeatedly running the check during provider cooldowns: each CLI run has
its own process/cache, and cannot see the application process's cooldown.

Actual failed upstream requests produce a `gmgn_market_failure` log containing only category, HTTP status, a bounded
numeric provider code (other values are `unreported`) and `json`/`non_json` response format. Cached failures produce
no duplicate log. No API keys, provider messages, response bodies, request headers or token addresses are logged by
this diagnostic. HTTP 401/403 identifies an access rejection, not its exact cause; `non_json` can help support
distinguish a gateway refusal from a JSON API rejection. Use the status/code with GMGN support rather than guessing
that every 403 is an expired key or an exhausted quota. No new environment variables are required.

## Sources and commercial deployment

Reviewed 2026-09-30: [official wiki](https://github.com/GMGNAI/gmgn-skills/wiki),
[token contract](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-token/SKILL.md),
[API client](https://github.com/GMGNAI/gmgn-skills/blob/main/src/client/OpenApiClient.ts),
[authentication](https://github.com/GMGNAI/gmgn-skills/blob/main/src/client/signer.ts),
[chain validation](https://github.com/GMGNAI/gmgn-skills/blob/main/src/validate.ts), and
[GMGN terms](https://gmgn.ai/static/tos.html).

The chart guide expressly invites website embedding; provider branding stays intact. The general terms restrict
unauthorized automation and do not establish an unrestricted public-data redistribution license. Before enabling
the server key for commercial public display, the operator must confirm that the API plan/any supplemental terms
cover Helm's use. The repository's MIT code license does not itself grant data redistribution rights. No website
scraping, private endpoints, hosted GMGN wallets or GMGN trading API have been integrated.

## Validation and remaining live check

Backend market tests cover authentication/ownership, the documented request contract, copied launches, unsupported
chains, missing credentials, malformed responses, missing numbers, cache bounds/coalescing, throttling and expiry.
Frontend tests exercise the exact embed URL builder, address validation and the separate chart-chain allowlist.
Browser review covers desktop/390px layouts, timeframe URLs, chart expansion, wallet selection, buy/sell review,
keyboard section navigation, missing-key, no-pool, stale and error states. No real transactions were submitted.

The public GMGN demo API returned the documented token-info response during initial verification. A later browser
check of the deployed HELM workspace confirmed that GMGN candles and volume render (the embedded chart was set to
market cap). Production market metrics still showed an access failure; chart loading is independent of API-key
access. The cooldown correction and diagnostics do not grant provider permissions or prove that the deployed key
works. Verify with the operator check above after deployment. Commercial display rights require the operator's plan.
