import { ReactNode, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { ALL_CHAIN_ORDER, CHAIN_ORDER, chainOf, Pad } from "../chains";
import { ChainMark } from "./marks";
import { Icon, IconName, Logo } from "./ui";

/*
 * Shared pieces of the public pages (landing and sign-in). Everything stated here is checked against the product:
 * chain and launchpad counts come live from /launchpads, the custody-mode label from /public-info, and trust facts
 * describe behaviour the API enforces (preview-then-confirm, 2FA on transfers out, per-tab sessions).
 */

export interface PublicInfo { pads: Pad[]; padsLoaded: boolean; health: { test_signer: boolean } | null }

export function usePublicInfo(): PublicInfo {
  const [pads, setPads] = useState<Pad[]>([]);
  const [padsLoaded, setPadsLoaded] = useState(false);
  const [health, setHealth] = useState<PublicInfo["health"]>(null);
  useEffect(() => {
    api<Pad[]>("/launchpads").then(setPads).catch(() => undefined).finally(() => setPadsLoaded(true));
    api<{ test_signer: boolean }>("/public-info").then(setHealth).catch(() => undefined);
  }, []);
  return { pads, padsLoaded, health };
}

/** Chains that have at least one launchpad listed, in display order (all chains until the list loads). */
export function chainsIn(pads: Pad[]) {
  return pads.length ? ALL_CHAIN_ORDER.filter((c) => pads.some((p) => p.network === c)) : CHAIN_ORDER;
}

export function Brand({ compact }: { compact?: boolean }) {
  return (
    <Link to="/" className="pub-brand">
      <Logo size={compact ? 30 : 34} />
      <span className="logo-name">Helm Terminals<small>Multichain launch desk</small></span>
    </Link>
  );
}

/** Top bar of the public pages. `authLinks` shows Sign in / Create account (hidden on the sign-in page itself). */
export function PublicHeader({ refCode, authLinks = true, children }: { refCode?: string; authLinks?: boolean; children?: ReactNode }) {
  const ref = refCode ? `&ref=${encodeURIComponent(refCode)}` : "";
  return (
    <header className="pub-header">
      <div className="pub-wrap pub-header-in">
        <Brand />
        <nav className="pub-nav hide-sm" aria-label="Overview">
          <a href="/#coverage">Coverage</a>
          <a href="/#capabilities">What it does</a>

          <a href="/#safeguards">Safeguards</a>
        </nav>
        <div className="spacer" />
        {children}
        {authLinks && (
          <div className="row nowrap pub-auth-links" style={{ gap: 8 }}>
            <Link to={`/login?mode=login${ref}`} className="btn ghost">Sign in</Link>
            <Link to={`/login?mode=register${ref}`} className="btn">Create account</Link>
          </div>
        )}
      </div>
    </header>
  );
}

/** Each supported chain with its official logo, name and (once loaded) how many launchpads Helm lists on it. */
export function ChainStrip({ pads, counts = true }: { pads: Pad[]; counts?: boolean }) {
  return (
    <ul className="chain-strip" aria-label="Supported chains">
      {chainsIn(pads).map((c) => {
        const n = pads.filter((p) => p.network === c).length;
        return (
          <li key={c}>
            <ChainMark network={c} size={18} />
            <span>{chainOf(c).name}</span>
            {counts && n > 0 && <span className="muted mono" title={`${n} launchpad${n === 1 ? "" : "s"}`}>{n}<span className="sr-only"> launchpad{n === 1 ? "" : "s"}</span></span>}
          </li>
        );
      })}
    </ul>
  );
}

/** What protects a user's funds, stated only as far as the product enforces it. */
export const SAFEGUARDS: [IconName, string, string][] = [
  ["eye", "Preview, then confirm", "Every action that moves funds returns a preview of its wallets, amounts and fees first. Confirming is tied to that exact preview; if anything changes, you review it again."],
  ["shield", "2FA on transfers out", "Withdrawals and token sends stay blocked until two-factor authentication is on, then ask for an authenticator code each time."],
  ["key", "Keys stay out of the browser", "Wallet private keys are never sent to or shown in the browser."],
  ["lock", "Session per tab", "Sign-in stays in this tab's memory. Closing or reloading the tab requires signing in again."],
];

export function SafeguardList({ compact }: { compact?: boolean }) {
  return (
    <ul className={`safeguards ${compact ? "compact" : ""}`}>
      {SAFEGUARDS.map(([ic, t, d]) => (
        <li key={t}>
          <span className="sg-ic" aria-hidden="true"><Icon name={ic} size={15} /></span>
          <div><b>{t}</b>{!compact && <span>{d}</span>}</div>
        </li>
      ))}
    </ul>
  );
}

/** Whether this is a local test deployment, from the deliberately minimal public-info endpoint. */
export function SignerNote({ health }: { health: PublicInfo["health"] }) {
  if (!health) return null;
  if (!health.test_signer) {
    return (
      <p className="signer-note"><Icon name="key" size={13} />Wallet keys are held by the Turnkey signing service; Helm stores each key's reference and address.</p>
    );
  }
  return (
    <p className="signer-note warn"><Icon name="alert" size={13} />This deployment uses a test signer. Don't hold real funds in its wallets.</p>
  );
}

export const RISK_NOTE = "Launching and trading tokens is risky and can lose money. Helm doesn't promise prices, liquidity or a successful launch.";

const FOOTER_LINKS: [string, [string, string][]][] = [
  ["Product", [["Overview", "/"], ["Coverage", "/#coverage"], ["What it does", "/#capabilities"], ["Safeguards", "/#safeguards"]]],
  ["Account", [["Sign in", "/login?mode=login"], ["Create account", "/login?mode=register"], ["Recover access", "/recover"]]],
  ["Legal", [["Terms of Service", "/terms"], ["Privacy Policy", "/privacy"], ["Risk Disclosure", "/risk"]]],
];

/** Footer for every public page: navigation, legal links, risk statement and data attribution. */
export function PublicFooter() {
  return (
    <footer className="pub-footer">
      <div className="pub-wrap">
        <div className="pub-footer-grid">
          <div className="pub-footer-brand">
            <Brand compact />
            <p className="muted small">{RISK_NOTE}</p>
          </div>
          {FOOTER_LINKS.map(([group, links]) => (
            <nav key={group} aria-label={group} className="pub-footer-col">
              <h2>{group}</h2>
              <ul>{links.map(([label, to]) => <li key={to}>{to.includes("#") ? <a href={to}>{label}</a> : <Link to={to}>{label}</Link>}</li>)}</ul>
            </nav>
          ))}
        </div>
        <div className="pub-footer-base muted small">
          <span>© {new Date().getFullYear()} Helm Terminals</span>
          <span>Market data by <a href="https://www.codex.io" target="_blank" rel="noreferrer">Codex</a>. Not investment advice.</span>
        </div>
      </div>
    </footer>
  );
}

/** Compact footer inside the signed-in app. */
export function AppFooter() {
  return (
    <footer className="app-footer muted small">
      <nav aria-label="Legal"><Link to="/terms">Terms</Link><Link to="/privacy">Privacy</Link><Link to="/risk">Risk disclosure</Link></nav>
      <span>Market data by Codex · charts by GMGN · indicative only</span>
    </footer>
  );
}
