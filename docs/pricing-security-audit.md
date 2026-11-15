# Pricing and experience audit — 15 November 2026

This is a repository implementation and engineering review, using isolated databases, offline chain fixtures and
headless browsers. It is not an independent smart-contract audit or proof of mainnet behavior. No production
migration, deployment, live wallet transaction or treasury payout was performed.

## Implemented policy

| Item | Behavior |
|---|---|
| Launch | $20 in a supported chain stablecoin, otherwise an expiring native-coin USD quote |
| Admin / approved exception | Launch fee waived; trading fee remains 1% |
| Manual / scheduled trade / wallet swap | Fixed 1%; no additional Helm router fee |
| Creator allocation | No new Helm recipient, splitter or commission |
| Bridging / transfers | No Helm markup; third-party costs remain |
| Referral | 30% of actual confirmed Helm fees; $6 on a paid $20 launch |

Launch fee consent binds the account, draft, network, managed payer, recipient, amount and expiry. A confirmed
payment is a credit for the same draft when deployment fails. Uncertain payments are reconciled by signature,
never automatically repeated. Payment and referral amounts stay in exact integer base units. Referral liabilities
are excluded from reported Helm income. Historical immutable on-chain reward allocations cannot be rewritten
by changing this application and retain their original beneficiaries.

Admin launch exceptions require the owner role, password, a fresh authenticator code and an audited reason.
Normal users cannot grant themselves exceptions. Fee policy values cannot be changed through stale environment
or database rate settings. Missing treasury configuration blocks the affected financial action.

Solana fees normally execute atomically with the trade. Standalone fees use the tracked payment ledger, including
failed-fee settlement and a guard against placing further trades while a definitively failed fee remains owed.
Scheduled plans stop on outstanding debt or changed fee consent. Swaps preserve signed preview output floors
and reject expired, changed or worse fresh quotes. Tracked EVM router steps retain contract-recipient validation.

## Integration coverage

The implemented adapters are Pump.fun, LetsBonk, Bags, Meteora DBC, Flap on BNB/Base/Robinhood, Clanker on
Base/Robinhood, Pons, Four.meme, Uniswap V2, SunPump, Meme.Cooking and Tolly: 15 variants. Creation and trading
use the common pricing policy. The API advertises claim, automation and configuration capabilities explicitly.
An implemented adapter may still need an API key, enabled network, funding or provider access.

Printr, Surge, Anoncoin, Stonkfun, Bankr and GasPump remain roadmap entries. They are not fully integrated by
this change. Elpida is also explicitly planned on its five target chains, pending mainnet verification. See
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

- 21 pricing/migration tests passed, including exact referral conservation, launch consent, payment reuse,
  expiry, failed-fee retry, reconciliation, account isolation and audited exception permissions.
- Frontend: 13 tests passed; TypeScript and Vite production build passed.
- Real isolated PostgreSQL: 2 execution lease/nonce tests passed.
- Legacy Pons contract execution: 6 tests passed; pinned-bytecode/source-drift check: 1 test passed.
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
2. Configure and fund each enabled network's treasury, stablecoin payer and gas reserves. Verify exact recipients
   and amounts with small controlled staging transactions. Native fallback pricing must have a fresh USD source.
3. Complete per-adapter mainnet acceptance: creation, curve trades, graduation/DEX trades, claims, simulation
   refusal, expired quotes, RPC loss and restart recovery. Protocol payload validation is not yet a universal
   semantic decoder: signer/recipient checks and simulation alone do not prove every opaque provider instruction
   matches user intent. Provider allowlists and Turnkey production policies need independent review.
4. Validate sell-fee notional against protocol receipts/traces, including chain-specific gas, token transfer taxes
   and unrelated balance movements. Current native balance/simulation measurements are not a universal receipt
   decoder. Test nonstandard input tokens before enabling them for fee-bearing swaps.
   Initial developer buys embedded inside launch transactions also need explicit 1% fee consent, collection and
   delayed-deployment recovery coverage; manual/scheduled trade collection does not establish that coverage.
5. Referral shares collected into treasuries are recorded as owed. This change does not provide an automatic
   multi-network treasury payout worker or an operator receipt-backed settlement workflow. Solana atomic direct
   referral transfers are marked paid only when their transaction confirms. Establish treasury settlement before
   promising automatic payouts; adding a payout address does not send money.
6. Exercise real mobile Safari/Firefox, screen readers and wallet extensions; verify operational alerts, rate
   limits, trusted proxy configuration, production signer restrictions and disaster recovery. The in-process
   job orchestrator reconciles evidence but deliberately does not blindly resume interrupted signing work.

The release should remain gated on these items; passing local tests does not certify that real funds are safe.

Regression status: the full backend run has reported a failure and remains in progress. Do not interpret the
passing focused checks as a passing full suite. The public-info response assertion was updated for the new
pricing field; remaining failures require review against the final source.
