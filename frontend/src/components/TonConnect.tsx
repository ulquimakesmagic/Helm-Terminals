import { useEffect, useRef, useState } from "react";
import { TonConnect, TonConnectUI, UserRejectsError, type ConnectedWallet, type IStorage } from "@tonconnect/ui";
import { api, errMsg, short } from "../api";
import { useSession } from "../session";
import { ReviewHero, summarize } from "./review";
import { Callout, Field, Icon, Modal, Spinner, StatusBadge, useToast } from "./ui";

/*
 * TON through TON Connect. Helm never receives a TON key: the user's wallet approves, signs and broadcasts every
 * request. A wallet is linked to the Helm account only after the backend verifies a ton_proof it signed for this
 * site. The TON Connect session is stored per Helm user (its storage keys carry the user id), so another account
 * signed in on the same browser never inherits it; switching accounts in the tab reloads the page for that reason.
 */

const MANIFEST = `${window.location.origin}/tonconnect-manifest.json`;
const TERMINAL = ["confirmed", "failed", "partial", "unconfirmed"];

function userStorage(userId: string): IStorage {
  const pre = `helm-ton:${userId}:`;
  const safe = <T,>(f: () => T, fallback: T) => { try { return f(); } catch { return fallback; } };
  return {
    setItem: async (k, v) => safe(() => localStorage.setItem(pre + k, v), undefined),
    getItem: async (k) => safe(() => localStorage.getItem(pre + k), null),
    removeItem: async (k) => safe(() => localStorage.removeItem(pre + k), undefined),
  };
}

let ui: TonConnectUI | null = null;
let uiUser: string | null = null;

function tonUI(userId: string): TonConnectUI {
  if (ui && uiUser !== userId) { window.location.reload(); throw new Error("switching accounts"); }
  if (!ui) {
    ui = new TonConnectUI({ connector: new TonConnect({ manifestUrl: MANIFEST, storage: userStorage(userId) }), restoreConnection: true });
    uiUser = userId;
  }
  return ui;
}

interface TonNet { id: string; name: string; testnet: boolean }

/** Connect (with proof), show and disconnect the TON wallet in this browser. */
export function TonConnectBox({ net, onLinked }: { net: TonNet; onLinked: () => void }) {
  const { me } = useSession();
  const toast = useToast();
  const [wallet, setWallet] = useState<ConnectedWallet | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const pending = useRef(false);
  const expected = net.testnet ? "-3" : "-239";

  useEffect(() => {
    if (!me) return;
    const t = tonUI(me.id);
    setWallet(t.wallet as ConnectedWallet | null);
    return t.onStatusChange(async (w) => {
      setWallet(w);
      const item = w?.connectItems?.tonProof;
      if (!w || !pending.current || !item) return;
      pending.current = false;
      if (!("proof" in item)) { setErr("The wallet didn't sign the sign-in request."); setBusy(false); return; }
      if (w.account.chain !== expected) {
        setErr(`That wallet is on ${w.account.chain === "-239" ? "TON mainnet" : "TON testnet"}, not ${net.name}. Switch networks in the wallet and connect again.`);
        setBusy(false);
        return;
      }
      try {
        await api(`/chains/${net.id}/ton/connect`, { body: {
          address: w.account.address, chain: w.account.chain, public_key: w.account.publicKey ?? null,
          state_init: w.account.walletStateInit ?? null, proof: item.proof } });
        toast("good", "TON wallet linked to your Helm account");
        onLinked();
      } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); t.setConnectRequestParameters(null); }
    }, (e) => { pending.current = false; setBusy(false); setErr(errMsg(e)); });
  }, [me, net.id, net.name, expected, onLinked, toast]);

  const connect = async () => {
    if (!me) return;
    setErr(""); setBusy(true);
    const t = tonUI(me.id);
    try {
      const { payload } = await api<{ payload: string }>(`/chains/${net.id}/ton/proof-payload`, { body: {} });
      if (t.connected) await t.disconnect();  // a fresh proof needs a fresh connection
      t.setConnectRequestParameters({ state: "ready", value: { tonProof: payload } });
      pending.current = true;
      await t.openModal();
      const stop = t.onModalStateChange((s) => {
        if (s.status === "closed") { stop(); if (pending.current && !t.connected) { pending.current = false; setBusy(false); } }
      });
    } catch (e) { pending.current = false; setBusy(false); setErr(errMsg(e)); }
  };
  const disconnect = async () => { if (me) { await tonUI(me.id).disconnect().catch(() => undefined); setWallet(null); } };

  return (
    <div className="stack tight">
      <div className="row wrap" style={{ gap: 8 }}>
        <button className="btn sm" onClick={connect} disabled={busy || !me}>{busy ? <Spinner /> : <Icon name="plus" size={13} />}Connect a TON wallet</button>
        {wallet && <button className="btn sm ghost" onClick={disconnect}>Disconnect in this browser</button>}
      </div>
      {wallet && <span className="muted small">Connected here: <span className="mono">{short(wallet.account.address)}</span> ({wallet.device.appName})</span>}
      <span className="muted small">Your wallet signs a sign-in message for this site, and every transfer is approved in the wallet. Helm never holds TON keys.</span>
      {err && <Callout tone="err">{err}</Callout>}
    </div>
  );
}

/** Preview on Helm, approve in the wallet, then follow the result. */
export function TonSendDialog({ net, wallet, tokens, onClose, onDone }: {
  net: TonNet; wallet: any; tokens: any[]; onClose: () => void; onDone: () => void;
}) {
  const { me } = useSession();
  const [to, setTo] = useState("");
  const [amount, setAmount] = useState("");
  const [token, setToken] = useState("");
  const [pf, setPf] = useState<any>(null);
  const [phase, setPhase] = useState<"form" | "review" | "wallet" | "following" | "done">("form");
  const [err, setErr] = useState("");
  const [job, setJob] = useState<any>(null);
  const key = useRef(`ton-${Date.now()}-${Math.random().toString(36).slice(2)}`);
  const body = { wallet_id: wallet.id, to: to.trim(), amount: amount.trim(), token: token || null };
  const asset = token ? tokens.find((t) => t.address === token) : null;

  const preview = async () => {
    setErr("");
    try { setPf(await api(`/chains/${net.id}/ton/transfer`, { body })); setPhase("review"); } catch (e) { setErr(errMsg(e)); }
  };
  const approve = async () => {
    if (!me || !pf) return;
    setErr("");
    const t = tonUI(me.id);
    const reviewed = pf.preflight.wallet_request;
    if (!t.connected || t.account?.address !== reviewed.from) {
      setErr("Connect this wallet in this browser first (Connect a TON wallet), then approve again.");
      return;
    }
    // Helm records the transfer first, so whatever the wallet then broadcasts is always followed.
    let job: { job_id: string; status: string; wallet_request: any; valid_for_s: number };
    try {
      job = await api(`/chains/${net.id}/ton/transfer`, { body: { ...body, confirm: true, preflight_hash: pf.preflight_hash, idempotency_key: key.current } });
    } catch (e) { setErr(errMsg(e)); return; }
    if (job.status !== "pending" || !job.wallet_request) { setJob(await api(`/jobs/${job.job_id}`).catch(() => job)); setPhase("done"); return; }
    const req = job.wallet_request;
    setPhase("wallet");
    let boc: string;
    try {
      const r = await t.sendTransaction({ validUntil: Math.floor(Date.now() / 1000) + (job.valid_for_s ?? 300),
        network: req.network, from: req.from, messages: req.messages });
      boc = r.boc;
    } catch (e) {
      if (e instanceof UserRejectsError) {
        await api(`/chains/${net.id}/ton/jobs/${job.job_id}/declined`, { body: {} }).catch(() => undefined);
        key.current = `ton-${Date.now()}-${Math.random().toString(36).slice(2)}`;  // a new attempt is a new transfer
        setPhase("review");
        setErr("You declined in your wallet. Nothing was sent.");
        return;
      }
      // any other wallet error may come after it broadcast: leave it for a look, and keep the key so it isn't resent
      await api(`/chains/${net.id}/ton/jobs/${job.job_id}/unknown`, { body: {} }).catch(() => undefined);
      setJob(await api(`/jobs/${job.job_id}`).catch(() => null));
      setErr(`The wallet reported an error (${errMsg(e)}), so the transfer may have been sent. Check the wallet's history before sending again.`);
      setPhase("done");
      return;
    }
    setPhase("following");
    try {
      await api(`/chains/${net.id}/ton/jobs/${job.job_id}/signed`, { body: { signed_boc: boc } });
      let j = await api<any>(`/jobs/${job.job_id}`);
      for (let i = 0; i < 80 && !TERMINAL.includes(j.status); i++) {
        await new Promise((res) => setTimeout(res, 2000));
        j = await api<any>(`/jobs/${job.job_id}`).catch(() => j);
      }
      setJob(j); setPhase("done");
    } catch (e) {
      setPhase("done");
      setErr(`Your wallet sent it, but Helm couldn't follow it: ${errMsg(e)}. Check the wallet's history before sending again.`);
    }
  };

  if (phase === "form") {
    return (
      <Modal title={`Send on ${net.name}`} sub={`From ${short(wallet.address)}`} onClose={onClose}
        foot={<><button className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn" disabled={!to.trim() || !(+amount > 0)} onClick={preview}>Review</button></>}>
        <div className="stack">
          <Field label="Asset"><select value={token} onChange={(e) => setToken(e.target.value)}>
            <option value="">TON (native)</option>
            {tokens.map((t) => <option key={t.address} value={t.address}>{t.symbol} · {short(t.address)}</option>)}</select></Field>
          <Field label="Recipient" hint="A TON address (UQ…, EQ…, or 0:…)"><input className="mono" value={to} onChange={(e) => setTo(e.target.value)} spellCheck={false} autoFocus /></Field>
          <Field label="Amount" hint={asset ? `Holds ${asset.amount} ${asset.symbol}` : undefined}><input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
          {err && <Callout tone="err">{err}</Callout>}
        </div>
      </Modal>
    );
  }
  const pre = pf?.preflight ?? {};
  const s = summarize(pre, net.id);
  const warnings: string[] = pre.warnings ?? [];
  return (
    <Modal title={`Send ${pre.asset ?? ""} on ${net.name}`} onClose={onClose}
      foot={phase === "done" ? <button className="btn" onClick={onDone}>Done</button> : <>
        <button className="btn ghost" onClick={onClose} disabled={phase === "wallet" || phase === "following"}>Cancel</button>
        <button className="btn" disabled={warnings.length > 0 || phase !== "review"} onClick={approve}>
          {phase === "wallet" ? <><Spinner />Waiting for your wallet…</> : phase === "following" ? <><Spinner />Following on the network…</> : <><Icon name="check" size={15} />Approve in wallet</>}
        </button></>}>
      <div className="stack">
        {s && <ReviewHero s={s} />}
        {warnings.map((w) => <Callout key={w} tone="err">{w}</Callout>)}
        {s && phase !== "done" && <ol className="next-steps">{s.next.map((n) => <li key={n}>{n}</li>)}</ol>}
        {pre.token && <span className="small muted">Jetton master <a className="mono" href={`${pre.testnet ? "https://testnet.tonviewer.com/" : "https://tonviewer.com/"}${pre.token}`} target="_blank" rel="noreferrer noopener">{short(pre.token)}</a></span>}
        {job && (
          <div className="row between">
            <StatusBadge status={job.status} />
            {job.transactions?.[0]?.signature && <a href={`${pre.testnet ? "https://testnet.tonviewer.com/transaction/" : "https://tonviewer.com/transaction/"}${job.transactions[0].signature}`} target="_blank" rel="noreferrer noopener">View on explorer <Icon name="ext" size={11} /></a>}
          </div>)}
        {job?.transactions?.[0]?.error && <Callout tone="warn">{job.transactions[0].error}</Callout>}
        {err && <Callout tone="err">{err}</Callout>}
      </div>
    </Modal>
  );
}
