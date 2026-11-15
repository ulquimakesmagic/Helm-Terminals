import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errMsg, setToken } from "../api";
import { Brand, PublicFooter } from "../components/public";
import { setMeta } from "../meta";
import { Callout, Field, Spinner } from "../components/ui";

export default function Recovery() {
  const [link] = useState(() => {
    const [purpose, token] = window.location.hash.slice(1).split(":");
    return { purpose, token };
  });
  const [mode, setMode] = useState<"password" | "mfa">("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    setMeta({ title: "Account recovery · Helm Terminals", index: false });
    history.replaceState(null, "", window.location.pathname);
  }, []);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const path = link.purpose === "verify" ? "/auth/email/verify" : link.purpose === "reset" ? "/auth/password/reset"
        : mode === "mfa" ? "/auth/mfa/recover" : "/auth/password/request";
      const result = await api(path, {body: {email: email || undefined, token: link.token, password,
        new_password: password, totp: totp || undefined, recovery_code: code || undefined}});
      setMessage(result.message);
      setPassword(""); setTotp(""); setCode("");
      if (path.endsWith("/reset") || path.endsWith("/recover")) setToken(null);
    } catch (e) { setError(errMsg(e)); }
    finally { setBusy(false); }
  }
  const reset = link.purpose === "reset";
  const verify = link.purpose === "verify";
  return <div className="pub auth2"><a href="#recover-main" className="skip-link">Skip to content</a><header className="pub-header"><div className="pub-wrap pub-header-in"><Brand /></div></header>
    <main id="recover-main" className="pub-wrap" style={{maxWidth: 560, paddingTop: 40, paddingBottom: 40}}>
      <section className="auth2-card"><h1>{verify ? "Verify your email" : "Recover account access"}</h1>
        <p className="text-2">Email reset requires a previously verified address. MFA remains protected by your authenticator or an offline recovery code.</p>
        {message ? <Callout tone="good">{message}</Callout> : <form className="stack" onSubmit={submit}>
          {error && <div role="alert"><Callout tone="err">{error}</Callout></div>}
          {!link.token && <Field label="Recovery method"><select value={mode} onChange={e => setMode(e.target.value as "password" | "mfa")}>
            <option value="password">Forgot password</option><option value="mfa">Lost authenticator</option>
          </select></Field>}
          {!link.token && <Field label="Email"><input type="email" required autoComplete="username" value={email} onChange={e => setEmail(e.target.value)} /></Field>}
          {(reset || mode === "mfa") && !verify && <Field label={reset ? "New password" : "Current password"}>
            <input type="password" required minLength={reset ? 10 : 1} autoComplete={reset ? "new-password" : "current-password"} value={password} onChange={e => setPassword(e.target.value)} />
          </Field>}
          {reset && <Field label="Authenticator code (if enabled)"><input autoComplete="one-time-code" inputMode="numeric" value={totp} onChange={e => setTotp(e.target.value)} /></Field>}
          {(reset || mode === "mfa") && !verify && <Field label={reset ? "Or use an offline recovery code" : "Offline recovery code"}>
            <input required={mode === "mfa" && !reset} autoComplete="off" value={code} onChange={e => setCode(e.target.value)} />
          </Field>}
          {!verify && <p className="muted small">Password resets and authenticator recovery end all sessions and hold spending for 24 hours. If you lost both factors and have no recovery code, contact support; email alone cannot remove MFA.</p>}
          <button className="btn" disabled={busy}>{busy && <Spinner />}{verify ? "Verify email" : reset ? "Reset password" : mode === "mfa" ? "Recover authenticator" : "Request reset link"}</button>
        </form>}
        <Link className="btn ghost" to="/login">Return to sign in</Link>
      </section>
    </main>
    <PublicFooter />
  </div>;
}
