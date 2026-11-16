# Helm experience review — 16 November 2026

The live signed-in review exposed oversized portfolio/profile cards, an overflowing
launch dialog at 659 × 571, and a dashboard timer that updated the age label
without fetching new balances. The wallet inventory also had an initial-read
race in React's development lifecycle. These are corrected in this change.

## Changes

- Compact private-page headings, metrics, tables, profile preview and launch cards.
- Dialog tracks shrink to the viewport; the launch form now has Token, Launch
  settings and Review steps with persistent values and explicit chain selection.
- Per-adapter controls remain sourced from the server schema. Auction deposits
  and starting liquidity have different descriptions from creator purchases.
- Dashboard, wallets and network-wallet views refresh every 30 seconds while
  visible; workspaces refresh every 10 seconds. Focus, visibility and connectivity
  events trigger catch-up reads. Scheduled reads do not overlap; failures retry.
- The chart leads the small-screen chart view. GMGN-supported tokens also offer
  an explicit Codex chart choice; this cannot repair an external provider outage.
- Landing and desktop authentication previews use an animated Helm logo and
  original transparent $TEST artwork. Motion can be paused and respects reduced
  motion. Authentication forms have smaller typography and clearer entry copy.
- Network redirects preserve the chosen network, sessions show six recent rows
  before expansion, and referral payout credentials stay closed until requested.
- Telegram copy reflects the deployed read-only monitoring capability.

## Validation and boundaries

The browser audit uses isolated users and simulated chain data, never production
credentials or funds. Its four sizes are 375 × 812, **659 × 571**, 800 × 600 and
1440 × 900. The general audit checks 60 page/form states for overflow, JavaScript
errors and axe WCAG A/AA violations. Separate checks exercise workspace tabs,
administrator views, session expansion, native-form schema rendering and timer
behavior. Third-party iframe content is outside Helm's accessibility audit.

The 16 adapter forms were exercised through draft review using production's
public schemas. Setup-gated routes use UI capability fixtures for this check.
No draft, deployment, trade, transfer or reward claim was submitted by that test.
Rendering a form does **not** establish upstream feature parity or mainnet readiness.

## Remaining native-flow integration work

| Route | Current Helm flow | Work still needed for the broader native experience |
|---|---|---|
| Pump.fun | SOL curve, metadata, optional creator purchase | Additional quote and reward modes, banners/video |
| LetsBonk | SOL metadata and creator purchase | Creator-reward collection must be verified before enabling new launches |
| Bags | SOL curve with configured royalty recipients | Custom quotes need DBC, Fee Share V2, staged plans and quote-asset claims; new custom-pair choices are disabled |
| Meteora DBC | Integrated Studio presets, SOL/USDC and anti-sniping | Reward collection gate, full custom curve and vesting configuration |
| Printr | Solana curve and supported graduation target | Reward collection gate; multichain/native advanced modes |
| Clanker, Base and Robinhood | Explicitly labeled **V4 WETH preset** | Versioned V5 adapter, tax/dividend/burn controls, registered quotes and matching reward escrow |
| Flap, BNB | V7 standard zero-tax route with holder LP dividends | Creator-tax collection before enabling taxed modes; advanced allocation/payment-token options |
| Flap, Robinhood | Standard zero-tax ETH route | Creator-tax collection and advanced native configuration |
| Flap, Base | Tax-token preset | Creator-reward collection gate |
| Pons | Prepared V2 ETH route | Operator-funded factory deployment, runtime verification and configuration; advanced pairs/recipients |
| Four.meme | BNB route with category | Open Mode, alternative raised tokens and configurable tax modes |
| Uniswap V2 | Token plus ETH pool, supply share and LP ownership | Broader position building is outside the current V2 preset |
| SunPump | Name/ticker publication, TRX creator purchase | Full upstream metadata and post-graduation routing |
| Meme.Cooking | Timed NEAR auction, caps and optional deposit | Scheduled starts and team allocations |
| Tolly | Fixed USDC pool preset | Additional native configuration where the protocol supports it |

The next adapter upgrades must preserve historical launches' versions and reward
recipients. They need native configuration discovery, matching transaction
simulation/receipt verification, treasury and referral accounting, and recovery
for partially completed staged plans. Removing a setup badge alone is insufficient.

Primary version checks: [Clanker V5 contracts](https://github.com/clanker-devco/DOCS/blob/main/references/core-contracts/v5.md)
and [Bags custom-quote launch guide](https://docs.bags.fm/how-to-guides/launch-token-non-sol-quote).
The current controls and restrictions are in `frontend/src/launchpadForms.ts` and
`backend/app/routes/launches.py`. Funding instructions and their limits are in
[the setup guide](mainnet-integration-setup.md) and [the dollar budget](mainnet-budget-2026-11-16.md).
