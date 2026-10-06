import { lazy, Suspense, useEffect, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, hasToken, setToken } from "./api";
import { Icon, IconName, Loading, Logo } from "./components/ui";
import { SessionProvider, useSession } from "./session";
import Auth from "./pages/Auth";
const Recovery = lazy(() => import("./pages/Recovery"));
import Landing from "./pages/Landing";
const Dashboard = lazy(() => import("./pages/Dashboard"));
import NotFound from "./pages/NotFound";
import { setMeta } from "./meta";
import { AppFooter } from "./components/public";
const Legal = lazy(() => import("./pages/Legal"));
// The landing, sign-in and dashboard pages load with the app; every other page is fetched when first opened,
// so the first screen doesn't wait for the admin console, the trading workspace or the QR code library.
const AutomationMoved = lazy(() => import("./pages/AutomationMoved"));
const Wallets = lazy(() => import("./pages/Wallets"));
const Networks = lazy(() => import("./pages/Networks"));
const Launches = lazy(() => import("./pages/Launches"));
const Workspace = lazy(() => import("./pages/Workspace"));
const Tracker = lazy(() => import("./pages/Tracker"));
const Activity = lazy(() => import("./pages/Activity"));
const Settings = lazy(() => import("./pages/Settings"));
const Profile = lazy(() => import("./pages/Profile"));
const PublicProfile = lazy(() => import("./pages/PublicProfile"));
const Admin = lazy(() => import("./pages/Admin"));
import { ProfileAvatar } from "./components/profile";

const NAV: { group: string; items: [string, string, IconName][] }[] = [
  { group: "Operate", items: [["/", "Dashboard", "dashboard"], ["/launches", "Launches", "rocket"], ["/wallets", "Wallets", "wallet"], ["/networks", "Networks", "layers"]] },
  { group: "Monitor", items: [["/tracker", "Tracker", "radar"], ["/activity", "Activity", "activity"]] },
  { group: "Account", items: [["/profile", "Profile", "user"], ["/settings", "Settings", "settings"]] },
];

const TITLES: Record<string, string> = {
  "": "Dashboard", launches: "Launches", wallets: "Wallets", networks: "Networks", tracker: "Tracker", activity: "Activity", profile: "Profile", settings: "Settings", admin: "Admin",
};

function Shell({ children }: { children: React.ReactNode }) {
  const nav = useNavigate();
  const loc = useLocation();
  const { me, health, healthChecked, testSigner, tg } = useSession();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [loc.pathname]);

  const seg = loc.pathname.split("/")[1] ?? "";
  useEffect(() => { setMeta({ title: `${TITLES[seg] ?? "Helm"} · Helm Terminals`, index: false }); }, [seg]);
  return (
    <div className={`app ${open ? "open" : ""}`}>
      <a href="#main" className="skip-link">Skip to content</a>
      <button type="button" className="scrim" aria-label="Close menu" tabIndex={-1} onClick={() => setOpen(false)} />
      <aside className="sidebar" aria-label="Main navigation">
        <Link to="/" className="logo">
          <Logo size={34} />
          <div className="logo-name">Helm Terminals<small>On-chain ops</small></div>
        </Link>
        <nav className="nav" aria-label="Sections">
          {(me?.admin_role ? [...NAV, { group: "Helm", items: [["/admin", "Admin", "shield"] as [string, string, IconName]] }] : NAV).map((g) => (
            <div key={g.group}>
              <div className="nav-group">{g.group}</div>
              {g.items.map(([to, label, icon]) => (
                <NavLink key={to} to={to} end={to === "/"}><Icon name={icon} size={17} />{label}</NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="side-foot">
          {me && !me.totp_enabled && seg !== "" && seg !== "settings" && (
            <div className="nudge">
              <strong><Icon name="shield" size={14} />2FA is off</strong>
              Withdrawals and token sends stay blocked until you turn it on.
              <Link to="/settings" className="btn sm secondary">Set up 2FA</Link>
            </div>
          )}
          <div className="me">
            <Link to="/profile" className="me-link" title="Your profile">
              <ProfileAvatar name={me?.display_name || me?.email || "?"} hue={me?.avatar_hue ?? 265} size={32} radius={9} />
            </Link>
            <Link to="/profile" className="grow me-link" style={{ minWidth: 0 }}>
              <div className="truncate" style={{ color: "var(--text)", fontWeight: 550 }}>{me?.display_name || me?.email || "…"}</div>
              <div className="muted small row nowrap" style={{ gap: 6 }}>
                <Icon name="telegram" size={12} />{tg?.linked ? "Telegram linked" : "Telegram not linked"}
              </div>
            </Link>
            <button className="icon-btn" title="Sign out" aria-label="Sign out" onClick={() => { api("/auth/logout", { body: {} }).catch(() => {}).finally(() => { forgetWalletSessions(); setToken(null); nav("/login"); }); }}>
              <Icon name="logout" size={15} />
            </button>
          </div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button className="btn icon secondary menu-btn" onClick={() => setOpen(true)} aria-label="Open menu" aria-expanded={open}><Icon name="menu" size={17} /></button>
          <nav className="crumbs" aria-label="Breadcrumb">
            <span className="hide-sm">Helm</span>
            <Icon name="chevron" size={13} className="hide-sm" />
            {seg === "launches" && loc.pathname.split("/")[2] ? (
              <><Link to="/launches" className="muted">Launches</Link><Icon name="chevron" size={13} /><b>Workspace</b></>
            ) : <b aria-current="page">{TITLES[seg] ?? ""}</b>}
          </nav>
          <div className="spacer" />
          {testSigner && (
            <span className="pill warn" title="Keys are held by the local development signer. Do not hold real funds.">
              <span className="dot" style={{ background: "var(--warn)" }} />Test signer<span className="hide-sm"> · no real funds</span>
            </span>
          )}
          <span className={`pill ${health || !healthChecked ? "hide-sm" : ""}`} role="status" title={health?.checkedAt ? `API liveness checked ${new Date(health.checkedAt).toLocaleTimeString()}. Individual providers may have separate outages.` : "API liveness; rechecked while this page is visible"}>
            <span className="dot" style={{ background: health ? "var(--good)" : healthChecked ? "var(--bad)" : "var(--muted)" }} />
            {health ? "API online" : healthChecked ? "API unreachable" : "Checking API"}
          </span>
        </header>
        <main className="content" id="main" tabIndex={-1}><Suspense fallback={<div className="card"><Loading rows={4} /></div>}>{children}</Suspense></main>
        <AppFooter />
      </div>
    </div>
  );
}

/** Signing out also clears any TON Connect or NEAR Connect session this browser kept from before Helm removed those
 * networks, so nothing is left behind for the next person. The wallets themselves are untouched. */
function forgetWalletSessions() {
  try {
    for (const k of Object.keys(localStorage)) if (k.startsWith("helm-ton:") || k.startsWith("helm-near:")) localStorage.removeItem(k);
  } catch { /* storage blocked */ }
}

/** Signed-out visitors go to sign-in and come back to the page they asked for (a reload signs out: memory-only tokens). */
function Private({ children }: { children: React.ReactNode }) {
  const loc = useLocation();
  if (hasToken()) return <Shell>{children}</Shell>;
  const next = encodeURIComponent(loc.pathname + loc.search);
  return <Navigate to={`/login?mode=login&next=${next}`} replace />;
}



/** "/" is the dashboard when signed in and the public landing page otherwise. Decided on each render, not once. */
function Home() {
  return hasToken() ? <Shell><Dashboard /></Shell> : <Landing />;  // Shell supplies the Suspense boundary
}

/** Unknown paths: the not-found page inside the app when signed in, the public one otherwise. Decided per render. */
function CatchAll() {
  return hasToken() ? <Shell><NotFound signedIn /></Shell> : <NotFound />;
}

/** The sign-in page is only for signed-out visitors. */
function LoginRoute() {
  return hasToken() ? <Navigate to="/" replace /> : <Auth />;
}

export default function App() {
  return (
    <SessionProvider>
      <Routes>
        <Route path="/login" element={<LoginRoute />} />
        <Route path="/recover" element={<Suspense fallback={null}><Recovery /></Suspense>} />
        <Route path="/" element={<Home />} />
        <Route path="/launches" element={<Private><Launches /></Private>} />
        <Route path="/launches/:id" element={<Private><Workspace /></Private>} />
        <Route path="/automation" element={<Private><AutomationMoved /></Private>} />
        <Route path="/automation/*" element={<Private><AutomationMoved /></Private>} />
        <Route path="/wallets" element={<Private><Wallets /></Private>} />
        <Route path="/networks" element={<Private><Networks /></Private>} />
        <Route path="/tracker" element={<Private><Tracker /></Private>} />
        <Route path="/activity" element={<Private><Activity /></Private>} />
        <Route path="/profile" element={<Private><Profile /></Private>} />
        <Route path="/settings" element={<Private><Settings /></Private>} />
        <Route path="/admin" element={<Private><Admin /></Private>} />
        <Route path="/u/:handle" element={<Suspense fallback={null}><PublicProfile /></Suspense>} />
        <Route path="/terms" element={<Suspense fallback={null}><Legal doc="terms" /></Suspense>} />
        <Route path="/privacy" element={<Suspense fallback={null}><Legal doc="privacy" /></Suspense>} />
        <Route path="/risk" element={<Suspense fallback={null}><Legal doc="risk" /></Suspense>} />
        <Route path="*" element={<CatchAll />} />
      </Routes>
    </SessionProvider>
  );
}
