import { useEffect, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { hasToken, setToken } from "./api";
import { Icon, IconName, Logo } from "./components/ui";
import { SessionProvider, useSession } from "./session";
import Auth from "./pages/Auth";
import Dashboard from "./pages/Dashboard";
import Wallets from "./pages/Wallets";
import Launches from "./pages/Launches";
import Workspace from "./pages/Workspace";
import Tracker from "./pages/Tracker";
import Activity from "./pages/Activity";
import Settings from "./pages/Settings";

const NAV: { group: string; items: [string, string, IconName][] }[] = [
  { group: "Operate", items: [["/", "Dashboard", "dashboard"], ["/launches", "Launches", "rocket"], ["/wallets", "Wallets", "wallet"]] },
  { group: "Monitor", items: [["/tracker", "Tracker", "radar"], ["/activity", "Activity", "activity"]] },
  { group: "Account", items: [["/settings", "Settings", "settings"]] },
];

const TITLES: Record<string, string> = {
  "": "Dashboard", launches: "Launches", wallets: "Wallets", tracker: "Tracker", activity: "Activity", settings: "Settings",
};

function Shell({ children }: { children: React.ReactNode }) {
  const nav = useNavigate();
  const loc = useLocation();
  const { me, health, tg } = useSession();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [loc.pathname]);

  const seg = loc.pathname.split("/")[1] ?? "";
  const testSigner = health && (health.signer !== "turnkey" || health.env !== "production");

  return (
    <div className={`app ${open ? "open" : ""}`}>
      <div className="scrim" onClick={() => setOpen(false)} />
      <aside className="sidebar">
        <Link to="/" className="logo">
          <Logo size={34} />
          <div className="logo-name">Helm Terminals<small>Multichain launch desk</small></div>
        </Link>
        <nav className="nav">
          {NAV.map((g) => (
            <div key={g.group}>
              <div className="nav-group">{g.group}</div>
              {g.items.map(([to, label, icon]) => (
                <NavLink key={to} to={to} end={to === "/"}><Icon name={icon} size={17} />{label}</NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="side-foot">
          {me && !me.totp_enabled && (
            <div className="nudge">
              <strong><Icon name="shield" size={14} />2FA is off</strong>
              Withdrawals and token sends stay blocked until you turn it on.
              <Link to="/settings" className="btn sm secondary">Set up 2FA</Link>
            </div>
          )}
          <div className="me">
            <div className="avatar">{(me?.email ?? "?").slice(0, 1).toUpperCase()}</div>
            <div className="grow">
              <div className="truncate">{me?.email ?? "…"}</div>
              <div className="muted small row nowrap" style={{ gap: 6 }}>
                <Icon name="telegram" size={12} />{tg?.linked ? "Telegram linked" : "Telegram not linked"}
              </div>
            </div>
            <button className="icon-btn" title="Sign out" onClick={() => { setToken(null); nav("/login"); }}>
              <Icon name="logout" size={15} />
            </button>
          </div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button className="btn icon secondary menu-btn" onClick={() => setOpen(true)} aria-label="Open menu"><Icon name="menu" size={17} /></button>
          <div className="crumbs">
            <span className="hide-sm">Helm</span>
            <Icon name="chevron" size={13} className="hide-sm" />
            {seg === "launches" && loc.pathname.split("/")[2] ? (
              <><Link to="/launches" className="muted">Launches</Link><Icon name="chevron" size={13} /><b>Workspace</b></>
            ) : <b>{TITLES[seg] ?? ""}</b>}
          </div>
          <div className="spacer" />
          {testSigner && (
            <span className="pill warn" title="Keys are held by the local development signer. Do not hold real funds.">
              <span className="dot" style={{ background: "var(--warn)" }} />Test signer<span className="hide-sm"> · no real funds</span>
            </span>
          )}
          <span className="pill hide-sm">
            <span className="dot" style={{ background: health ? "var(--good)" : "var(--bad)" }} />
            {health ? "API online" : "API offline"}
          </span>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}

function Private({ children }: { children: React.ReactNode }) {
  return hasToken() ? <Shell>{children}</Shell> : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <SessionProvider>
      <Routes>
        <Route path="/login" element={<Auth />} />
        <Route path="/" element={<Private><Dashboard /></Private>} />
        <Route path="/launches" element={<Private><Launches /></Private>} />
        <Route path="/launches/:id" element={<Private><Workspace /></Private>} />
        <Route path="/wallets" element={<Private><Wallets /></Private>} />
        <Route path="/tracker" element={<Private><Tracker /></Private>} />
        <Route path="/activity" element={<Private><Activity /></Private>} />
        <Route path="/settings" element={<Private><Settings /></Private>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </SessionProvider>
  );
}
