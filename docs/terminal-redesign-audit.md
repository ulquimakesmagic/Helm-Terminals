# Terminal redesign and automation retirement

Current scope: `docs/uiux-rebuild-prompt.md`; branch `codex/terminal-redesign`.

## Implemented

- Editorial landing page and authentication redesign, interactive Test Launch / $TEST demonstration,
  short entrance transitions and reduced-motion support. Pricing marketing is removed; transaction disclosures remain.
- Independent sidebar scrolling with a reachable account footer. Network status and transaction records move
  into Settings. Historical fee records, recovery settlement and referrals are retained.
- Clearer dashboard hierarchy, compact chain coverage, launch filters with searchable icons/names, automatic
  visible-page wallet refresh, and correct extra-chain balance availability checks.
- Private profile identity and performance-card preferences. No public profile route or publishing switch.
  Three themes, PNG/JPEG backgrounds (5 MB), MP4 backgrounds (10 MB / 30 seconds), crop/overlay/privacy controls,
  PNG export and browser-supported muted WebM export. Background media is owner-only and encrypted at rest.
- Trading automation routes, UI and execution worker are removed. A central dispatcher refuses historical
  scheduled job kinds. Migration 0005 cancels schedules and unsigned queued work; signed identities, receipts,
  historical terms and financial records remain. Rollback does not reactivate schedules or publish accounts.
- Migration 0006 adds private presentation preferences/media. Background reconciliation and manual job execution remain.
- Post-launch folder/single-wallet attachment, chain/ownership checks, developer-role protection, explicit
  distribution/custom amounts and token consolidation controls. Existing core-chain transfer previews and 2FA remain.
- Exact distribution inputs and raw amounts in consent hashes prevent rounding or collapsing distinct base-unit amounts.
- The top protocol banner is moved to launch details. Live workspaces prioritize chart and manual trading.
- BNB Flap standard creation uses current V7 with the documented holder LP-dividend / Infinity CL configuration;
  the configured mainnet RPC accepted a read-only zero-value creation simulation with matching predicted address.

## Verification so far

- TypeScript and production frontend build pass.
- Eight initial privacy/media/removal tests pass; additional dispatcher, precision and populated migration tests added.
- Forty isolated public/private browser checks at 375, 768, 1024 and 1440 widths report no JavaScript errors,
  automated WCAG A/AA violations or document overflow. Additional wallet/terminal/media/admin interactions are being checked.
- Short-screen sidebar inspection at 1440 × 650: account footer stays inside the viewport.
- The corrected migration/manual launchdesk follow-up passes 15 checks. Complete release-head CI is still required.
- Production-configured read-only NEAR and TRON balance probes succeeded. The previous UI checked only `balance_sol`
  when deciding whether an extra-chain balance was unavailable; those chains return exact `balance_native` strings.

## Operational and protocol gates

No release deployment or production schema migration has yet been performed for this branch. The old production
worker was configured off ahead of retirement. Back up and validate a restored production copy before upgrading.

See `launchpad-capability-review-2026-11-15.md`. Existing Bags, Jupiter and Pinata credentials are configured.
LetsBonk, Meteora DBC, Printr and taxed Flap routes require creator-reward collection integration; Pons requires
the verified V2 factory. Bankr needs a compatible partner agreement, credentials and custody/recipient integration.
Other planned pads and Elpida require verified deployed interfaces and acceptance tests. No unsupported route is
enabled by relabelling its availability. Full upstream option parity is not claimed.

Token distribution/consolidation currently use Helm's existing Solana/core EVM transfer engine. Other network
variants need equivalent storage/activation/token-tax handling and receipt-backed transfer acceptance before exposure.
The account card reports measured net flows; it does not invent realized/unrealized investment PnL where complete
cost basis and valuations are unavailable. These limitations stay visible in the UI.
