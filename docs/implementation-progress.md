# Helm pricing, integrations and experience

Requested policy: $20 per launch, 1% of trades, no Helm creator-reward share, no other Helm fees. Launches are free for admin accounts and explicitly exempted accounts. Referrers earn 30% of actual Helm fees, including launch fees. Stablecoin launch payments are preferred where supported; otherwise use an expiring native-coin quote.

Implementation preserves existing local changes and historical on-chain reward entitlements. Immutable deployed reward splitters cannot be rewritten by an application upgrade. New launches must not configure them.

Work areas:
- Central pricing policy, auditable launch exemptions and asset-aware referral accounting.
- Reviewed, tracked launch payments with one reusable payment per launch draft.
- Consistent fees on manual/scheduled trades and swaps, including curve integrations.
- Backend/UI capability registry, accurate readiness and reward descriptions.
- Shared responsive design and landing, review, admin and referral experiences using UI UX Pro Max.
- Security, crash recovery, migration, dependency and browser verification; evidence and limitations.

Launch payment semantics: the fee is reviewed and paid before deployment. A confirmed payment remains credited to the same draft if the launchpad fails. Uncertain payments are reconciled and never automatically charged again. Network and launchpad costs are separate.

Current evidence: 21 pricing/migration checks and 13 frontend tests pass; TypeScript and production build pass.
The isolated PostgreSQL execution checks pass (2 tests). Browser checks cover 40 authenticated and 8 public
page/viewport combinations with no reported accessibility violations, runtime errors or page-wide overflow.
The full backend regression run is continuing; its log is `audit-evidence/pricing-backend-tests.txt`.
The Python dependency scan is clean after updating aiohttp, certifi, dnspython and Pillow. Frontend high-severity
findings are fixed; three low-severity transitive TON Connect findings remain pending an upstream fix.

The requested skill fork is https://github.com/ulquimakesmagic/ui-ux-pro-max-skill and is installed locally.
Design rules are persisted in `../design-system/helm-terminals/MASTER.md`.
See `pricing-security-audit.md` for scope, evidence, remaining limitations and release gates.

Upstream integration: merged origin/main at a149eae after pushing checkpoint 3f34a2b to
codex/pricing-ux-audit. This preserves Printr, legal pages, the same-origin API proxy and routing infrastructure,
while retaining the requested $20/1%/zero creator-share policy. The integrated production build and 19 frontend
tests pass; 24 focused pricing, migration and Printr tests pass. The earlier broad backend diagnostic was stopped
before replacing its source tree; it reported failures and was not a passing full run.

Follow-up: initial developer buys now pay the reviewed 1% trading fee after confirmation, including admin and
launch-exempt accounts. A late/recovered deployment records a settleable fee without automatically signing.
Distinct Solana payments carry unique public references to avoid duplicate transaction signatures in one block.
The pricing/API pass reports 52 tests passed, and the focused collection/recovery/reference pass reports 3 passed.
Upstream route-provider guards and optional simulated Solana spend limits are retained in the executor.
