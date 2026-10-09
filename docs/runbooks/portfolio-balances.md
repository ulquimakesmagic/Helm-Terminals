# Portfolio balance troubleshooting

The dashboard counts every managed wallet returned by Helm, including wallets on TRON, NEAR and Arc.
Connected and watch-only wallets are excluded and identified separately. TON native reads are supported by
the balance service, but Helm currently offers connected/watch-only TON wallets rather than managed TON keys.

The portfolio covers native coins plus the stablecoins listed in the chain table. TRON, NEAR and TON token
balances are not included in this aggregate. NEAR shows available balance after storage. Arc's native USDC is
counted once in the stablecoin total, not again through its ERC-20 interface or in the network-coin total.
USDC and USDG use an explicit $1 assumption; native prices come from DefiLlama. Missing or old prices are not
silently reused as fresh values. A zero native balance needs no price to be valued at zero.

## Incomplete balances

An HTTP 200 from `/portfolio` can contain partial results. Check `complete`, `incomplete_chains`,
`unpriced_chains`, `native_complete` and `stable_complete`; do not infer health from the HTTP status alone.
Unread amounts and totals with no successful contributing reads are null. Partial totals include only known
amounts and the dashboard labels them explicitly. The full managed-wallet count includes wallets beyond the
300-wallet scan limit; those unread wallets are marked incomplete rather than silently dropped.

For Robinhood Chain, check the existing `EVM_RPC_ROBINHOOD` setting. The endpoint must match the configured
chain ID and support `eth_chainId`, `eth_getBalance` and `eth_call` for USDG's `balanceOf`. A provider plan
that rejects contract reads needs an endpoint or plan that allows them. A 429 means the provider's quota is
exhausted; configure sufficient capacity. Helm respects endpoint cooldowns, including the public fallback,
and does not repeatedly probe a cooling endpoint for each wallet. It does not bypass provider access limits.

Portfolio requests share in-flight work per user and wallet list. Normal reads are cached for 20 seconds;
manual refreshes have a 5-second minimum interval. Wallet-list or enabled-chain changes invalidate that cache.
Wallets are read sequentially within each chain; different chains can be read concurrently. Each chain has
a 20-second read deadline. Successful reads are retained if that deadline expires; the remainder is marked
unavailable rather than holding the whole dashboard open indefinitely.

No new environment variables or database migrations are required for this fix.

## Verification

Run the portfolio, EVM, EVM-wallet, extra-chain and adapter tests, then build the frontend. Check the dashboard
with partial, unavailable, complete and empty portfolios at desktop and mobile widths. Confirm that both
wallet counts agree, unknown values do not appear as zero, the new chains are present, and provider diagnostics
do not fill the main balance table. Use synthetic wallets for UI tests; no live transaction is needed.
