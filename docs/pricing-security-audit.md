# Pricing and experience audit — 15 November 2026

This is a repository implementation and engineering review, using isolated databases, offline chain fixtures and
headless browsers. It is not an independent smart-contract audit or proof of mainnet behavior. No production
migration, deployment, live wallet transaction or treasury payout was performed.

## Implemented policy

| Item | Behavior |
|---|---|
| Launch | Free for every account; no subscription or price-feed prerequisite |
| Admin / approved exception | Launches are already free; trading fee remains 2.5% |
| Manual / scheduled trade / wallet swap | Fixed 2.5%; no additional Helm router fee |
| Creator allocation | 2.5% on new Helm launches; unsupported collection paths block new reward-bearing deployment |
| Bridging / transfers | No Helm markup; third-party costs remain |
| Referral | 30% of actual confirmed Helm fees; $0.75 from a $2.50 fee |

Launch collectors are disabled, including retired helpers supplied historical quotations. Trading consent binds policy, network, managed payer, recipient and amounts. Uncertain payments reconcile by signature without automatically repeating them. Integer base units keep referral liabilities separate from Helm income. Historical immutable reward contracts retain their original rates and beneficiaries.

Old launch-exemption records no longer affect pricing because all launches are free. Fee policy values cannot be changed by stale environment/database rates. Missing treasury configuration blocks the affected financial action.

Solana fees normally execute atomically with the trade. Standalone fees use the tracked payment ledger, including
failed-fee settlement and a guard against placing further trades while a definitively failed fee remains owed.
Scheduled plans stop on outstanding debt or changed fee consent. Swaps preserve signed preview output floors
and reject expired, changed or worse fresh quotes. Tracked EVM router steps retain contract-recipient validation.

## Integration coverage

Implemented adapters include Pump.fun, LetsBonk, Bags, Meteora DBC, Printr, Flap on BNB/Base/Robinhood, Clanker on Base/Robinhood, Pons, Four.meme, Uniswap V2, SunPump, Meme.Cooking and Tolly: 16 identifiers. Implementation is not mainnet certification. Capabilities reveal collection readiness separately. New reward-bearing deployments require verified 2.5% collection, while existing tokens remain manageable. Pump.fun/Bags SOL/Clanker use native splits. Pons V2 artifacts reserve 250 bps; historical 600-bps artifacts remain pinned. Claims do not collect a second Helm fee from already split rewards. Other reward paths remain blocked pending integration.

The subsequently integrated upstream changes add Printr as a sixteenth implemented adapter and remove GasPump
from the roadmap. Surge, Anoncoin, Stonkfun and Bankr remain planned. Elpida is also explicitly planned on its
five target chains, pending mainnet verification. See
[Elpida integration requirements](elpida-integration.md). Availability in the directory means implemented and
configured; it does not mean a mainnet acceptance test passed.

Coverage retains protocol-specific limits: SunPump's post-graduation SunSwap routing is not implemented;
Meme.Cooking uses auction deposits/withdrawals and settlement rather than ordinary curve buys/sells. Creator
claim modes differ by pad. These limits must stay visible instead of presenting every pad as feature-equivalent.

## Interface and portability

The requested [UI UX Pro Max fork](https://github.com/ulquimakesmagic/ui-ux-pro-max-skill) is installed and applied.
The landing page, pricing presentation, billing history, referrals and admin exceptions use the shared dark
emerald design. Existing IBM Plex fonts remain bundled. Shared controls gain touch sizing, reduced-motion support
and mobile safe-area navigation. The mobile menu supports Escape, contained keyboard focus, background inertness
and focus restoration. Loading placeholders have a valid live-status role.

Browser evidence covers 375, 768, 1024 and 1440 CSS pixels: landing, sign-in, dashboard, launches, workspace,
wallets, networks, tracker, activity, profile, fees, settings and the admin entry page. The final recorded passes
have no automated WCAG A/AA violations, page errors or document overflow. Screenshots and JSON findings are in
`audit-evidence/pricing-ui/`. These Chrome checks do not establish parity on every iOS/Android browser, screen
reader, wallet extension, admin sub-dialog or provider error state. Wide tables intentionally scroll inside
labelled regions. Browser fixtures are simulated, with no real financial execution.

A separate pass covers all six admin sections at each width (24 checks). Its Treasury mobile overflow was fixed;
the final admin JSON reports no overflow, accessibility violations or runtime errors.

## Verification and dependency findings

- Updated pricing/API regressions are running; no passing full-suite claim yet. Older pricing results below predate the latest policy.
- Frontend: 19 tests passed; TypeScript and Vite production build passed for current pricing.
- Real isolated PostgreSQL: 2 execution lease/nonce tests passed.
- Twenty Solidity execution tests pass across new Pons V2, historical Pons and historical generalized splitters: rounding, replay protection, rejecting beneficiaries and reentrancy.
- The final migration-only rerun passes all 3 tests, including scientific-notation legacy SQLite values.
- Full backend and contract results are recorded separately in the evidence logs after completion.
- Python dependency audit: no known vulnerabilities after updating four packages. The lock includes transitive
  dependencies; the audit used the complete lock without resolving floating versions.
- Frontend Vite/source-map high-severity findings were fixed. Three low-severity findings remain in the
  TON Connect/deepmerge dependency chain. The [deepmerge advisory](https://github.com/advisories/GHSA-gcqx-w48m-w2h4)
  has no patched release listed; changing its internals without upstream compatibility tests is not a safe fix.

## Remaining release requirements

1. Review and apply migration `42c9a01f0004` to an isolated staging copy, verify backup restoration, then schedule
   the production migration with writers stopped. Production startup checks schema revision.
2. Configure each enabled network's treasury and gas reserves. Deploy and verify the separate Pons V2 factory. Complete missing reward collection and receipt indexing before enabling new reward-bearing routes. No stablecoin launch payer or USD launch-price source is required.
3. Complete per-adapter mainnet acceptance: creation, curve trades, graduation/DEX trades, claims, simulation
   refusal, expired quotes, RPC loss and restart recovery. Protocol payload validation is not yet a universal
   semantic decoder: signer/recipient checks and simulation alone do not prove every opaque provider instruction
   matches user intent. Provider allowlists and Turnkey production policies need independent review.
4. Validate sell-fee notional against protocol receipts/traces, including chain-specific gas, token transfer taxes
   and unrelated balance movements. Current native balance/simulation measurements are not a universal receipt
   decoder. Test nonstandard input tokens before enabling them for fee-bearing swaps.
   Initial developer buys now have explicit 2.5% consent and post-confirmation collection, including exempt/admin
   launches. Recovery records unsettled fees for explicit billing settlement without resuming signing. Focused
   tests exercise collection and recovery; per-adapter live acceptance remains necessary.
5. Referral shares collected into treasuries are recorded as owed. This change does not provide an automatic
   multi-network treasury payout worker or an operator receipt-backed settlement workflow. Solana atomic direct
   referral transfers are marked paid only when their transaction confirms. Establish treasury settlement before
   promising automatic payouts; adding a payout address does not send money.
6. Exercise real mobile Safari/Firefox, screen readers and wallet extensions; verify operational alerts, rate
   limits, trusted proxy configuration, production signer restrictions and disaster recovery. The in-process
   job orchestrator reconciles evidence but deliberately does not blindly resume interrupted signing work.

The release remains gated on these items; passing local tests does not certify mainnet behavior. Railway Hobby snapshots are unavailable and no public database URL is configured; no production database writes occurred during backup attempts. The repository is now public and CI jobs start successfully; release-head results remain pending. The live apex still serves an older build; www maps to a missing Vercel deployment. See [Proxima infrastructure analysis](proxima-infrastructure-analysis.md) for the next features and dependencies.

Regression status: the broad diagnostic reported failures and was stopped before integrating 33 upstream commits.
It was not a passing full suite. After integration, the production build, 19 frontend tests and 24 focused
pricing/migration/Printr tests pass. Earlier browser evidence predates that upstream merge. Remaining backend
failures and updated browser behavior require review against the integrated source.
