# Release notes and deployment guide — audit remediation, 6 October 2026

Branch `audit-remediation-2026-10` (local commits on top of `main` at `c69ddc0`). Not pushed, not deployed.

## Reports

- Audit: `docs/audits/helm-audit-2026-10-06.md`
- Remediation tracker: `docs/audits/helm-remediation-tracker-2026-10-06.md`
- UI/UX: `docs/audits/ui-ux-report-2026-10-06.md`
- Codex integration and coverage: `docs/market-data.md`
- Imagery prompts: `docs/higgsfield-imagery.md`

## Test results (local, Python 3.13, Node 22)

| Suite | Result |
|---|---|
| Backend `pytest` (whole suite incl. contract bytecode tests) | 480 passed, 2 skipped (PostgreSQL-only tests need `HELM_TEST_POSTGRES_URL`) — `docs/audit-evidence/2026-10-06/final-backend-tests.txt` |
| New security/market tests | `test_spend_bounds.py` 16, `test_hardening_2026_10.py` 6, `test_market.py` 26 (replaces the GMGN/DexScreener suites) |
| Contracts `npm run check` | passes on Linux |
| Frontend `tsc` + `vite build` + `node --test` | pass; 10 tests |
| `npm audit` (all deps) / `pip-audit` (lock) | 0 / no known vulnerabilities |
| Visual layout checks / axe-core | 0 problems on 95 page-width combinations / 0 violations on 17 pages |

A passing build is not proof every workflow works: no launchpad, router, bridge, signer or Codex call was made
against a real service.

## Performance

| Measure | Before | After |
|---|---|---|
| Main JS chunk (first load for every visitor) | 350.6 kB (116.8 kB gzip) | 261.7 kB (85.7 kB gzip) |
| Dashboard, recovery | in main chunk | loaded on demand (Dashboard 21.7 kB) |
| Workspace chunk | 75.4 kB | 79.1 kB (+ own candle chart, − GMGN iframe and its third-party page load) |
| Market requests per token | GMGN or 2 Codex POSTs per 60 s, plus a third-party chart iframe per viewer | 1 Codex request per 30 s shared by all viewers, 1 per timeframe for candles (15–120 s cache) |
| Market polling in the browser | 60 s | 30 s snapshot, 20–60 s candles, paused while the tab is hidden |
| Extra cost per provider-built Solana transaction | — | 2–3 RPC reads + 1 simulation before signing (HT-01) |

API latency, database query timing and background job cost were not measured against production; the local
harness uses SQLite and a fake RPC, so its timings are not representative.

## Environment variables

| Variable | Change |
|---|---|
| `CODEX_API_KEY` | **Required for market data and portfolio USD values** (server secret) |
| `CODEX_REQUESTS_PER_MINUTE` | Optional, default 120 |
| `GMGN_API_KEY`, `GMGN_REQUESTS_PER_MINUTE` | **Obsolete** — remove |
| `JUPITER_API_URL` | Now blank by default; blank = `https://api.jup.ag` with a key, lite endpoint without. Remove any value you set only to work around the old duplicate |
| `JUPITER_API_KEY` | Unchanged meaning (now declared once) |

No other variables changed. **No database migration** is needed: no models changed.

## Deploy (when authorized)

1. Review and merge the branch; let CI run (all jobs should now pass, including `contracts` and `audit-frontend`).
2. Set `CODEX_API_KEY` in the API service; remove the GMGN variables.
3. Deploy the API, then the frontend (`npm run build`, served by `node server.mjs`). Order matters only for the market
   panel: the new frontend reads the new `/market` shape and `/market/candles` route.
4. Check: `GET /health`, `GET /health/ready`, `/docs` returns 404 in production, `python -m app.market_check --network
   solana --token <a known token>`, a launch workspace shows Codex data and candles, `/terms` and an unknown path
   (404 status) on the frontend.
5. Rehearse with small amounts on each Solana launchpad (Pump.fun buy/sell/deploy, LetsBonk, Bags, Meteora DBC), a
   Jupiter swap, a Relay bridge and an EVM swap. Watch for refusals that mention "refused before signing"; if a
   legitimate transaction is refused, record the reason and adjust the allowance in `services/solana_guard.py`
   rather than disabling the check.

## Rollback

Redeploy the previous image/commit (`c69ddc0`) for API and frontend. The release adds no schema changes or stored
state, so rollback needs no data steps. Restore `GMGN_API_KEY` only if rolling back the API. Jobs confirmed under the
new code are ordinary jobs and remain readable by the old code.

## Operations recommendations (not enabled; each needs approval)

| Integration | Benefit | Cost / data sharing | Variables |
|---|---|---|---|
| Error monitoring (Sentry or self-hosted GlitchTip) | Exceptions in jobs and API with stack traces | Free tier / paid; sends stack traces and request metadata — scrub headers, bodies and addresses | `SENTRY_DSN` |
| Uptime checks (Better Stack, UptimeRobot) on `/health/ready` and the frontend | Outage alerts | Free tiers; only public URLs | — |
| Structured log drain with alerts on `refused before signing`, `codex_market_failure`, stuck `submitted` jobs, signer errors | Detect provider compromise attempts and outages | Hosting log drain pricing; logs contain no secrets by design | Platform setting |
| Privacy-conscious analytics (Plausible, self-hosted Umami) | Page-level usage without cookies | Paid or self-hosted; page URLs only. Would require updating the privacy policy | `VITE_ANALYTICS_*` |
| Support desk (shared inbox or Plain) | A real support contact, needed for legal pages and recovery | Paid; user emails | — |
| Status page | Communicate incidents | Free/paid | — |

## Prioritized next steps

1. Small-amount production rehearsal of every provider-built path after HT-01/02 (above).
2. Verify Codex with the real key; decide the plan tier (holders).
3. Complete the legal placeholders with counsel; add a support contact; then publish.
4. Configure SMTP so password reset works; consider requiring email verification before creating wallets.
5. Turnkey policy review: per-key spend limits and destination allowlists as an independent layer.
6. Independent review of the Pons splitter before enabling it.
7. Backup restore drill and alerting (AUD-16).
8. Cross-browser (WebKit/Firefox) and real-device mobile checks; a screen-reader pass of the trade ticket and dialogs.
9. Program allowlists per Solana adapter once real transaction shapes are recorded, to complement simulation checks.
