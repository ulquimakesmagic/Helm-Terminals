# Helm audit remediation tracker

> Superseded for current status by `docs/audits/helm-remediation-tracker-2026-10-06.md` (6 Oct 2026). The table below is the 4 Oct snapshot.

Baseline: `main` at `2b8928d`. Scope: `docs/helm-professional-audit.md`, findings AUD-01–16 and sections 6–11. Application changes, safe local checks and reports are authorized; no commit, push, deployment, production configuration changes or live financial transactions.

Statuses distinguish local implementation from production verification. No finding is closed by a passing unrelated test or by hiding a warning.

| Finding | Work / acceptance evidence | Status |
|---|---|---|
| AUD-01 | Typed transaction intent; reject malicious provider mutations before signing; valid adapter fixtures | Open |
| AUD-02 | Persist operation requirements; approval-only/partial execution cannot recover as success; crash tests | Open |
| AUD-03 | Bind original output floor, input ceiling, recipient, fees and expiry; quote deterioration tests | Open |
| AUD-04 | Correct CI paths; direct committed-artifact verification; independently enforced dependency checks | Open |
| AUD-05 | Receipt-backed accounting; unverified estimates separated; idempotent reconciliation | Open |
| AUD-06 | Provider/pair scope, pair selection and details, response validation, market error-state tests | Open |
| AUD-07 | Explicit SOL-only flow scope and missing-data semantics | Open |
| AUD-08 | Pons wallet eligibility and unswept/escrow/splitter accounting; protocol permissions verified | Open |
| AUD-09 | Email verification, password/session lifecycle and MFA recovery with custody safeguards | Open |
| AUD-10 | Current dependency scans, patched versions and compatibility checks | Open |
| AUD-11 | Fail closed on lost execution lease; isolated PostgreSQL ownership/nonce tests | Open |
| AUD-12 | Bounded off-loop password work; aggregate abuse limits; responsiveness checks | Open |
| AUD-13 | Freshness-aware health and inventory error states | Open |
| AUD-14 | Accurate Telegram/funding/chain capabilities shared with UI | Open |
| AUD-15 | Intentional enforced CSP and hardened/revocable browser sessions | Open |
| AUD-16 | Schema/build identity and release gates; isolated restore/rollback and operational evidence | Open |

## Additional required work

- Revenue path inventory and financial invariants: integer amounts, consent-frozen fee recipients, liabilities, external claims and supported launchpad mechanisms.
- UI: homepage, auth, dashboard, launch form, workspace, wallets, admin, activity/settings; desktop/mobile screenshots and keyboard/accessibility checks.
- Performance: measure stage timing, preserve bounded concurrency, assess optional connector loading; no shortcut around financial checks.
- Test coverage: full suite where feasible, malicious provider fixtures, restart tests, actual PostgreSQL semantics, component/browser checks, dependency and artifact scans.
- Delivery: `docs/helm-remediation-report.md`, environment variable reference, migrations, rollback instructions and remaining release gates.

## External release dependencies

Independent Pons contract review, live deployed bytecode/eligibility acceptance, Turnkey organization policies, production origin/configuration review, hosting branch protection, actual production backup restoration and GMGN network access cannot be certified by local source edits. Prepare concrete verification instructions and keep affected activation gates closed until evidence exists.
