# UI/UX report — 6 October 2026

## Decision: unify and harden, don't rebuild

The existing frontend already has a coherent token-based design system (`frontend/src/styles.css`: surfaces, text,
emerald accent, status colours with ≥4.5:1 text contrast, a 4 px spacing grid, a nine-step type scale, IBM Plex Sans
/ Mono, reduced-motion support), shared primitives (`components/ui.tsx`: Card, Metrics, Callout, Field, Seg, Modal,
toasts, status badges, addresses with explorer links) and one confirmation flow for every fund-moving action
(`SpendDialog` + `review.tsx`). The baseline scan found no inconsistent bespoke dialogs: every dialog already uses
the shared `Modal`. A full rebuild would have risked regressions in safety-critical flows for little visual gain, so
the work targeted the gaps the evidence showed. Brand colours are unchanged.

## Reference research

| Reference | Access | What was used |
|---|---|---|
| Awwwards, Mobbin, Dribbble (URLs in the brief) | **Not accessible.** The fetch tool requires the user to approve URLs that appear only in an attached document; the approval requests timed out while working unattended. Mobbin also requires a login. | — |
| Watermelon UI, recent.design | **Not accessible.** Found by web search; their showcase pages timed out. | — |
| Dribbble crypto-dashboard search results | Titles only (no visual detail in the fetched content) | Confirms the genre (dark, dense dashboards) but gave no specific patterns |
| [Circle: confirmation UIs](https://developers.circle.com/sdks/user-controlled/confirmation-uis.md) | Read | Show from/to (masked with first/last characters), token, fiat equivalent, network fee estimate, and contract/function for contract calls; never sign without a confirmation surface |
| [Wavespace: 2026 website design examples](https://www.wavespace.agency/blog/best-website-design-examples) | Read | Two type families, consistent spacing rhythm, restrained motion, trust signals surfaced early, high-contrast fintech palettes |
| Widely used DeFi terminals (Jupiter, Uniswap, Coinbase Advanced, Hyperliquid) | General product knowledge, not re-inspected | Price strip above a candle chart; timeframe segmented control; OHLC legend on hover; ticket beside the chart on desktop and as a tab on mobile; explicit "minimum received" and quote countdown in swap reviews |

No layouts, assets or branding were copied.

## Changes

- **Market terminal** (`Market.tsx`, new `CandleChart.tsx`): Codex price strip (price, 24h change, market cap and FDV
  labelled separately, liquidity, volume, holders), freshness label, scam flag, honest state callouts, a candlestick
  chart drawn from Codex OHLCV (six timeframes, all-pools or a selected pool, OHLC legend, last-price line, crosshair
  for mouse/touch, ←/→/Home/End keyboard inspection announced to screen readers, a candle data table), pool facts and
  5m/1h/4h/12h/24h activity.
- **Dialogs** (`Modal`): rendered in a portal; the page behind is `inert` and scroll-locked (nesting-safe); focus
  trap, Escape, focus restoration (verified); mobile bottom sheet uses `dvh` and a sticky footer above the safe area.
  Toasts moved to a portal so they're still announced while a dialog is open.
- **Financial confirmations** (`SpendDialog`, `review.tsx`): a changed preflight or expired quote no longer dead-ends
  — the dialog re-previews and explains that nothing was sent; held quotes show a countdown and require a refresh
  after expiry; swap reviews list the wallet and the locked minimum and say what happens if the market moves.
  Every review leads with the network and token; full server preflight remains available.
- **Navigation**: real not-found pages (public and in-app), return to the requested page after sign-in, public footer
  (product, account, legal) and app footer, legal pages.
- **Responsive fixes** found by the harness: settings grid at 320 px, activity filter wrapping, long source URLs on
  Networks, labelled search fields.
- **Copy accuracy**: landing page describes swap/route floors correctly; dashboard no longer says unpriced days count
  as $0.

## Verification

Harness: `tools/visual-qa/` — fixtures captured from the real API with a seeded disposable database
(`capture_fixtures.py`), screenshots and layout checks with Playwright Chromium 141 (`shoot.py`), axe-core 4 scan
(`axe_scan.py`).

| Check | Before | After |
|---|---|---|
| Layout problems (overflow, elements wider than the viewport, unlabeled controls, images without alt) across 19 pages × 5 widths (320, 390, 768, 1440, 1920) | 15 page/width combinations | 0 |
| axe-core WCAG 2.0/2.1/2.2 A/AA violations, 17 pages | not run | 0 (after fixing a brand-link name mismatch and legal contents target sizes) |
| Dialog keyboard behaviour (focus trap, Escape, restore, inert background) | not tested | pass |
| Enforced CSP (inline script refused) | — | confirmed |

Screenshots: `docs/audit-evidence/2026-10-06/screens/` (before/after workspace at 1440 and 390, settings and activity at
320, not-found, terms at 390, landing, dashboard, a dialog at 390). Layout reports: `layout-before.json`,
`layout-after.json`.

**Coverage limits:** Chromium only (no Safari/WebKit, Firefox or real devices); no screen-reader session; mobile
keyboard behaviour was not exercised on a real device (the sheet layout keeps actions in a sticky footer, but iOS
Safari's visual-viewport behaviour is unverified). Pages were rendered against captured fixtures; EVM and extra-chain
reads were offline, so those screenshots show the honest "couldn't read" states rather than balances. Open
confirmation dialogs with live previews and the admin dashboard (viewed only as a non-admin) were not screenshot.
WCAG 2.2 AA conformance is not certified: automated tools catch a minority of issues.

## Remaining UI gaps

- Light theme does not exist (dark only).
- Charts have no indicators or drawing tools.
- Admin dashboard not re-reviewed visually with an admin fixture.
- Product imagery not generated (see `docs/higgsfield-imagery.md`); the landing page's live example review card is the
  current hero visual and remains the fallback.
