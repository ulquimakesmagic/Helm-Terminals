import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, errMsg, fmt, short } from "../api";
import { chainOf, explorerUrl, padName } from "../chains";
import SpendDialog from "../components/SpendDialog";
import {
  Address, Callout, Card, ChainBadge, Empty, Field, Icon, PageHeader, Seg, Skeleton, Stat, StatusBadge, TokenAvatar, UnitInput, parseDate, relTime, useToast,
} from "../components/ui";

type Dialog = { title: string; path: string; body: Record<string, unknown> } | null;
type Op = "claim" | "burn" | "send" | "lock";

function TransparencyPanel({ t, network }: { t: any; network: string }) {
  const hot = t.warnings.length > 0;
  const maxPct = Math.max(1, ...t.top_holders.map((h: any) => h.pct));
  return (
    <Card title="Supply transparency" icon="pie" sub="Your wallets vs. the real holder list">
      <div className="stack">
        <div className="row between">
          <span className="text-2">Your wallets hold</span>
          <strong className={`num ${hot ? "warn-t" : ""}`} style={{ fontSize: 20 }}>{fmt(t.controlled_pct, 2)}%</strong>
        </div>
        <div className={`bar ${hot ? "hot" : ""}`}><div style={{ width: `${Math.min(t.controlled_pct, 100)}%` }} /></div>
        {t.warnings.map((w: string) => <Callout key={w} tone="warn">{w}</Callout>)}
      </div>
      <h3 style={{ margin: "18px 0 8px" }}>Top holders</h3>
      <div className="stack tight">
        {t.top_holders.map((h: any, i: number) => (
          <div key={h.owner} className="row nowrap" style={{ gap: 10, fontSize: 12.5 }}>
            <span className="muted num" style={{ width: 18 }}>{i + 1}</span>
            <div className="grow">
              <div className="row nowrap between" style={{ gap: 6 }}>
                <span className="row nowrap" style={{ gap: 6 }}><Address address={h.owner} network={network} />{h.is_yours && <span className="badge info">yours</span>}</span>
                <span className="num" style={{ fontWeight: 600 }}>{fmt(h.pct, 2)}%</span>
              </div>
              <div className="bar thin" style={{ marginTop: 4 }}>
                <div style={{ width: `${(h.pct / maxPct) * 100}%`, background: h.is_yours ? undefined : "var(--line-2)" }} />
              </div>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

function downloadPnlCard(launch: any, pnl: any) {
  const unit = chainOf(launch.network).native;
  const c = document.createElement("canvas");
  c.width = 1200; c.height = 630;
  const g = c.getContext("2d")!;
  const bg = g.createLinearGradient(0, 0, 1200, 630);
  bg.addColorStop(0, "#0a0f1a"); bg.addColorStop(1, "#07090d");
  g.fillStyle = bg; g.fillRect(0, 0, 1200, 630);
  const glow = g.createRadialGradient(1000, 100, 0, 1000, 100, 600);
  glow.addColorStop(0, pnl.net_sol_flow >= 0 ? "rgba(52,211,153,0.18)" : "rgba(248,113,113,0.18)"); glow.addColorStop(1, "rgba(0,0,0,0)");
  g.fillStyle = glow; g.fillRect(0, 0, 1200, 630);
  g.fillStyle = "#e8ecf4"; g.font = "700 56px Inter, sans-serif"; g.fillText(launch.name, 70, 140);
  g.fillStyle = "#6d788e"; g.font = "500 34px 'JetBrains Mono', monospace"; g.fillText(`$${launch.symbol}`, 70, 192);
  g.fillStyle = pnl.net_sol_flow >= 0 ? "#34d399" : "#f87171"; g.font = "700 132px Inter, sans-serif";
  g.fillText(`${pnl.net_sol_flow >= 0 ? "+" : ""}${fmt(pnl.net_sol_flow, 3)} ${unit}`, 70, 380);
  g.fillStyle = "#b0b9cb"; g.font = "28px Inter, sans-serif";
  g.fillText(`Net ${unit} flow on ${chainOf(launch.network).name} · ${pnl.confirmed_txs} confirmed transactions`, 70, 440);
  g.fillStyle = "#6d788e"; g.font = "24px Inter, sans-serif";
  g.fillText("Unsold tokens are not valued. Fees included.", 70, 480);
  const lg = g.createLinearGradient(70, 0, 400, 0); lg.addColorStop(0, "#38bdf8"); lg.addColorStop(1, "#818cf8");
  g.fillStyle = lg; g.font = "700 30px Inter, sans-serif"; g.fillText("Helm Terminals", 70, 570);
  const a = document.createElement("a");
  a.download = `${launch.symbol}-pnl.png`; a.href = c.toDataURL("image/png"); a.click();
}

const BUY_PRESETS = ["0.05", "0.1", "0.25", "0.5", "1"];
const SELL_PRESETS = ["10", "25", "50", "75", "100"];

export default function Workspace() {
  const { id } = useParams();
  const toast = useToast();
  const [ws, setWs] = useState<any>(null);
  const [allWallets, setAllWallets] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [dialog, setDialog] = useState<Dialog>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [attachId, setAttachId] = useState("");
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [amount, setAmount] = useState("0.1");
  const [mode, setMode] = useState<"stagger" | "parallel">("stagger");
  const [stagger, setStagger] = useState("1000");
  const [slippage, setSlippage] = useState("10");
  const [advanced, setAdvanced] = useState(false);
  const [op, setOp] = useState<Op>("claim");
  const [claimWallet, setClaimWallet] = useState("");
  const [burnPct, setBurnPct] = useState("10");
  const [sendTo, setSendTo] = useState("");
  const [sendPct, setSendPct] = useState("10");
  const [updated, setUpdated] = useState<Date | null>(null);

  const load = useCallback(async () => {
    try {
      const [w, all] = await Promise.all([api(`/launches/${id}/workspace`), api<any[]>("/wallets")]);
      setWs(w);
      setAllWallets(all.filter((x) => x.kind === "managed"));
      setError("");
      setUpdated(new Date());
    } catch (e) { setError(errMsg(e)); }
  }, [id]);

  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);

  if (!ws) {
    return error ? <Callout tone="err">{error}</Callout> : (
      <div className="stack"><Skeleton h={48} w={320} /><div className="grid g-stats">{[1, 2, 3, 4].map((i) => <Skeleton key={i} h={96} />)}</div><Skeleton h={300} /></div>
    );
  }
  const l = ws.launch;
  const chain = chainOf(l.network);
  const cur = chain.native;
  const live = l.status === "live";
  const sel = [...selected];
  const toggle = (wid: string) => setSelected((s) => { const n = new Set(s); n.has(wid) ? n.delete(wid) : n.add(wid); return n; });
  const attached = new Set(ws.wallets.map((w: any) => w.id));
  const totalSol = ws.wallets.reduce((s: number, w: any) => s + w.sol, 0);
  const totalTok = ws.wallets.reduce((s: number, w: any) => s + w.tokens, 0);
  const allSel = ws.wallets.length > 0 && ws.wallets.every((w: any) => selected.has(w.id));
  const firstSel = ws.wallets.find((w: any) => w.id === sel[0]);
  const presets = side === "buy" ? BUY_PRESETS : SELL_PRESETS;

  const attach = () => api(`/launches/${id}/wallets`, { body: { wallet_ids: [attachId] } })
    .then(() => { setAttachId(""); toast("good", "Wallet attached"); load(); }).catch((e) => toast("bad", errMsg(e)));

  return (
    <>
      <div className="row" style={{ marginBottom: -8 }}>
        <Link to="/launches" className="btn sm ghost" style={{ marginLeft: -10 }}><Icon name="arrowleft" size={14} />All launches</Link>
      </div>
      <PageHeader
        title={
          <span className="row nowrap" style={{ gap: 14 }}>
            <TokenAvatar symbol={l.symbol} size={48} />
            <span>
              <span className="row" style={{ gap: 10 }}>{l.name}<StatusBadge status={l.status} />{l.imported && <span className="badge violet">imported</span>}</span>
              <span className="row small muted" style={{ fontWeight: 400, fontSize: 13, gap: 10, marginTop: 2 }}>
                <span className="mono">${l.symbol}</span>·<ChainBadge network={l.network} />·<span>{padName(l.launchpad)}</span>{l.quote && <span className="badge info">paired with {l.quote.symbol}</span>}
                {l.mint_address && <>·<Address address={l.mint_address} type="token" chars={6} network={l.network} /></>}
              </span>
            </span>
          </span>
        }
        actions={<>
          {updated && <span className="muted small hide-sm row" style={{ gap: 6 }}><span className="badge good pulse" style={{ padding: "2px 6px" }}><span className="dot" /></span>auto-refresh · {updated.toLocaleTimeString()}</span>}
          {live && <Link to={`/automation?launch=${id}`} className="btn secondary"><Icon name="calendar" size={14} />Schedule trades</Link>}
          <button className="btn secondary" onClick={load}><Icon name="refresh" size={14} /></button>
          <button className="btn secondary" onClick={() => api(`/launches/${id}/${l.status === "archived" ? "restore" : "archive"}`, { method: "POST", body: {} })
            .then(() => { toast("good", l.status === "archived" ? "Launch restored" : "Launch archived"); load(); }).catch((e) => toast("bad", errMsg(e)))}>
            <Icon name="archive" size={14} />{l.status === "archived" ? "Restore" : "Archive"}
          </button>
        </>}
      />

      {error && <Callout tone="err">{error}</Callout>}
      {ws.rpc_error && <Callout tone="warn"><b>Chain data is partly unavailable.</b> Balances may be missing. {ws.rpc_error}</Callout>}

      {(l.status === "draft" || l.status === "failed") && (
        <div className="card" style={{ padding: 20, borderColor: "rgba(56,189,248,0.35)", background: "linear-gradient(135deg, rgba(56,189,248,0.08), rgba(129,140,248,0.05))" }}>
          <div className="row between" style={{ gap: 16 }}>
            <div className="row nowrap" style={{ gap: 14 }}>
              <div className="feed-ic info" style={{ width: 42, height: 42, borderRadius: 12 }}><Icon name="rocket" size={20} /></div>
              <div>
                <h2>{l.status === "failed" ? "Deployment failed. Ready to retry." : "Ready to deploy"}</h2>
                <p className="muted small" style={{ marginTop: 2 }}>
                  {l.dev_wallet_id ? <>Dev buy <b className="text-2">{l.dev_buy_sol} {cur}</b>. You'll see a full preview, including cost, before anything is sent.</> : "Set a dev wallet on this launch before deploying."}
                </p>
              </div>
            </div>
            <button className="btn lg" disabled={!l.dev_wallet_id} onClick={() => setDialog({ title: `Deploy ${l.name}`, path: `/launches/${id}/deploy`, body: {} })}>
              Review deployment<Icon name="arrowright" size={15} />
            </button>
          </div>
        </div>
      )}
      {l.status === "deploying" && <Callout><b>Deploying…</b> This page refreshes automatically.</Callout>}

      <div className="grid g-stats">
        <Stat accent label={`Net ${cur} flow`} icon="trend" value={`${ws.pnl.net_sol_flow > 0 ? "+" : ""}${fmt(ws.pnl.net_sol_flow)}`} unit={cur}
          tone={ws.pnl.net_sol_flow > 0 ? "good" : ws.pnl.net_sol_flow < 0 ? "bad" : undefined} hint={`${ws.pnl.confirmed_txs} confirmed txs`} />
        <Stat label="Supply you control" icon="pie" value={ws.transparency ? `${fmt(ws.transparency.controlled_pct, 2)}%` : "—"} hint={ws.transparency ? "across attached wallets" : "after the token is live"} />
        <Stat label={`${cur} in wallets`} icon="coins" value={fmt(totalSol)} unit={cur} hint={`${ws.wallets.length} attached wallet${ws.wallets.length === 1 ? "" : "s"}`} />
        <Stat label="Tokens held" icon="layers" value={fmt(totalTok, 0)} hint={`$${l.symbol}`} />
      </div>

      <div className="grid g-main">
        <div className="col">
          <Card title="Wallets" icon="wallet" sub={sel.length ? `${sel.length} selected for trading` : "Select wallets to trade from"} flush
            actions={<div className="row nowrap">
              <select value={attachId} onChange={(e) => setAttachId(e.target.value)} style={{ width: 200 }}>
                <option value="">Attach a wallet…</option>
                {allWallets.filter((w) => !attached.has(w.id)).map((w) => <option key={w.id} value={w.id}>{w.label || short(w.address)}</option>)}
              </select>
              <button className="btn secondary" disabled={!attachId} onClick={attach}><Icon name="plus" size={14} /></button>
            </div>}>
            {ws.wallets.length === 0 ? (
              <Empty icon="wallet" title="No wallets attached" text="Attach managed wallets to trade this token and see how much of the supply you control." />
            ) : (
              <div className="table-wrap"><table>
                <thead><tr>
                  <th className="cb"><input type="checkbox" checked={allSel} aria-label="Select all" onChange={() => setSelected(allSel ? new Set() : new Set(ws.wallets.map((w: any) => w.id)))} /></th>
                  <th>Wallet</th><th className="hide-sm">Role</th><th className="r">{cur}</th><th className="r">Tokens</th>
                </tr></thead>
                <tbody>
                  {ws.wallets.map((w: any) => (
                    <tr key={w.id} className={`clickable ${selected.has(w.id) ? "sel" : ""}`} onClick={() => toggle(w.id)}>
                      <td className="cb"><input type="checkbox" checked={selected.has(w.id)} onChange={() => toggle(w.id)} onClick={(e) => e.stopPropagation()} /></td>
                      <td><div style={{ fontWeight: 550 }}>{w.label || "Untitled"}</div><Address address={w.address} network={l.network} /></td>
                      <td className="hide-sm"><span className="badge cap">{w.role}</span></td>
                      <td className="r num">{fmt(w.sol)}</td>
                      <td className="r num">{fmt(w.tokens, 0)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot><tr><td /><td>Total</td><td className="hide-sm" /><td className="r num">{fmt(totalSol)}</td><td className="r num">{fmt(totalTok, 0)}</td></tr></tfoot>
              </table></div>
            )}
          </Card>

          <Card title="Transactions" icon="activity" flush sub="Latest 50 for this launch">
            {ws.transactions.length === 0 ? <Empty icon="activity" title="No transactions yet" /> : (
              <div className="table-wrap"><table>
                <thead><tr><th>Time</th><th>Type</th><th>Status</th><th className="r">{cur}</th><th className="r">Tx</th></tr></thead>
                <tbody>
                  {ws.transactions.map((t: any) => (
                    <tr key={t.id}>
                      <td className="muted nowrap" title={parseDate(t.created_at).toLocaleString()}>{relTime(t.created_at)}</td>
                      <td style={{ textTransform: "capitalize" }}>{t.kind.replace(/_/g, " ")}</td>
                      <td><StatusBadge status={t.status} /></td>
                      <td className={`r num ${t.sol_delta > 0 ? "good" : t.sol_delta < 0 ? "bad" : "muted"}`}>{t.sol_delta > 0 ? "+" : ""}{fmt(t.sol_delta)}</td>
                      <td className="r mono">{t.signature ? <a href={explorerUrl(l.network, "tx", t.signature)} target="_blank" rel="noreferrer">{short(t.signature)} <Icon name="ext" size={11} /></a> : <span className="bad small" title={t.error}>{t.error ? "error" : "—"}</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            )}
          </Card>
        </div>

        <div className="col">
          {live && (
            <Card title="Order ticket" icon="zap">
              <div className="stack">
                <Seg full className="trade" value={side} onChange={(s) => { setSide(s); setAmount(s === "buy" ? "0.1" : "25"); }} options={[["buy", "Buy"], ["sell", "Sell"]]} />
                <Field label={side === "buy" ? `${cur} per wallet` : "Share of each wallet's tokens"}>
                  <UnitInput unit={side === "buy" ? cur : "%"} value={amount} onChange={(e) => setAmount(e.target.value)} />
                </Field>
                <div className="chips">
                  {presets.map((p) => <button key={p} className={`chip ${amount === p ? "on" : ""}`} onClick={() => setAmount(p)}>{p}{side === "sell" ? "%" : ""}</button>)}
                </div>
                <button className="btn ghost sm" style={{ alignSelf: "flex-start", marginLeft: -8 }} onClick={() => setAdvanced(!advanced)}>
                  <Icon name="settings" size={13} />{advanced ? "Hide" : "Show"} execution settings
                </button>
                {advanced && (
                  <div className="stack" style={{ padding: 12, borderRadius: 10, background: "var(--bg-2)", border: "1px solid var(--line)" }}>
                    <Field label="Execution"><Seg full value={mode} onChange={setMode} options={[["stagger", "Staggered"], ["parallel", "All at once"]]} /></Field>
                    <div className="row nowrap">
                      {mode === "stagger" && <Field label="Delay" style={{ flex: 1 }}><UnitInput unit="ms" value={stagger} onChange={(e) => setStagger(e.target.value)} /></Field>}
                      <Field label="Slippage" style={{ flex: 1 }}><UnitInput unit="%" value={slippage} onChange={(e) => setSlippage(e.target.value)} /></Field>
                    </div>
                  </div>
                )}
                <div className="kv small" style={{ padding: "0 2px" }}>
                  <div className="k">Wallets</div><div className="v num">{sel.length}</div>
                  <div className="k">{side === "buy" ? "Total spend" : "Selling"}</div>
                  <div className="v num">{side === "buy" ? `${fmt(sel.length * (+amount || 0))} ${cur}` : `${amount || 0}% of holdings`}</div>
                </div>
                <button className={`btn lg block ${side}`} disabled={!sel.length || !(+amount > 0)} onClick={() => setDialog({
                  title: `${side === "buy" ? "Buy" : "Sell"} $${l.symbol}`, path: `/launches/${id}/trade`,
                  body: { side, wallet_ids: sel, amount: +amount, mode, stagger_ms: +stagger, slippage_pct: +slippage },
                })}>
                  {sel.length ? `Review ${side} · ${sel.length} wallet${sel.length === 1 ? "" : "s"}` : "Select wallets to trade"}
                </button>
                <p className="muted small">Manual orders from your selected wallets. You approve each one after seeing a simulated preview.</p>
              </div>
            </Card>
          )}

          {ws.transparency ? <TransparencyPanel t={ws.transparency} network={l.network} /> : (
            <Card title="Supply transparency" icon="pie"><p className="muted small">Available once the token is live and chain data loads.</p></Card>
          )}

          {live && (
            <Card title="Token operations" icon="layers">
              <div className="stack">
                <Seg full value={op} onChange={setOp} options={[["claim", "Claim"], ["burn", "Burn"], ["send", "Send"], ["lock", "Lock"]]} />
                {op === "claim" && (
                  <>
                    <p className="muted small">Collect creator fees earned by the token's creator wallet.</p>
                    <Field label="Creator wallet">
                      <select value={claimWallet} onChange={(e) => setClaimWallet(e.target.value)}>
                        <option value="">Choose…</option>
                        {ws.wallets.map((w: any) => <option key={w.id} value={w.id}>{w.label || short(w.address)}</option>)}
                      </select>
                    </Field>
                    <button className="btn block" disabled={!claimWallet} onClick={() => setDialog({ title: "Claim creator fees", path: `/launches/${id}/claim-fees`, body: { wallet_id: claimWallet } })}>
                      <Icon name="coins" size={15} />Review claim</button>
                  </>
                )}
                {op === "burn" && (
                  <>
                    <Callout tone="warn">Burning permanently destroys tokens. It can't be undone.</Callout>
                    <Field label="Amount" hint={firstSel ? `From ${firstSel.label || short(firstSel.address)} (first selected wallet)` : "Select a wallet in the table first"}>
                      <UnitInput unit="%" value={burnPct} onChange={(e) => setBurnPct(e.target.value)} />
                    </Field>
                    <button className="btn danger-ghost block" disabled={!firstSel || !(+burnPct > 0)} onClick={() => setDialog({ title: "Burn tokens (irreversible)", path: `/launches/${id}/burn`, body: { wallet_id: sel[0], percent: +burnPct } })}>
                      <Icon name="flame" size={15} />Review burn</button>
                  </>
                )}
                {op === "send" && (
                  <>
                    <Field label="Recipient"><input className="mono" placeholder={`${chain.name} address`} value={sendTo} onChange={(e) => setSendTo(e.target.value)} /></Field>
                    <Field label="Amount" hint={firstSel ? `From ${firstSel.label || short(firstSel.address)} · needs 2FA` : "Select a wallet in the table first"}>
                      <UnitInput unit="%" value={sendPct} onChange={(e) => setSendPct(e.target.value)} />
                    </Field>
                    <button className="btn secondary block" disabled={!firstSel || sendTo.trim().length < 32} onClick={() => setDialog({ title: "Send tokens", path: `/launches/${id}/send`, body: { wallet_id: sel[0], recipient: sendTo.trim(), percent: +sendPct } })}>
                      <Icon name="send" size={15} />Review send</button>
                  </>
                )}
                {op === "lock" && (
                  <Empty icon="lock" title="Supply locking is coming" text="Public, verifiable token locks through Streamflow are planned." />
                )}
              </div>
            </Card>
          )}

          <Card title="Performance" icon="trend" actions={<button className="btn sm secondary" onClick={() => downloadPnlCard(l, ws.pnl)}><Icon name="download" size={13} />P&amp;L card</button>}>
            <div className={`num ${ws.pnl.net_sol_flow > 0 ? "good" : ws.pnl.net_sol_flow < 0 ? "bad" : ""}`} style={{ fontSize: 28, fontWeight: 700, letterSpacing: "-0.02em" }}>
              {ws.pnl.net_sol_flow > 0 ? "+" : ""}{fmt(ws.pnl.net_sol_flow)} {cur}
            </div>
            <p className="muted small" style={{ marginTop: 6 }}>{ws.pnl.note}</p>
          </Card>
        </div>
      </div>

      {dialog && <SpendDialog {...dialog} network={l.network} onClose={() => setDialog(null)} onDone={load} />}
    </>
  );
}
