import { Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { hasToken, setToken } from "./api";
import Auth from "./pages/Auth";
import Dashboard from "./pages/Dashboard";
import Wallets from "./pages/Wallets";
import Launches from "./pages/Launches";
import Workspace from "./pages/Workspace";
import Tracker from "./pages/Tracker";
import Activity from "./pages/Activity";
import Settings from "./pages/Settings";

function Shell({ children }: { children: React.ReactNode }) {
  const nav = useNavigate();
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          Helm <small>v0.1</small>
        </div>
        <nav className="nav">
          <NavLink to="/" end>Dashboard</NavLink>
          <NavLink to="/launches">Launches</NavLink>
          <NavLink to="/wallets">My Wallets</NavLink>
          <NavLink to="/tracker">Tracker</NavLink>
          <NavLink to="/activity">Activity</NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
        <div className="spacer" />
        <button
          className="ghost"
          onClick={() => {
            setToken(null);
            nav("/login");
          }}
        >
          Sign out
        </button>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}

function Private({ children }: { children: React.ReactNode }) {
  return hasToken() ? <Shell>{children}</Shell> : <Navigate to="/login" replace />;
}

export default function App() {
  return (
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
  );
}
