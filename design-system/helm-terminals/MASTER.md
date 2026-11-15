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
Use one clear primary action per section. Financial operations preview their amount, network, wallet, recipient, costs and consequences before confirmation. Disable unavailable capabilities with an explanation. Preserve drafts, show recoverable errors, distinguish pending from confirmed, and never invite a blind retry of uncertain money movement. Show account exemptions and existing launch credits before payment.

## Responsive and accessible behavior
Support 375, 768, 1024 and 1440 CSS-pixel layouts. Stack actions and forms on narrow screens; use contained, labelled horizontal scrolling for wide tables. Touch targets reach 44 pixels. Keep focus visible, respect reduced motion, name inputs, use semantic headings and live status messages. Mobile navigation closes on Escape, contains keyboard focus, makes the background inert and restores focus.

## Pages
Landing: large editorial heading, labelled example terminal, short workflow, transparent pricing and truthful coverage.
Application: persistent navigation, mobile quick navigation, consistent cards, amounts and review dialogs.
Billing: $20 launch, 1% trade, creator allocation retained; exact base-unit payment history and recoverable fee settlement.
Admin: immutable pricing, password/TOTP step-up for exceptions, audited reasons, separate received income and referral liabilities.
Referrals: 30% examples, balances by network/asset, explicit owed status and per-network payout addresses.

Page-specific guidance in pages/ takes precedence where it provides additional detail. Browser evidence is under docs/audit-evidence/pricing-ui; automated accessibility scans complement manual visual and keyboard review.
