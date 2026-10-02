# Jupiter swaps in the wallets surface

Status: **implemented behind the standard spend pipeline; not yet exercised on mainnet with a real
key.** The first live swap should be small and watched end to end.

## What it does

`POST /wallets/swap` converts two Solana tokens through Jupiter's aggregator from a managed wallet:

1. **Quote** — Jupiter's `/swap/v1/quote` (restricted intermediate tokens) prices the trade.
2. **Preflight** — Helm returns the standard review: input, minimum output at the chosen slippage,
   estimated output, route labels, price impact, and `platform_fee: none`. A ≥10% price impact adds
   a blocking warning. Nothing is sent.
3. **Confirm** — the usual `preflight_hash` + `idempotency_key` (+ 2FA when configured for the
   kind, like trades). A replayed confirmation returns the original job.
4. **Execution** — a **fresh** quote is taken (quotes expire; the reviewed price is an estimate),
   Jupiter's `/swap/v1/swap` returns an *unsigned* transaction with `wrapAndUnwrapSol`, and Helm
   signs it for the wallet through the standard executor: the tx_guard verifies the transaction
   needs no signature Helm doesn't hold, and it is simulated before broadcast.

## Boundaries, stated plainly

- **Solana only.** Cross-chain conversion stays on Relay routes ("Route funds" in the wallets
  selection bar); the swap dialog links there instead of imitating it.
- **`platform_fee: none`** — Helm charges no fee on swaps today; Jupiter's platform-fee field is
  not used. If a swap fee is added later it must appear in the preflight first.
- **Third-party trust** mirrors PumpPortal: Helm does not audit Jupiter's route. The guardrails are
  structural — signature checking, simulation, and the slippage bound are what Helm verifies, not
  the route's fairness. A malicious quote can, at worst, execute at the reviewed slippage floor.
- Tokens come from Jupiter's list (`/tokens/v2/search`); the preflight refuses a mint Jupiter
  doesn't know. SOL, USDC and USDT are offered first, then the user's own live launch tokens.

## Key handling

`JUPITER_API_URL` defaults to `https://lite-api.jup.ag` (no key, rate-limited). For production set
`JUPITER_API_URL=https://api.jup.ag` and `JUPITER_API_KEY` from dev.jup.ag — the key is sent as
`x-api-key` from the backend only; the browser never talks to Jupiter.

## Tests

`backend/tests/test_wallet_export_swap.py` runs the swap path against a fake Jupiter: preflight
shape and route display, confirm → job confirmed through the real executor with a wallet-signed
transaction, same-token refusal, non-Solana refusal, and slippage pass-through.
