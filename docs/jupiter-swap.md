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

- **Solana only.** Cross-chain conversion — and same-chain conversion on EVM chains — is the
  separate **Bridge** action (below); the swap dialog links to it instead of imitating it.
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

---

# Bridge: cross-chain conversion (Relay)

The **Bridge** button in the wallets selection bar opens the chain-to-chain surface. It drives
`POST /bridge`, which is the Relay-powered route engine (`app/chains/relay.py`,
`app/services/routing.py`) — the same machinery that funds gas from a dollar balance.

## What it can do

- **Any supported chain → any supported chain**: Solana ↔ Base ↔ BNB Chain ↔ Ethereum ↔ Robinhood
  Chain. Send the source chain's dollar stablecoin (USDC, USDG on Robinhood) or its native coin;
  receive the destination's stablecoin or native coin. Four combinations: stable→native, native→stable,
  native→native, stable→stable. **SOL → USDC** and **USDC → gas anywhere** are the two the UI nudges
  toward.
- **Same-chain EVM conversion**: picking two wallets on one EVM chain with different assets
  (e.g. USDC → ETH on Base) routes through Relay's same-chain swap. On Solana, use the Jupiter swap
  dialog instead.
- Both ends must be managed wallets on the account — the preview says so (`own_wallets_only`).

## How a route executes

1. **Preflight + quote** — Helm asks Relay for a quote and shows what should arrive, the **minimum
   after slippage**, Relay's fees, Helm's route fee (when `ROUTE_FEE_BPS` + `PLATFORM_FEE_WALLET_EVM`
   are set) and the time estimate. Balance and fee-reserve warnings block confirmation.
2. **Confirm** — the standard hash + idempotency + 2FA pipeline.
3. **Execution** — Helm re-quotes fresh, signs the **origin** transaction (EVM: to/data/value;
   Solana: Relay's instructions compiled by Helm), and Relay's solver fills on the destination chain.
   If the fill fails, Relay **refunds** the source wallet.
4. **Tracking** — `GET /bridge/{job_id}` reports Relay's live fill status and destination tx hashes
   while the job is in flight.

Arbitrary launch tokens are deliberately **not** routable here yet — only the native coin and the
dollar stablecoin on each chain, which keeps the preview honest and the failure modes refundable.

## Key handling (Relay)

`RELAY_API_KEY` is **optional** — without it Relay works at public rate limits; with it, higher.
Helm's own route fee only applies when both `ROUTE_FEE_BPS` and `PLATFORM_FEE_WALLET_EVM` are set
(Relay pays the fee out as USDC, claimable at relay.link). Browser code never calls Relay.

