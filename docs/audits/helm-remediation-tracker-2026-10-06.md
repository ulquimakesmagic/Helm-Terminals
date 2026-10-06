# Remediation tracker — audit of 6 October 2026

Branch `audit-remediation-2026-10` (local, not pushed). Statuses: **Fixed** (code and tests in this branch),
**Partial**, **Unresolved**, **Externally blocked** (needs operator, provider, legal or production evidence).
"Fixed" means fixed in source with tests; none of it is deployed or production-verified.

## Findings from this audit (`helm-audit-2026-10-06.md`)

| ID | Severity | Status | Evidence | Remaining |
|---|---|---|---|---|
| HT-01 Solana provider transactions unbounded | High | **Fixed (source)** | `services/solana_guard.py`, `SolanaRPC.wallet_effects`, wired into trades, deploys (+setup/cleanup), claims, Pump.fun fee-share, Jupiter swaps, Relay Solana steps. `tests/test_spend_bounds.py` (drain, delegate, owner/close-authority change, reassignment, over-sell, floor, fail-closed RPC, real JSON-RPC shapes) | Simulation-based: a program that behaves differently when simulated is not caught. Allowances (1% provider fee, 0.015 SOL trade / 0.1 SOL create overhead) should be tuned after observing real PumpPortal/Bags/Meteora transactions with small amounts |
| HT-02 Relay EVM steps unbounded | High | **Fixed (source)** | `services/routing.check_route_call`, `quote_approval.check_relay_recipient`; bridges, auto-funding, fund-cash, EVM swaps. Tests in `test_spend_bounds.py` | Relay deposit contract addresses are not pinned (no verified list); loss is bounded to the routed amount |
| HT-03 Swap floor not bound | High | **Fixed (source)** | Jupiter and Relay same-chain swaps use HMAC quote approvals; execution refuses a lower re-quote; Solana output floor enforced in simulation. `test_swap_never_signs_below_the_reviewed_floor` | — |
| HT-04 Swap hash includes volatile fields | Medium | **Fixed** | Hash covers wallet, assets, raw amount, slippage, fee terms; display fields outside the hash. `test_price_moves_no_longer_invalidate_a_swap_confirmation`; UI re-previews automatically on 409 | — |
| HT-05 Contract artifact check fails on Linux | Medium | **Fixed** | `.gitattributes` pins `contracts/*.sol` to CRLF; `npm run check` passes on Linux, artifacts unchanged | Confirm on GitHub Actions after push |
| HT-06 Market data not Codex-only; substituted prices | Medium | **Fixed (source)** | `services/market.py` rewrite, `routes/market.py` candles, `portfolio.py`, frontend `Market.tsx`/`CandleChart.tsx`; `tests/test_market.py` (26), portfolio tests | **Externally blocked:** live verification with a real `CODEX_API_KEY` (field availability per plan, holders, TRON addresses) |
| HT-07 Unbounded request bodies | Medium | **Fixed** | `main.BodyLimit` (3.5 MB, declared or streamed), `image_b64` max length. `tests/test_hardening_2026_10.py` | Also set a body limit at the edge proxy if one is used |
| HT-08 Jupiter config shadowed | Low | **Fixed** | Single declaration; keyed → api.jup.ag, keyless → lite | Live check of swaps without a key |
| HT-09 Public profile exposure | — | **Withdrawn** | Verified not an issue | — |
| HT-10 OpenAPI docs public in production | Low | **Fixed** | Disabled when `ENV=production`; test | — |
| HT-11 Idempotency race → 500 | Low | **Fixed** | `deps.begin_spend` returns the winning job; concurrent test | — |
| HT-12 Test blind spots | Low | **Fixed** | Duplicate test removed; deterministic folder merge (keeps the folder with the most wallets, not a random one); varying-quote swap tests added | — |
| HT-13 Launch socials unvalidated | Low | **Fixed** | https-only, no credentials; test | — |
| HT-14 Dev-only npm advisory | Info | **Fixed** | Lockfile refresh; `npm audit` 0 | — |
| HT-15 Reload/deep links lose destination; no 404 | Medium (UX) | **Fixed** | `next` return path (same-site only, tested), not-found page, server 404 status (tested) | Memory-only tokens still require sign-in after reload (deliberate AUD-15 hardening) |
| HT-16 Float fee basis | Low | **Fixed** | `executor.sol_to_lamports` (Decimal, half-even); test | — |

## Earlier findings (`helm-professional-audit.md`)

| ID | Status | Evidence / remaining |
|---|---|---|
| AUD-01 | **Partial → mostly fixed** | Kyber (earlier) plus Solana providers and Relay (HT-01/02). Remaining: no per-program allowlist for opaque Solana programs; Four.meme login and other EVM adapters build calldata locally against pinned contracts and were not re-reviewed call by call |
| AUD-02 | Fixed (earlier, verified) | `execution_journal.can_resolve` |
| AUD-03 | **Fixed** | Bridges earlier; swaps now (HT-03) |
| AUD-04 | **Fixed (source)** | CI paths earlier; artifact drift now (HT-05); npm audit job now passes (HT-14). Branch protection is operator work |
| AUD-05 | Fixed (earlier, verified) | DEX fees pending until trace-verified |
| AUD-06 | **Superseded/fixed** | Codex-only rebuild (HT-06) |
| AUD-07 | **Fixed** | Label accurate; no nearest-day substitution; UI no longer says unpriced days "count as $0" |
| AUD-08 | Fixed (earlier, verified) | Pons gated; eligibility check before deploy. Independent splitter review remains **externally blocked** |
| AUD-09 | Partial | Recovery flows exist; needs SMTP configured; email verification not required before wallet creation (product decision) |
| AUD-10 | Fixed | 0 npm advisories (all/prod), pip-audit clean |
| AUD-11 | Fixed (earlier, verified) | Turnkey signer checks the lease before every signature |
| AUD-12 | Fixed (earlier, verified) | Off-loop Argon2, aggregate limits |
| AUD-13 | Fixed (earlier, verified) | Health revalidation |
| AUD-14 | **Fixed** | Landing copy now matches swap/route behaviour |
| AUD-15 | Fixed (earlier) + HSTS added to static server | Enforced CSP verified in the browser harness (inline script refused) |
| AUD-16 | **Externally blocked** | Restore drill, alerting, deployment SHA identity need production access |

## External release dependencies (cannot be closed from source)

Real-funds rehearsal of each launchpad with small amounts after HT-01 (watch for false refusals), live Codex key
verification, Turnkey policy review (per-key limits, allowlists), production CORS/proxy CIDRs, SMTP for recovery,
legal review and operator details for the legal pages, independent smart-contract review before Pons activation,
backup restore drill, monitoring/alerting.
