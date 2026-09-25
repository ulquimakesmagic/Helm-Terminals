import { useEffect, useState } from "react";
import { api, ApiError } from "../api";

interface S { id: string; ip: string | null; user_agent: string | null; created_at: string; expires_at: string }
interface Tg { linked: boolean; linked_at: string | null; bot_username: string; enabled: boolean }
interface Code { code: string; expires_in_minutes: number; bot_username: string }

export default function Settings() {
  const [sessions, setSessions] = useState<S[]>([]);
  const [setup, setSetup] = useState<{ secret: string; provisioning_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [tg, setTg] = useState<Tg | null>(null);
  const [linkCode, setLinkCode] = useState<Code | null>(null);
  const [error, setError] = useState("");

  const load = () => api<S[]>("/auth/sessions").then(setSessions).catch((e) => setError(e.detail));
  const loadTg = () => api<Tg>("/telegram/status").then(setTg).catch((e) => setError(e.detail));
  useEffect(() => { load(); loadTg(); }, []);

  return (
    <>
      <h1>Settings</h1>
      {error && <div className="banner err">{error}</div>}
      <div className="grid cols2">
        <div className="card stack">
          <h2>Two-factor authentication</h2>
          <p className="muted" style={{ margin: 0 }}>
            Sign-in asks for a code once 2FA is on. Withdrawals and token sends out of your wallets always need one, and are blocked until 2FA is set up.
          </p>
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

        <div className="card stack">
          <h2>Telegram</h2>
          {tg && !tg.enabled && <div className="banner info">The Telegram bot is not configured on this server.</div>}
          {tg?.enabled && tg.linked && (
            <>
              <div className="banner info">Linked{tg.linked_at ? ` since ${new Date(tg.linked_at + "Z").toLocaleDateString()}` : ""}. Commands: /wallets, /launches, /buy, /sell, /claim, /burn, /withdraw.</div>
              <button className="danger" onClick={() => api("/telegram/unlink", { method: "POST", body: {} }).then(() => { setLinkCode(null); loadTg(); }).catch((e: ApiError) => setError(e.detail))}>Unlink Telegram</button>
            </>
          )}
          {tg?.enabled && !tg.linked && !linkCode && (
            <>
              <p className="muted" style={{ margin: 0 }}>Trade and monitor from Telegram. Every action shows a preview and needs your tap to confirm. Buys from Telegram have a daily cap.</p>
              <button onClick={() => api<Code>("/telegram/link-code", { body: {} }).then(setLinkCode).catch((e: ApiError) => setError(e.detail))}>Link Telegram</button>
            </>
          )}
          {linkCode && !tg?.linked && (
            <>
              <p style={{ margin: 0 }}>
                Open {linkCode.bot_username ? <b>@{linkCode.bot_username}</b> : "the Helm Terminals bot"} in a private chat and send:
              </p>
              <div className="mono" style={{ fontSize: 16 }}>/link {linkCode.code}</div>
              <p className="muted" style={{ margin: 0 }}>The code works once and expires in {linkCode.expires_in_minutes} minutes.</p>
              <button className="ghost" onClick={loadTg}>I sent it: check status</button>
            </>
          )}
        </div>

        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <h2>Active sessions</h2>
          <table>
            <thead><tr><th>Started</th><th>Client</th><th>IP</th><th /></tr></thead>
            <tbody>
              {sessions.map((s) => (
                <tr key={s.id}>
                  <td className="muted">{new Date(s.created_at + "Z").toLocaleString()}</td>
                  <td>{s.user_agent === "telegram-bot" ? "Telegram bot" : "Web"}</td>
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
