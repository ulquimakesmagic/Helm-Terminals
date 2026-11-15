# Helm Terminals design system

Applied with [UI UX Pro Max](https://github.com/ulquimakesmagic/ui-ux-pro-max-skill), forked from nextlevelbuilder on 15 November 2026. Product guidance: cryptocurrency exchange, clear hierarchy, restrained motion, explicit financial review. Existing Helm brand tokens override generic search-result colors and fonts.

## Visual language
Dark near-black surfaces, emerald primary actions, clear text hierarchy and thin separators. Use IBM Plex Sans for prose and IBM Plex Mono for amounts, addresses and transaction data. Existing bundled fonts avoid external font requests.

| Purpose | Token |
|---|---|
| Page / panel | --bg / --panel |
| Main / secondary / muted text | --text / --text-2 / --muted |
| Primary action / text on action | --accent / --accent-ink |
| Success / warning / failure | --good / --warn / --bad |

Use the shared CSS variables in frontend/src/styles.css. Labels or icons accompany status colors. Avoid decorative dashboard numbers, fabricated live activity, gradients behind dense data, and competing primary buttons.

## Interaction
Use one clear primary action per section. Financial operations preview their amount, network, wallet, recipient, costs and consequences before confirmation. Disable unavailable capabilities with an explanation. Preserve drafts, show recoverable errors, distinguish pending from confirmed, and never invite a blind retry of uncertain money movement. Show free launch pricing, 2.5% trading fees and exact creator-reward terms before confirmation.

## Responsive and accessible behavior
Support 375, 768, 1024 and 1440 CSS-pixel layouts. Stack actions and forms on narrow screens; use contained, labelled horizontal scrolling for wide tables. Touch targets reach 44 pixels. Keep focus visible, respect reduced motion, name inputs, use semantic headings and live status messages. Mobile navigation closes on Escape, contains keyboard focus, makes the background inert and restores focus.

## Pages
Landing: editorial headline, interactive Test Launch / $TEST demonstration, restrained entrance animation, concise workflow and truthful coverage. No pricing navigation, tables or fee-focused marketing. Transaction reviews retain all disclosures.
Application: persistent navigation, mobile quick navigation, consistent cards, amounts and review dialogs.
Settings: network status, private performance-card preferences and transaction records. No standalone billing page. Launches remain free; trading and new creator allocations remain 2.5%. Historical records and recoverable settlement remain available.
Admin: immutable pricing, password/TOTP step-up for exceptions, audited reasons, separate received income and referral liabilities.
Referrals: 30% examples, balances by network/asset, explicit owed status and per-network payout addresses.

Page-specific guidance in pages/ takes precedence where it provides additional detail. Browser evidence is under docs/audit-evidence/pricing-ui; automated accessibility scans complement manual visual and keyboard review.

Trading automation is permanently retired. No unattended trading controls, APIs or worker may be introduced. Profiles are private; nicknames identify exported cards without public account pages. Media backgrounds are owner-only, bounded and encrypted at rest. Sidebar navigation scrolls independently and keeps the account footer reachable on short screens.
