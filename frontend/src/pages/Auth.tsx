import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, errMsg, setToken } from "../api";
import { Brand, ChainStrip, PublicFooter, SafeguardList, SignerNote, usePublicInfo } from "../components/public";
import { Callout, Field, Icon, Spinner } from "../components/ui";
import { useSession } from "../session";
import { safeNext } from "../nav";
import { setMeta } from "../meta";

type Mode = "login" | "register";
const MIN_PASSWORD = 10; // matches the API's registration rule

/** Server messages, in words a person can act on. Anything unrecognised is shown as the server sent it. */
function friendly(detail: string): { text: string; switchTo?: Mode } {
  const d = detail.toLowerCase();
  if (d === "invalid credentials") return { text: "That email and password don't match an account. Check both and try again." };
  if (d === "email already registered") return { text: "An account with this email already exists.", switchTo: "login" };
  if (d.startsWith("referral code")) return { text: "That referral code isn't valid or has no uses left. Check it, or clear the field to continue without one." };
  if (d.startsWith("password")) return { text: `Password: ${detail.replace(/^password:\s*/i, "")}` };
  return { text: detail };
}

export default function Auth() {
  const nav = useNavigate();
  const { refresh } = useSession();
  const [params, setParams] = useSearchParams();
  const urlMode = params.get("mode");
  const [mode, setModeState] = useState<Mode>(urlMode === "register" || (!urlMode && params.get("ref")) ? "register" : "login");
  const [ref, setRef] = useState((params.get("ref") ?? "").toUpperCase());
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [totp, setTotp] = useState("");
  const [needTotp, setNeedTotp] = useState(false);
  const [error, setError] = useState<{ text: string; switchTo?: Mode } | null>(null);
  const [busy, setBusy] = useState(false);
  const [triedSubmit, setTriedSubmit] = useState(false);
  const { pads, health } = usePublicInfo();
  const heading = useRef<HTMLHeadingElement>(null);
  const wide = typeof window !== "undefined" && window.matchMedia?.("(min-width: 900px)").matches;

  useEffect(() => { setMeta({ title: `${mode === "register" ? "Create account" : "Sign in"} · Helm Terminals`, path: "/login", index: false }); }, [mode]);

  // Email, password and referral code carry over when switching modes; only the error and 2FA step reset.
  function setMode(m: Mode) {
    setModeState(m);
    setError(null);
    setTriedSubmit(false);
    const next = new URLSearchParams(params);
    next.set("mode", m);
    setParams(next, { replace: true });
  }

  const pwShort = mode === "register" && password.length < MIN_PASSWORD;
  const emailBad = !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
  const pwMissing = password.length === 0;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setTriedSubmit(true);
    if (!needTotp && (emailBad || pwMissing || pwShort)) {
      (document.querySelector(emailBad ? "input[name=email]" : "input[name=password]") as HTMLInputElement | null)?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (mode === "register" && !needTotp) {
        await api("/auth/register", { body: { email, password, referral_code: ref.trim() || undefined } });
      }
      const r = await api("/auth/login", { body: { email, password, totp: totp || undefined } });
      setToken(r.token);
      refresh();
      nav(safeNext(params.get("next")), { replace: true });
    } catch (err) {
      const d = errMsg(err);
      if (d === "totp_required") {
        if (needTotp) setError({ text: "That code didn't match. Codes change every 30 seconds; enter the current one." });
        else { setNeedTotp(true); setError(null); setTimeout(() => heading.current?.focus(), 0); }
        setTotp("");
      } else setError(friendly(d));
    } finally {
      setBusy(false);
    }
  }

  const title = needTotp ? "Two-factor check" : mode === "login" ? "Sign in" : "Create your account";
  const sub = needTotp
    ? "Enter the 6-digit code from your authenticator app to finish signing in."
    : mode === "login" ? "Welcome back to your launch desk." : "An email and password is all it takes. No wallet connection needed.";
  const cta = needTotp ? "Verify and sign in" : mode === "login" ? "Sign in" : "Create account";
  const busyLabel = needTotp ? "Verifying…" : mode === "login" ? "Signing in…" : "Creating account…";

  return (
    <div className="pub auth2">
      <a href="#auth-form" className="skip-link">Skip to form</a>
      <header className="pub-header">
        <div className="pub-wrap pub-header-in">
          <Brand />
          <div className="spacer" />
          <Link to="/" className="btn ghost"><Icon name="arrowleft" size={14} />Overview</Link>
        </div>
      </header>

      <main className="pub-wrap auth2-in">
        <section className="auth2-card" aria-labelledby="auth-title">
          {!needTotp && (
            <div className="mode-tabs" role="tablist" aria-label="Account">
              {(["login", "register"] as Mode[]).map((m) => (
                <button key={m} type="button" role="tab" id={`tab-${m}`} aria-selected={mode === m} aria-controls="auth-form"
                  tabIndex={mode === m ? 0 : -1} onKeyDown={e => {
                    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
                    e.preventDefault();
                    const next: Mode = e.key === "Home" ? "login" : e.key === "End" ? "register" : mode === "login" ? "register" : "login";
                    setMode(next); document.getElementById(`tab-${next}`)?.focus();
                  }}
                  className={mode === m ? "on" : ""} onClick={() => setMode(m)}>
                  {m === "login" ? "Sign in" : "Create account"}
                </button>
              ))}
            </div>
          )}

          <div className="auth2-head">
            <h1 id="auth-title" ref={heading} tabIndex={-1}>{title}</h1>
            <p className="text-2">{sub}</p>
          </div>
          {mode === "login" && <Link className="small" to="/recover">Forgot password or lost your authenticator?</Link>}
          {safeNext(params.get("next")) !== "/" && <Callout icon="lock">Sign in to continue to <span className="mono">{safeNext(params.get("next"))}</span>. Sessions live in this tab's memory, so a reload asks you to sign in again.</Callout>}
          <p className="small muted">Your session stays in this tab's memory. Reloading the page or opening a new tab requires signing in again.</p>

          <form id="auth-form" className="stack" style={{ gap: 14 }} onSubmit={submit} noValidate
            role={needTotp ? undefined : "tabpanel"} aria-labelledby={needTotp ? "auth-title" : `tab-${mode}`}>
            {error && (
              <Callout tone="err">
                {error.text}
                {error.switchTo && (
                  <> <button type="button" className="linkish" onClick={() => setMode(error.switchTo!)}>Sign in instead</button></>
                )}
              </Callout>
            )}

            {!needTotp ? (
              <>
                <Field label="Email" hint={triedSubmit && emailBad ? <span className="bad" id="email-err">{email ? "Enter a full email address, like you@example.com." : "Enter your email address."}</span> : undefined}>
                  <input type="email" name="email" autoComplete={mode === "login" ? "username" : "email"} inputMode="email"
                    value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" required autoFocus={wide}
                    aria-invalid={triedSubmit && emailBad ? true : undefined} aria-describedby={triedSubmit && emailBad ? "email-err" : undefined} />
                </Field>
                <Field label="Password" extra={mode === "register" ? `at least ${MIN_PASSWORD} characters` : undefined}
                  hint={triedSubmit && pwMissing && mode === "login" ? <span className="bad" id="pw-err">Enter your password.</span> : undefined}>
                  <div className="input-wrap">
                    <input type={show ? "text" : "password"} name="password" autoComplete={mode === "login" ? "current-password" : "new-password"}
                      value={password} onChange={(e) => setPassword(e.target.value)} required
                      minLength={mode === "register" ? MIN_PASSWORD : 1} aria-invalid={triedSubmit && (pwShort || pwMissing) ? true : undefined}
                      aria-describedby={mode === "register" ? "pw-help" : triedSubmit && pwMissing ? "pw-err" : undefined} style={{ paddingRight: 44 }} />
                    <button type="button" className="icon-btn pw-toggle" onClick={() => setShow(!show)}
                      aria-label={show ? "Hide password" : "Show password"} aria-pressed={show}>
                      <Icon name={show ? "eyeoff" : "eye"} size={16} />
                    </button>
                  </div>
                </Field>
                {mode === "register" && (
                  <div className="stack tight" id="pw-help" aria-live="polite">
                    <div className={`bar thin ${password.length >= 14 ? "good" : ""}`} aria-hidden="true">
                      <div style={{ width: `${Math.min(100, (password.length / 16) * 100)}%` }} />
                    </div>
                    <span className={`small ${triedSubmit && pwShort ? "bad" : "muted"}`}>
                      {password.length === 0 ? `Use at least ${MIN_PASSWORD} characters. A passphrase works well.`
                        : pwShort ? `${MIN_PASSWORD - password.length} more character${MIN_PASSWORD - password.length === 1 ? "" : "s"} needed.`
                        : password.length < 14 ? "Long enough. Longer is stronger." : "Strong length."}
                    </span>
                  </div>
                )}
                {mode === "register" && (
                  <Field label="Referral code" extra="optional">
                    <input className="mono" name="referral" value={ref} onChange={(e) => setRef(e.target.value.toUpperCase())}
                      placeholder="e.g. 43E9C262" maxLength={16} autoComplete="off" spellCheck={false} />
                  </Field>
                )}
              </>
            ) : (
              <>
                <p className="small text-2">Signing in as <b>{email}</b></p>
                <Field label="Authentication code">
                  <input value={totp} onChange={(e) => setTotp(e.target.value.replace(/\D/g, "").slice(0, 6))} inputMode="numeric"
                    autoComplete="one-time-code" placeholder="123456" autoFocus className="mono otp-input" aria-describedby="otp-help" />
                </Field>
                <span id="otp-help" className="muted small">6 digits. Codes refresh every 30 seconds.</span>
              </>
            )}

            <button className="btn lg block" disabled={busy || (needTotp && totp.length !== 6)} aria-busy={busy}>
              {busy ? <><Spinner />{busyLabel}</> : <>{cta}<Icon name="arrowright" size={15} /></>}
            </button>
            {needTotp && (
              <button type="button" className="btn ghost block" onClick={() => { setNeedTotp(false); setTotp(""); setError(null); }}>
                <Icon name="arrowleft" size={14} />Use a different account
              </button>
            )}
          </form>

          {!needTotp && (
            <p className="small muted auth2-switch">
              {mode === "login" ? <>New to Helm? <button type="button" className="linkish" onClick={() => setMode("register")}>Create an account</button></>
                : <>Already have an account? <button type="button" className="linkish" onClick={() => setMode("login")}>Sign in</button></>}
            </p>
          )}
        </section>

        <aside className="auth2-side" aria-label="About Helm Terminals">
          <h2>A multichain launch desk</h2>
          <p className="text-2">Launch tokens, manage the wallets behind them, and review every amount and fee before anything is signed.</p>
          <ChainStrip pads={pads} counts={false} />
          <SafeguardList compact />
          <SignerNote health={health} />
          <Link to="/" className="small">See what Helm does<Icon name="arrowright" size={12} /></Link>
        </aside>
      </main>

      <PublicFooter />
    </div>
  );
}
