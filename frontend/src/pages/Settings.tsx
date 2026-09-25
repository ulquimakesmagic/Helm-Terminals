import { useEffect, useState } from "react";
import { api, ApiError } from "../api";

interface S { id: string; ip: string | null; user_agent: string | null; created_at: string; expires_at: string }

export default function Settings() {
  const [sessions, setSessions] = useState<S[]>([]);
  const [setup, setSetup] = useState<{ secret: string; provisioning_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [error, setError] = useState("");

  const load = () => api<S[]>("/auth/sessions").then(setSessions).catch((e) => setError(e.detail));
  useEffect(() => { load(); }, []);

  return (
    <>
      <h1>Settings</h1>
      {error && <div className="banner err">{error}</div>}
      <div className="grid cols2">
        <div className="card stack">
          <h2>Two-factor authentication</h2>
          <p className="muted" style={{ margin: 0 }}>Once enabled, every sign-in and every on-chain confirmation asks for a 6-digit code.</p>
          {enabled ? <div className="banner info">2FA is enabled.</div> : !setup ? (
            <button onClick={() => api("/auth/totp/enable", { body: {} }).then(setSetup).catch((e) => setError(e.detail))}>Set up 2FA</button>
          ) : (
            <>
              <p>Add this secret to your authenticator app, then enter a code to finish.</p>
              <div className="mono" style={{ wordBreak: "break-all" }}>{setup.secret}</div>
              <div className="mono muted" style={{ wordBreak: "break-all" }}>{setup.provisioning_uri}</div>
              <div className="row">
                <input placeholder="123456" value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" />
                <button onClick={() => api("/auth/totp/verify", { body: { code } }).then(() => { setEnabled(true); setSetup(null); setError(""); }).catch((e: ApiError) => setError(e.detail))}>Verify</button>
              </div>
            </>
          )}
        </div>
        <div className="card">
          <h2>Active sessions</h2>
          <table>
            <thead><tr><th>Started</th><th>IP</th><th /></tr></thead>
            <tbody>
              {sessions.map((s) => (
                <tr key={s.id}>
                  <td className="muted">{new Date(s.created_at + "Z").toLocaleString()}</td>
                  <td className="mono">{s.ip}</td>
                  <td><button className="ghost" onClick={() => api(`/auth/sessions/${s.id}`, { method: "DELETE" }).then(load)}>Revoke</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
