import { FormEvent, useState } from "react";
import { api, errMsg, setToken } from "../api";
import { useSession } from "../session";
import { Callout, Card, Field, Spinner } from "./ui";

export default function AccountSecurity() {
  const {me, capabilities} = useSession();
  const [action, setAction] = useState("email");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [codes, setCodes] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(""); setMessage(""); setCodes([]);
    try {
      const r = await api(action === "email" ? "/auth/email/request" : action === "password" ? "/auth/password/change" : "/auth/recovery-codes",
        {body: {password, new_password: newPassword || undefined, totp: totp || undefined}});
      setMessage(r.message); setCodes(r.codes ?? []); setPassword(""); setTotp(""); setNewPassword("");
      if (action === "password") { setToken(null); window.location.assign("/login"); }
    } catch (e) { setError(errMsg(e)); } finally { setBusy(false); }
  }
  return <Card title="Account recovery" icon="shield" sub={me?.email_verified ? "Email verified" : "Verify your email before you need to recover access"}>
    <form className="stack" onSubmit={submit}>
      {capabilities?.email_recovery === false && <Callout tone="warn">Email delivery is not configured yet. Password changes and offline MFA recovery remain available.</Callout>}
      {me?.recovery_hold_until && Date.parse(`${me.recovery_hold_until}Z`) > Date.now() && <Callout tone="warn">Spending is held until {new Date(`${me.recovery_hold_until}Z`).toLocaleString()} after account recovery.</Callout>}
      {error && <div role="alert"><Callout tone="err">{error}</Callout></div>}
      {message && <Callout tone="good">{message}</Callout>}
      <Field label="Security action"><select value={action} onChange={e => {setAction(e.target.value); setCodes([]); setMessage("");}}>
        <option value="email">Verify email</option><option value="password">Change password</option><option value="codes" disabled={!me?.totp_enabled}>Replace recovery codes (requires MFA)</option>
      </select></Field>
      <Field label="Current password"><input type="password" required autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} /></Field>
      {action === "password" && <Field label="New password"><input type="password" required minLength={10} autoComplete="new-password" value={newPassword} onChange={e => setNewPassword(e.target.value)} /></Field>}
      {me?.totp_enabled && <Field label="Current authenticator code"><input required inputMode="numeric" autoComplete="one-time-code" value={totp} onChange={e => setTotp(e.target.value)} /></Field>}
      <button className="btn" disabled={busy}>{busy && <Spinner />}Continue</button>
      {codes.length > 0 && <div className="card" aria-label="Save these single-use recovery codes offline"><p>Store offline before leaving this page. These will not be shown again. Previous codes are invalid.</p><ul className="mono">{codes.map(code => <li key={code}>{code}</li>)}</ul><button type="button" className="btn secondary" onClick={() => setCodes([])}>I saved my codes — hide them</button></div>}
      <p className="muted small">Recovery does not export wallet keys. Protect your email and offline codes. Email alone cannot remove MFA.</p>
    </form>
  </Card>;
}
