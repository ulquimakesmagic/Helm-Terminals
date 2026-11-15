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

Current evidence: 19 frontend tests, TypeScript and production build pass. Twenty local Solidity execution checks pass across Pons V2 and historical splitters. Pricing acceptance (28 cases), focused follow-ups (14) and new reward-policy/receipt checks (3) pass. All 72 refreshed public, signed-in and admin viewport checks report no accessibility violations, runtime errors or page-wide overflow. The full backend suite is running; a scheduled-sell fixture with no simulated proceeds was corrected without weakening fee verification. Earlier PostgreSQL/migration checks predate this policy. Three low-severity transitive TON Connect findings remain pending upstream.

The requested skill fork is https://github.com/ulquimakesmagic/ui-ux-pro-max-skill and is installed locally.
Design rules are persisted in `../design-system/helm-terminals/MASTER.md`.
See `pricing-security-audit.md` for scope, evidence, remaining limitations and release gates.

The [Proxima research](proxima-infrastructure-analysis.md) records feature gaps, architecture, candidate launchpads and delivery order. Elpida remains planned until deployed mainnet programs/contracts are verified.

Branch: codex/pricing-ux-audit; draft PR https://github.com/ulquimakesmagic/Helm-Terminals/pull/11. The repository is now public as requested; CI jobs start successfully after the visibility change. Matching checks for this pricing release remain pending. No new production code deployment or migration has completed. The apex still serves an older Railway build; www points to a missing Vercel deployment. Railway frontend source metadata now uses the renamed GitHub account. Migration 42c9a01f0004 requires a restorable backup and staging-copy validation: Hobby snapshots are unavailable, and no public database URL is configured. No production database writes occurred during backup attempts.
