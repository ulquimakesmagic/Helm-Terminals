# Helm audit remediation status

Updated 29 September 2026 against the current checkout (main, b9934e6, plus the uncommitted changes in this workspace). The attached professional audit remains historical evidence and has not been edited. This status does not certify production security or deployment state.

## Changes made in this workspace

| Finding | Current status | Evidence and remaining action |
|---|---|---|
| SEC-1 | Fixed in code; operator configuration required | Superadmin binding now fails closed unless the designated email and exact SUPERADMIN_USER_ID both match. The server warns when the ID is missing. Configure the owner's ID from an authenticated /auth/me response; do not guess it. |
| SEC-2 | Fixed by disabling bot spending | Telegram sessions still require the explicit `TELEGRAM_ALLOWED_USER_IDS` list, but the bot now exposes read-only commands and the API confirmation boundary rejects every non-web spending session. Per-action limits were not invented; bot spending stays off until each action is bounded and reviewed. |
| SEC-3 | Open; highest-priority code gap | Current web execution still accepts provider-built Solana/EVM transactions without semantic allowlist/effect validation. This finding is not fixed. Verified per-provider program/contract, recipient, token, and method policies plus preview-bounded simulated effects are required; no addresses or ABI selectors were guessed, and a partial validator was not added because it would leave a false sense of safety. |
| SEC-4, PRIV-3 | Fixed in code; proxy configuration required | Forwarded IP headers are ignored unless the immediate peer falls within configured proxy CIDRs; session and audit IPs use the same resolver. Configure TRUSTED_PROXY_CIDRS only from verified hosting-provider ranges. |
| SEC-5 | Partly fixed | Registration attempts are limited per resolved client address. Email verification before creating signer wallets remains open because no email delivery provider is configured. |
| SEC-6 | Production verification pending | Existing static-serving headers and report-only CSP from the audit commit are preserved. Observe real CSP reports for at least a week before considering enforcement; this workspace cannot verify production headers or wallet-connect flows. |
| SEC-7, SEC-9, SEC-11 | Carried from audit commit | The HTTPS-only provider-link filter, generic signer errors, and bounded input/image handling remain in the current checkout. Full regression verification is pending. |
| SEC-8 | Partly fixed in audit commit | Enabling 2FA revokes other web sessions. Password reset/change, recovery codes, and idle timeout remain open. |
| SEC-10 | Fixed in code; targeted tests passed | Metadata URLs are limited to public HTTPS on port 443; the production fetcher connects through an aiohttp resolver pinned to the exact public IP set that was checked, retains TLS hostname validation, disables redirects and proxy environment use, and enforces response size/time limits. The deterministic MockTransport test still exercises metadata copying; a resolver test verifies there is no second hostname lookup. |
| SEC-12, REV-4 | Partly fixed | Referral payout changes require password and fresh 2FA; creation is capped at 10 codes per account; payout to a wallet known to belong to a referred Helm account is rejected and flagged in the admin audit log. This cannot detect external wallet ownership or related accounts outside Helm. |
| SEC-13, REL-3 | Partly fixed | SIGNER=local is refused in production and test environments. Public /health is minimal; /health/ready checks the database and signer setup; /admin/config exposes configuration presence without values, and the Admin Operations tab displays it. External-provider reachability is not probed by readiness. |
| SEC-14 | Open | Runtime dependencies are still not locked or split into runtime/test sets; dependency audit CI is not added. |
| SEC-15 | Open | No new web spend cap or step-up threshold was selected. This requires an explicit product policy before changing trade behavior. |
| SEC-16 | Partly open | Job exceptions are logged by the existing worker path; structured authentication events and actionable alert delivery remain open. |
| SEC-17 | Carried from audit commit; package verification blocked | Pillow is constrained to the reported fixed release range in requirements, but Pillow is absent from this local virtual environment, so image tests and a current audit could not run here. |
| SEC-18 | Open | React Router upgrade remains a breaking dependency task; current dependency advisories need a fresh audit after dependencies are installed. |
| REV-1 | Fixed for Helm trade fees in code; tested | Solana and EVM signed transaction identifiers are committed before broadcast. Solana trade fees create pending `FeeRecord` and `RevenueEvent` rows atomically with the signature; `/jobs/{id}` rechecks pending signatures/receipts and resolves the fee ledger only on a terminal chain result. A durable background queue is still open. |
| REV-2 | Partly fixed | Admin revenue now shows confirmed referral shares owed as a separate read-only liability total, excluded from Helm revenue. An idempotent, explicitly confirmed payout workflow remains open; no arbitrary treasury withdrawal control was added. |
| REV-3 | Fixed in code | Relay success now replaces the quote amount with the provider-reported raw amount. If Relay confirms collection without a parseable raw amount, the ledger stores an unknown amount rather than treating the quote as received. Non-final statuses remain pending. |
| REV-5 | Partly fixed | Admin revenue warnings now flag effective fee settings outside admin bounds. The configured rates, recipients, treasury addresses, and ROUTE_FEE_BPS default of 30 bps were not changed. |
| REV-6 | Partly fixed | Admin asset/source summaries now keep estimated raw amounts out of measured received/pending/claimable/failed totals and show estimated values separately with their underlying status. Receipt-log parsing for EVM DEX fees remains open; estimated sell fees must still be treated as estimates. |
| REV-7 | Partly fixed | Failed and pending Solana trade-fee events now remain visible in the admin ledger and totals; separate-transfer fees are rechecked by signature. Waived/skipped fees and all other fee paths still need complete reporting. |
| REV-8, REV-9, REV-10 | Open | Solana creator-share recheck recording, Bags/Flap partner entries, and confirmed-rate snapshots at send time remain. |
| REV-11 | Carried from audit report | The audit's local fee-math check remains the recorded evidence; no fee formulas or live rates were changed here. |
| PRD-1, REL-2 | Partly fixed in code; tested | Solana and EVM managed-wallet sends persist their deterministic signature/hash before broadcast. Unconfirmed jobs can recheck receipts; EVM deploy recheck can recover its hash from the job ledger, and predicted/mint addresses are saved before sending. Uncertain launches remain non-redeployable. A process crash before the first signature, long-running in-process workers, multi-replica coordination, and a durable queue remain open. |
| PRD-2, PRD-3, PRD-4, PRD-5, PRD-6 | Carried from audit commit | Transfer-lock checks, bot unconfirmed/explorer messaging, external-wallet pending states, unknown-decimal handling, and portfolio price age are present in the current checkout. Full regression verification remains pending. |
| PRD-7 | Open | docs/SPEC.md still needs a behavior-by-behavior reconciliation. |
| REL-1 | Fixed in code; local tests passed | Added a frozen Alembic baseline, async database configuration, migration instructions, and SQLite tests for a fresh bootstrap and adoption of an existing `create_all` schema without data loss. No production database was stamped or migrated; production adoption still requires a verified backup and schema comparison. |
| REL-4 | Partly fixed in audit commit | Hidden-tab polling is paused. Concurrent, bounded, cached workspace RPC reads remain. |
| REL-5 | Fixed in code and documented, with durability work open | PostgreSQL startup takes a session advisory lock and refuses a second API replica; SQLite dev/test is unchanged. This enforces the single-replica limit while jobs, nonce coordination, and caches remain process-local. Tests cover lock acquisition, refusal, cleanup, and the SQLite path. |
| REL-6, REL-8 | Open / operator work | Production RPC/key setup remains operator work. A backup/restore procedure is documented in `docs/runbooks/database-backup-recovery.md`, but no production/staging restore drill was performed. |
| REL-7 | Carried from audit report | Existing provider retry and chain-ID behavior remains; no new provider integration was introduced. |
| PRIV-1, PRIV-2 | Open; qualified review required | Export/deletion and retention policy need an approved data map and qualified privacy/legal review. No user data was accessed or changed. |
| PRIV-4 | Fixed in code | Admin user searches and plain list views are now audited without storing search text. |
| PRIV-5 | Carried from audit commit | Signing out clears local TON and NEAR connector sessions. |
| PRIV-6 | Open | A real support contact, status information, and user-facing suspension notice still need product/operator inputs. |
| PERF-1, UX-1–UX-7 | Carried from audit commit | Route splitting and the audit's UI fixes remain present; no unrelated redesign was made. |
| A11Y-1–A11Y-5, A11Y-7 | Carried from audit commit | Existing contrast, type, link, target-size, table-region, and reduced-motion work remains. A fresh visual/axe run is pending. |
| A11Y-6, A11Y-8 | Carried from audit report | The report's existing positive findings remain unchanged. |

The market chart now has an expandable text summary and a scrollable table of the displayed OHLCV candles; the table is outside the chart image semantics and remains available to keyboard and screen-reader users. No real screen-reader walkthrough was available in this task, so that verification remains outstanding.

## Verification

- Focused backend checks: 29 earlier tests passed; 8 transaction-recovery/EVM checks passed; 14 trade/fee regressions passed; the targeted bridge, distribute, referral-liability, failed-fee, and migration set passed (9 tests); final post-change transaction-recovery, EVM funding/withdrawal, admin fee, and migration checks passed (11 tests). Python compileall passed after the latest changes.
- Alembic tests passed against temporary SQLite databases: a fresh baseline upgrade and stamping/upgrading an existing schema both passed. The local virtual environment used Alembic 1.20.0; no production database was accessed.
- An early full backend run, started before edits but finishing while edits were underway, reported 302 passed and 3 failed. All three trace to Pillow being absent from the virtual environment: two image tests could not import it, and Meme.Cooking returned a ModuleNotFoundError instead of a deploy preflight. A clean full-suite run remains outstanding; Pillow is declared in requirements but absent locally, and the request to install it in the workspace virtual environment was rejected.
- npm run build reached TypeScript checking, then failed because @tonconnect/ui and @hot-labs/near-connect are missing from frontend/node_modules; the related implicit-any errors are downstream of those absent packages. No type errors were reported in the edited Admin page.
- No production deploy, production data access, wallet transaction, fee-setting change, push, or migration was performed.

## Operator configuration

New or clarified variable names only: SUPERADMIN_USER_ID, TRUSTED_PROXY_CIDRS, TELEGRAM_ALLOWED_USER_IDS, and optional REFERRAL_MAX_CODES_PER_USER (default 10). Production must use SIGNER=turnkey. Do not populate proxy ranges from guesses; leave them empty until verified. No secret values belong in this file.

This task adds no new environment variables and changes no live settings. `aiohttp` is now declared directly because the pinned metadata-fetch transport uses it. Telegram spend commands are disabled in code; linking and read-only status still require the existing allowlist.

## Current verification snapshot

- Focused regression runs passed: 13 tests covering PostgreSQL singleton enforcement, Telegram read-only/API guard, metadata DNS pinning and clone behavior, admin estimated-versus-measured totals, and Relay/admin ledger behavior; the final 11-test pass rechecked the runtime lock, bot guard, metadata resolver, and revenue summary in 37.62 seconds. Python `compileall` passed after the final backend edits.
- The most recent full backend run reported 321 passed and 3 failed. All 3 failures were caused by Pillow being absent from the local virtual environment: image-limit testing could not import PIL, the Meme.Cooking deploy preflight could not import PIL, and the PNG icon assertion could not import PIL. That run predates the latest Telegram, metadata transport, and estimated-revenue edits, so it is not a full validation of this exact working tree.
- `npm run build` remains blocked by missing `@tonconnect/ui` and `@hot-labs/near-connect` packages in `frontend/node_modules`; the resulting connector typing errors are downstream. Re-run after dependencies are restored.
- The audit branch and `main` currently point at the same `b9934e6` audit commit. Remediation work is uncommitted in the working tree. No commit, push, production access, deployment, production migration, fee/recipient change, or transaction was performed.
