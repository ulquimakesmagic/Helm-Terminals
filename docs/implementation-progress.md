# Helm pricing, integrations and experience

Current policy: launches are free for everyone; trades on Helm cost 2.5%; creator rewards on new Helm launches allocate 2.5% to Helm. Referrers earn 30% of actual collected Helm fees. Gas and launchpad costs remain separate. Optional future Pro infrastructure must not gate basic launches. Previous launch-payment and 1%/2% trading proposals are superseded.

Historical immutable reward contracts keep their original rate and recipients. New Pons V2 artifacts reserve 250 bps; the historical 600-bps artifacts remain pinned. New deployment requires a separate, code-verified V2 factory. Pump.fun, Bags SOL and Clanker configure native 97.5% / 2.5% splits. Claims do not charge again for those splits. Missing collection integrations block new reward-bearing deployments and remain explicit in capabilities; existing token management continues.

Work areas:
- Fixed pricing policy and asset-aware referral accounting.
- Disabled launch payments, including retired collectors supplied historical quotations.
- Consistent fees on manual/scheduled trades and swaps, including curve integrations.
- Backend/UI capability registry, accurate readiness and reward descriptions.
- Shared responsive design and landing, review, admin and referral experiences using UI UX Pro Max.
- Security, crash recovery, migration, dependency and browser verification; evidence and limitations.

Trading payments retain frozen consent, exact amounts and signed identities before broadcast. Uncertain payments reconcile without repeating a signature. Confirmed Pons reward receipts reserve referral liabilities and report Helm net income; other native reward ingestion and treasury payout settlement remain outstanding.

Current evidence: GitHub CI run 43726775985 passed all seven jobs on release source 519b310. The backend passed 547 tests with 2 skips; PostgreSQL execution checks, Solidity execution tests, contract artifact checks, frontend tests/build and both dependency audits passed. All 72 refreshed public, signed-in and admin viewport checks report no accessibility violations, runtime errors or page-wide overflow. Three low-severity transitive TON Connect findings remain pending upstream. A restored production database copy migrated from 42c9a01f0003 to 42c9a01f0004 with all original table/column hashes unchanged.

The requested skill fork is https://github.com/ulquimakesmagic/ui-ux-pro-max-skill and is installed locally.
Design rules are persisted in `../design-system/helm-terminals/MASTER.md`.
See `pricing-security-audit.md` for scope, evidence, remaining limitations and release gates.

The [Proxima research](proxima-infrastructure-analysis.md) records feature gaps, architecture, candidate launchpads and delivery order. Elpida remains planned until deployed mainnet programs/contracts are verified.

Release source: 519b3107b1425c1833b1f982105fa119ff31605f; PR https://github.com/ulquimakesmagic/Helm-Terminals/pull/11. The repository is public as requested, after history and discussion secret review. The API, production migration and frontend rollout completed successfully, in that order. The www Vercel redirect is verified and preserves paths and query strings. A validated encrypted production backup is stored locally with its key separately, and the temporary database proxy was removed and its removal verified. No live wallet transaction or treasury payout was performed. The production landing page passed four viewport checks. See [release verification](release-verification-2026-11-15.md) for hosting and browser results.
