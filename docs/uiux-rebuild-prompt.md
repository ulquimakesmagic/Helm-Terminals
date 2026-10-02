# Helm Terminals — full UI/UX audit & rebuild + wallet/launch feature expansion

> Reusable execution prompt. A fresh agent session can run this without prior context.
> Invariants and scope decisions were reviewed 2026-10-02; re-verify repo state before starting.

## Context (read first — you have none from any prior session)

Repository: Helm Terminals — a multichain token-launch and wallet-operations platform (Solana,
Base, BSC, Robinhood Chain, Ethereum + TRON/NEAR/Arc/TON). Backend: FastAPI + async SQLAlchemy in
`backend/app/` (400+ passing tests; run with `cd backend && ./.venv/Scripts/python.exe -m pytest -q
-p no:cacheprovider`). Frontend: React 18 + Vite + TypeScript strict, no state library, no UI kit —
a deliberate hand-rolled token-driven design system in `frontend/src/styles.css` (~1,237 lines) with
primitives in `frontend/src/components/ui.tsx`. `npm run build` = `tsc --noEmit` + vite; `npm test`
runs node:test on pure domain logic. Read `docs/SPEC.md`, `docs/creator-reward-integration.md`,
`docs/runbooks/`, and skim `frontend/src/launchpadExperience.ts` before touching anything.

## Safety invariants — violating any of these is a failed task, no matter how good the UI looks

1. Every fund-moving action flows through the SpendDialog pipeline: server preflight → field-by-field
   review → optional TOTP → confirm with `preflight_hash` + `idempotency_key` → job polling. Never
   render a shortcut that spends without it.
2. 2FA kinds, simulate-before-send, "Unavailable ≠ zero" honesty markers, and honest-revenue language
   are product law. Don't redesign them away.
3. Private keys/signing material never appear in GET responses, logs, screenshots, or the browser
   except through the export flow defined below. The backend validates everything; client checks are
   convenience only.
4. Backend: tests stay green; every new spend endpoint follows the existing preflight/confirm/idempotency
   pattern (see `app/routes/launches.py` claim_fees and `app/deps.py` begin_spend for the pattern).
5. Accessibility stays at WCAG AA (contrast, focus-visible rings, focus-trapped modals, 44px touch
   targets, `prefers-reduced-motion`). Mobile must work at 380px.

## Phase A — audit (do this before changing anything)

Inventory every route in `frontend/src/pages/` and every component in `frontend/src/components/`.
For each: render it (run backend + frontend locally), screenshot desktop and 380px, and grade
against: typography rhythm, spacing/elevation consistency, loading/empty/error state coverage,
interaction feedback, information density, accessibility, and motion quality. Produce
`docs/audits/ui-ux-2026-10.md`: a findings table (severity P0–P3, screen, evidence, prescribed fix)
plus a design-system gap list. This document drives Phase B priorities — show it before rebuilding.

## Phase B — rebuild to a best-in-class bar

Design direction: research **Watermelon UI** as the primary aesthetic reference, then benchmark
against Linear (linear.app), Vercel dashboard, Stripe Dashboard, Raycast, and crypto-native dark
terminals (jup.ag, Axiom, Photon, GMGN) — Helm is a terminal, so: dense but calm, tabular numerals,
precise hierarchy, restrained color with one accent per chain, motion that communicates state
change rather than decorates.

- Evolve the existing token system (don't migrate to Tailwind or a component kit): refine the
  surface/color/type/spacing tokens, add elevation and motion-duration tokens, unify status badges,
  empty states, skeletons, and error/LoadError patterns across all screens.
- Upgrade `ui.tsx` primitives: add Sheet/Drawer, Tabs, Tooltip, Progress, Segmented improvements,
  a Command-palette-quality Modal, and better toasts. Every primitive keyboard-accessible.
- Motion with **@react-spring/web** (the only new runtime dependency): spring-based modal/sheet
  entrances, list mount/unmount transitions, number tickers for balances/quotes, staged card
  reveals on the Dashboard, progress feedback on job polling. All gated behind
  `prefers-reduced-motion`. Lazy-load it where it's not on the first screen.
- Rebuild worst screens first per the audit (expect: Workspace, Wallets, the Create-launch modal
  flow, Admin), then sweep the rest.

## New features

### 1. Export private key (wallet settings + wallet detail)
Per managed wallet: password re-entry + fresh TOTP (step-up pattern exists — see Admin's
StepUpDialog). Local-signer keys: server decrypts and returns the key exactly once over the
response; Turnkey-managed keys: use Turnkey's export-private-key encrypted-bundle flow client-side,
never plaintext through Helm's server. UI: show-once modal — explicit "I understand this cannot be
shown again" acknowledgment, copy button, 20-second auto-hide, visible warning that exporting moves
custody to the holder, audit-log entry, rate-limited (e.g. 1/10min/user), web-channel only. New
backend endpoint + tests covering 2FA/password gates, audit logging, and that the key never lands
in activity logs or GET endpoints.

### 2. Withdrawal: specific amounts
Today withdrawals sweep-all. Add an amount input with MAX (spendable minus fees — reuse the
existing reserve/fee logic, `NATIVE_RESERVE_WEI` + `spendable()`), per-chain fee preview, and for
multi-wallet withdrawals either an even split or per-wallet amounts with a running total. Still
goes through SpendDialog. Extend the backend withdraw path if it only supports `all: true`, with
validation (amount > 0, ≤ spendable) and tests.

### 3. Jupiter swap inside the wallets surface
Research Jupiter's current APIs first (quote/swap, free lite endpoint vs API-key production tier)
and whether its cross-chain swap is production-ready — report findings before building. Solana
swaps: backend-proxied Jupiter quote + swap (never call Jupiter from the browser; keep any API key
server-side in settings with the same hygiene as GMGN's — see `docs/market-data.md`), route built
through Helm's existing executor (simulate-before-send, transaction recorded), slippage control,
route/price-impact display, USD values from the existing portfolio price source. Cross-chain
conversion/bridging: keep it on the existing Relay routes but unify the UX into one "Swap & Bridge"
surface in the wallets area. Explicitly preserve disperse/consolidate. Document the integration in
`docs/jupiter-swap.md` (endpoints, key handling, boundaries) — match the honesty standard of
`docs/market-data.md`.

### 4. Instant post-launch supply dispersal (launch workdesk)
After a launch confirms live, the Workspace's token-operations area offers "Distribute supply":
pick a folder → even split or custom percentages across its wallets → executes through the existing
`/wallets/disperse` token path and SpendDialog. Two modes: manual (one click post-launch) and
"queue on confirmation" — the user confirms the disperse preflight at deploy time and the job fires
automatically once the launch confirms, using the existing job/automation infrastructure (fail
safe: if the disperse fails, it reports and stays retryable; it never redeploys or retries into a
double-send). Respect allocation locks (the allocations service already enforces them on sells —
extend to dispersal), and keep the Supply-Transparency warning visible when dispersal would put
>20% of supply under user-controlled wallets.

## Working method

Branch per phase, commit per coherent unit, merge to main only with `npm run build`, `npm test`,
and the full backend suite green. Verify rendered pages visually (screenshot each rebuilt screen at
desktop + 380px) before declaring a phase done. If a design decision conflicts with a safety
invariant, the invariant wins — note it in the audit doc and move on.
