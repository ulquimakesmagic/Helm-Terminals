import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError, setToken } from "../api";

export default function Auth() {
  const nav = useNavigate();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [needTotp, setNeedTotp] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (mode === "register") await api("/auth/register", { body: { email, password } });
      const r = await api("/auth/login", { body: { email, password, totp: totp || undefined } });
      setToken(r.token);
      nav("/");
    } catch (err) {
      const d = (err as ApiError).detail ?? String(err);
      if (d === "totp_required") {
        setNeedTotp(true);
        setError("Enter your 2FA code.");
      } else setError(d);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card auth stack" onSubmit={submit}>
      <div className="brand">Helm</div>
      <p className="muted" style={{ margin: 0 }}>
        Launch and operate tokens from wallets you own. Every action is previewed before it is sent.
      </p>
      {error && <div className="banner err">{error}</div>}
      <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
      <label>
        Password {mode === "register" && <span>(min 10 characters)</span>}
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={mode === "register" ? 10 : 1} required />
      </label>
      {needTotp && <label>2FA code<input value={totp} onChange={(e) => setTotp(e.target.value)} inputMode="numeric" autoFocus /></label>}
      <button disabled={busy}>{mode === "login" ? "Sign in" : "Create account"}</button>
      <button type="button" className="ghost" onClick={() => setMode(mode === "login" ? "register" : "login")}>
        {mode === "login" ? "Need an account?" : "Have an account?"}
      </button>
    </form>
  );
}
