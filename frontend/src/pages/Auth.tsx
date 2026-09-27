import { FormEvent, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, errMsg, setToken } from "../api";
import { CHAIN_ORDER, CHAINS, Pad } from "../chains";
import { coverage } from "../components/Launchpads";
import { ChainMark } from "../components/marks";
import { Callout, Field, Icon, Logo, Seg, Spinner } from "../components/ui";
import { useSession } from "../session";

const FEATURES: [Parameters<typeof Icon>[0]["name"], string, string][] = [
  ["layers", "Every chain, one desk", "Solana, Base, BNB Chain, Robinhood Chain and Ethereum launchpads in one workflow."],
  ["eye", "Preview every action", "Each spend shows its exact wallets, amounts and fees before anything is signed."],
  ["pie", "Supply transparency", "See how much of a token your own wallets control, next to the real holders."],
  ["telegram", "Telegram terminal", "Trade and monitor from chat, with tap-to-confirm on every order."],
];

export default function Auth() {
  const nav = useNavigate();
  const { refresh } = useSession();
  const [params] = useSearchParams();
  const [ref, setRef] = useState((params.get("ref") ?? "").toUpperCase());
  const [mode, setMode] = useState<"login" | "register">(params.get("ref") ? "register" : "login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [totp, setTotp] = useState("");
  const [needTotp, setNeedTotp] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pads, setPads] = useState<Pad[]>([]);
  useEffect(() => { api<Pad[]>("/launchpads").then(setPads).catch(() => undefined); }, []);
  const cov = coverage(pads);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (mode === "register") await api("/auth/register", { body: { email, password, referral_code: ref.trim() || undefined } });
      const r = await api("/auth/login", { body: { email, password, totp: totp || undefined } });
      setToken(r.token);
      refresh();
      nav("/");
    } catch (err) {
      const d = errMsg(err);
      if (d === "totp_required") {
        setNeedTotp(true);
        setError("");
      } else setError(d);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <div className="auth-hero">
        <div className="row" style={{ gap: 12 }}>
          <Logo size={38} />
          <div className="logo-name" style={{ fontSize: 17 }}>Helm Terminals<small>Multichain launch desk</small></div>
        </div>
        <div className="stack" style={{ gap: 20 }}>
          <div className="row" style={{ gap: 8 }}>
            {CHAIN_ORDER.map((c) => (
              <span key={c} className="pill"><ChainMark network={c} size={16} />{CHAINS[c].name}</span>
            ))}
          </div>
          <h1>Launch on any chain. <span className="grad-text">Run it in the open.</span></h1>
          <p className="text-2" style={{ maxWidth: 500, fontSize: 15 }}>
            One desk for creating tokens across {cov.pads || "a dozen"} launchpads on {cov.chains || 5} chains, managing your wallets and trading, with every order previewed and every wallet you control shown next to the real holder list.
          </p>
          <div className="feat">
            {FEATURES.map(([ic, t, d]) => (
              <div key={t}><b><Icon name={ic} size={15} />{t}</b><span>{d}</span></div>
            ))}
          </div>
        </div>
        <p className="muted small">Non-custodial by design. Trading memecoins is risky; only use funds you can afford to lose.</p>
      </div>

      <div className="auth-form">
        <form className="auth-card" onSubmit={submit}>
          <div className="row show-sm" style={{ gap: 10, marginBottom: 8 }}>
            <Logo size={34} /><div className="logo-name">Helm Terminals<small>Multichain launch desk</small></div>
          </div>
          <div>
            <h1>{needTotp ? "Two-factor check" : mode === "login" ? "Welcome back" : "Create your account"}</h1>
            <p className="muted" style={{ marginTop: 6 }}>
              {needTotp ? "Enter the 6-digit code from your authenticator app." : mode === "login" ? "Sign in to your launch desk." : "It takes a few seconds. No wallet connection needed."}
            </p>
          </div>

          {!needTotp && (
            <Seg full value={mode} onChange={(m) => { setMode(m); setError(""); }} options={[["login", "Sign in"], ["register", "Create account"]]} />
          )}

          {error && <Callout tone="err">{error}</Callout>}

          {!needTotp ? (
            <>
              <Field label="Email">
                <input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" required autoFocus />
              </Field>
              <Field label="Password" extra={mode === "register" ? "min 10 characters" : undefined}>
                <div className="input-wrap">
                  <input type={show ? "text" : "password"} autoComplete={mode === "login" ? "current-password" : "new-password"}
                    value={password} onChange={(e) => setPassword(e.target.value)} minLength={mode === "register" ? 10 : 1} required />
                  <button type="button" className="icon-btn" style={{ position: "absolute", right: 8 }} onClick={() => setShow(!show)} aria-label={show ? "Hide password" : "Show password"}>
                    <Icon name={show ? "eyeoff" : "eye"} size={15} />
                  </button>
                </div>
              </Field>
              {mode === "register" && (
                <Field label="Referral code" extra="optional">
                  <input className="mono" value={ref} onChange={(e) => setRef(e.target.value.toUpperCase())} placeholder="e.g. 43E9C262" maxLength={16} />
                </Field>
              )}
              {mode === "register" && password.length > 0 && (
                <div className="stack tight">
                  <div className={`bar thin ${password.length >= 14 ? "good" : ""}`}><div style={{ width: `${Math.min(100, (password.length / 16) * 100)}%` }} /></div>
                  <span className="muted small">{password.length < 10 ? `${10 - password.length} more characters` : password.length < 14 ? "OK. Longer is stronger." : "Strong length."}</span>
                </div>
              )}
            </>
          ) : (
            <Field label="Authentication code">
              <input value={totp} onChange={(e) => setTotp(e.target.value.replace(/\D/g, "").slice(0, 6))} inputMode="numeric"
                autoComplete="one-time-code" placeholder="123456" autoFocus className="mono" style={{ fontSize: 20, letterSpacing: "0.4em", textAlign: "center" }} />
            </Field>
          )}

          <button className="btn lg block" disabled={busy || (needTotp && totp.length !== 6)}>
            {busy ? <Spinner /> : null}
            {needTotp ? "Verify and sign in" : mode === "login" ? "Sign in" : "Create account"}
            {!busy && <Icon name="arrowright" size={15} />}
          </button>
          {needTotp && <button type="button" className="btn ghost" onClick={() => { setNeedTotp(false); setTotp(""); }}><Icon name="arrowleft" size={14} />Back</button>}
        </form>
      </div>
    </div>
  );
}
