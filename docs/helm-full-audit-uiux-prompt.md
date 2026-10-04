# Helm Terminals — deep security audit + full UI/UX rebuild & professional hardening

> Reusable execution prompt. A fresh agent session can run this without prior context.
> Supersedes `docs/uiux-rebuild-prompt.md` (2026-10-02). Prepared 2026-10-04; re-verify repo
> state before starting — the repo may have moved, and a phase-0 landing step is mandatory.

## Mission

Two deliverables, executed in order:

1. **A fresh, evidence-based security audit of the entire codebase** — every route, every chain
   adapter, every signing path, every provider integration — hunting every possible loophole
   (authorization gaps, IDOR, race conditions, injection, SSRF, secret handling, frontend taint,
   dependency risk). This is an audit: findings are reported, not fixed.
2. **A ground-up UI/UX rebuild** of the whole product surface — inspired by the craft level of
   awwwards.com, mobbin.com, dribbble.com, recent.design and Watermelon UI — while preserving the
   existing color scheme and every functional invariant. Includes: Terms & Conditions, Privacy
   Policy, a proper footer, rebuilt dashboards, working favicon/app icons, sitemap.xml and SEO
   plumbing, verified multi-device responsiveness with zero layout glitches, AI-generated product
   photography for the landing page, and verification that market data is served by the Codex API.

---

## Context (read first — you have none from any prior session)

Repository: `C:\Users\user\Desktop\OneForAll\helm` (GitHub: Helm-Terminals). Helm is a multichain
token-launch and wallet-operations desk (Solana, Base, BNB Chain, Robinhood Chain, Ethereum,
TRON, NEAR, Arc, TON).

- **Backend**: FastAPI + async SQLAlchemy in `backend/app/`. Run tests with
  `cd backend && ./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` (run from `backend/`).
  Market-data provider chain lives in `backend/app/services/market.py` (Codex primary → DexScreener
  fallback → GMGN when no Codex key); tests in `backend/tests/test_market.py`.
- **Frontend**: React 18 + Vite + TypeScript strict, react-router-dom, no UI kit — a hand-rolled
  token-driven design system in `frontend/src/styles.css` with primitives in
  `frontend/src/components/ui.tsx`. `npm run build` = `tsc --noEmit` + vite build.
- **Read before touching anything**: `docs/SPEC.md`, `docs/helm-professional-audit.md` (findings
  AUD-01..16 + amendment), `docs/helm-remediation-tracker.md`, `docs/market-data.md`,
  `docs/runbooks/`, and the old `docs/uiux-rebuild-prompt.md` for prior design decisions.

## Safety invariants — violating any of these is a failed task, no matter how good the result looks

1. **Spend pipeline is product law**: every fund-moving action flows through server preflight →
   field-by-field review → optional TOTP → confirm with `preflight_hash` + `idempotency_key` →
   durable job polling. "Sent is never failed." Never render a shortcut that spends without it.
2. **Honesty markers**: "Unavailable ≠ zero", preview-then-confirm, provider-attributed market
   data, honest revenue language. Do not redesign them away.
3. **Secrets**: private keys/signing material never appear in GET responses, logs, screenshots,
   the report, or the browser. Provider API keys stay server-side. `.env` and `helm.db` are
   gitignored and stay that way. Never touch the real `helm.db`; disposable instances use
   `audit.db` on port 8010 and are deleted afterward.
4. **No live financial actions**: never broadcast real transactions, deploy contracts, or change
   production settings on the user's behalf. Production is Railway — config changes are listed for
   the user, not applied.
5. **Backend tests stay green** through every phase; new server-side code follows existing
   patterns (see `app/routes/launches.py` claim_fees, `app/deps.py` begin_spend).
6. **Accessibility stays WCAG AA**: contrast, focus-visible rings, focus-trapped modals, 44px
   touch targets, `prefers-reduced-motion` respected.
7. **Mimosa commit gate**: `git commit` runs a security gate that flags fake test credentials.
   Use the shared conftest constants (`TEST_PW`, `TEST_NEW_PW`, `TEST_BAD_PW`, `TEST_OPENAPI`) —
   never inline credential-looking literals at sensitive call sites. If the gate still blocks on
   pre-existing findings, stop and present them to the user; do not obfuscate code to evade it and
   do not modify plugin configuration.
8. **Server-side fetches**: http/https only; validate the host before every request; reject
   localhost, loopback, private and reserved addresses.
9. **XML parsing** (sitemap/robots/OG handling or any XML input): reject DOCTYPE/ENTITY
   declarations before parsing; never enable external entity resolution (XXE-safe by construction).

## Phase 0 — land the pending work first (mandatory, do not skip)

The repo carries two uncommitted layers that must be landed before a rebuild can start, or the
rebuild tangles with them:

1. The **uncommitted remediation tree** (~55 modified + ~40 new files: ExecutionIntent,
   quote_approval, execution journal/lease/nonce, receipt evidence, account recovery, async
   Argon2, security headers, CI fixes; tracker `docs/helm-remediation-tracker.md` documents the
   intent). Review it, run the full test suite, commit it in coherent commits, and deploy.
2. The **staged Codex market-data commit** (Codex primary provider + fixture-credential
   centralization) — commit and push it with the same pass.

Also gate the Pons catalog entry (`app/chains/registry.py` `needs_setup`) so the launchpad is not
listed as available while its splitter is disabled — flagged as a release blocker in the audit.

If the Mimosa gate blocks any commit, follow invariant 7. Do not start Phase 2 until the tree is
clean, pushed, and deployed (verify the deployed bundle matches HEAD via feature markers).

## Phase 1 — deep security & loopholes audit (report only; no code changes)

Goal: find every possible loophole a mature financial-software security team would care about.
Work from the current, post-phase-0 HEAD. Produce `docs/audits/security-2026-10.md` with findings
severity-ranked (Critical/High/Medium/Low/Info), each with: exact file:line evidence, a
reproduction or concrete attack path, impact on real user funds/data, and a prescribed fix. No
finding without evidence; no "no vulnerabilities exist" claims; say what was covered and what was
not.

Systematically cover at minimum:

- **Authorization on every route**: enumerate every endpoint in `app/routes/`; verify the
  ownership/role check on each (superadmin, launch owner, wallet owner). Hunt IDOR: object IDs
  from requests used without scoping to the authenticated user. Compare sibling routes against
  each other (one route having a check its sibling lacks was a real pattern found before).
- **Spend pipeline integrity**: preflight→confirm binding, `preflight_hash` coverage, idempotency
  races, lease fencing, replay protection, amount/recipient binding between quote, preview and
  signed payload; interruption semantics ("sent is never failed") for every job kind.
- **Chain adapters & signing**: every adapter in `app/chains/` — address validation, decimals and
  unit handling, approval scopes, intent checking before signing, RPC failover behavior,
  per-chain surprise cases (TRON throttle, NEAR fallback RPCs, TON proof domains).
- **Provider responses as attack surface**: market.py, Relay, Jupiter, Kyber, GMGN, Codex —
  malformed/untrusted payload handling, secret echo into errors/logs/UI, SSRF in any URL that
  derives from provider data, negative-cache poisoning, rate-limit bypass.
- **Frontend taint**: anything rendered from API/provider data (XSS), the memory-only bearer
  token discipline, all `window`/`localStorage` usage, dialog preflight rendering (can a spoofed
  payload change what the user confirms?).
- **Auth & sessions**: session TTL, 2FA enforcement on ALWAYS_2FA kinds, recovery flows, password
  hashing (async Argon2), proof-of-possession paths, superadmin binding.
- **Infrastructure**: security headers actually enforced (not report-only), CORS, CI workflows,
  dependency advisories (`npm audit --omit=dev`, pip constraints), create_all vs migrations,
  health-check honesty, single-replica lock fencing.
- **Re-verify closure** of AUD-01..16 from `docs/helm-professional-audit.md` and every item in the
  remediation tracker — confirm each with evidence or reopen it.
- **XML/XXE**: wherever XML is parsed or served (sitemap handling), apply invariant 9 and report
  any parser that accepts DOCTYPE/ENTITY.

Present the report with an executive summary, a funds-risk section first, and a prioritized fix
roadmap. The user decides what gets fixed when; Phase 2 may proceed in parallel only for items
unrelated to flagged findings.

## Phase 2 — UI/UX rebuild (the craft pass)

Inspiration: study current references on awwwards.com, mobbin.com, dribbble.com, recent.design
and Watermelon UI (browser them; note specific patterns: layout rhythm, micro-interactions,
empty states, motion). Translate the *craft level* — hierarchy, restraint, motion quality — not
any specific site's look. Helm keeps its identity.

### 2a. Design system foundation

- **Preserve the palette and token names.** Current tokens (from `frontend/src/styles.css`):
  `--bg #06090b`, `--bg-2 #090d10`, `--panel #0c1114 / #10171b / #151e23`, `--line #19232a /
  #233139 / #2e3f48`, `--text #e4ecee`, `--text-2 #a7b5ba`, `--muted #81939a`, `--faint #7a8c93`,
  `--accent #35d0a0` (+ `--accent-ink/-soft/-line`), `--info #5cb8c6`, `--good #3dcf8a`,
  `--bad #ef6b6b`, `--warn #e3ae4c` (each with `-soft` variants). IBM Plex Sans + IBM Plex Mono.
  You may rebuild everything above the tokens (spacing scale, elevation, radius, motion curves,
  component geometry) — the colors and the dark-terminal character stay.
- Rebuild `styles.css` and `ui.tsx` primitives deliberately: type scale, grid, elevation, motion
  (@react-spring is available), skeleton/loading shapes, empty states, focus rings.
- A total from-scratch rebuild of screens is allowed; the token contract and invariants are not
  negotiable. Rebuild in screen-sized phases, each shipped with screenshots before moving on.

### 2b. Dialog & modal standardization

Inventory every dialog/modal/popover (SpendDialog, BridgeDialog, SwapDialog, SweepDialog,
Distribute, DistributeSupply, WalletSettings, TonConnect, fund modal, confirmation flows, admin
dialogs) and rebuild them on ONE shared modal primitive with: focus trap + restore, Escape
handling, scroll lock, mobile bottom-sheet behavior, consistent header/body/footer anatomy,
loading/error/disabled states, and the preflight-review choreography intact. No dialog may spend
without the pipeline (invariant 1).

### 2c. Screens

Rebuild every page in `frontend/src/pages/`: Landing, Login/Auth, Launches, Workspace (the
dashboard — the flagship), Wallets, Networks, Tracker, Activity, Profile, PublicProfile, Settings,
Admin, Recovery. Plus a proper **404 page** (currently a silent redirect). The Workspace dashboard
gets the deepest treatment: information hierarchy, market panel, dialogs, activity feed, mobile
layout.

### 2d. Legal, footer & trust pages

- Site-wide **footer** on public pages: product links, docs, explorer links, status, social,
  legal, and the provider/attribution notes (GMGN chart embed, Codex/DexScreener market data).
- **Terms & Conditions** and **Privacy Policy** pages (`/terms`, `/privacy`) with real content
  matching Helm's actual behavior: what Helm custodies (key management via Turnkey, "wallets you
  own"), fees and where they go, third-party providers and what data they receive, market-data
  attribution, no-financial-advice language, jurisdiction disclaimer, contact. Use solid standard
  templates adapted to these facts — and flag clearly in the handoff that the user must have
  counsel review before relying on them. Link them from the footer and from the signup flow.

### 2e. Favicon, icons & SEO plumbing

- Fix favicon properly: keep `favicon.svg`, add `favicon.ico` fallback, `apple-touch-icon`
  (180×180 PNG), 192/512 PNG icons, and a `site.webmanifest`; correct any meta/theme-color
  mismatches; make the icon legible at 16px.
- Add `sitemap.xml` (real public routes only — `/`, `/login`, `/recover`, `/terms`, `/privacy`,
  `/u/:handle` pattern documented, NOT private app routes) and `robots.txt`; canonical URLs;
  Open Graph + Twitter card meta; JSON-LD structured data on the landing page; `<html lang>`
  correctness; per-route document titles.
- If any code parses XML anywhere, invariant 9 applies (DOCTYPE/ENTITY rejected, no external
  entities).

### 2f. Multi-device & visual acceptance

- Test and fix at **390×844 (mobile), 768×1024 (tablet), 1440×900 (desktop)** plus 320px
  worst-case. No horizontal scroll, no clipped text, no overlapping layers, no touch-target
  violations, safe-area insets respected (`viewport-fit=cover` is already set).
- Screenshot every screen at all three sizes; run the visual judge on the rendered pages; fix and
  re-render until they pass. Glitches reported by the user take priority over new features.

### 2g. Accessibility & performance pass

Keyboard-only walkthrough of every flow (login → launch → fund → swap → spend), screen-reader
labels on icon-only controls, ARIA on tabs/menus/dialogs, `prefers-reduced-motion`, color-contrast
audit against WCAG AA. Performance: route-level code splitting, image/asset optimization, no
layout shift on market-data load (reserve space — "Unavailable ≠ zero" applies to layout too).

## Phase 3 — verify DEX data is Codex-served

With `CODEX_API_KEY` configured (locally mocked in tests; on Railway for production):

1. Verify every market panel (launch workspace, wallets, tracker, market route payload) reports
   `provider: "Codex"` and the UI attributes Codex wherever data is shown.
2. Verify the fallback chain by fault injection: Codex denial/empty/malformed → DexScreener; no
   Codex key → previous behavior. `backend/tests/test_market.py` (50 tests) must stay green.
3. Confirm market cap/FDV derivation, the unreported 6h window, and GMGN-only holder counts are
   honestly labeled in the UI.
4. The DexScreener fallback is deliberate resilience, not an accident — do NOT remove it without
   the user's explicit instruction. "Solely Codex" means: when the key is set and healthy, Codex
   and only Codex serves data.

## Phase 4 — Higgsfield product photos for the landing page

You cannot generate these yourself; set the user up to succeed:

1. Propose 3–5 shot concepts matching Helm's identity (dark terminal, `#35d0a0` accent,
   IBM Plex type; e.g. hero shot of a multi-chain command deck, macro device mockups, abstract
   liquidity-flow renders). For each: a ready-to-paste **Higgsfield prompt** (subject, style,
   lighting, color anchors `#06090b`/`#35d0a0`, aspect ratio 16:9 hero + 4:5 mobile, no text in
   image).
2. Specify exact slot dimensions and file names (`frontend/public/landing/*.webp`), and wire the
   landing page so dropping the files in completes the integration (with CSS fallback art if the
   images are absent — no broken slots).
3. Ask the user to generate the assets in their Higgsfield account and drop them in; then
   optimize (WebP, lazy-load, LQIP/blur placeholder) and finish the layout.

## Phase 5 — professional integrations (propose, user opts in per item; each needs an external account)

Error tracking (Sentry or similar, both ends), privacy-respecting analytics (Plausible/Umami),
OG share image, PWA install support, uptime/status link, transaction-explorer deep links from
activity, email deliverability notes (the SMTP settings exist for recovery mail), changelog or
"what's new" surface. For each: what it costs, what data leaves the user's browser, and the exact
env vars/Railway config — never enable silently.

## Deliverables & acceptance

- `docs/audits/security-2026-10.md` (Phase 1) — evidence-based, severity-ranked, coverage stated.
- Rebuilt UI shipped in screen-sized commits; every screen passes the visual judge at all three
  viewports; `npm run build` and the backend suite green at every commit.
- Legal pages + footer live; favicon set + manifest + sitemap.xml + robots.txt + OG meta live;
  404 page live; Codex attribution verified; landing photo slots wired for Higgsfield assets.
- A final handoff note: what changed, what to review (legal text!), Railway env vars if any, and
  open items.
- Commit discipline per invariant 7; PowerShell for any command the user runs themselves.

## Environment facts

- The user runs commands in **PowerShell** (not Git Bash); give PowerShell-native steps one at a
  time for deploy-style commands.
- Local dev: Vite must run on **port 5173** (CORS allow-list); backend `.env` Solana RPC key is
  known-bad — override `SOLANA_RPC_URL=https://api.mainnet-beta.solana.com` for local runs.
- Production is **Railway**; local `.env` changes do not deploy themselves.
