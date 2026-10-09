import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, errMsg, short } from "../api";
import { useSession } from "../session";
import { ChainMark } from "./marks";
import { ReviewHero, summarize } from "./review";
import { Address, Callout, Empty, Field, Icon, LoadError, Loading, Modal, Spinner, StatusBadge, useToast } from "./ui";

/*
 * Settings > Wallets for Arc: every wallet there with its network (Helm offers mainnet only), signer (Helm-managed, your own wallet, or watch-only), balances and what a transfer costs there. Link
 * your own wallet by signing a Helm challenge in it; linking authorises nothing. Sending from a linked wallet is
 * reviewed here first, then approved and signed in the wallet.
 */

export interface OvNet { id: string; name: string; family: string; enabled: boolean; chain_id: number | null;
  native: string; managed: boolean; external: string }
export interface OvWallet { id: string; network: string; family: string; network_name: string; enabled: boolean;
  address: string; kind: string; label: string; explorer: string; native: { asset: string; amount: string } | null;
  tokens: { symbol: string; address: string; amount: string }[]; fees: Record<string, string | number> | null; error: string | null;
  can_send: boolean; signer: string }

const TERMINAL = ["confirmed", "failed", "partial", "unconfirmed"];
const connectors = () => import("./walletConnectors");

function NetBadge() {
  return <span className="badge warn">mainnet</span>;
}

export default function WalletSettings() {
  const { me } = useSession();
  const toast = useToast();
  const [data, setData] = useState<{ networks: OvNet[]; wallets: OvWallet[] } | null>(null);
  const [err, setErr] = useState("");
  const [pick, setPick] = useState<string>("");
  const [busy, setBusy] = useState("");
  const [linkErr, setLinkErr] = useState("");
  const [send, setSend] = useState<OvWallet | null>(null);
  const load = useCallback(() => api<any>("/chains/wallets/overview").then((d) => { setData(d); setErr(""); }).catch((e) => setErr(errMsg(e))), []);
  useEffect(() => { load(); }, [load]);
  if (err && !data) return <LoadError what="Wallets" error={err} onRetry={load} />;
  if (!data) return <Loading />;
  const net = data.networks.find((n) => n.id === pick) ?? null;

  const link = async () => {
    if (!net || !me) return;
    setLinkErr(""); setBusy("link");
    try {
      const c = await connectors();
      await c.linkOwnWallet({ id: net.id, name: net.name, family: net.family, chain_id: net.chain_id }, me.id);
      toast("good", `${net.name} wallet linked`);
      await load();
    } catch (e: any) { setLinkErr(e?.message ?? errMsg(e)); } finally { setBusy(""); }
  };
  const create = () => {
    if (!net) return;
    setBusy("create");
    api("/wallets", { body: { network: net.id, count: 1, label_prefix: `${net.name} wallet` } })
      .then(() => { toast("good", `Helm-managed ${net.name} wallet created`); return load(); })
      .catch((e) => toast("bad", errMsg(e))).finally(() => setBusy(""));
  };
  const act = (fn: () => Promise<unknown>, done: string) => fn().then(() => { toast("good", done); return load(); }).catch((e) => toast("bad", errMsg(e)));
  const byNet = data.networks.map((n) => ({ n, list: data.wallets.filter((w) => w.network === n.id) })).filter((g) => g.list.length);

  return (
    <div className="stack">
      <div className="stack tight">
        <span className="small muted">Choose a network first. Every network here is a mainnet: balances and transfers are real money.</span>
        <div className="chips" role="group" aria-label="Network">
          {data.networks.map((n) => (
            <button key={n.id} type="button" className={`chip ${pick === n.id ? "on" : ""}`} aria-pressed={pick === n.id} onClick={() => { setPick(n.id); setLinkErr(""); }}>
              <ChainMark network={n.id} size={16} />{n.name}{!n.enabled && <span className="muted"> · off</span>}
            </button>))}
        </div>
      </div>
      {net && (
        <div className="stack tight" style={{ padding: 12, borderRadius: 8, background: "var(--bg-2)", border: "1px solid var(--line)" }}>
          <div className="row" style={{ gap: 8 }}><ChainMark network={net.id} size={18} /><b>{net.name}</b><NetBadge />
            {net.chain_id && <span className="muted small">chain ID {net.chain_id}</span>}</div>
          {!net.enabled ? <Callout tone="warn">{net.name} isn't enabled on this server (EXTRA_NETWORKS, ALLOW_MAINNET_EXTRA_NETWORKS), so nothing can be added here yet.</Callout> : (
            <>
              <Callout tone="warn" icon="alert">{net.name} is a mainnet: transfers move real money.</Callout>
              <div className="row wrap" style={{ gap: 8 }}>
                <button className="btn sm" onClick={link} disabled={!!busy}>{busy === "link" ? <Spinner /> : <Icon name="plus" size={13} />}Link my own wallet ({net.external})</button>
                {net.managed && <button className="btn sm secondary" onClick={create} disabled={!!busy}>{busy === "create" ? <Spinner /> : <Icon name="wallet" size={13} />}New Helm-managed wallet</button>}
              </div>
              <span className="muted small">Linking asks your wallet to sign a message naming this site, the network and a one-time code. It doesn't authorise any transaction.</span>
              {linkErr && <Callout tone="err">{linkErr}</Callout>}
            </>
          )}
        </div>
      )}
      {byNet.length === 0 ? <Empty icon="wallet" title="No wallets on these networks yet" text="Pick a network above to link your own wallet or create a Helm-managed one." /> : byNet.map(({ n, list }) => (
        <div key={n.id} className="stack tight">
          <div className="row" style={{ gap: 8 }}><ChainMark network={n.id} size={16} /><b className="small">{n.name}</b><NetBadge /></div>
          {list.map((w) => <WalletRow key={w.id} w={w} onSend={() => setSend(w)} act={act} />)}
        </div>
      ))}
      {send && <ExternalSendDialog w={send} onClose={() => setSend(null)} onDone={() => { setSend(null); load(); }} />}
    </div>
  );
}

function WalletRow({ w, onSend, act }: { w: OvWallet; onSend: () => void; act: (fn: () => Promise<unknown>, done: string) => void }) {
  const { me } = useSession();
  const [edit, setEdit] = useState(false);
  const [label, setLabel] = useState(w.label);
  const save = () => { setEdit(false); if (label.trim() && label.trim() !== w.label) act(() => api(`/wallets/${w.id}`, { method: "PATCH", body: { label: label.trim() } }), "Renamed"); };
  const disconnect = () => act(async () => {
    await api(`/wallets/${w.id}/disconnect`, { body: {} });
    const c = await connectors();
    if (me) await c.forgetWallet({ id: w.network, name: w.network_name, family: w.family }, me.id);
  }, "Unlinked: it's now watch-only");
  return (
    <div className="card" style={{ padding: 12 }}>
      <div className="row between wrap" style={{ gap: 8 }}>
        <div className="row wrap" style={{ gap: 8, minWidth: 0 }}>
          {edit ? <input autoFocus value={label} onChange={(e) => setLabel(e.target.value)} onBlur={save} onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") { setLabel(w.label); setEdit(false); } }} aria-label="Wallet name" maxLength={80} />
            : <b className="truncate">{w.label || "Wallet"}</b>}
          <button type="button" className="icon-btn" aria-label="Rename" title="Rename" onClick={() => setEdit(true)}><Icon name="edit" size={13} /></button>
          <span className={`badge ${w.kind === "managed" ? "good" : w.kind === "connected" ? "info" : ""}`}>{w.signer}</span>
          {!w.enabled && <span className="badge">network off on this server</span>}
        </div>
        <div className="row wrap" style={{ gap: 6 }}>
          {w.can_send && w.enabled && (w.kind === "connected"
            ? <button className="btn sm" onClick={onSend}><Icon name="send" size={13} />Send</button>
            : <Link className="btn sm" to={`/networks?network=${w.network}`}><Icon name="send" size={13} />Send</Link>)}
          {w.kind === "connected" && <button className="btn sm ghost" onClick={disconnect}>Unlink</button>}
          {w.kind === "watch" && <button className="btn sm ghost" onClick={() => act(() => api(`/wallets/${w.id}`, { method: "DELETE" }), "Removed")}>Remove</button>}
        </div>
      </div>
      <div className="row wrap small" style={{ gap: 10, marginTop: 6 }}>
        <Address address={w.address} network={w.network} />
        <a href={w.explorer} target="_blank" rel="noreferrer noopener">Explorer <Icon name="ext" size={11} /></a>
      </div>
      {w.error ? <Callout tone="warn">Balances unavailable right now: {w.error}</Callout> : w.native && (
        <div className="row wrap small" style={{ gap: 14, marginTop: 6 }}>
          <span><b className="mono">{w.native.amount}</b> {w.native.asset}</span>
          {w.tokens.map((t) => <span key={t.address}><b className="mono">{t.amount}</b> {t.symbol}</span>)}
        </div>
      )}
      {w.fees && (
        <details style={{ marginTop: 6 }}>
          <summary className="small" style={{ cursor: "pointer" }}>What a transfer costs on {w.network_name}</summary>
          <ul className="small muted" style={{ margin: "6px 0 0 16px" }}>
            {["transfer", "token_transfer"].filter((k) => w.fees![k]).map((k) => <li key={k}>{String(w.fees![k])}</li>)}
          </ul>
        </details>
      )}
      {!w.enabled && <p className="muted small" style={{ marginTop: 6 }}>Balances aren't read while this network is switched off.</p>}
    </div>
  );
}

/** Send from your own linked wallet: review in Helm, then approve and sign in the wallet. */
export function ExternalSendDialog({ w, onClose, onDone }: { w: OvWallet; onClose: () => void; onDone: () => void }) {
  const { me } = useSession();
  const [to, setTo] = useState("");
  const [amount, setAmount] = useState("");
  const [token, setToken] = useState("");
  const [pf, setPf] = useState<any>(null);
  const [phase, setPhase] = useState<"form" | "review" | "wallet" | "following" | "done">("form");
  const [err, setErr] = useState("");
  const [job, setJob] = useState<any>(null);
  const key = useRef(`ext-${Date.now()}-${Math.random().toString(36).slice(2)}`);
  const body = { wallet_id: w.id, to: to.trim(), amount: amount.trim(), token: token || null };
  const net = { id: w.network, name: w.network_name, family: w.family, chain_id: w.network === "arc" ? 5042 : null };

  const preview = async () => {
    setErr("");
    try { setPf(await api(`/chains/${w.network}/external/transfer`, { body })); setPhase("review"); } catch (e) { setErr(errMsg(e)); }
  };
  const approve = async () => {
    if (!me || !pf) return;
    setErr("");
    let jobRes: any;
    try {
      jobRes = await api(`/chains/${w.network}/external/transfer`, { body: { ...body, confirm: true, preflight_hash: pf.preflight_hash, idempotency_key: key.current } });
    } catch (e) { setErr(errMsg(e)); return; }
    if (jobRes.status !== "pending" || !jobRes.wallet_request) { setJob(await api(`/jobs/${jobRes.job_id}`).catch(() => jobRes)); setPhase("done"); return; }
    setPhase("wallet");
    const c = await connectors();
    let signed: any;
    try {
      signed = await c.signTransfer(net, me.id, w.address, jobRes.wallet_request);
    } catch (e: any) {
      if (e?.nothingSent) {  // declined or stopped before the wallet could send: a new attempt is a new transfer
        await api(`/chains/${w.network}/external/jobs/${jobRes.job_id}/declined`, { body: {} }).catch(() => undefined);
        key.current = `ext-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        setPhase("review");
        setErr(e?.message ?? errMsg(e));
        return;
      }
      // the wallet may have sent it: keep the same key so pressing Send again can't make a second transfer
      await api(`/chains/${w.network}/external/jobs/${jobRes.job_id}/unknown`, { body: {} }).catch(() => undefined);
      setJob(await api(`/jobs/${jobRes.job_id}`).catch(() => null));
      setErr(`${e?.message ?? errMsg(e)} The transfer may have been sent; check the wallet's history before sending again.`);
      setPhase("done");
      return;
    }
    setPhase("following");
    try {
      await api(`/chains/${w.network}/external/jobs/${jobRes.job_id}/signed`, { body: signed });
      let j = await api<any>(`/jobs/${jobRes.job_id}`);
      for (let i = 0; i < 90 && !TERMINAL.includes(j.status); i++) {
        await new Promise((r) => setTimeout(r, 2000));
        j = await api<any>(`/jobs/${jobRes.job_id}`).catch(() => j);
      }
      setJob(j);
    } catch (e) {
      setErr(`Your wallet signed it, but Helm couldn't follow it: ${errMsg(e)}. Check the wallet's history before sending again.`);
    }
    setPhase("done");
  };

  if (phase === "form") {
    return (
      <Modal title={`Send on ${w.network_name}`} sub={`From ${w.label || short(w.address)} · mainnet`} onClose={onClose}
        foot={<><button className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn" disabled={!to.trim() || !(+amount > 0)} onClick={preview}>Review</button></>}>
        <div className="stack">
          <Field label="Asset"><select value={token} onChange={(e) => setToken(e.target.value)}>
            <option value="">{w.native?.asset ?? "Native"} (native)</option>
            {w.tokens.map((t) => <option key={t.address} value={t.address}>{t.symbol} · {short(t.address)}</option>)}</select></Field>
          <Field label="Recipient"><input className="mono" value={to} onChange={(e) => setTo(e.target.value)} spellCheck={false} autoFocus /></Field>
          <Field label="Amount"><input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
          {err && <Callout tone="err">{err}</Callout>}
        </div>
      </Modal>
    );
  }
  const pre = pf?.preflight ?? {};
  const s = summarize(pre, w.network);
  const warnings: string[] = pre.warnings ?? [];
  const tx = job?.transactions?.[0];
  return (
    <Modal title={`Send ${pre.asset ?? ""} on ${w.network_name}`} onClose={onClose}
      foot={phase === "done" ? <button className="btn" onClick={onDone}>Done</button> : <>
        <button className="btn ghost" onClick={onClose} disabled={phase === "wallet" || phase === "following"}>Cancel</button>
        <button className="btn" disabled={warnings.length > 0 || phase !== "review"} onClick={approve}>
          {phase === "wallet" ? <><Spinner />Waiting for your wallet…</> : phase === "following" ? <><Spinner />Following on the network…</> : <><Icon name="check" size={15} />Approve in wallet</>}
        </button></>}>
      <div className="stack">
        <div className="row" style={{ gap: 8 }}><ChainMark network={w.network} size={16} /><b className="small">{w.network_name}</b><NetBadge /></div>
        {s && <ReviewHero s={s} />}
        {warnings.map((x) => <Callout key={x} tone="err">{x}</Callout>)}
        {s && phase !== "done" && <ol className="next-steps">{s.next.map((n) => <li key={n}>{n}</li>)}</ol>}
        {job && <div className="row between"><StatusBadge status={job.status} />{tx?.signature && <Address address={tx.signature} type="tx" network={w.network} />}</div>}
        {tx?.error && <Callout tone="warn">{tx.error}</Callout>}
        {err && <Callout tone="err">{err}</Callout>}
      </div>
    </Modal>
  );
}
