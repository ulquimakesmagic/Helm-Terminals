import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, compact, errMsg, fmt, short, signed } from "../api";
import { chainOf, explorerUrl } from "../chains";
import SpendDialog from "../components/SpendDialog";
import MarketPanel from "../components/MarketPanel";
import { ChainLabel, PadLabel } from "../components/marks";
import {
  Address, Callout, Card, Empty, Field, Icon, Metrics, Seg, Skeleton, Spinner, StatusBadge, TokenImage, UnitInput,
  parseDate, relTime, useToast,
} from "../components/ui";

type Dialog = { title: string; path: string; body: Record<string, unknown> } | null;
type Op = "claim" | "burn" | "send";

/** Draft → Deploying → Live, with Failed as a stop on the way. Reflects the launch's real status only. */
function Pipeline({ status }: { status: string }) {
  if (status === "archived") return <span className="badge cap">archived</span>;
  const order = ["draft", "deploying", "live"];
  const at = status === "failed" ? 1 : Math.max(0, order.indexOf(status));
  const labels = ["Draft", status === "failed" ? "Failed" : "Deploying", "Live"];
  return (
    <div className="pipeline" aria-label={`Launch status: ${status}`}>
      {labels.map((l, i) => (
        <span key={l} style={{ display: "contents" }}>
          {i > 0 && <span className="pl" aria-hidden="true" />}
          <span className={`ps ${status === "failed" && i === 1 ? "fail" : i < at || status === "live" ? "done" : i === at ? "now" : ""}`}>
            <i aria-hidden="true">{status === "failed" && i === 1 ? "!" : i < at || status === "live" ? "✓" : i + 1}</i>{l}
          </span>
        </span>
      ))}
    </div>
  );
}

function TransparencyPanel({ t, network, symbol }: { t: any; network: string; symbol: string }) {
  const hot = t.warnings.length > 0;
  const maxPct = Math.max(1, ...t.top_holders.map((h: any) => h.pct));
  const evm = network !== "solana";
  return (
    <Card title="Supply transparency" icon="pie" sub="What your wallets hold, measured against the whole supply">
      <div className="stack">
        <div className="row between nowrap">
          <div>
            <div className="muted small">Held by your attached wallets</div>
            <div className="num" style={{ font: "500 24px var(--mono)", color: hot ? "var(--warn)" : undefined }}>{fmt(t.controlled_pct, 2)}%</div>
          </div>
          <div style={{ textAlign: "right" }}>
            <div className="muted small">of total supply</div>
            <div className="mono small">{compact(t.controlled_supply)} / {compact(t.total_supply)} ${symbol}</div>
          </div>
        </div>
        <div className={`bar ${hot ? "hot" : ""}`} role="img" aria-label={`${fmt(t.controlled_pct, 2)}% of supply held by your wallets`}>
          <div style={{ width: `${Math.min(t.controlled_pct, 100)}%` }} />
        </div>
        {t.warnings.map((w: string) => <Callout key={w} tone="warn">{w}</Callout>)}
      </div>
      <h3 style={{ margin: "18px 0 10px" }}>Largest holders</h3>
      {t.top_holders.length === 0 ? (
        <p className="muted small">{evm ? "EVM nodes can't list a token's largest holders, so only your own wallets are measured here." : "Holder data isn't available yet."}</p>
      ) : (
        <ol className="stack tight" style={{ margin: 0, padding: 0, listStyle: "none" }}>
          {t.top_holders.map((h: any, i: number) => (
            <li key={h.owner} className="holder">
              <span className="rank">{i + 1}</span>
              <div style={{ minWidth: 0 }}>
                <div className="row nowrap" style={{ gap: 6 }}><Address address={h.owner} network={network} />{h.is_yours && <span className="badge info">yours</span>}</div>
                <div className="bar thin" style={{ marginTop: 3 }} aria-hidden="true">
                  <div style={{ width: `${(h.pct / maxPct) * 100}%`, background: h.is_yours ? undefined : "var(--line-3)" }} />
                </div>
              </div>
              <span className="pct">{fmt(h.pct, 2)}%</span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function downloadPnlCard(launch: any, pnl: any) {
  const unit = chainOf(launch.network).native;
  const up = pnl.net_sol_flow >= 0;
  const c = document.createElement("canvas");
  c.width = 1200; c.height = 630;
  const g = c.getContext("2d")!;
  g.fillStyle = "#06090b"; g.fillRect(0, 0, 1200, 630);
  g.strokeStyle = "rgba(120,170,160,0.07)"; g.lineWidth = 1;
  for (let x = 0; x <= 1200; x += 40) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, 630); g.stroke(); }
  for (let y = 0; y <= 630; y += 40) { g.beginPath(); g.moveTo(0, y); g.lineTo(1200, y); g.stroke(); }
  g.fillStyle = "#35d0a0"; g.fillRect(70, 70, 4, 110);
  g.fillStyle = "#e4ecee"; g.font = "600 56px 'IBM Plex Sans', sans-serif"; g.fillText(launch.name, 96, 128);
  g.fillStyle = "#6f8087"; g.font = "500 32px 'IBM Plex Mono', monospace"; g.fillText(`$${launch.symbol}`, 96, 176);
  g.fillStyle = up ? "#3dcf8a" : "#ef6b6b"; g.font = "500 120px 'IBM Plex Mono', monospace";
  g.fillText(`${signed(pnl.net_sol_flow, 3)} ${unit}`, 70, 380);
  g.fillStyle = "#a7b5ba"; g.font = "28px 'IBM Plex Sans', sans-serif";
  g.fillText(`Net ${unit} flow on ${chainOf(launch.network).name} · ${pnl.confirmed_txs} confirmed transactions`, 70, 440);
  g.fillStyle = "#6f8087"; g.font = "24px 'IBM Plex Sans', sans-serif";
  g.fillText("Unsold tokens are not valued. Fees included.", 70, 480);
  g.fillStyle = "#35d0a0"; g.font = "600 28px 'IBM Plex Sans', sans-serif"; g.fillText("Helm Terminals", 70, 570);
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
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const [w, all] = await Promise.all([api(`/launches/${id}/workspace`), api<any[]>("/wallets")]);
      setWs(w);
      setAllWallets(all.filter((x) => x.kind === "managed"));
      setError("");
      setUpdated(new Date());
    } catch (e) { setError(errMsg(e)); }
    finally { setRefreshing(false); }
  }, [id]);

  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);

  if (!ws) {
    return error ? (
      <div className="stack">
        <Link to="/launches" className="btn sm ghost" style={{ alignSelf: "flex-start" }}><Icon name="arrowleft" size={14} />All launches</Link>
        <Callout tone="err"><b>This launch couldn't be loaded.</b> {error}</Callout>
      </div>
    ) : (
      <div className="stack" aria-busy="true"><Skeleton h={56} w={360} /><Skeleton h={76} /><div className="grid g-main"><Skeleton h={320} /><Skeleton h={320} /></div></div>
    );
  }
  const l = ws.launch;
  const chain = chainOf(l.network);
  const cur = chain.native;
  const live = l.status === "live";
  const sel = [...selected];
  const toggle = (wid: string) => setSelected((s) => { const n = new Set(s); n.has(wid) ? n.delete(wid) : n.add(wid); return n; });
  const attached = new Set(ws.wallets.map((w: any) => w.id));
  const totalSol = ws.wallets.reduce((s: number, w: any) => s + (w.sol ?? 0), 0);
  const totalTok = ws.wallets.reduce((s: number, w: any) => s + (w.tokens ?? 0), 0);
  const supply = ws.transparency?.total_supply ?? 0;
  const allSel = ws.wallets.length > 0 && ws.wallets.every((w: any) => selected.has(w.id));
  const selW = ws.wallets.filter((w: any) => selected.has(w.id));
  const firstSel = selW[0];
  const selSol = selW.reduce((s: number, w: any) => s + (w.sol ?? 0), 0);
  const selTok = selW.reduce((s: number, w: any) => s + (w.tokens ?? 0), 0);
  const presets = side === "buy" ? BUY_PRESETS : SELL_PRESETS;
  const amt = +amount || 0;
  const flow = ws.pnl.net_sol_flow as number;

  const attach = () => api(`/launches/${id}/wallets`, { body: { wallet_ids: [attachId] } })
    .then(() => { setAttachId(""); toast("good", "Wallet attached"); load(); }).catch((e) => toast("bad", errMsg(e)));

  return (
    <>
      <Link to="/launches" className="btn sm ghost" style={{ alignSelf: "flex-start", marginLeft: -8, marginBottom: -8 }}><Icon name="arrowleft" size={14} />All launches</Link>

      <header className="page-head">
        <div className="row nowrap" style={{ gap: 14, minWidth: 0 }}>
          <TokenImage launch={l} size={52} />
          <div style={{ minWidth: 0 }}>
            <div className="row" style={{ gap: 10 }}>
              <h1 className="truncate">{l.name}</h1>
              <span className="mono muted" style={{ fontSize: 14 }}>${l.symbol}</span>
              {l.imported && <span className="badge violet">imported</span>}
            </div>
            <div className="row small muted" style={{ gap: 8, marginTop: 4 }}>
              <ChainLabel network={l.network} size={16} /><span aria-hidden="true">·</span><PadLabel pad={l.launchpad} network={l.network} size={18} />
              {l.quote && <span className="badge info">paired with {l.quote.symbol}</span>}
              {l.mint_address && <><span aria-hidden="true">·</span><Address address={l.mint_address} type="token" chars={6} network={l.network} /></>}
            </div>
          </div>
        </div>
        <div className="actions">
          {updated && (
            <span className="muted small hide-sm row nowrap" style={{ gap: 6 }} aria-live="polite">
              {refreshing ? <Spinner /> : <span className="badge good pulse" style={{ padding: "0 5px" }}><span className="dot" /></span>}
              Updated {updated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
            </span>
          )}
          {live && <Link to={`/automation?launch=${id}`} className="btn secondary"><Icon name="calendar" size={14} />Schedule</Link>}
          <button className="btn icon secondary" onClick={load} aria-label="Refresh now" title="Refresh now"><Icon name="refresh" size={14} /></button>
          <button className="btn secondary" onClick={() => api(`/launches/${id}/${l.status === "archived" ? "restore" : "archive"}`, { method: "POST", body: {} })
            .then(() => { toast("good", l.status === "archived" ? "Launch restored" : "Launch archived"); load(); }).catch((e) => toast("bad", errMsg(e)))}>
            <Icon name="archive" size={14} />{l.status === "archived" ? "Restore" : "Archive"}
          </button>
        </div>
      </header>

      <Pipeline status={l.status} />

      {error && <Callout tone="err">Latest refresh failed: {error}</Callout>}
      {ws.rpc_error && <Callout tone="warn"><b>Chain data is partly unavailable.</b> Balances may be missing. {ws.rpc_error}</Callout>}

      {(l.status === "draft" || l.status === "failed") && (
        <div className={`next-action ${l.status === "failed" ? "bad" : ""}`}>
          <span className="na-ic"><Icon name={l.status === "failed" ? "alert" : "rocket"} size={18} /></span>
          <div className="grow">
            <h2>{l.status === "failed" ? "Deployment failed. Review and retry." : "Next: deploy this token"}</h2>
            <p className="muted small" style={{ marginTop: 2 }}>
              {l.dev_wallet_id ? <>Dev buy <b className="text-2 mono">{fmt(l.dev_buy_sol, 6)} {cur}</b> from the dev wallet. The preview shows every cost before anything is sent.</> : "This launch has no dev wallet yet. Set one before deploying."}
            </p>
          </div>
          <button className="btn" disabled={!l.dev_wallet_id} onClick={() => setDialog({ title: `Deploy ${l.name}`, path: `/launches/${id}/deploy`, body: {} })}>
            Review deployment<Icon name="arrowright" size={15} />
          </button>
        </div>
      )}
      {l.status === "deploying" && <Callout><b>Deploying.</b> Waiting for the chain to confirm; this page refreshes every 10 seconds.</Callout>}

      <Metrics label="Launch figures" items={[
        { label: `Net ${cur} flow`, value: signed(flow), unit: cur, tone: flow > 0 ? "good" : flow < 0 ? "bad" : undefined, hint: `${ws.pnl.confirmed_txs} confirmed tx${ws.pnl.confirmed_txs === 1 ? "" : "s"}` },
        { label: "Supply you hold", value: ws.transparency ? `${fmt(ws.transparency.controlled_pct, 2)}%` : "—", tone: ws.transparency?.warnings?.length ? "warn" : undefined, hint: ws.transparency ? "across attached wallets" : "once the token is live" },
        { label: "Tokens held", value: compact(totalTok), hint: `$${l.symbol}` },
        { label: `${cur} in wallets`, value: fmt(totalSol, 4), unit: cur, hint: "for trades and gas" },
        { label: "Wallets", value: String(ws.wallets.length), hint: sel.length ? `${sel.length} selected` : "attached" },
      ]} />

      <MarketPanel launchId={id!} network={l.network} mint={l.mint_address} live={live} symbol={l.symbol} native={cur} />

      <div className="grid g-main">
        <div className="col">
          <Card title="Wallets" icon="wallet" sub={sel.length ? `${sel.length} selected · ${fmt(selSol, 4)} ${cur} · ${compact(selTok)} tokens` : "Select the wallets to trade or operate from"} flush
            actions={<div className="row nowrap">
              <label className="sr-only" htmlFor="attach-wallet">Attach a wallet</label>
              <select id="attach-wallet" value={attachId} onChange={(e) => setAttachId(e.target.value)} style={{ width: 200 }}>
                <option value="">Attach a wallet…</option>
                {allWallets.filter((w) => !attached.has(w.id) && w.network === l.network).map((w) => <option key={w.id} value={w.id}>{w.label || short(w.address)}</option>)}
              </select>
              <button className="btn icon secondary" disabled={!attachId} onClick={attach} aria-label="Attach selected wallet"><Icon name="plus" size={14} /></button>
            </div>}>
            {ws.wallets.length === 0 ? (
              <Empty icon="wallet" title="No wallets attached" text={`Attach managed ${chain.name} wallets to trade this token and to count the supply you control.`} />
            ) : (
              <div className="table-wrap"><table className="rtable">
                <caption className="sr-only">Wallets attached to this launch</caption>
                <thead><tr>
                  <th className="cb"><input type="checkbox" checked={allSel} aria-label="Select all wallets" onChange={() => setSelected(allSel ? new Set() : new Set(ws.wallets.map((w: any) => w.id)))} /></th>
                  <th>Wallet</th><th className="hide-sm">Role</th><th className="r">{cur}</th><th className="r">Tokens</th>{supply > 0 && <th className="r hide-sm">Supply</th>}
                </tr></thead>
                <tbody>
                  {ws.wallets.map((w: any) => (
                    <tr key={w.id} className={`clickable has-cb ${selected.has(w.id) ? "sel" : ""}`} onClick={() => toggle(w.id)}>
                      <td className="cb"><input type="checkbox" checked={selected.has(w.id)} onChange={() => toggle(w.id)} onClick={(e) => e.stopPropagation()} aria-label={`Select ${w.label || w.address}`} /></td>
                      <td className="span"><div className="row" style={{ gap: 8 }}><span style={{ fontWeight: 550 }}>{w.label || "Untitled"}</span>{w.role === "dev" && <span className="badge info show-sm">dev</span>}</div><Address address={w.address} network={l.network} /></td>
                      <td className="hide-sm"><span className={`badge cap ${w.role === "dev" ? "info" : ""}`}>{w.role}</span></td>
                      <td className="r num" data-label={cur}>{fmt(w.sol, 4)}</td>
                      <td className="r num" data-label="Tokens" title={fmt(w.tokens, 6)}>{compact(w.tokens)}</td>
                      {supply > 0 && <td className="r num hide-sm muted">{fmt((w.tokens / supply) * 100, 2)}%</td>}
                    </tr>
                  ))}
                </tbody>
                <tfoot><tr><td className="m-hide" /><td className="span">Total</td><td className="hide-sm" /><td className="r num">{fmt(totalSol, 4)}</td><td className="r num">{compact(totalTok)}</td>{supply > 0 && <td className="r num hide-sm">{fmt((totalTok / supply) * 100, 2)}%</td>}</tr></tfoot>
              </table></div>
            )}
          </Card>

        </div>

        <div className="col">
          {live && (
            <Card title="Order ticket" icon="zap" sub="Manual orders from the selected wallets">
              <div className="stack">
                <Seg full className="trade" value={side} onChange={(s) => { setSide(s); setAmount(s === "buy" ? "0.1" : "25"); }} options={[["buy", "Buy"], ["sell", "Sell"]]} />
                <Field label={side === "buy" ? `Spend per wallet` : "Sell from each wallet"} extra={side === "sell" && sel.length ? `${compact(selTok)} tokens selected` : side === "buy" && sel.length ? `${fmt(selSol, 4)} ${cur} available` : undefined}>
                  <UnitInput unit={side === "buy" ? cur : "%"} value={amount} onChange={(e) => setAmount(e.target.value)} />
                </Field>
                <div className="chips" role="group" aria-label="Preset amounts">
                  {presets.map((p) => <button key={p} type="button" className={`chip ${amount === p ? "on" : ""}`} aria-pressed={amount === p} onClick={() => setAmount(p)}>{p}{side === "sell" ? "%" : ""}</button>)}
                </div>
                <div className="kv small">
                  <div className="k">Wallets</div>
                  <div className="v">{sel.length ? <span className="mono">{sel.length}</span> : <button type="button" className="btn sm ghost" style={{ height: 22, padding: "0 6px" }} onClick={() => setSelected(new Set(ws.wallets.map((w: any) => w.id)))} disabled={!ws.wallets.length}>Select all {ws.wallets.length}</button>}</div>
                  <div className="k">{side === "buy" ? "Total spend" : "Selling"}</div>
                  <div className="v mono">{side === "buy" ? `${fmt(sel.length * amt, 6)} ${cur}` : `${fmt(amt, 2)}% ≈ ${compact((selTok * Math.min(amt, 100)) / 100)} tokens`}</div>
                  <div className="k">Execution</div>
                  <div className="v">{mode === "stagger" ? `One at a time · ${stagger} ms` : "All at once"} · {slippage}% slippage</div>
                </div>
                <button type="button" className="btn ghost sm" style={{ alignSelf: "flex-start", marginLeft: -8 }} aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}>
                  <Icon name="settings" size={13} />{advanced ? "Hide" : "Change"} execution settings
                </button>
                {advanced && (
                  <div className="stack" style={{ padding: 12, borderRadius: 8, background: "var(--bg-2)", border: "1px solid var(--line)" }}>
                    <Field label="Execution"><Seg full value={mode} onChange={setMode} options={[["stagger", "One at a time"], ["parallel", "All at once"]]} /></Field>
                    <div className="row nowrap">
                      {mode === "stagger" && <Field label="Delay between wallets" style={{ flex: 1 }}><UnitInput unit="ms" value={stagger} onChange={(e) => setStagger(e.target.value)} /></Field>}
                      <Field label="Max slippage" style={{ flex: 1 }}><UnitInput unit="%" value={slippage} onChange={(e) => setSlippage(e.target.value)} /></Field>
                    </div>
                  </div>
                )}
                <button className={`btn lg block ${side}`} disabled={!sel.length || !(amt > 0) || (side === "sell" && amt > 100)} onClick={() => setDialog({
                  title: `${side === "buy" ? "Buy" : "Sell"} $${l.symbol}`, path: `/launches/${id}/trade`,
                  body: { side, wallet_ids: sel, amount: amt, mode, stagger_ms: +stagger, slippage_pct: +slippage },
                })}>
                  {sel.length ? `Review ${side} · ${sel.length} wallet${sel.length === 1 ? "" : "s"}` : "Select wallets to trade"}
                </button>
                <p className="muted small">The next step shows the server's preview, fees and any blockers. Nothing is sent until you confirm.</p>
              </div>
            </Card>
          )}

          {ws.transparency ? <TransparencyPanel t={ws.transparency} network={l.network} symbol={l.symbol} /> : (
            <Card title="Supply transparency" icon="pie"><p className="muted small">Measured once the token is live and chain data loads.</p></Card>
          )}

          {live && (
            <Card title="Token operations" icon="layers" sub="Claim, burn or send from your wallets">
              <div className="stack">
                <Seg full value={op} onChange={setOp} options={[["claim", "Claim fees"], ["burn", "Burn"], ["send", "Send"]]} />
                {op === "claim" && (
                  <>
                    <p className="muted small">Collect creator fees earned by the token's creator wallet.</p>
                    <Field label="Creator wallet">
                      <select value={claimWallet} onChange={(e) => setClaimWallet(e.target.value)}>
                        <option value="">Choose…</option>
                        {ws.wallets.map((w: any) => <option key={w.id} value={w.id}>{w.label || short(w.address)}{w.role === "dev" ? " (dev)" : ""}</option>)}
                      </select>
                    </Field>
                    <button className="btn block" disabled={!claimWallet} onClick={() => setDialog({ title: "Claim creator fees", path: `/launches/${id}/claim-fees`, body: { wallet_id: claimWallet } })}>
                      <Icon name="coins" size={15} />Review claim</button>
                  </>
                )}
                {op === "burn" && (
                  <>
                    <Callout tone="warn">Burning permanently destroys tokens. It can't be undone.</Callout>
                    <Field label="Share of the wallet's tokens" hint={firstSel ? `From ${firstSel.label || short(firstSel.address)} (first selected wallet) · ${compact(firstSel.tokens)} tokens` : "Select a wallet in the table first"}>
                      <UnitInput unit="%" value={burnPct} onChange={(e) => setBurnPct(e.target.value)} />
                    </Field>
                    <button className="btn danger-ghost block" disabled={!firstSel || !(+burnPct > 0) || +burnPct > 100} onClick={() => setDialog({ title: "Burn tokens (irreversible)", path: `/launches/${id}/burn`, body: { wallet_id: firstSel.id, percent: +burnPct } })}>
                      <Icon name="flame" size={15} />Review burn</button>
                  </>
                )}
                {op === "send" && (
                  <>
                    <Field label="Recipient"><input className="mono" placeholder={`${chain.name} address`} value={sendTo} onChange={(e) => setSendTo(e.target.value)} autoComplete="off" spellCheck={false} /></Field>
                    <Field label="Share of the wallet's tokens" hint={firstSel ? `From ${firstSel.label || short(firstSel.address)} · needs your 2FA code` : "Select a wallet in the table first"}>
                      <UnitInput unit="%" value={sendPct} onChange={(e) => setSendPct(e.target.value)} />
                    </Field>
                    <button className="btn secondary block" disabled={!firstSel || sendTo.trim().length < 32 || !(+sendPct > 0) || +sendPct > 100} onClick={() => setDialog({ title: "Send tokens", path: `/launches/${id}/send`, body: { wallet_id: firstSel.id, recipient: sendTo.trim(), percent: +sendPct } })}>
                      <Icon name="send" size={15} />Review send</button>
                    <p className="muted small">To move tokens between your own wallets, use Disperse or Consolidate on the Wallets page; those wallets stay counted in transparency.</p>
                  </>
                )}
              </div>
            </Card>
          )}

          <Card title="Performance" icon="trend" actions={<button className="btn sm secondary" onClick={() => downloadPnlCard(l, ws.pnl)}><Icon name="download" size={13} />Share card</button>}>
            <div className={`num ${flow > 0 ? "good" : flow < 0 ? "bad" : ""}`} style={{ font: "500 26px var(--mono)" }}>
              {signed(flow)} <span className="muted" style={{ fontSize: 14 }}>{cur}</span>
            </div>
            <p className="muted small" style={{ marginTop: 6 }}>{ws.pnl.note}</p>
          </Card>
        </div>
      </div>

      <Card title="Transactions" icon="activity" flush sub="Latest 50 for this launch, newest first">
        {ws.transactions.length === 0 ? <Empty icon="activity" title="No transactions yet" text="Deploys, trades, claims and transfers for this token appear here." /> : (
          <div className="table-wrap"><table>
            <caption className="sr-only">Transactions for this launch</caption>
            <thead><tr><th>Time</th><th>Type</th><th>Status</th><th className="r">{cur} change</th><th className="r">Transaction</th></tr></thead>
            <tbody>
              {ws.transactions.map((t: any) => (
                <tr key={t.id}>
                  <td className="muted nowrap mono" style={{ fontSize: 12 }} title={parseDate(t.created_at).toLocaleString()}>{relTime(t.created_at)}</td>
                  <td style={{ textTransform: "capitalize" }}>{t.kind.replace(/_/g, " ")}</td>
                  <td><StatusBadge status={t.status} /></td>
                  <td data-label={`${cur} change`} className={`r num ${t.sol_delta > 0 ? "good" : t.sol_delta < 0 ? "bad" : "muted"}`}>{t.sol_delta == null ? "—" : signed(t.sol_delta)}</td>
                  <td className="r mono" data-label="Transaction">{t.signature ? <a href={explorerUrl(l.network, "tx", t.signature)} target="_blank" rel="noreferrer">{short(t.signature)} <Icon name="ext" size={11} /><span className="sr-only"> (opens explorer)</span></a> : <span className="muted small">{t.error ? "not sent" : "—"}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </Card>

      {dialog && <SpendDialog {...dialog} network={l.network} onClose={() => setDialog(null)} onDone={load} />}
    </>
  );
}
