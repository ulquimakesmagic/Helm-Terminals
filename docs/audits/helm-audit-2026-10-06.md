# Helm Terminals — engineering, security and product audit (6 October 2026)

**Audited revision:** `main` at `c69ddc0` (4 Oct 2026, "Fix CI paths and the contract drift check; add the audit, remediation tracker and evidence").
**Method:** source review of backend, frontend, contracts, CI and documentation; the full local test suites; dependency scans; targeted reproduction tests written for this audit. No production system, database, secret, wallet, or signer was accessed. No transaction was signed or broadcast. The live site could not be reached from the audit workspace (egress policy), so deployed-behaviour claims below are limited to what the source establishes.

This report does not claim that Helm has no vulnerabilities. It records what was checked, how, and what was found.

## 1. Baseline evidence

| Check | Result | Evidence |
|---|---|---|
| Backend suite (`pytest`, excluding the bytecode job) | 483 passed, 2 skipped (PostgreSQL-only) | `docs/audit-evidence/2026-10-06/baseline-backend-tests.txt` |
| Contract bytecode tests | 6 passed | local run |
| `contracts: npm run check` (artifact drift) | **Fails on a Linux checkout** | see HT-05 |
| Frontend `npm run build` + `npm test` | Build OK (main chunk 351 kB / 117 kB gzip; TonConnect chunk 448 kB); 11 tests pass | local run |
| `npm audit` (frontend, all deps) | 1 High (`source-map-js`, dev-only, via Vite/PostCSS) | `baseline-npm-audit.json` |
| `npm audit --omit=dev` (frontend, contracts) | 0 | local run |
| `pip-audit -r backend/requirements.lock` | No known vulnerabilities | local run |
| Audit reproductions | 4 of 4 defects reproduced | `baseline-reproductions.txt` (each test asserts the *defective* behaviour, so PASSED = reproduced) |

One backend test (`test_folders.py::test_merge_duplicates_and_isolation`) failed once in a `-x` run and passed in the full run: it is order-dependent.

## 2. Platform map

**Backend:** FastAPI modular monolith, 139 routes (inventory: `routes.txt` in the audit workspace; every non-public route depends on `current_user`, account-security and admin routes on `web_user`, admin routes on `require_role`). Single API replica enforced with a PostgreSQL advisory lock plus a fenced lease checked before EVM signing. Jobs run in-process; every spend is a preview → hash-bound confirmation → `Job` with an idempotency key.

**Custody:** managed wallets are signed by Turnkey (`SIGNER=turnkey`, required in production) or the local dev signer (refused in production and test). Watch-only wallets cannot sign; "connected" TRON/NEAR/TON/Arc wallets sign in the user's own wallet. Withdrawals, token sends and funding non-managed wallets always need TOTP.

**Chains and launchpads (registry `backend/app/chains/registry.py`):**

| Chain | Launchpads implemented | Transaction builder | Notes |
|---|---|---|---|
| Solana | Pump.fun, LetsBonk, Bags, Meteora DBC | Provider-built (PumpPortal, Bags API, Jupiter Studio) | Signed after a signer-identity check and simulation only — HT-01 |
| BNB Chain | Four.meme, Flap | Local calldata against pinned contracts; Four.meme signs a provider login message | Graduated tokens trade through KyberSwap (pinned router, decoded and bounded) |
| Base | Clanker, Flap (tax tokens) | Local calldata | |
| Robinhood Chain | Clanker, Flap, Pons (gated off until the splitter is reviewed and deployed) | Local calldata | Kyber router coverage on 4663 unverified |
| Ethereum | Uniswap V2 | Local calldata | |
| TRON / NEAR / Arc | SunPump / Meme.Cooking / Tolly | Mixed (see `chains/extra_pads.py`) | Mainnet only |
| TON | None (GasPump planned) | TON Connect transfers only | |

**Cross-chain/same-chain routing:** Relay (provider-built EVM calldata and Solana instructions). **Solana swaps:** Jupiter (provider-built). **EVM DEX:** KyberSwap (decoded against a pinned router and the reviewed intent).

**Revenue paths:** Solana trade fee in-transaction (platform + referral split), KyberSwap router fee (pending until a trace verifies it), Relay app fee (claimable), creator-share configuration on Pump.fun/Bags/Clanker (configured, amount unknown), Flap commission (configured), Pons 94/6 splitter (gated). Treasury addresses are receive-only and set by the superadmin with password + fresh TOTP.

**Market data:** Codex (primary when keyed) → DexScreener fallback → GMGN; chart is a GMGN iframe; portfolio and dashboard USD values use DefiLlama. See HT-06.

## 3. Verification of earlier findings (`docs/helm-professional-audit.md`)

| Finding | Status at `c69ddc0` | Evidence |
|---|---|---|
| AUD-01 provider transactions vs intent | **Partial.** KyberSwap is decoded against a pinned router and an `ExecutionIntent` (`services/kyber_intent.py`). Solana provider paths and Relay are not bounded. | HT-01, HT-02 |
| AUD-02 interrupted jobs falsely confirmed | Fixed in code. `execution_journal.can_resolve` requires a sealed dispatch, every required step and destination settlement. | `routes/launches.py:1254`, `tests/test_remediation.py` |
| AUD-03 bridge minimum not bound | Fixed for Relay bridges and auto-funding (`quote_approval.py` HMAC-bound floor, checked before each step). **Not fixed for swaps.** | HT-03 |
| AUD-04 CI | Paths fixed; artifact check now runs before build. **The check fails on Linux** because artifacts were compiled from CRLF sources. Frontend audit job fails on a dev-only High. | HT-05 |
| AUD-05 DEX revenue inferred | Fixed: recorded `pending`/estimated; trace verification promotes it. | `services/revenue.py:46–76` |
| AUD-06 pair metrics labelled token-wide | Fixed in labels (`metric_scope`). Superseded by the Codex-only rebuild. | HT-06 |
| AUD-07 SOL-only net flow | Labelled correctly. Missing daily prices are back-filled from the nearest day, contradicting the label. | HT-06 |
| AUD-08 Pons gating | Fixed: `needs_setup` blocks Pons unless the splitter is enabled and deployed; eligibility checked before deploy. | `chains/registry.py:96`, `routes/launches.py:1284` |
| AUD-09 recovery | Implemented: verified-email reset, recovery codes, password change, 24 h spend hold, notifications. Requires SMTP configuration. Email verification is not required before wallets are created. | `routes/recovery.py` |
| AUD-10 dependencies | Vite 7 / React Router 7. One dev-only High remains. | HT-14 |
| AUD-11 lease | Fixed: the Turnkey signer calls `execution_lease.authorize_signer_ref` (lease, owner, suspension and recovery hold) before every signature on every chain; EVM nonces are reserved in the database. | `signing/turnkey.py:175,194`, `services/evm_exec.py:124` |
| AUD-12 password work | Fixed: bounded off-loop Argon2; aggregate per-origin login limit. | `services/passwords.py`, `routes/auth.py:91` |
| AUD-13 stale health | Fixed: 30 s revalidation, visibility/online triggers. | `frontend/src/session.tsx` |
| AUD-14 capability claims | Telegram copy fixed. The landing page still says execution confirms "that preview, not a new one", which swaps do not do. | HT-03 |
| AUD-15 browser session | Token is memory-only; enforced CSP served by `server.mjs`. | HT-15 (UX consequence) |
| AUD-16 operations | `require_schema` in production; backup runbook exists; restore drill not evidenced. | External |

## 4. New findings

Severity: **High** = material funds, consent or release-integrity defect; **Medium** = correctness, availability or control weakness; **Low** = limited impact. "Confirmed" means reproduced or directly evident in source; "suspected" means plausible but not demonstrated.

### HT-01 — Solana provider-built transactions are signed without any spend bound (High, confirmed)

- **Where:** `backend/app/services/executor.py:98` (only `check_solana_signers`), `services/tx_guard.py:177`; callers `chains/pumpfun.py:161,224` (PumpPortal trade-local), `chains/letsbonk.py` (PumpPortal), `chains/bags.py:132–228`, `chains/meteora_dbc.py:173–225` (Jupiter Studio, `submit_via` relaxes the signer check), `routes/wallets.py:806` (Jupiter swap), `services/routing.py:55–71` (Relay Solana instructions).
- **Reproduction:** `test_r1_solana_drain_passes_signer_only_guard` builds a transaction that transfers 1,000 SOL from the wallet to an unrelated address; the guard accepts it. Simulation would also succeed if the balance exists.
- **Impact:** a compromised or malicious response from PumpPortal, Bags, Jupiter or Relay can drain every managed Solana wallet that trades, swaps, deploys or routes, despite the user approving a much smaller action. The trust boundary is a third-party HTTP API.
- **Remediation:** before signing, simulate the unsigned transaction with the wallet's system account and every SPL / Token-2022 account it owns as `accounts`, and enforce: lamport decrease ≤ approved debit (amount + Helm fee + bounded network/rent overhead); no decrease of any token except the approved one, by at most the approved amount; no owner, delegate or close-authority change; the wallet stays a System-owned account. For swaps, also enforce the reviewed minimum on the output token. Fail closed on any simulation or parsing error.
- **Residual after fix:** a malicious on-chain program can behave differently in simulation than in execution. Simulation-effect checks are a strong control against drain-shaped payloads, not a proof of intent.
- **Confidence:** high.

### HT-02 — Relay EVM steps can move more value than approved (High, confirmed)

- **Where:** `services/routing.py:72–82`, `services/tx_guard.py:51`. Tracked jobs call `send_evm_tracked` without `require_contract_recipients`; any call with empty calldata is accepted; unknown selectors are accepted; `value` is unbounded. `quote_approval.check_relay_bounds` validates only the provider-declared `minimumAmount`, not the transactions or the destination recipient.
- **Reproduction:** `test_r2_evm_plain_transfer_to_eoa_passes_guard` — a 1,000 ETH plain transfer to an externally owned address passes with `max_allowance=1` and `require_contract_recipients=True`.
- **Impact:** a compromised Relay response can send the wallet's native balance anywhere during a bridge, auto-fund or EVM swap.
- **Remediation:** bound every Relay EVM item: native `value` ≤ approved native input (0 for token input); ERC-20 calls only on the origin token, `approve`/`transfer` amounts ≤ approved amount; reject unknown selectors on the origin token contract; require the quote's recipient to equal the destination wallet. Apply the Solana effect guard (HT-01) to Relay Solana steps.
- **Confidence:** high.

### HT-03 — Swaps re-quote at execution without the reviewed floor (High, confirmed)

- **Where:** `routes/wallets.py:806` (Jupiter), `:895` (Relay same-chain).
- **Reproduction:** `test_r4_execution_requote_ignores_reviewed_floor` — preview shows ≈12.16 USDC minimum; the execution quote's minimum is 5.0 USDC; the job is signed and confirmed.
- **Impact:** a user approves one floor and receives up to the provider's new floor. Contradicts the landing-page promise.
- **Remediation:** issue an HMAC-bound quote approval (as bridges already do) carrying the minimum output; at execution, refuse a fresh quote whose minimum is lower, and enforce the floor in the simulated effects (HT-01).

### HT-04 — Swap confirmations hash volatile quote fields (Medium, confirmed)

- **Where:** `routes/wallets.py:791` (Jupiter: `min_out` rounded with `:g`), `:880` (Relay: the whole `summarize()` output, which includes `request_id`).
- **Reproduction:** `test_r3_moving_jupiter_quote_makes_swap_unconfirmable` — a 1.6% price move between preview and confirm returns 409. For Relay, each quote has a new request ID, so in production every EVM swap confirmation should mismatch (not reproduced live; the existing test uses a static mock).
- **Impact:** swaps are intermittently (Jupiter) or likely always (Relay) impossible to confirm, while very small adverse moves that round identically are accepted.
- **Remediation:** hash only the stable intent (wallet, mints, raw amount, slippage, fee terms); bind the floor through the quote approval.

### HT-05 — Contract artifact check fails on Linux (Medium, confirmed)

- **Where:** `contracts/build.mjs`, `.gitattributes` (pins JSON artifacts but not `.sol` sources).
- **Verification:** `npm run check` fails on a Linux checkout; converting the three `.sol` files to CRLF reproduces every committed artifact byte-for-byte. Only the CBOR metadata hash differs, so runtime logic is identical, but CI's `contracts` job fails on every push and explorer source verification depends on the developer's line endings.
- **Remediation:** pin `contracts/*.sol text eol=crlf` so every checkout compiles the reviewed bytes (or rebuild from LF and re-review; the splitter is not deployed).

### HT-06 — Market data is not Codex-only and some values are substituted (Medium, confirmed)

- **Where:** `services/market.py:437–473` (Codex → DexScreener → GMGN fallbacks), `frontend/src/components/GmgnChart.tsx:16` (GMGN iframe), `services/portfolio.py:24,63,103–109` (DefiLlama current and historical prices; unpriced days filled from the nearest priced day or today's price), `routes/misc.py:82` (label says missing prices remain unavailable).
- **Impact:** contradicts the product requirement; the dashboard can show USD values derived from a different day's price without disclosure; chart freshness cannot be verified; NEAR/TON/TRON coverage is inconsistent.
- **Remediation:** a single server-side Codex client (`filterTokens`, `listPairsWithMetadataForToken`, `getBars`, `getTokenPrices`) with caching, de-duplication, bounded retries and honest states; render charts from Codex candles; remove the substitution.

### HT-07 — Unbounded request bodies (Medium, confirmed in source)

- **Where:** `routes/launches.py:146` (`image_b64: str | None` has no length limit; decoded before the 2 MB check); no global body limit in `main.py`.
- **Impact:** an authenticated user can make the single API process allocate very large buffers, degrading or crashing the only replica (which also runs every job).
- **Remediation:** cap `image_b64` at the base64 size of 2 MB and add a request-size middleware.

### HT-08 — Swap and auto-fund Jupiter configuration is shadowed (Low, confirmed in source; impact suspected)

- **Where:** `backend/app/config.py:88–89` and `:105–106` declare `jupiter_api_url`/`jupiter_api_key` twice. The later default (`https://api.jup.ag`) wins, so `chains/jupiter.py`'s keyless lite-endpoint fallback is never used.
- **Impact:** without `JUPITER_API_KEY`, Solana swaps call the keyed endpoint; behaviour depends on Jupiter's current policy (not tested live).
- **Remediation:** one declaration; keep the lite endpoint as the keyless default.

### HT-09 — Withdrawn after verification

An initial reading suggested public profiles reuse the private launch serializer. They do not: `routes/profile.py` has its own narrow `_launch_out` (name, symbol, network, launchpad, status, token address, quote symbol, dates), and the public page drops the ID and creation date. No exposure.

### HT-10 — Production exposes the OpenAPI schema and docs (Low, confirmed)

`main.py:131` uses FastAPI defaults (`/docs`, `/redoc`, `/openapi.json`). Not an authorization bypass, but it publishes the full attack surface. Disable in production.

### HT-11 — Concurrent confirmations with one idempotency key return 500 (Low, confirmed in source)

`deps.py:175–186`: two concurrent confirmations both miss the existing row; the second commit violates `UNIQUE(user_id, idempotency_key)` and surfaces as a 500 rather than the original job. Catch the integrity error and return the existing job.

### HT-12 — Test-suite blind spots (Low, confirmed)

`tests/test_wallet_export_swap.py:96` and `:144` define the same test name; the first is never run. `test_folders.py` is order-dependent. Swap tests use static quotes, which hid HT-03/HT-04.

### HT-13 — Launch social links are not validated (Low, confirmed)

`routes/launches.py:148` accepts any string; values are written into token metadata that other sites render. Require `https://` URLs.

### HT-14 — CI dependency audit fails on a dev-only advisory (Info, confirmed)

`npm audit --audit-level=high` reports `source-map-js` (GHSA-68fv-2mgg-jv7q) through Vite's toolchain. Not shipped to browsers; a lockfile refresh resolves it.

### HT-15 — Reload and deep links sign the user out and drop the destination (Medium UX, confirmed)

`frontend/src/api.ts:3–9` keeps the token in memory only (a deliberate AUD-15 hardening), and `App.tsx:123` redirects to `/login` without remembering the requested page; unknown routes redirect to `/` (`App.tsx:155`) and the static server answers 200 for every path. Keep memory-only tokens, but return the user to the requested page after sign-in, add a real not-found page and a 404 status for unknown paths.

### HT-16 — Fee basis uses binary floating point (Low, confirmed in source)

`services/executor.py:285` computes `int(float(amount) * LAMPORTS_PER_SOL)`; some decimal inputs truncate by one lamport. Use `Decimal` and round half-even so preview, transaction and ledger agree.

## 5. Coverage gaps (not findings)

- No live, fork or mainnet replay of any launchpad; no independent smart-contract review of the splitters; Turnkey organisation policies, production CORS/proxy configuration, backups and monitoring were not inspected.
- Not every adapter's provider schema was re-verified against current documentation; PumpPortal, Bags and Jupiter Studio formats can change without notice.
- TRON/NEAR/TON adapters were reviewed structurally, not exercised.
- No load test, penetration test, or screen-reader session.

## 6. Priority order

1. HT-01, HT-02, HT-03 (bound provider-built transactions and swap floors).
2. HT-04, HT-07, HT-11, HT-16 (correctness and availability).
3. HT-05, HT-14 (make CI a real release gate).
4. HT-06 (Codex-only market data), HT-15 (navigation), HT-10/HT-13 (exposure).
