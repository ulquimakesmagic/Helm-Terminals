/*
 * One-time private-key export. Phases: step-up (password + fresh 2FA + acknowledgment) → reveal
 * (shown once, copyable, 20-second countdown) → done. The server refuses anything less; this UI
 * just makes the custody hand-off explicit before the secret ever appears on screen.
 */
import { useEffect, useRef, useState } from "react";
import { api, errMsg } from "../api";
import { Callout, CopyButton, Field, Icon, Modal, Spinner } from "./ui";

export default function ExportKeyDialog({ wallet, onClose, onExported }: {
  wallet: { id: string; label: string; address: string; network: string };
  onClose: () => void; onExported?: () => void;
}) {
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [understood, setUnderstood] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [secret, setSecret] = useState<string | null>(null);
  const [left, setLeft] = useState(20);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (secret === null) return;
    timer.current = window.setInterval(() => setLeft((n) => n - 1), 1000);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [secret]);

  useEffect(() => {
    if (secret !== null && left <= 0) {
      setSecret(null);
      onExported?.();
      onClose();
    }
  }, [left, secret]); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async () => {
    setBusy(true); setError("");
    try {
      const r = await api<{ secret: string }>(`/wallets/${wallet.id}/export-key`, {
        body: { password, totp },
      });
      setSecret(r.secret); // held in memory only; never persisted anywhere
      setPassword(""); setTotp("");
    } catch (e) { setError(errMsg(e)); }
    finally { setBusy(false); }
  };

  if (secret !== null) {
    return (
      <Modal title="Your private key — shown once" onClose={() => { setSecret(null); onExported?.(); onClose(); }} lock>
        <Callout tone="warn">
          <b>This is the only time Helm shows this key.</b> Anyone holding it controls the wallet.
          Store it offline; Helm cannot recover or reset it.
        </Callout>
        <div className="stack tight">
          <Field label={`${wallet.label || "Wallet"} · ${wallet.network}`}>
            <input className="mono" readOnly value={secret} onFocus={(e) => e.currentTarget.select()} style={{ wordBreak: "break-all" }} />
          </Field>
          <div className="row" style={{ gap: 8 }}>
            <CopyButton text={secret} />
            <span className="muted small">Hides in {left}s</span>
            <span className="grow" />
            <button className="btn" onClick={() => { setSecret(null); onExported?.(); onClose(); }}>I've stored it — close now</button>
          </div>
        </div>
      </Modal>
    );
  }

  return (
    <Modal title={`Export private key — ${wallet.label || "wallet"}`} sub={wallet.address}
      onClose={() => { onClose(); }}>
      <Callout tone="warn">
        Exporting moves custody out of Helm. <b>Anyone with this key controls the wallet</b> and its
        funds — Helm's own keys stay, but the exported copy can't be revoked. Confirm only on a
        device you trust, in private.
      </Callout>
      <label className="row" style={{ gap: 8, alignItems: "flex-start" }}>
        <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} style={{ marginTop: 3 }} />
        <span className="small">I understand the key will be shown <b>once</b> and that exporting gives full control of the wallet to whoever holds it.</span>
      </label>
      <Field label="Password"><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></Field>
      <Field label="2FA code" hint="A fresh code from your authenticator app — codes are single-use.">
        <input className="mono" value={totp} onChange={(e) => setTotp(e.target.value)} inputMode="numeric" maxLength={8} placeholder="123 456" />
      </Field>
      {error && <Callout tone="err">{error}</Callout>}
      <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn" disabled={!understood || !password || !totp || busy} onClick={submit}>
          {busy ? <Spinner /> : <Icon name="shield" size={14} />}Reveal key
        </button>
      </div>
    </Modal>
  );
}
