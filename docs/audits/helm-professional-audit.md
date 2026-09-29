# Helm Terminals: professional audit and UI/UX review

| | |
|---|---|
| Date | 29 September 2026 |
| Code reviewed | `main` at `aa6638f` on the owner's machine (container copy `26fec78`, same content). The fixes in §9 are on branch `audit/2026-09-29`, not merged |
| Live site | https://helmterminals.xyz and https://api.helmterminals.xyz, public pages and public endpoints only |
| Prepared for | Product owner and engineering team |
| Status | **Final.** Findings (§3), the fixes made and rechecked (§9), and what remains open. Nothing is deployed. |

An audit narrows risk; it does not prove the absence of vulnerabilities. This is not a penetration test or an external
certification, and nothing in it is a legal or compliance opinion.

---

## 1. Executive summary

Helm is a working multichain launch and trading desk with careful money-movement design:

- Every spend is a server-built preview whose hash the confirmation must repeat.
- Idempotency keys make retries return the original job.
- Transfers out of the platform need 2FA.
- Signing keys live in Turnkey; the database holds only a key reference.
- An "unconfirmed" state is used instead of "failed" whenever a sent transaction might still land.

The revenue ledger is built on actual events, not volume, and keeps each amount in its own asset.

**Overall readiness: operating, with conditions.** No Critical findings. Before promoting Helm more widely, the owner
should:

1. **Pin the superadmin (SEC-1).** Set `SUPERADMIN_USER_ID` in production. Without it, whoever first registers the
   designated email on an empty or reset database becomes superadmin, because emails are not verified.
2. **Close the two known ways a launch can be deployed twice (PRD-1, REL-2).** An EVM deploy that times out is marked
   failed and can be redeployed. A server restart during any deploy can leave a launch that archive/restore returns to
   draft.
3. **Add schema migrations before the next column change (REL-1).** The database is created with `create_all` only.
4. **Serve the web app with security headers (SEC-6).** The live site sends no CSP, HSTS or frame protection
   (observed on production).
5. **Constrain the Telegram bot channel (SEC-2)** and **validate third-party-built transactions (SEC-3).** Both are
   design work, not quick fixes.

Revenue: the ledger is sound in design. Its main gaps are recording and reconciliation:

- Fees on trades that land late are never recorded (REV-1).
- A Relay reconciliation rule can wrongly zero a real fee (REV-3).
- Referral amounts Helm holds on behalf of referrers have no admin view or payout path (REV-2).

UI/UX: the product already has a coherent dark "terminal" design, and automated WCAG 2.2 checks found few issues. The
main gaps are:

- Chain lists that still show only the original five chains on the landing page, sign-in page, dashboard and metadata
  (UX-1).
- 19 different font sizes, some at 10–11.5 px, and a "faint" text colour at 3.2–3.8:1 contrast (A11Y-1, A11Y-2).
- Some small tap targets and colour-only links (A11Y-3, A11Y-4).
- The whole app ships in one 511 kB script (PERF-1).

The redesign in §9 addresses these without changing any fee rate or transaction behaviour.

**Since the first draft (see §9):** 22 findings are fixed and rechecked, 8 more are partly fixed, including the High deploy-duplication risk
(PRD-1 and the restart path of REL-2). Two more came out of the dependency and journey checks: a Pillow version cap
that kept a vulnerable release (SEC-17, fixed) and React Router advisories (SEC-18, open). Still open and most
important: pin the superadmin (B-0, config only), schema migrations (REL-1), enforcing a CSP after watching the
report-only one (SEC-6), and the bot, provider and revenue design items.

## 2. Scope, method and limitations

### In scope

- The backend (FastAPI, `backend/app`), Telegram bot (`backend/helm_bot`) and web app (`frontend/src`) as committed.
- Configuration defaults (`config.py`, `.env.example`).
- Public live pages and endpoints.

### Method

- **Code review.** Four parallel read-only reviews:
  - security;
  - revenue and treasury;
  - product coverage, reliability and privacy;
  - UI/UX and accessibility.

  Each finding was then spot-checked by hand in the files cited. Line numbers refer to the reviewed commit and may
  drift by a few lines after the changes in §9.
- **Local verification.**
  - Backend suite: `pytest`, SQLite, faked chains and providers.
  - Local proof-of-concept scripts against a temporary database: superadmin binding, the `X-Forwarded-For` limiter,
    Relay reconciliation, fee split maths.
  - `tsc --noEmit` and `vite build`.
  - A Playwright walkthrough of every route at 1440 × 900, 834 × 1112 and 390 × 844, with seeded demo data and the
    superadmin view.
  - axe-core 4 with the WCAG 2.0/2.1/2.2 A and AA rule sets.
- **Live, non-destructive checks** (from the owner's browser, signed out of Helm in a fresh tab):
  - response headers of `https://helmterminals.xyz/`;
  - the TON Connect manifest;
  - `GET https://api.helmterminals.xyz/health`, `/chains` and `/launchpads`;
  - visual review of `/` and `/login`.

### Standards

- OWASP Top 10 2021 (with 2025 draft categories noted where they differ).
- OWASP ASVS 5.0, checked selectively.
- WCAG 2.2 AA.

### Not done, by design

- No sign-in to production and no access to any user's data.
- No transactions.
- No intrusive or load testing.
- No changes to production settings.
- No contact with providers.
- Nothing sent outside the repository.

### Could not be inspected

- Production environment values other than those `/health` exposes.
- Whether a superadmin is already bound in production.
- Railway's `X-Forwarded-For` behaviour.
- Turnkey policies.
- Database backups.
- Provider dashboards (Relay, Bags, Clanker, Flap).
- Live latency.
- Signed-in production pages.
- Live dependency CVEs (`pip-audit` is not installed and the audit ran offline).

### Verified on production

`/health` reports:

- `env: production` and `signer: turnkey`;
- 4 Solana RPC endpoints and 2 per EVM chain, with no public fallback;
- TRON, NEAR, Arc and TON enabled, all on the public default endpoints.

`/chains` lists the four new networks with no testnet entries, so the mainnet-only change is deployed.

## 3. Findings

Severity:

| Severity | Meaning |
|---|---|
| **Critical** | Loss of funds or full compromise, exploitable now. |
| **High** | Likely loss or serious integrity failure under plausible conditions. |
| **Medium** | Real risk that needs specific conditions, or a significant accounting or UX integrity gap. |
| **Low** | Defence in depth, or a limited impact. |
| **Info** | Observation. |

Status is **Open** unless §9 says otherwise.

### 3.1 Security

| ID | Sev. | Title | Evidence | Impact | Verify safely | Fix |
|---|---|---|---|---|---|---|
| SEC-1 | Medium | First account registered with the superadmin email becomes superadmin; emails are not verified | `services/admin.py:30,36-57`; called at login `routes/auth.py:106` and `/auth/me` `:144`; registration `routes/auth.py:48-75` has no verification | On a fresh or reset DB without `SUPERADMIN_USER_ID`, an attacker who registers `maliksucess@gmail.com` first gets treasury, fee and suspension control. Once bound, no other account can take the role, and the pin overrides everything | Local: register the email on an empty DB and log in; `admin_role` is `superadmin` | Set `SUPERADMIN_USER_ID` in production (config only). Warn at startup when production runs without the pin |
| SEC-2 | Medium | Bot key and bot sessions can do far more than the Solana buy cap covers | `routes/telegram.py:102-113`. Sells, burn (`launches.py:728`), deploy with dev buy (`:138,382`), disperse, consolidate and bridge all accept bot sessions without 2FA | A leaked `BOT_API_KEY` or hijacked Telegram account can burn or spend value, though not withdraw it to outside addresses | Local: a bot session calling burn with confirm succeeds | Per-channel allowlist in `begin_spend` plus caps (backlog B-5) |
| SEC-3 | Medium | Transactions built by third parties are signed without checking what is in them | `services/routing.py:47-72` (Relay). PumpPortal and Kyber build similarly | A compromised provider could drain trading wallets | Code review | Allowlist program IDs and contracts per chain, and compare simulated balance changes to the preview (B-6) |
| SEC-4 | Low (Medium if the edge passes client headers) | Login limiter trusts the last `X-Forwarded-For` entry | `services/guard.py:50-55`, `routes/auth.py:81-83` | With a spoofable header, the per-client limit (10) is bypassed; the per-email limit (100 per 15 min) remains | Local: 15 bad logins with a rotating header all return 401, never 429 | Run uvicorn with `--proxy-headers --forwarded-allow-ips=<Railway range>` and use `request.client.host` (B-7) |
| SEC-5 | Low | Sign-up has no rate limit or email verification, and each sign-up creates signer keys | `routes/auth.py:48-75` (`ensure_main_wallets`) | Turnkey quota and cost abuse | Code review | Per-IP sign-up limit, then verification before key creation (B-8) |
| SEC-6 | Low | Web app served without security headers; session token readable by scripts | **Live:** `/` returns no CSP, HSTS, `X-Frame-Options` or `Referrer-Policy` (server `railway-hikari`, served by `vite preview`, `frontend/vite.config.ts:7`). Token in `sessionStorage` (`frontend/src/api.ts:14`) | Any future XSS would expose the session token. No XSS sink was found | Read response headers of `/` | Serve `dist/` from a static server with CSP, `frame-ancestors 'none'`, HSTS and `nosniff` (B-3) |
| SEC-7 | Low | A URL from DEX Screener is used as a link without checking its scheme | `services/market.py:206` → `frontend/src/components/Market.tsx:229` | A malicious API response could inject a `javascript:` link | Code review | Keep only `https://` URLs |
| SEC-8 | Low | Session lifecycle gaps | Fixed 7-day sessions (`config.py:13`); enabling 2FA doesn't revoke other sessions (`routes/auth.py:124-133`); no password change or reset, and no 2FA recovery | Hijacked sessions survive a 2FA upgrade; locked-out users need support | Code review | Revoke other sessions when 2FA is enabled; add password change and recovery (B-9) |
| SEC-9 | Low | Turnkey error text reaches users | `signing/turnkey.py:70,126` puts `r.text[:300]` into `SigningError`; shown via `main.py:142-144` | May reveal organisation, key or policy details | Code review | Log the detail; return a generic message |
| SEC-10 | Low | DNS-rebinding gap in token-metadata fetch | `services/token_meta.py:34-56`: checks the resolved IP, then `httpx` resolves again | Hard-to-exploit SSRF | Code review | Connect to the checked address, or use an egress proxy (B-10) |
| SEC-11 | Low | Unbounded inputs | `Login.password` has no `max_length` (`routes/auth.py:36`); `CreateLaunch.socials: dict` is unbounded (`routes/launches.py:137`); Pillow decodes images on the event loop (`chains/extra_pads.py:301`) | Memory or CPU denial of service | Code review | Bound inputs; lower `MAX_IMAGE_PIXELS`; decode in a thread |
| SEC-12 | Low | Referral abuse | Self-referral via a second account (`routes/auth.py:54-64`); unlimited codes (`routes/misc.py:93-98`); payout address changes need no password or 2FA (`misc.py:137-151`) | Up to 25 % of Helm's trade fee returned to the trader | Code review | Require the password to change the payout address; cap codes (see REV-4) |
| SEC-13 | Info | Unsafe defaults if `ENV` is unset | `config.py:10,12,39` | None in production today: **live `/health` shows `env: production, signer: turnkey`** | `GET /health` | Refuse `SIGNER=local` outside development and test (B-11). `/health` also reveals config detail publicly (see REL-3) |
| SEC-14 | Info | Dependencies unpinned | `backend/requirements.txt` uses `>=` and ships `pytest` in runtime; `passlib` is unmaintained | Supply-chain drift | — | Lock file, split dev dependencies, `pip-audit` and `npm audit` in CI (B-12) |
| SEC-15 | Info | Trades and deploys don't need 2FA by design | `deps.py:56-61` | A stolen web session can buy into an attacker's market | — | Optional daily web spend cap, or 2FA above a threshold (B-13) |
| SEC-17 | Medium | Pillow capped below 12, which keeps a release with published decoder advisories | `backend/requirements.txt:17` was `Pillow>=10,<12`; `pip-audit` reports 11.3.0 affected by PYSEC-2026-2249, -2874, -3451, -3453, -3493 and others, fixed in 12.1.1–12.3.0 | Pillow decodes user-supplied token images (Meme.Cooking icons) | `pip-audit -r backend/requirements.txt` | Raise the pin to `>=12.3,<13` |
| SEC-18 | Low | React Router 6.30.6 has two moderate advisories | `npm audit --omit=dev`: GHSA-wrjc-x8rr-h8h6 (open redirect via a backslash in `<Link>`/`useNavigate`) and GHSA-337j-9hxr-rhxg (SSR hydration; Helm doesn't use SSR). The fix is React Router 7 | Only matters where a route path comes from user input; none found in the code | `npm audit --omit=dev` in `frontend/` | Upgrade to React Router 7 (breaking: B-33) |
| SEC-16 | Info | Security logging is thin | Failed logins live only in `AuthFailure` (pruned after about 1 h); no structured logs or alerts | Slow incident detection | — | Structured auth logs and alerting (B-14) |

**Controls verified as working:**

- **Passwords and sessions.**
  - Argon2 password hashing (`security.py:13`).
  - 256-bit session tokens, stored hashed (`models.py:40`).
  - Every request checks revocation, expiry and suspension (`deps.py:28-44`).
  - Server-side logout.
- **2FA.**
  - Codes can't be reused (`guard.py:70-90`).
  - 2FA brute force is capped.
  - The 2FA secret is Fernet-encrypted.
- **Spending.**
  - The preview hash is enforced on confirm (`deps.py:151`).
  - Idempotency keys are unique per user (`models.py:122`).
  - 2FA is required for `withdraw`, `send_tokens` and `fund_external`.
  - Disperse, consolidate and bridge accept only the user's own managed wallets.
- **Access control.** Every object lookup is scoped to `user_id`; no IDOR found.
- **Admin.**
  - Admin needs a web session, the role and 2FA, with a step-up (password plus fresh code) for changes.
  - Viewers see masked emails.
  - The admin audit log is append-only in Postgres.
- **Secrets.**
  - `signer_ref` is never returned.
  - 422 responses don't echo input.
  - Imported keys are held as `SecretStr` and deleted after use.
- **Wallet linking.** Ownership proofs are bound to the user, network, domain and expiry (TRON, EVM, NEAR and TON).
- **Injection.** Queries use SQLAlchemy only. Admin search escapes `LIKE` and allowlists sort fields.
- **API headers and CORS.**
  - The API sends `nosniff`, `DENY` and `no-referrer`.
  - CORS uses an explicit origin list and no credentials.
- **Earlier audit findings** H1, H2, M1, M2, L2 and L3 are fixed.

**Superadmin:** the designated superadmin is `maliksucess@gmail.com` (`services/admin.py:30`), enforced server-side on
every admin route (`services/admin.py:74-121`). It is bound, not verified: see SEC-1.

### 3.2 Revenue and treasury

| ID | Sev. | Title | Evidence | Impact | Fix |
|---|---|---|---|---|---|
| REV-1 | Medium | Fees on trades that land late are never recorded | `services/executor.py:266` writes fee records only on in-time confirmation; timeouts at `:115-122` | Real treasury income and referral earnings missing from the ledger | Record `pending` ledger rows with the signature, then a re-check job (B-15) |
| REV-2 | Medium | Referral money Helm holds but owes has no admin view or payout path | `executor.py:216-218` ("owed" kept in the treasury), `routes/misc.py:110-120` | The on-chain treasury balance exceeds the ledger, and liabilities to referrers are invisible to admin | Read-only admin totals of owed amounts; a payout workflow (B-16) |
| REV-3 | Medium | Relay reconciliation can permanently zero a real route fee | `services/revenue.py:84-100`: any status other than pending, waiting or depositing (e.g. `delayed`, `submitted`) sets `amount_raw = 0` and marks the entry reconciled. On success the quoted amount is kept rather than the collected one | Claimable revenue can vanish from the ledger | Zero only on explicit failure or refund; treat other statuses as pending; store the collected amount |
| REV-4 | Medium | Self-referral gives a 25 % rebate | `routes/auth.py:53-63`, `misc.py:132-149` | Leaks a quarter of the trading fee | Refuse payout addresses that belong to the referred accounts' wallets; flag in admin (B-17) |
| REV-5 | Low | Env limits don't match admin limits; route fee on by default | `PLATFORM_FEE_BPS` env ≤ 500 vs admin ≤ 200; `ROUTE_FEE_BPS` env ≤ 300 vs admin ≤ 100, **default 30** (`config.py:15-20,83,105`; `platform_config.py:25-30`) | The live rate can exceed what admin allows; a 0.3 % route fee starts as soon as an EVM treasury is set | Admin warning when the effective rate is out of bounds (B-18) |
| REV-6 | Low | DEX fees counted as received without an on-chain check; sell amounts are estimates | `revenue.py:46-52`, `kyber.py:103-111`, `admin.py:126-137` | Totals mix estimated and measured amounts | Show estimated separately; parse receipt logs (B-19) |
| REV-7 | Low | Skipped or failed fees are invisible | `executor.py:203-206,219-220,251-257,272` | Lost revenue goes unnoticed | Count waived and failed fees in admin; mark separate-transfer timeouts `pending` (B-20) |
| REV-8 | Low | Creator-share ledger entry is missing for Solana deploys confirmed via `/recheck` | `routes/launches.py:639-662` | Claimable share unrecorded | Write the entry from the saved preview during recheck |
| REV-9 | Low | Partner revenue (Bags partner, Flap commission) never recorded | `admin.py:33` shows 0 always | Under-reported claimables | Claimable entries with no amount, per launch (B-21) |
| REV-10 | Low | EVM DEX and Relay use the rate at send time, not the confirmed rate | `executor.py:44-77`, `kyber.py:84-89`, `routing.py:110-116` | A rate change between confirm and send changes what the user pays | Pass the confirmed rate and abort on mismatch (B-22; changes transaction behaviour, so not done here) |
| REV-11 | Info | Fee maths checked | 10,000 lamports → 75 to Helm and 25 to the referrer; fee rounds down; no double charging found | — | — |

**Not found:** revenue estimated from trading volume. The admin summary adds up actual ledger rows per asset without
conversion (`routes/admin.py:113-170`).

### 3.3 Product and chain coverage

| ID | Sev. | Title | Evidence | Impact | Fix |
|---|---|---|---|---|---|
| PRD-1 | High | EVM deploy that times out is marked failed, which allows a redeploy | `routes/launches.py:1016-1017` (any non-confirmed status → `failed`); `:389` allows deploy from `failed` | The first deploy may still land, and a redeploy creates a second token and spends a second dev buy | Keep `deploying` with the tx hash, plus an EVM recheck (B-1) |
| PRD-2 | Medium | Allocation locks can be bypassed on TRON, NEAR and Arc via the generic token transfer | Locks enforced in trade, burn, send, disperse, consolidate and automations (`launches.py:566,746,796,1036,1077`; `distribute.py:240,355`) but not in `POST /chains/{network}/transfer` (`routes/chains.py:240-283`) | Locked tokens can leave a wallet the UI says is locked | Call `lock_warning` when the token is one of the user's launches |
| PRD-3 | Medium | Telegram bot misreports uncertain outcomes and explorers | `helm_bot/controller.py:24` (`unconfirmed` isn't terminal), `render.py:6,116` (Solscan for every chain), `controller.py:257` ("Withdraw SOL" on every chain) | Users told "still running" instead of "check before retrying"; broken links | Add `unconfirmed` handling and per-chain explorers |
| PRD-4 | Low | A wallet-approval request left pending is later called "server restarted" | `launches.py:907-914` applies to external and TON jobs awaiting a signature | Misleading message; a late signature is refused | Exclude jobs awaiting a wallet signature |
| PRD-5 | Low | Workspace assumes 6 decimals if the supply read fails | `launches.py:847,878-879` | 1000× misscaling for 9-decimal tokens (a callout warns) | Return `null` token amounts when decimals are unknown (B-23) |
| PRD-6 | Low | Portfolio prices can be stale without saying so | `portfolio.py:38-46,90-95` | Old prices shown as current | Return and show price age; mark filled days (B-24) |
| PRD-7 | Info | Spec items not in code | `POST /launches/{id}/lock` → 501 (not called by the UI); `SPEC.md:192-194` promises exports, spend rate limits and Turnkey spend policies | Expectations drift | Update SPEC or build (B-25) |

### 3.4 Reliability and performance

| ID | Sev. | Title | Evidence | Impact | Fix |
|---|---|---|---|---|---|
| REL-1 | High | No schema migrations | `db.py:49-53`, `main.py:26` (`create_all` only); columns were added to existing tables before (`ee48578`) | The next column added to an existing table fails in production | Alembic baseline and `alembic upgrade` in the release step (B-2) |
| REL-2 | High | Jobs run in the API process; a restart mid-deploy can lead to a redeploy | `deps.py:175-198`; `launches.py:461,982,324`; the catch-all marks jobs `failed` even after a send (`deps.py:186-191`) | Stuck launches; archive → restore → redeploy creates a second token | Record the tx before sending; catch-all → `unconfirmed` when a signature exists; block restore of `deploying`; durable queue later (B-1) |
| REL-3 | Medium | Observability is thin; `/health` doesn't check the DB and reveals config publicly | About 10 log calls; `run_job` failures are not logged (`deps.py:186`); `main.py:146-157` | Slow incident response | `log.exception` in `run_job`; `/health/ready`; move detail behind admin (B-26) |
| REL-4 | Medium | Workspace polls every 10 s even in background tabs, with sequential per-wallet reads | `frontend/src/pages/Workspace.tsx:171-175`; `launches.py:855-875` | Needless RPC load and cost | Pause polling while hidden; read concurrently with a short cache (B-27) |
| REL-5 | Low | Single-replica assumptions | Nonce locks, caches and the job set live in process memory (`evm_exec.py:96-121`) | Scaling to more than one replica breaks nonce ordering | Stay at one replica until jobs move to a queue |
| REL-6 | Low | Extra networks use public endpoints | **Live `/health`:** TRON, NEAR, Arc and TON `custom_rpc: false`. Keyless toncenter allows about 1 request/s | Rate-limit errors under load | Set `TONCENTER_API_KEY` and dedicated endpoints |
| REL-7 | Info | Provider failure handling is reasonable | Keyring rotation with `Retry-After`, chain-ID checks, stale-while-error market cache, balance reads return `null` rather than 0 | — | — |
| REL-8 | Info | No backup or recovery runbook | Nothing in the repo | Recovery untested | Runbook and restore drill (B-28) |
| PERF-1 | Medium | One 511 kB entry script (157 kB gzip) holds every page, Admin included | `frontend/src/App.tsx:6-19` imports every page eagerly | Slower first load on mobile | Load routes lazily |

### 3.5 Privacy and operations (not legal advice; items marked ⚖ need qualified review)

| ID | Sev. | Title | Evidence | Fix |
|---|---|---|---|---|
| PRIV-1 ⚖ | Medium | No account deletion or data export | No route exists | Self-service delete and export (B-29) |
| PRIV-2 ⚖ | Medium | No retention periods | Sessions (IP, user agent), activity and jobs kept indefinitely; `admin_audit` is append-only with IPs (`models.py:401-408`) | Retention policy; prune sessions; pseudonymise old IPs (B-30) |
| PRIV-3 | Low | Session IP is probably the proxy's | `routes/auth.py:98` uses `request.client.host` | Fix together with SEC-4 |
| PRIV-4 | Low | Admin list and search aren't audited; exact-email search confirms whether an account exists | `routes/admin.py:441-486` | Audit list and search queries (B-31) |
| PRIV-5 | Info | Wallet-connector sessions survive sign-out | TON Connect and NEAR Connect keys stay in `localStorage` (`TonConnect.tsx:19-24`, `walletConnectors.ts:95-106`) | Clear them on sign-out |
| PRIV-6 | Info | No in-app support contact, status page or suspension notice | — | B-32 |

**Personal data stored:**

- email;
- password hash;
- encrypted TOTP secret;
- session IP and user agent;
- Telegram IDs (removed on unlink);
- optional profile handles and bio;
- wallet addresses;
- job parameters;
- activity log;
- admin audit IP and user agent.

### 3.6 Accessibility and UI/UX

Evidence comes from the local walkthrough: 16 routes and states at 3 widths, 34 axe runs. The live pages `/` and
`/login` match the local build visually.

| ID | Sev. | Title | Evidence | Impact | Fix |
|---|---|---|---|---|---|
| UX-1 | Medium | Chain lists show only the original five chains in several places | Landing hero chips and "Coverage" (`components/public.tsx:29`), sign-in side panel (`pages/Auth.tsx`), dashboard "Chain coverage" (`pages/Dashboard.tsx:258`), page meta description (`frontend/index.html:7`, also live), Tracker chain filter (`pages/Tracker.tsx:45`). The Launches filter omits TON (`pages/Launches.tsx:331`) | Users can't tell TRON, NEAR, Arc and TON are supported; the dashboard summary contradicts itself ("on 9 chains" above a list of 5) | Derive every chain list from one source that includes the enabled extra networks |
| UX-2 | Low | Launchpad names break mid-word | "Meme.C / ooking" in the launchpad grid (Launches page) | Looks broken | Keep names whole; wrap at word boundaries |
| UX-3 | Low | Launches chain-filter row is cut off at desktop widths | Chips overflow the card at 1440 px ("Arc" clipped) | Hidden filters | Let the row wrap |
| UX-4 | Low | Headings set in the monospace data face | Dashboard portfolio heading "No wallets yet" | Weaker hierarchy; mono belongs to numbers and addresses | Sans for headings, mono only for data |
| UX-5 | Low | On mobile the order ticket sits below the chart and pool stats | Workspace at 390 px: the ticket starts about 1,300 px down | Harder to act | "Trade" jump link at the top of the workspace on small screens |
| UX-7 | Medium | The trade review didn't name the network or the token contract | Found by the journey check: the Buy/Sell review (`components/review.tsx`, `summarize` "trade") showed amount, wallets, slippage and fee, but the chain and token address only in the collapsed full preview | A user reviewing on the wrong launch or chain has nothing on the main review to catch it | Journey script: open a workspace, review a buy | Lead every review with Network and, for token actions, the token contract |
| UX-6 | Info | Decision-critical data is present | Network, token address, wallets, fees, slippage, price impact, status, data source and "as of" freshness are shown in the market card, order ticket and review dialogs; market data is labelled "indicative" and kept apart from executable previews | — | Preserve in the redesign |
| A11Y-1 | Medium | "Faint" text colour fails contrast | `--faint #5d6e75` is 3.18–3.76:1 on Helm's surfaces (needs 4.5:1). axe `color-contrast` on 6 pages (`.market-note`) | Hard to read for low-vision users (WCAG 1.4.3) | Raise the token to at least 4.5:1 on every surface |
| A11Y-2 | Low | 19 font sizes, with body text at 10–11.5 px | 35 declarations below 12 px in `styles.css` | Readability; inconsistent hierarchy | A 9-step type scale with a 11 px floor for uppercase labels and 12 px for body text |
| A11Y-3 | Medium | Links identified by colour only inside text | axe `link-in-text-block` on 8 pages (e.g. the DEX Screener and GeckoTerminal credits, profile text) | WCAG 1.4.1 | Underline links inside running text |
| A11Y-4 | Medium | Small tap targets | axe `target-size`: address and explorer links 18 px tall on Launches cards | WCAG 2.5.8 | At least 24 × 24 px hit areas for inline controls |
| A11Y-5 | Low | Scrollable table region not reachable by keyboard | axe `scrollable-region-focusable` on Networks | Keyboard users can't scroll the capability matrix (WCAG 2.1.1) | Make scroll regions focusable and labelled |
| A11Y-6 | Info | Already good | Skip link, visible focus ring, labelled icon buttons, dialog focus trap and restore, `aria-live` on selection counts, reduced "Test signer" noise, no horizontal overflow at any tested width, axe clean on 25 of 34 runs | — | — |
| A11Y-7 | Info | Motion | Transitions are short; no `prefers-reduced-motion` rule | Minor | Honour reduced motion |
| A11Y-8 | Info | Accessible authentication | Password fields allow paste and password managers; 2FA uses a numeric one-time code field; no cognitive tests (WCAG 3.3.8 met) | — | — |

## 4. Revenue-flow matrix

Production values are **unknown**. The table gives env names and code defaults only.

| Fee | Where configured (default) | Shown in preview | Collected how | Recipient | Recorded | Reachable states | Verified vs unknown |
|---|---|---|---|---|---|---|---|
| Solana trade fee (pump.fun, LetsBonk, Bags, Meteora DBC) | Admin `trade_fee_bps_solana`, else `PLATFORM_FEE_BPS` (0) | Yes (`launches.py:581-586`, `fees.py:60-72`); in the preview hash | SOL transfer inside the trade tx, or a separate transfer after a confirmed trade | Solana treasury (`treasury_addresses` or `PLATFORM_FEE_WALLET`) | `fee_records`, `revenue_events: trade_fee` | Configured → received; reconciled manually | Path verified; landing inferred from trade confirmation, not read from the treasury |
| Referral share | `REFERRAL_SHARE_BPS` (2500 = 25 % of the fee) | Yes (`fees.py:70-71`) | Paid in the same tx, or kept by Helm as "owed" | Referrer, or Helm (owed) | `fee_records.referral_*` | Paid or owed; no payout path | Split maths verified locally |
| EVM DEX fee (KyberSwap) | Admin `trade_fee_bps_evm_dex`, else `PLATFORM_FEE_BPS` (0) | Yes (`launches.py:1044`) | Router fee inside the swap | `evm` treasury or `PLATFORM_FEE_WALLET_EVM` | `revenue_events: dex_trade_fee` (sells estimated) | Received on swap confirmation | Amount not read from receipt logs |
| Relay route fee | Admin `route_fee_bps`, else `ROUTE_FEE_BPS` (**30**) | Yes (`bridge.py:82`, `auto_fund.py:83`) | Relay app fee, held by Relay until claimed | `evm` treasury | `revenue_events: route_fee` on success | Claimable → reconciled via the Relay API | Reconciliation has a bug (REV-3) |
| Creator share, Bags | Admin `creator_share_bps` (0) | Yes (`launches.py:419-446`) | Bags fee-share lists the treasury as a claimer | Solana treasury | `revenue_events: creator_share`, amount unknown | Claimable | Not verifiable from Helm |
| Creator share, Clanker | Admin, else `CLANKER_PLATFORM_REWARD_BPS` (0) | Yes (`clanker.py:135-146`) | Treasury added as an LP-locker reward recipient | Chain or `evm` treasury | Same as Bags | Claimable | Not verifiable from Helm |
| Bags partner share | `BAGS_PARTNER_WALLET` / `_CONFIG` ("") | Yes, as text | Paid by Bags | Partner wallet | **Not recorded** | — | Unknown |
| Flap commission | Treasury address | Yes, as a note | Paid by Flap | Chain or `evm` treasury | **Not recorded** | — | Unknown |
| Deploy fee | None | "No Helm fee on the deploy itself" | — | — | — | — | Verified |
| TRON, NEAR, Arc, TON | None | "None" (`extra_launch.py:69,169`, `ton.py:355`) | — | — | — | — | Verified |

**What "reconciled" means here:**

- An admin marks an entry reconciled by hand (`/admin/revenue/reconcile`).
- Relay route fees are also checked automatically against Relay's API.
- Nothing matches the ledger against on-chain treasury inflows automatically.

## 5. Chain and product support matrix

"Code" means verified in code. "UI" means shown in the web app.

| Chain | Wallets | Balances | History | Transfers | Launches | Trading | Market data | Cross-chain | Fee estimate | Confirmation |
|---|---|---|---|---|---|---|---|---|---|---|
| Solana | Managed, import, watch (no external connect) | SOL, USDC, launch tokens; failed read → "Unavailable", not 0 | Helm activity only | Yes | pump.fun, LetsBonk, Bags*, Meteora DBC* | PumpPortal, Bags, Jupiter | DEX Screener + GeckoTerminal | Relay (via deploy auto-fund; no screen) | Flat 0.01 SOL reserve | Confirmed commitment; timeout → unconfirmed; deploy recheck |
| Base | Managed, import, watch | ETH, USDC | Helm only | Yes | Clanker | KyberSwap | Yes | Relay | Worst-case gas | One receipt; deploy timeout → failed (PRD-1) |
| BNB Chain | Same | BNB, USDC | Helm only | Yes | Flap, Four.meme | Curves + Kyber | Yes | Relay | Yes | Same (PRD-1) |
| Ethereum | Same | ETH, USDC | Helm only | Yes | Uniswap V2 | Kyber | Yes | Relay | Yes | Same (PRD-1) |
| Robinhood Chain | Same | ETH, USDG | Helm only | Yes | Flap, Clanker, Pons | Curves + Kyber (Kyber coverage unverified) | Yes (coverage unverified) | Relay (unverified for chain 4663) | Yes | Same (PRD-1) |
| TRON | Managed, import, watch, TronLink | TRX, TRC-20 | TronGrid | Yes (lock gap PRD-2) | SunPump | Curve only | No | No | Bandwidth, energy, activation | Solidified; unknown → timeout |
| NEAR | Managed (implicit), import (implicit), watch, NEAR Connect | NEAR (after storage), NEP-141 | Helm only | Yes, with storage deposit | Meme.Cooking* | Auction deposit and withdraw | No | No | Gas × price | Final execution |
| Arc | Managed, import, watch, EVM wallet | USDC (18 decimals), EURC | Helm only | Yes (lock gap PRD-2) | Tolly* | Uniswap V3 | No | No | Fixed gas × max fee | One confirmation |
| TON | TON Connect, watch (no managed keys, by design) | TON, Jettons | toncenter | Signed in the user's wallet | None (GasPump planned) | No | No | No | Static text | Message hash |

\* Needs a provider key or setting. Live `/launchpads`: Bags available; `meme-cooking` and `meteora-dbc` status not
shown in the excerpt read.

Launchpads marked planned in the UI ("Soon"): Printr, Surge, Anoncoin, Stonk.fun, Bankr, GasPump.

Market-data and price providers:

- DEX Screener and GeckoTerminal for the market view. Keyless GeckoTerminal isn't cleared for commercial use
  (`docs/market-data.md`).
- DefiLlama for portfolio prices. It is not listed in the provider-terms document ⚖.

## 6. Improvement backlog

Owner areas: BE (backend), FE (frontend), OPS (ops and config), PM (product).

Effort:

| Size | Time |
|---|---|
| S | Under a day |
| M | 1–3 days |
| L | 1–2 weeks |

| # | Item (findings) | Owner | Effort | Depends on | Acceptance criteria |
|---|---|---|---|---|---|
| B-0 | Set `SUPERADMIN_USER_ID` in production (SEC-1) | OPS | S | — | `/admin` works for the owner; a new account with the same email on a copy DB gets no role |
| B-1 | Deploy safety: EVM timeout keeps `deploying` with tx hash; EVM recheck; tx recorded before send; `run_job` catch-all → `unconfirmed` after a send; no restore of `deploying` (PRD-1, REL-2) | BE | M | — | Tests: EVM timeout ⇒ `deploying` + hash; recheck resolves the token; restore refused for `deploying`; exception after send ⇒ `unconfirmed` |
| B-2 | Alembic baseline and release-step upgrade (REL-1) | BE/OPS | M | Production schema snapshot | `alembic upgrade head` is a no-op on current production; CI fails if models and migrations diverge |
| B-3 | Static hosting with CSP, HSTS, `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy` (SEC-6) | OPS/FE | S | — | Live `/` shows the headers; app works; securityheaders.com grade A |
| B-5 | Bot channel allowlist and caps (SEC-2) | BE | M | PM decision on bot scope | Bot session refused for burn and deploy; sells capped; tests |
| B-6 | Provider transaction validation (SEC-3) | BE | L | Per-chain program lists | A route with an unknown program or recipient is refused before signing; tests per provider |
| B-7 | Trusted proxy configuration (SEC-4, PRIV-3) | OPS/BE | S | Railway proxy range | Spoofed `X-Forwarded-For` doesn't reset the limiter; session IP is the client's |
| B-8 | Sign-up rate limit and verification before key creation (SEC-5) | BE | M | Email provider | 429 after N sign-ups per IP; no Turnkey key before verification |
| B-9 | Password change and reset, 2FA recovery codes, idle timeout (SEC-8) | BE/FE | M | Email provider | Flows tested; old sessions revoked on password change |
| B-10 | Pin the resolved IP for metadata fetches (SEC-10) | BE | S | — | Rebinding test refused |
| B-11 | Refuse `SIGNER=local` unless `ENV` is development or test (SEC-13) | BE | S | — | Startup fails with a clear error |
| B-12 | Lock dependencies; `pip-audit` and `npm audit` in CI (SEC-14) | OPS | S | CI | CI fails on high CVEs |
| B-13 | Optional web spend caps or 2FA threshold (SEC-15) | PM/BE | M | PM | Setting per account; enforced in `begin_spend` |
| B-14 | Structured security logs and alerts (SEC-16) | OPS/BE | M | Log sink | Failed-login spikes alert |
| B-15 | Pending fee records for late-landing trades plus a re-check job (REV-1) | BE | M | — | Timeout ⇒ `pending` ledger row; re-check moves it to received or failed |
| B-16 | Referral owed ledger: admin view and payout workflow (REV-2) | BE/FE | M | PM | Admin sees owed totals per referrer; payouts recorded |
| B-17 | Self-referral checks (REV-4, SEC-12) | BE | S | — | Payout to a referred account's wallet refused; flagged in admin |
| B-18 | Align env and admin fee bounds; warn when out of range (REV-5) | BE/FE | S | — | Admin warning shown when the effective rate is above admin bounds |
| B-19 | Separate estimated from measured fee amounts; parse receipt logs (REV-6) | BE/FE | M | — | Admin totals have separate "estimated" column |
| B-20 | Show waived and failed fees (REV-7) | BE/FE | S | — | Admin count visible |
| B-21 | Partner revenue entries (REV-9) | BE | S | — | Bags and Flap launches create claimable entries |
| B-22 | Lock the confirmed fee rate through execution (REV-10) | BE | M | — | Rate change between confirm and send aborts with a clear message |
| B-23 | Unknown decimals ⇒ no token amounts (PRD-5) | BE/FE | S | — | UI shows "Unavailable" instead of a misscaled number |
| B-24 | Price age in portfolio (PRD-6) | BE/FE | S | — | "as of" shown on portfolio totals |
| B-25 | Reconcile SPEC.md with the product (PRD-7) | PM | S | — | SPEC matches features |
| B-26 | Observability: logging, readiness endpoint, admin-only config detail (REL-3) | BE/OPS | M | — | `/health/ready` checks the DB; job failures appear in logs |
| B-27 | Concurrent, cached workspace reads (REL-4) | BE | S | — | 10-wallet workspace loads in one RPC round-trip time |
| B-28 | Backup and restore runbook with a drill (REL-8) | OPS | S | — | Documented restore tested on staging |
| B-29 | Account deletion and export (PRIV-1) ⚖ | BE/FE | M | Qualified review | User can export and delete |
| B-30 | Retention policy and pruning (PRIV-2) ⚖ | BE/OPS | M | Qualified review | Sessions pruned; IPs pseudonymised after N days |
| B-31 | Audit admin list and search (PRIV-4) | BE | S | — | List and search queries appear in the audit log |
| B-32 | Support contact, status page, suspension notice (PRIV-6) | PM/FE | S | — | Visible in app |
| B-33 | Upgrade to React Router 7 (SEC-18) | FE | M | — | `npm audit --omit=dev` clean; all routes and deep links work |
| B-34 | Enforce the CSP after a week of report-only (SEC-6) | FE/OPS | S | B-3 or the headers in §9 | No CSP reports from real use of TON Connect, NEAR Connect and charts; header switched to `Content-Security-Policy` |

## 7. Checks run

All on the audit branch, 29 Sep 2026, in the local environment (SQLite, faked chains and providers, local signer)
unless marked live.

| Check | Result |
|---|---|
| Backend suite, `pytest` | **305 passed** (288 before, plus 17 new tests in `tests/test_audit_fixes.py`). A known intermittent failure in `test_folders.py::test_merge_duplicates_and_isolation` (about one run in three, before and after these changes) did not occur in the final runs |
| Frontend types, `tsc --noEmit` | Clean |
| Frontend build, `vite build` | Clean. First-load script 280 kB (89 kB gzip), was 511 kB (157 kB gzip); pages load on first visit |
| axe-core 4, WCAG 2.0/2.1/2.2 A and AA | 34 runs (16 routes and states × desktop and mobile, plus signed-out pages): **0 violations**, was 29 nodes across 4 rules (`link-in-text-block` 14, `target-size` 8, `color-contrast` 6, `scrollable-region-focusable` 1) |
| Route walkthrough at 1440 × 900, 834 × 1112, 390 × 844 | Every route rendered; no page errors, no console errors, no horizontal overflow |
| Journeys, desktop and mobile (Playwright) | 20 of 20 checks pass: sign-in lands on the dashboard; a buy review shows network, token, fee, slippage and "nothing is sent until you confirm"; confirming reaches a final result; the Add wallets dialog shows New, Import, Connect and Watch-only with a stable tab bar; keyboard focus is visible |
| Wallet linking (from the wallet-settings work, same session) | Simulated TronLink and EVM wallets on desktop and mobile: link, wrong network refused, declined switch, TON via TON Connect only, unlink |
| `pip-audit -r backend/requirements.txt` | Before: Pillow 11.3.0 with published advisories (SEC-17). After the pin change: **no known vulnerabilities**. This resolves the newest versions the ranges allow; production installs whatever was newest at its last build, so re-run it against production's lock once one exists (B-12) |
| `npm audit --omit=dev` | 2 moderate (React Router, SEC-18), open |
| `ruff` (pyflakes and bugbear rules) | No new issues from these changes; 25 pre-existing style warnings (unused imports, `zip()` without `strict`) left alone |
| Security headers from `npm run start` (the production serve command) | `Strict-Transport-Security`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy` and a report-only CSP present on pages and assets |
| Live site (non-destructive, before these changes) | See §2 "Verified on production". The live site still runs the pre-audit build until this branch is deployed |
| Independent review | A separate reviewer read the backend diff and found four problems in the first version of the deploy fix (a stale hash from an earlier attempt, multi-step plans, treating an unknown hash as dropped, launches stuck with no way to archive) and then one in the revision (a stale archive flag). All five were fixed and are covered by the tests above |

Not run: penetration testing, load testing, a production database or backup check, a screen-reader pass with NVDA or
VoiceOver (axe and keyboard checks only), a real-wallet end-to-end test.

## 8. Shareable summary for an engineering or security reviewer

> Helm Terminals (FastAPI, React, Turnkey signer, Railway) was reviewed on 29 Sep 2026 from source at `aa6638f`, with
> read-only checks of the live site. Fixes are on branch `audit/2026-09-29` (not deployed): 22 findings fixed and 8
> partly fixed, with 17 new tests (305 backend tests pass; axe WCAG 2.2 AA shows 0 violations across 34 runs).
>
> **No Critical issues found.**
>
> **High:**
> - There are no database migrations (open).
> - An EVM deploy that timed out could be redeployed (fixed on the branch); a restart mid-deploy can still strand a
>   launch in "deploying", though it can no longer be deployed twice.
>
> **Medium:**
> - Unverified-email superadmin binding (mitigated by setting `SUPERADMIN_USER_ID`).
> - The Telegram bot channel is broader than its caps.
> - Provider-built transactions are signed unvalidated.
> - Missing web security headers (confirmed live).
> - Revenue ledger gaps: late fees, a Relay zeroing rule, owed referral amounts, self-referral.
> - Allocation locks can be bypassed via the generic transfer.
> - The bot misreports uncertain outcomes.
> - Workspace polling load.
> - Account deletion and retention (needs qualified review).
> - Accessibility: contrast, colour-only links, target sizes.
>
> **Strong controls:**
> - Preview-hash confirmation with idempotency.
> - 2FA on outbound transfers.
> - Keys only in Turnkey.
> - Per-user scoping on every object.
> - Proof-bound wallet linking.
> - Append-only admin audit.
>
> Not a penetration test. Production configuration was not inspected beyond `/health`.

## 9. Implemented changes, results and what remains

Everything below is on branch `audit/2026-09-29` and is **not deployed**. No fee rate, fee recipient or treasury
setting changed. Transaction semantics changed only where the old behaviour could send money twice or hide an
outcome; each such change is listed with its test.

### 9.1 Fixed and rechecked

| Finding | What changed | Where | Verified by |
|---|---|---|---|
| PRD-1 (High) | An EVM deploy is marked `failed` only when nothing of it confirmed on-chain. A single-transaction deploy that times out stays `deploying` with its hash, and `/launches/{id}/recheck` settles it from the receipt alone: success makes it live (token from the plan's resolver, its predicted address, or the receipt), a revert makes it failed. A missing receipt never counts as failure, because another node may still mine it. A multi-step deploy (Uniswap V2: token, approve, liquidity) that fails after the token exists stays `deploying` with a note to check the explorer instead of becoming retryable. A new attempt clears the previous attempt's hash and errors. The workspace shows the transaction link or the note and rechecks automatically | `routes/launches.py` (EVM deploy worker, `_recheck_evm_deploy`, `DEPLOY_TRACKING`, `_launch_out`), `pages/Workspace.tsx` | `test_evm_deploy_that_times_out_stays_deploying_until_the_receipt_decides`, `test_evm_deploy_recheck_fails_only_on_a_revert`, `test_a_new_deploy_attempt_forgets_the_previous_attempts_hash`, `test_a_multi_step_deploy_that_fails_after_the_token_exists_is_never_redeployable` |
| PRD-2 | Allocation locks now apply to the generic TRON, NEAR and Arc token transfer when the token is one of the user's launches | `routes/chains.py` (`_lock_warnings`) | `test_generic_token_transfer_respects_allocation_locks` |
| PRD-3 | The Telegram bot treats `unconfirmed` as final ("check the explorer before trying again"), links each network's own explorer (no link when the network is unknown) and no longer says "Withdraw SOL" on every chain. `/jobs/{id}` now returns the job's network | `helm_bot/controller.py`, `render.py`, `main.py`; `routes/launches.py` | `test_bot_renders_unconfirmed_jobs_with_the_right_explorer` |
| PRD-4 | A transfer waiting for the user's own wallet (TON Connect, TronLink, EVM, NEAR) is no longer relabelled "server restarted" | `routes/launches.py` (`AWAITING_WALLET`) | `test_a_transfer_waiting_for_the_users_wallet_isnt_called_lost` |
| SEC-7 | Provider links are kept only if they are `https://` | `services/market.py` (`_https`) | `test_provider_links_are_https_only` |
| SEC-9 | Signer HTTP errors go to server logs; users see "the signing service returned HTTP 403…" without Turnkey's text | `signing/turnkey.py` | `test_turnkey_http_errors_are_generic_for_users` |
| SEC-11 | Login password ≤ 200 and 2FA code ≤ 12 characters; launch socials ≤ 10 entries of ≤ 300 characters; token images over 25 megapixels are refused from the header, JPEGs decode at reduced size, and decoding runs off the event loop | `routes/auth.py`, `routes/launches.py`, `chains/extra_pads.py` | `test_oversized_login_and_socials_are_refused`, `test_a_huge_image_is_refused_before_it_is_decoded` |
| SEC-17 | Pillow pin raised to `>=12.3,<13` | `backend/requirements.txt` | `pip-audit` clean; image tests pass on 12.3.0 |
| PRIV-5 | Signing out clears this browser's TON Connect and NEAR Connect sessions | `App.tsx` | Walkthrough |
| PERF-1 | Pages other than landing, sign-in and dashboard load on first visit | `App.tsx` | Build: first-load script 511 → 280 kB (157 → 89 kB gzip) |
| UX-1 | Every chain list (landing, sign-in, dashboard coverage, Launches filter, page description) includes TRON, NEAR, Arc and TON. The Tracker now takes TRON, NEAR, Arc and TON addresses too, with a format hint per chain (the server does the full check) | `components/public.tsx`, `pages/*`, `chains.ts`, `index.html`, `pages/Tracker.tsx` | Walkthrough screenshots; `test_tracker_accepts_tron_near_arc_and_ton_addresses` |
| UX-2, UX-3 | Launchpad names wrap at word boundaries; the chain filter row wraps instead of clipping | `styles.css`, `Launches.tsx` | Walkthrough at 1440 px |
| UX-4 | Headings use the sans face; mono is kept for numbers and addresses. Every signed-in page has exactly one `h1` (the dashboard had none, the profile had two) | `styles.css`, `Dashboard.tsx`, `Profile.tsx` | Script: one `h1` on each of 10 signed-in routes |
| UX-5 | A "Trade" jump to the order ticket at the top of the workspace on small screens; polling pauses while the tab is hidden (part of REL-4) | `Workspace.tsx`, `styles.css` | Mobile walkthrough |
| UX-7 | Every review (trade, fund, withdraw, burn, send, disperse, consolidate, deploy, claim) leads with the network and, for token actions, the token contract | `components/review.tsx` | Journey check "preview names the network and token" |
| A11Y-1 | `--faint` and `--muted` raised to at least 4.5:1 on every surface | `styles.css` tokens | axe `color-contrast`: 6 → 0 |
| A11Y-2 | A ten-step type scale (`--fs-2xs` 11 px, uppercase labels only, to `--fs-display`); body text never below 12 px. The one remaining smaller size is the 9 px initials inside the fallback logo tile, which is decorative (the name is always printed beside it) | `styles.css` | Visual walkthrough |
| A11Y-3 | Links inside running text are underlined | `styles.css` | axe `link-in-text-block`: 14 → 0 |
| A11Y-4 | Inline address and explorer links have 24 px hit areas; touch screens get 44 px primary controls and 36 px compact ones | `styles.css` | axe `target-size`: 8 → 0 |
| A11Y-5 | Tables that scroll sideways become labelled, focusable regions (`TableWrap`, used by every table) | `components/ui.tsx`, pages | axe `scrollable-region-focusable`: 1 → 0 |
| A11Y-7 | `prefers-reduced-motion` honoured everywhere | `styles.css` | Code review |

### 9.2 Partly fixed

| Finding | Done | Still open |
|---|---|---|
| SEC-1 | Production start-up logs a warning when `SUPERADMIN_USER_ID` is empty | Set the pin in Railway (B-0) |
| SEC-6 | The production serve command (`vite preview`) now sends HSTS, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy` and a **report-only** CSP (`vite.config.ts`) | Watch the browser console for CSP reports with TON Connect and NEAR Connect, then enforce it (B-34). Session token still in `sessionStorage` |
| SEC-8 | Turning 2FA on signs out the account's other web sessions (the current one and the Telegram bot's stay) (`test_turning_on_2fa_signs_out_other_web_sessions`) | Password change and reset, 2FA recovery codes, idle timeout (B-9) |
| REV-3 | Relay entries are zeroed only on a final outcome (`success` without a fee, `failure`, `refund`); `delayed`, `submitted` and unknown statuses stay pending (`test_relay_fee_is_only_zeroed_on_a_final_outcome`) | On success the quoted amount is still kept rather than the collected one |
| REL-2 | A job that raises after a transaction was sent ends `unconfirmed`, never `failed` (`test_a_job_that_crashes_after_sending_is_unconfirmed`). A launch archived while `deploying` comes back as `deploying` when restored, never as a deployable draft, and it can't be archived while its deploy job is still running (`test_a_deploy_still_running_cant_be_archived`). Timed-out EVM deploys keep their hash (PRD-1) | A restart during a deploy, before the hash is saved, leaves the launch `deploying` with nothing to recheck: it can't be redeployed (safe) and can be archived, but settling it needs a manual look at the explorer. A durable job queue (B-1) removes this |
| REL-3 | `run_job` failures are logged with a stack trace | Readiness endpoint and admin-only config detail (B-26) |
| REL-4 | The workspace stops polling while its tab is hidden | Concurrent, cached backend reads (B-27) |
| PRIV-4 | Admin user searches are audited (who searched and how many matched, never the search text) (`test_admin_user_search_is_audited_without_the_query`) | Plain list views aren't audited |

### 9.3 Design system

The existing dark "terminal" identity was kept and made systematic rather than replaced, so every screen changed
together and no verified flow was rebuilt:

- **Tokens** for type (ten steps), space (4 px grid), control heights (32 / 38 / 44 px, larger on touch screens), focus
  ring and motion, documented at the top of `styles.css`.
- **Colour:** one emerald accent for actions; status colours always paired with a word or icon; every text colour at
  least 4.5:1.
- **Type:** IBM Plex Sans for words, IBM Plex Mono only for numbers, addresses and data.
- **Components:** `TableWrap` for tables; a single focus style for buttons, chips, tiles and segmented controls;
  reviews that lead with network and token; underlined inline links.
- **Unchanged on purpose:** the preview → confirm → result flow, fee disclosures and their wording, "indicative"
  labelling of market data (kept apart from executable previews), the "as of" freshness stamps and the data-source
  credits.

### 9.4 Still open

Everything in §6 not listed above. In priority order:

1. **B-0** Pin the superadmin (config only).
2. **REL-1 / B-2** Schema migrations, before the next column change.
3. **B-34** Enforce the CSP after a week of report-only.
4. **B-1 remainder** Durable jobs, so a restart mid-deploy can't strand a launch.
5. **SEC-2 / B-5** Telegram bot channel allowlist and caps.
6. **SEC-3 / B-6** Validate provider-built transactions before signing.
7. **REV-1, REV-2, REV-4 / B-15–B-17** Late fees, owed referral balances, self-referral.
8. **SEC-18 / B-33** React Router 7.
9. **PRIV-1, PRIV-2** Account deletion, export and retention (qualified review needed).

Accessibility gaps not closed: no screen-reader pass with real assistive technology; the candle chart has no text
alternative beyond its price labels and the market table; some decorative motion remains on hover (short, and off
under reduced motion).

### 9.5 Deployment notes

- **Migrations:** none. No model or column changed.
- **Environment variables:** none new. Recommended now: `SUPERADMIN_USER_ID` (your user ID from `/auth/me`),
  `TONCENTER_API_KEY` (REL-6). Existing names are unchanged (`backend/.env.example`).
- **Dependencies:** the backend image must reinstall requirements to pick up Pillow 12.3.
- **Behaviour users will notice:**
  - An EVM deploy that doesn't confirm in time now shows "Sent, not confirmed yet" with a transaction link, instead
    of "failed".
  - Turning on 2FA signs out other browsers.
  - Transfers of a launch's token from TRON, NEAR or Arc wallets respect its allocation locks.
  - Signing out forgets connected TON and NEAR wallets in that browser.
- **After deploying:**
  - Check the response headers of `https://helmterminals.xyz/`.
  - Watch the browser console on the Networks page, Settings › Wallets and a TON Connect link for CSP reports.
