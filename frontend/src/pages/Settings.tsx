import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { api, errMsg } from "../api";
import {
  Callout, Card, CopyButton, Empty, Icon, LoadError, Loading, PageHeader, parseDate, relTime, Spinner, TableWrap, useToast,
} from "../components/ui";
import { useSession } from "../session";
import WalletSettings from "../components/WalletSettings";
import AccountSecurity from "../components/AccountSecurity";

interface S { id: string; ip: string | null; user_agent: string | null; created_at: string; expires_at: string }
interface Code { code: string; expires_in_minutes: number; bot_username: string }

function device(ua: string | null) {
  if (!ua) return "Unknown client";
  if (ua === "telegram-bot") return "Telegram bot";
  const os = /Windows/.test(ua) ? "Windows" : /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Mac OS/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "";
  const br = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "Browser";
  return `${br}${os ? ` on ${os}` : ""}`;
}

function TwoFactor() {
  const toast = useToast();
  const { me, refresh } = useSession();
  const [setup, setSetup] = useState<{ secret: string; provisioning_uri: string } | null>(null);
  const [qr, setQr] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!setup) return;
    QRCode.toDataURL(setup.provisioning_uri, { margin: 1, width: 360, color: { dark: "#0b0e14", light: "#ffffff" } }).then(setQr).catch(() => setQr(""));
  }, [setup]);

  const start = () => {
    setBusy(true); setError("");
    api<{ secret: string; provisioning_uri: string }>("/auth/totp/enable", { body: { password } })
      .then((r) => { setSetup(r); setPassword(""); setAsking(false); })
      .catch((e) => setError(errMsg(e))).finally(() => setBusy(false));
  };
  const verify = () => {
    setBusy(true); setError("");
    api("/auth/totp/verify", { body: { code } })
      .then(() => { setSetup(null); toast("good", "Two-factor authentication is on"); refresh(); })
      .catch((e) => setError(errMsg(e))).finally(() => setBusy(false));
  };

  return (
    <Card title="Two-factor authentication" icon="shield" sub="Authenticator app (TOTP)"
      actions={me && <span className={`badge ${me.totp_enabled ? "good" : "warn"}`}><span className="dot" />{me.totp_enabled ? "On" : "Off"}</span>}>
      <div className="stack">
        <p className="muted small">Once on, sign-in asks for a code. Withdrawals and token sends out of your wallets always need one, and stay blocked until 2FA is set up.</p>
        {error && <Callout tone="err">{error}</Callout>}
        {me?.totp_enabled ? (
          <Callout tone="good">Your account is protected. Keep your authenticator app backed up.</Callout>
        ) : !setup && !asking ? (
          <button className="btn" style={{ alignSelf: "flex-start" }} onClick={() => setAsking(true)}><Icon name="shield" size={15} />Set up 2FA</button>
        ) : !setup ? (
          <form className="row" style={{ alignItems: "flex-end" }} onSubmit={(e) => { e.preventDefault(); start(); }}>
            <label className="field" style={{ flex: "1 1 240px", maxWidth: 320 }}>
              <span>Confirm your password</span>
              <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus />
            </label>
            <button className="btn" type="submit" disabled={!password || busy}>{busy && <Spinner />}Continue</button>
            <button className="btn ghost" type="button" onClick={() => { setAsking(false); setPassword(""); }}>Cancel</button>
          </form>
        ) : (
          <div className="row" style={{ alignItems: "flex-start", gap: 20 }}>
            <div style={{ background: "#fff", padding: 8, borderRadius: 12, width: 164, height: 164, flex: "none" }}>
              {qr ? <img src={qr} alt="2FA QR code" width={148} height={148} style={{ display: "block" }} /> : <Loading rows={1} />}
            </div>
            <div className="stack grow" style={{ minWidth: 220 }}>
              <div className="small text-2"><b>1.</b> Scan the code with Google Authenticator, Authy or 1Password.</div>
              <div className="small muted">Can't scan? Enter this key:</div>
              <div className="row nowrap" style={{ gap: 4, background: "var(--bg-2)", border: "1px solid var(--line)", borderRadius: 8, padding: "6px 8px" }}>
                <span className="mono truncate grow" style={{ letterSpacing: "0.08em" }}>{setup.secret}</span><CopyButton text={setup.secret} />
              </div>
              <div className="small text-2"><b>2.</b> Enter the 6-digit code it shows.</div>
              <div className="row nowrap">
                <input className="mono" placeholder="123456" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                  inputMode="numeric" style={{ width: 130, letterSpacing: "0.3em", fontSize: 16 }} />
                <button className="btn" disabled={code.length !== 6 || busy} onClick={verify}>{busy && <Spinner />}Verify</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

function Telegram() {
  const toast = useToast();
  const { tg, refresh } = useSession();
  const [linkCode, setLinkCode] = useState<Code | null>(null);
  const [checking, setChecking] = useState(false);

  // While a code is showing, poll so the card flips to "linked" on its own.
  useEffect(() => {
    if (!linkCode || tg?.linked) return;
    const t = setInterval(refresh, 4000);
    return () => clearInterval(t);
  }, [linkCode, tg?.linked, refresh]);

  const bot = linkCode?.bot_username || tg?.bot_username;
  return (
    <Card title="Telegram terminal" icon="telegram" sub={bot ? `@${bot}` : undefined}
      actions={tg?.enabled && <span className={`badge ${tg.linked ? "good" : ""}`}><span className="dot" />{tg.linked ? "Linked" : "Not linked"}</span>}>
      <div className="stack">
        {!tg ? <Loading rows={2} /> : !tg.enabled ? (
          <Callout>The Telegram bot isn't configured on this server yet.</Callout>
        ) : tg.linked ? (
          <>
            <p className="muted small">Linked{tg.linked_at ? ` ${relTime(tg.linked_at)}` : ""}. Every action in chat shows a preview and needs your tap to confirm.</p>
            <div className="chips">
              {["/wallets", "/launches", "/buy", "/sell", "/claim", "/burn", "/send", "/withdraw", "/status"].map((c) => <span key={c} className="badge mono" style={{ textTransform: "none" }}>{c}</span>)}
            </div>
            <div className="row">
              {bot && <a className="btn secondary" href={`https://t.me/${bot}`} target="_blank" rel="noreferrer"><Icon name="telegram" size={15} />Open bot</a>}
              <button className="btn danger-ghost" onClick={() => api("/telegram/unlink", { method: "POST", body: {} })
                .then(() => { setLinkCode(null); toast("good", "Telegram unlinked"); refresh(); }).catch((e) => toast("bad", errMsg(e)))}>Unlink</button>
            </div>
          </>
        ) : !linkCode ? (
          <>
            <p className="muted small">Trade and monitor from Telegram. Every action shows a preview and needs your tap to confirm. Buys from Telegram have a daily cap.</p>
            <button className="btn" style={{ alignSelf: "flex-start" }} onClick={() => api<Code>("/telegram/link-code", { body: {} }).then(setLinkCode).catch((e) => toast("bad", errMsg(e)))}>
              <Icon name="link" size={15} />Link Telegram</button>
          </>
        ) : (
          <>
            <div className="small text-2">Open {bot ? <a href={`https://t.me/${bot}`} target="_blank" rel="noreferrer">@{bot}</a> : "the bot"} in a private chat and send:</div>
            <div className="row nowrap" style={{ background: "var(--bg-2)", border: "1px solid var(--line-2)", borderRadius: 10, padding: "10px 12px" }}>
              <span className="mono grow" style={{ fontSize: 17, fontWeight: 600 }}>/link {linkCode.code}</span>
              <CopyButton text={`/link ${linkCode.code}`} size={15} />
            </div>
            <p className="muted small row" style={{ gap: 8 }}><Spinner />Waiting for the bot… The code works once and expires in {linkCode.expires_in_minutes} minutes.</p>
            <div className="row">
              {bot && <a className="btn" href={`https://t.me/${bot}`} target="_blank" rel="noreferrer"><Icon name="telegram" size={15} />Open Telegram</a>}
              <button className="btn secondary" disabled={checking} onClick={() => { setChecking(true); refresh(); setTimeout(() => setChecking(false), 800); }}>Check now</button>
            </div>
          </>
        )}
      </div>
    </Card>
  );
}

export default function Settings() {
  const toast = useToast();
  const { me } = useSession();
  const [sessions, setSessions] = useState<S[] | null>(null);
  const [sessErr, setSessErr] = useState("");
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);
  const load = () => api<S[]>("/auth/sessions").then((r) => { setSessions(r); setSessErr(""); }).catch((e) => setSessErr(errMsg(e)));
  const revoke = (id: string) => {
    setRevoking(id);
    api(`/auth/sessions/${id}`, { method: "DELETE" })
      .then(() => { toast("good", "Session revoked"); setConfirmId(null); return load(); })
      .catch((e) => toast("bad", errMsg(e)))
      .finally(() => setRevoking(null));
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <PageHeader title="Settings" sub="Account security, wallets on TRON, NEAR, Arc and TON, the Telegram terminal, and where you're signed in." />

      <Card>
        <div className="row" style={{ gap: 16 }}>
          <div className="avatar" style={{ width: 52, height: 52, fontSize: 20, borderRadius: 14 }}>{(me?.email ?? "?").slice(0, 1).toUpperCase()}</div>
          <div className="grow">
            <h2 style={{ fontSize: 17 }}>{me?.email ?? "…"}</h2>
            <p className="muted small">{me ? `Member since ${parseDate(me.created_at).toLocaleDateString(undefined, { month: "long", year: "numeric" })}` : ""}</p>
          </div>
        </div>
      </Card>

      <div className="grid g-2" style={{ alignItems: "start" }}>
        <TwoFactor />
        <AccountSecurity />
        <Telegram />
      </div>

      <Card title="Wallets on TRON, NEAR, Arc and TON" icon="wallet" sub="Link your own wallet or add a Helm-managed one, rename, unlink, and see balances and network costs">
        <WalletSettings />
      </Card>

      <Card title="Active sessions" icon="monitor" sub={sessions ? `${sessions.length} signed in · revoke any you don't recognise` : "Revoke any you don't recognise"} flush>
        {sessErr && !sessions ? <LoadError what="Sessions" error={sessErr} onRetry={load} /> : !sessions ? <Loading /> : sessions.length === 0 ? <Empty icon="monitor" title="No active sessions" /> : (
          <TableWrap><table className="rtable">
            <caption className="sr-only">Signed-in sessions</caption>
            <thead><tr><th>Device</th><th className="hide-sm">IP address</th><th>Started</th><th className="hide-sm">Expires</th><th className="r"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {sessions.map((s) => (
                <tr key={s.id}>
                  <td className="span"><span className="row nowrap" style={{ gap: 10 }}>
                    <span className="feed-ic" aria-hidden="true" style={{ width: 28, height: 28 }}><Icon name={s.user_agent === "telegram-bot" ? "telegram" : "monitor"} size={14} /></span>
                    <span style={{ fontWeight: 500 }}>{device(s.user_agent)}</span>
                  </span></td>
                  <td className="hide-sm mono muted">{s.ip ?? "—"}</td>
                  <td className="muted" data-label="Started" title={parseDate(s.created_at).toLocaleString()}>{relTime(s.created_at)}</td>
                  <td className="hide-sm muted">{parseDate(s.expires_at).toLocaleDateString()}</td>
                  <td className="r act">
                    {confirmId === s.id ? (
                      <span className="row nowrap" style={{ gap: 6, justifyContent: "flex-end" }}>
                        <button className="btn sm ghost" onClick={() => setConfirmId(null)} disabled={revoking === s.id}>Keep</button>
                        <button className="btn sm danger-ghost" onClick={() => revoke(s.id)} disabled={revoking === s.id} aria-label={`Confirm revoking ${device(s.user_agent)} session`}>{revoking === s.id ? <Spinner /> : null}Revoke</button>
                      </span>
                    ) : <button className="btn sm ghost" onClick={() => setConfirmId(s.id)} aria-label={`Revoke ${device(s.user_agent)} session started ${relTime(s.created_at)}`}>Revoke…</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table></TableWrap>
        )}
      </Card>
    </>
  );
}
