import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, errMsg, short } from "../api";
import { chainOf, ExtraChainId } from "../chains";
import { ChainLabel, ChainMark } from "../components/marks";
import SpendDialog from "../components/SpendDialog";
import {
  Address, Callout, Card, Empty, Field, Icon, Loading, Modal, PageHeader, Spinner, TableWrap, useToast,
} from "../components/ui";

// TON Connect is only loaded when a TON network is opened.
const TonConnectBox = lazy(() => import("../components/TonConnect").then((m) => ({ default: m.TonConnectBox })));
const TonSendDialog = lazy(() => import("../components/TonConnect").then((m) => ({ default: m.TonSendDialog })));
const ExternalSendDialog = lazy(() => import("../components/WalletSettings").then((m) => ({ default: m.ExternalSendDialog })));

/** Exact decimal string of a base-unit integer (no floats). */
function fmtRaw(raw: string, decimals: number): string {
  if (!/^\d+$/.test(raw)) return raw;
  const p = raw.padStart(decimals + 1, "0");
  const whole = p.slice(0, p.length - decimals), frac = p.slice(p.length - decimals).replace(/0+$/, "");
  return frac ? `${whole}.${frac}` : whole;
}

/* TRON, NEAR, Arc and TON: what Helm supports on each, and the wallets, balances, transfers and history it offers there. */

interface Net { id: ExtraChainId; name: string; family: string; native: string; decimals: number; chain_id: number | null; network_id?: string | null;
  enabled: boolean; notes: string; capabilities: Record<string, string>; sources: string[]; explorer: Record<string, string> }
interface Chains { core: { id: string; name: string; capabilities: string }[]; extra: Net[]; note: string }
const CAP_LABEL: Record<string, string> = {
  wallets: "Wallets & signing", balances: "Balances", token_metadata: "Token metadata", history: "History",
  transfers: "Transfers & confirmations", launches: "Launches", swaps: "Swaps", market_data: "Market data", cross_chain: "Cross-chain routes",
};

function Cap({ text }: { text: string }) {
  const [state] = text.split(":");
  const tone = state === "yes" ? "good" : state === "partial" ? "warn" : "";
  return <span className={`badge ${tone}`} title={text}>{state === "yes" ? "Yes" : state === "partial" ? "Partial" : "No"}</span>;
}

export default function Networks() {
  const [search, setSearch] = useSearchParams();
  const [data, setData] = useState<Chains | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { api<Chains>("/chains").then(setData).catch((e) => setErr(errMsg(e))); }, []);
  if (err) return <Callout tone="err">{err}</Callout>;
  if (!data) return <Loading />;
  const enabled = data.extra.filter((n) => n.enabled);
  const pick = (search.get("network") as ExtraChainId | null) ?? null;
  const current = enabled.find((n) => n.id === pick) ?? null;
  return (
    <>
      <PageHeader title="Networks" sub="What Helm supports on TRON, NEAR, Arc and TON. Wallets, balances and transfers only: no launches, swaps or market data there yet." />
      <Card title="Capability matrix" icon="layers" flush sub={data.note}>
        <TableWrap><table className="rtable">
          <caption className="sr-only">What Helm supports on each network</caption>
          <thead><tr><th>Network</th>{Object.values(CAP_LABEL).map((l) => <th key={l}>{l}</th>)}<th>Status</th></tr></thead>
          <tbody>{data.extra.map((n) => (
            <tr key={n.id}>
              <td className="span"><span className="row nowrap" style={{ gap: 6 }}><ChainMark network={n.id} size={20} label /><b>{n.name}</b>
                <span className="badge warn">mainnet · real money</span></span>
                <div className="muted small">{n.native} · {n.decimals} decimals{n.chain_id ? ` · chain id ${n.chain_id}` : ""}{n.network_id ? ` · TON Connect network ${n.network_id}` : ""}</div></td>
              {Object.keys(CAP_LABEL).map((k) => <td key={k} data-label={CAP_LABEL[k]}><Cap text={n.capabilities[k]} /></td>)}
              <td data-label="Status">{n.enabled ? <span className="badge good">Enabled</span> : <span className="badge">Off on this server</span>}</td>
            </tr>))}</tbody>
        </table></TableWrap>
      </Card>
      <details className="card" style={{ padding: 14 }}>
        <summary style={{ cursor: "pointer" }}><b>Why each feature is or isn't available, and the documentation used</b></summary>
        <div className="stack" style={{ marginTop: 10 }}>{data.extra.map((n) => (
          <div key={n.id} className="stack tight">
            <b>{n.name}</b>{n.notes && <span className="muted small">{n.notes}</span>}
            {Object.entries(n.capabilities).map(([k, v]) => <span key={k} className="small"><span className="muted">{CAP_LABEL[k]}:</span> {v}</span>)}
            <span className="small muted">Sources: {n.sources.map((s, i) => <span key={s}>{i ? " · " : ""}<a href={s} target="_blank" rel="noreferrer noopener">{s.replace(/^https:\/\//, "").split("/").slice(0, 3).join("/")}</a></span>)}</span>
          </div>))}</div>
      </details>
      {enabled.length === 0 ? <Empty icon="globe" title="None of these networks are enabled here" text="The server operator lists them in EXTRA_NETWORKS (and ALLOW_MAINNET_EXTRA_NETWORKS must not be false)." /> : (
        <Card title="Use a network" icon="globe" sub="Pick one explicitly. These are mainnets: transfers move real money.">
          <div className="chips">{enabled.map((n) => (
            <button key={n.id} type="button" className={`chip ${current?.id === n.id ? "on" : ""}`} onClick={() => setSearch({ network: n.id })}>
              <ChainMark network={n.id} size={16} />{n.name}
            </button>))}</div>
        </Card>
      )}
      {current && <NetworkPanel net={current} />}
    </>
  );
}

function NetworkPanel({ net }: { net: Net }) {
  const toast = useToast();
  const [wallets, setWallets] = useState<any[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const load = useCallback(() => {
    api<any[]>("/wallets?with_balances=true").then((ws) => setWallets(ws.filter((w) => w.network === net.id))).catch((e) => toast("bad", errMsg(e)));
  }, [net.id, toast]);
  useEffect(() => { setWallets(null); setOpenId(null); load(); }, [load]);
  const create = () => {
    setBusy(true);
    api("/wallets", { body: { network: net.id, count: 1, label_prefix: `${net.name} wallet` } })
      .then(() => { toast("good", `${net.name} wallet created`); load(); }).catch((e) => toast("bad", errMsg(e))).finally(() => setBusy(false));
  };
  return (
    <div className="stack">
      <Callout tone="warn" icon="alert">{net.name} is a mainnet: transfers move real money.</Callout>
      <Card title={`${net.name} wallets`} icon="wallet" flush
        actions={net.family === "ton" ? undefined : <button className="btn sm" onClick={create} disabled={busy}>{busy ? <Spinner /> : <Icon name="plus" size={13} />}New {net.name} wallet</button>}>
        {net.family === "ton" && <div style={{ padding: "12px 16px" }}><Suspense fallback={<Loading rows={1} />}><TonConnectBox net={net} onLinked={load} /></Suspense></div>}
        {!wallets ? <Loading /> : wallets.length === 0 ? <Empty icon="wallet" title={`No ${net.name} wallets yet`} text={net.family === "ton" ? "Connect your own TON wallet to use it here." : <>Create one, or <Link to="/settings">link your own wallet in Settings</Link>.</>} /> : (
          <TableWrap><table className="rtable">
            <caption className="sr-only">{net.name} wallets</caption>
            <thead><tr><th>Wallet</th><th>Address</th><th className="r">Balance</th><th className="r"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{wallets.map((w) => (
              <tr key={w.id}>
                <td className="span"><b>{w.label || "Wallet"}</b> {w.kind === "connected" ? <span className="badge info">{net.family === "ton" ? "your wallet · TON Connect" : "your wallet"}</span> : w.kind !== "managed" && <span className="badge">watch-only</span>}</td>
                <td data-label="Address"><Address address={w.address} network={net.id} /></td>
                <td className="r mono" data-label="Balance">{w.balance_native != null ? `${w.balance_native} ${net.native}` : <span className="muted">unavailable</span>}</td>
                <td className="r act"><button className="btn sm ghost" onClick={() => setOpenId(openId === w.id ? null : w.id)}>{openId === w.id ? "Close" : "Open"}</button></td>
              </tr>))}</tbody>
          </table></TableWrap>
        )}
      </Card>
      {openId && wallets && <WalletDetail net={net} wallet={wallets.find((w) => w.id === openId)} onChanged={load} />}
    </div>
  );
}

function WalletDetail({ net, wallet, onChanged }: { net: Net; wallet: any; onChanged: () => void }) {
  const [tokens, setTokens] = useState<string[]>([]);
  const [add, setAdd] = useState("");
  const [bal, setBal] = useState<any>(null);
  const [hist, setHist] = useState<any>(null);
  const [send, setSend] = useState(false);
  const load = useCallback(() => {
    api(`/chains/${net.id}/wallets/${wallet.id}/balances?tokens=${encodeURIComponent(tokens.join(","))}`).then(setBal).catch(() => undefined);
    api(`/chains/${net.id}/wallets/${wallet.id}/history`).then(setHist).catch(() => undefined);
  }, [net.id, wallet.id, tokens]);
  useEffect(load, [load]);
  return (
    <div className="grid g-2">
      <Card title="Balances" icon="coins" sub={<span className="row nowrap" style={{ gap: 6 }}><ChainLabel network={net.id} size={14} /> · {short(wallet.address)}</span>}
        actions={wallet.kind === "managed" || wallet.kind === "connected" ? <button className="btn sm" onClick={() => setSend(true)}><Icon name="send" size={13} />Send</button> : undefined}>
        {!bal ? <Loading rows={2} /> : (
          <div className="stack tight">
            <div className="row between"><b>{net.native}</b><span className="mono">{bal.native ? bal.native.amount : "unavailable"}</span></div>
            {bal.tokens.map((t: any) => (
              <div key={t.address} className="row between small">
                <span><b>{t.symbol}</b> <a className="mono muted" href={t.explorer} target="_blank" rel="noreferrer noopener">{short(t.address)}</a></span>
                <span className="mono">{t.amount}</span>
              </div>))}
            {bal.errors.map((e: string) => <Callout key={e} tone="warn">{e}</Callout>)}
            <form className="row" onSubmit={(e) => { e.preventDefault(); if (add.trim()) { setTokens([...new Set([...tokens, add.trim()])]); setAdd(""); } }}>
              <input className="mono grow" placeholder={net.family === "near" ? "Token contract account (e.g. usdt.tether-token.near)" : net.family === "ton" ? "Jetton master address" : "Token contract address"} value={add} onChange={(e) => setAdd(e.target.value)} aria-label="Token contract" />
              <button className="btn sm secondary" type="submit">Add token</button>
            </form>
            <span className="muted small">Tokens are matched by contract address, never by ticker.</span>
          </div>
        )}
      </Card>
      <Card title="History" icon="activity" sub={<a href={net.explorer.account + wallet.address} target="_blank" rel="noreferrer noopener">Open in explorer <Icon name="ext" size={11} /></a>}>
        {!hist ? <Loading rows={2} /> : (
          <div className="stack tight">
            {hist.note && <span className="muted small">{hist.note}</span>}
            {[...hist.helm.map((h: any) => ({ ...h, src: "Helm" })), ...hist.chain.map((c: any) => ({ ...c, src: "chain" }))].slice(0, 30).map((h: any, i: number) => (
              <div key={i} className="row between small">
                <span>{h.src === "Helm" ? `${h.kind.replace(/_/g, " ")} · ${h.status}` : `${h.raw != null && h.decimals != null ? `${fmtRaw(String(h.raw), Number(h.decimals))} ` : ""}${h.symbol ?? net.native}${h.to ? ` → ${short(h.to)}` : ""}${h.status ? ` · ${h.status}` : ""}`}{h.error ? <span className="muted"> · {h.error}</span> : null}</span>
                {h.reference ? <a className="mono" href={h.explorer} target="_blank" rel="noreferrer noopener">{short(h.reference)}</a> : <span className="muted">{h.src === "Helm" ? "not sent" : "—"}</span>}
              </div>))}
            {hist.helm.length + hist.chain.length === 0 && <span className="muted small">Nothing yet.</span>}
          </div>
        )}
      </Card>
      {send && net.family === "ton" && <Suspense fallback={null}><TonSendDialog net={net} wallet={wallet} tokens={bal?.tokens ?? []} onClose={() => setSend(false)} onDone={() => { setSend(false); load(); onChanged(); }} /></Suspense>}
      {send && net.family !== "ton" && wallet.kind === "connected" && <Suspense fallback={null}><ExternalSendDialog
        w={{ id: wallet.id, network: net.id, family: net.family, network_name: net.name, enabled: true, address: wallet.address,
          kind: wallet.kind, label: wallet.label, explorer: net.explorer.account + wallet.address, native: { asset: net.native, amount: bal?.native?.amount ?? "" },
          tokens: (bal?.tokens ?? []).map((t: any) => ({ symbol: t.symbol, address: t.address, amount: t.amount })), fees: null, error: null, can_send: true, signer: "Your wallet" }}
        onClose={() => setSend(false)} onDone={() => { setSend(false); load(); onChanged(); }} /></Suspense>}
      {send && net.family !== "ton" && wallet.kind !== "connected" && <SendDialog net={net} wallet={wallet} tokens={bal?.tokens ?? []} onClose={() => setSend(false)} onDone={() => { setSend(false); load(); onChanged(); }} />}
    </div>
  );
}

function SendDialog({ net, wallet, tokens, onClose, onDone }: { net: Net; wallet: any; tokens: any[]; onClose: () => void; onDone: () => void }) {
  const [to, setTo] = useState("");
  const [amount, setAmount] = useState("");
  const [token, setToken] = useState("");
  const [review, setReview] = useState(false);
  const asset = token ? tokens.find((t) => t.address === token) : null;
  if (review) {
    return <SpendDialog title={`Send ${asset ? asset.symbol : net.native} on ${net.name}`} path={`/chains/${net.id}/transfer`}
      body={{ wallet_id: wallet.id, to: to.trim(), amount: amount.trim(), token: token || null }} network={net.id}
      onClose={onClose} onDone={onDone} />;
  }
  return (
    <Modal title={`Send on ${net.name}`} sub={`From ${short(wallet.address)} · ${chainOf(net.id).name}`} onClose={onClose}
      foot={<><button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn" disabled={!to.trim() || !(+amount > 0)} onClick={() => setReview(true)}>Review</button></>}>
      <div className="stack">
        <Field label="Asset"><select value={token} onChange={(e) => setToken(e.target.value)}>
          <option value="">{net.native} (native)</option>
          {tokens.map((t) => <option key={t.address} value={t.address}>{t.symbol} · {short(t.address)}</option>)}</select></Field>
        <Field label="Recipient" hint={net.family === "near" ? "A named account (alice.near) or a 64-character implicit account" : net.family === "tron" ? "A TRON address starting with T" : "An 0x address on Arc"}>
          <input className="mono" value={to} onChange={(e) => setTo(e.target.value)} spellCheck={false} autoFocus /></Field>
        <Field label="Amount" hint={asset ? `Holds ${asset.amount} ${asset.symbol}` : undefined}><input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
        <span className="muted small">The next step shows the network costs ({net.family === "tron" ? "bandwidth, energy and any account activation" : net.family === "near" ? "gas and any storage deposit" : "gas, paid in USDC"}) and needs your 2FA code.</span>
      </div>
    </Modal>
  );
}
