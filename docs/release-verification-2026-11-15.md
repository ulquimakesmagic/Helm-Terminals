# Helm hosting release — 15 November 2026

Application source: `519b3107b1425c1833b1f982105fa119ff31605f`.
PR: https://github.com/ulquimakesmagic/Helm-Terminals/pull/11.
CI: https://github.com/ulquimakesmagic/Helm-Terminals/actions/runs/43726775985.

## Verification

All seven CI jobs passed. The backend passed 547 tests with 2 skipped; separate
PostgreSQL execution, Solidity execution/artifact, frontend test/build and Python/
frontend dependency audit jobs passed. Three low-severity transitive TON Connect
findings remain documented in the engineering audit.

Before changing the production schema, a custom-format PostgreSQL backup was
validated and encrypted locally; its key is stored separately outside the repository.
A disposable local PostgreSQL instance restored the backup and migrated revision
`42c9a01f0003` to `42c9a01f0004`. Hashes of every original table's original columns
were unchanged. The local database copy was stopped and removed. The temporary
production database TCP proxy was removed and its removal verified. No credentials,
backup data or encryption keys are included in this repository.

## Live rollout

The API deployed first, using the committed lockfile and an Alembic pre-deploy step.
The additive PostgreSQL migration completed and API startup completed successfully.
The frontend then deployed from the same source snapshot. Snapshots were exported
with `git archive`, excluding local environments, audit data and dependencies.

| Surface | Deployment / observed result |
| --- | --- |
| Railway API | `2a8422bc-a12e-43c6-8460-41c3a0343588`, successful |
| Railway frontend | `d3671f4e-478e-4e18-b987-89397e07c2d3`, successful |
| Vercel www redirect | `dpl_CpNv28m7dn7RUQP2X1fGEAHtvCZ7`, ready; only www assigned |
| API readiness | `https://api.helmterminals.xyz/health/ready`: 200, `ready: true` |
| Public pricing | Free launches; trade 250 bps; creator share 250 bps; referral share 3,000 bps; router fee zero |
| Apex landing and `/billing` | 200 HTML; landing uses `index-Dj0dmpL0.js` / `index-BRvzPMpm.css` |
| Browser API preflight | 200; origin `https://helmterminals.xyz` allowed |
| www path/query preservation | `/launches?ref=release-check`: 308 to matching apex URL |

The Vercel project `helm-www-redirect` uses the static configuration in
`tools/deploy/www-redirect/`. Existing apex/API Railway DNS and unrelated Vercel
projects were retained. Railway frontend source metadata uses the renamed GitHub
account. The repository is public after the documented history/discussion review.

Fresh production landing-page browser checks passed at 375, 768, 1024 and 1440 CSS
pixels: HTTP 200, current free/2.5% copy, no page-wide overflow, no JavaScript page
errors and no automated WCAG A/AA violations. Chrome screenshots and JSON were
captured locally. These complement the 72 committed isolated public, signed-in and
admin checks; no production account was used and no transaction was signed.

## Limits

This verifies the code rollout and schema migration, not universal mainnet execution.
New reward-bearing creation remains blocked where 2.5% collection is not implemented.
Pons V2 still requires a separately deployed and verified factory. Missing native
reward receipt indexing, treasury referral payout settlement, protocol-specific
mainnet acceptance and real-device testing remain in `pricing-security-audit.md`.
Historical immutable splitters retain their original terms. No live wallet
transaction, treasury payout or optional Pro subscription was created by this release.
