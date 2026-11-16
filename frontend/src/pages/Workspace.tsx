import Distribute from "../components/Distribute";
import { useAutoRefresh } from "../components/useAutoRefresh";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, Location, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, compact, errMsg, fmt, short, signed } from "../api";
import { chainOf, explorerUrl, isExtraChain } from "../chains";
import { launchpadExperience } from "../launchpadExperience";
import MarketTerminal from "../components/Market";
import PonsRewards from "../components/PonsRewards";
import Allocations from "../components/Allocations";
import DistributeSupply from "../components/DistributeSupply";
import { DeleteDraft } from "./Launches";
import SpendDialog from "../components/SpendDialog";
import { PnlImage, downloadPnl, launchPnlFacts } from "../components/pnl";
import { ChainLabel, PadLabel } from "../components/marks";
import {
  Address, Callout, Card, Empty, Field, Icon, Metrics, parseDate, relTime, Seg, Skeleton, Spinner, StatusBadge, TableWrap, TokenImage, UnitInput, useToast,
} from "../components/ui";

type Dialog = { title: string; path: string; body: Record<string, unknown> } | null;
type Op = "claim" | "burn" | "send" | "distribute";

function launchSetting(key: string, value: unknown): string {
  const labels: Record<string, string> = {
    tax_rate_pct: "Trading tax", creator_tax_pct: "Creator tax", lp_share_pct: "Supply in pool",
    lp_tokens: "LP tokens", sniper_start_pct: "Opening sniper fee", sniper_decay_s: "Sniper decay",
    dbc_quote: "Curve currency", anti_sniping: "Anti-sniping", label: "Category",
    duration_h: "Auction length", soft_cap_near: "Soft cap", hard_cap_near: "Hard cap",
  };
  const unit = key.endsWith("_pct") ? "%" : key.endsWith("_s") ? "s" : key.endsWith("_h") ? "h" : key.endsWith("_near") ? " NEAR" : "";
  return `${labels[key] ?? key.replace(/_/g, " ")}: ${String(value)}${unit}`;
}

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

function TransparencyPanel({ t, network, symbol, actions }: { t: any; network: string; symbol: string; actions?: React.ReactNode }) {
  const hot = t.warnings.length > 0;
  const maxPct = Math.max(1, ...t.top_holders.map((h: any) => h.pct));
  const evm = network !== "solana";
  return (
    <Card title="Supply transparency" icon="pie" sub="What your wallets hold, measured against the whole supply" actions={actions}>
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

const BUY_PRESETS = ["0.05", "0.1", "0.25", "0.5", "1"];
const SELL_PRESETS = ["10", "25", "50", "75", "100"];

/** Bottom panel of the terminal: one section at a time, like a trading terminal's tables. */
function WsTabs({ tabs, on, setOn }: { tabs: [string, string, React.ReactNode][]; on: string; setOn: (k: string) => void }) {
  const cur = tabs.find((t) => t[0] === on) ?? tabs[0];
  return (
    <section className="ws-tabs">
      <div className="ws-tablist" role="tablist" aria-label="Launch sections" onKeyDown={(e) => {
        const index = tabs.indexOf(cur);
        const next = e.key === "ArrowRight" ? (index + 1) % tabs.length : e.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length
          : e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : -1;
        if (next < 0) return;
        e.preventDefault(); setOn(tabs[next][0]);
        e.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
      }}>
        {tabs.map(([k, label]) => (
          <button key={k} type="button" role="tab" id={`wt-${k}`} tabIndex={cur[0] === k ? 0 : -1} aria-selected={cur[0] === k} aria-controls="wt-panel"
            className={cur[0] === k ? "on" : ""} onClick={() => setOn(k)}>{label}</button>
        ))}
      </div>
      <div id="wt-panel" role="tabpanel" aria-labelledby={`wt-${cur[0]}`}>{cur[2]}</div>
    </section>
  );
}

export default function Workspace() {
  const { id } = useParams();
  // The launches list hands over what it already knows, so the header renders at once instead of
  // after the workspace payload; a cold load (refresh, deep link) falls back to granular skeletons.
  const cached: any | undefined = (useLocation().state as Location["state"])?.launch;
  const nav = useNavigate();
  const [search, setSearch] = useSearchParams();
  const tab = search.get("tab") ?? "wallets";
  const setTab = (k: string) => setSearch((p) => { const n = new URLSearchParams(p); n.set("tab", k); return n; }, { replace: true });
  const toast = useToast();
  const [ws, setWs] = useState<any>(null);
  const [allWallets, setAllWallets] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [dialog, setDialog] = useState<Dialog>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [attachId, setAttachId] = useState("");
  const [folders, setFolders] = useState<{id:string;name:string}[]>([]);
  const [consolidateOpen, setConsolidateOpen] = useState(false);
  useEffect(() => { api<{id:string;name:string}[]>("/folders").then(setFolders).catch(() => {}); }, []);
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
  const workspaceFlights = useRef(new Set<string | undefined>());
  const currentWorkspace = useRef(id);
  currentWorkspace.current = id;
  const [mobileView, setMobileView] = useState<"chart" | "trade" | "activity">("chart");
  const [supplyOpen, setSupplyOpen] = useState(false);

  const load = useCallback(async () => {
    if (workspaceFlights.current.has(id)) return;
    workspaceFlights.current.add(id);
    setRefreshing(true);
    try {
      let [w, all] = await Promise.all([api<any>(`/launches/${id}/workspace`), api<any[]>("/wallets")]);
      if (w.launch.status === "deploying" && ((w.launch.mint_address && w.launch.network === "solana") || isExtraChain(w.launch.network) || w.launch.deploy_tx)) {
        // The create was sent but not confirmed in time: look for the token on-chain.
        const l = await api<any>(`/launches/${id}/recheck`, { body: {} }).catch(() => null);
        if (l && l.status !== "deploying") w = await api(`/launches/${id}/workspace`);
      }
      if (currentWorkspace.current !== id) return;
      setWs(w);
      setAllWallets(all.filter((x) => x.kind === "managed"));
      setError("");
      setUpdated(new Date());
    } catch (e) { if (currentWorkspace.current === id) setError(errMsg(e)); }
    finally { if (currentWorkspace.current === id) setRefreshing(false); workspaceFlights.current.delete(id); }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);
  useAutoRefresh(load, 10_000);

  if (!ws) {
    const head = cached ? (
      // Real identity from the list hand-over: the page reads as loaded before the payload arrives.
      <div className="stack" aria-busy="true">
        <Link to="/launches" className="btn sm ghost" style={{ alignSelf: "flex-start", marginLeft: -8, marginBottom: -8 }}><Icon name="arrowleft" size={14} />All launches</Link>
        <header className="page-head">
          <div className="row nowrap" style={{ gap: 14, minWidth: 0 }}>
            <TokenImage launch={cached} size={52} />
            <div style={{ minWidth: 0 }}>
              <div className="row" style={{ gap: 10 }}>
                <h1 className="truncate">{cached.name}</h1>
                <span className="mono muted" style={{ fontSize: 14 }}>${cached.symbol}</span>
                {cached.imported && <span className="badge violet">imported</span>}
              </div>
              <div className="row small muted" style={{ gap: 8, marginTop: 4 }}>
                <ChainLabel network={cached.network} size={16} /><span aria-hidden="true">·</span><PadLabel pad={cached.launchpad} network={cached.network} size={18} />
                {cached.mint_address && <><span aria-hidden="true">·</span><Address address={cached.mint_address} type="token" chars={6} network={cached.network} /></>}
              </div>
            </div>
          </div>
          <div className="actions"><Spinner /></div>
        </header>
        <Pipeline status={cached.status} />
        <Skeleton h={92} style={{ borderRadius: "var(--radius)" }} />
        <div className="grid g-main"><Skeleton h={320} style={{ borderRadius: "var(--radius)" }} /><Skeleton h={320} style={{ borderRadius: "var(--radius)" }} /></div>
      </div>
    ) : (
      <div className="stack" aria-busy="true">
        <Skeleton h={28} w={130} style={{ borderRadius: 6 }} />
        <div className="row nowrap" style={{ gap: 14 }}>
          <Skeleton h={52} w={52} style={{ borderRadius: 14 }} />
          <div className="stack tight" style={{ gap: 8 }}><Skeleton h={22} w={280} /><Skeleton h={13} w={200} /></div>
        </div>
        <Skeleton h={40} w={320} style={{ borderRadius: 999 }} />
        <Skeleton h={92} style={{ borderRadius: "var(--radius)" }} />
        <div className="grid g-main">
          <div className="stack tight"><Skeleton h={44} w={240} /><Skeleton h={280} style={{ borderRadius: "var(--radius)" }} /><Skeleton h={180} style={{ borderRadius: "var(--radius)" }} /></div>
          <div className="stack tight"><Skeleton h={120} style={{ borderRadius: "var(--radius)" }} /><Skeleton h={200} style={{ borderRadius: "var(--radius)" }} /></div>
        </div>
      </div>
    );
    return error ? (
      <div className="stack">
        <Link to="/launches" className="btn sm ghost" style={{ alignSelf: "flex-start" }}><Icon name="arrowleft" size={14} />All launches</Link>
        <Callout tone="err"><b>This launch couldn't be loaded.</b> {error}</Callout>
      </div>
    ) : head;
  }
  const l = ws.launch;
  const chain = chainOf(l.network);
  const experience = launchpadExperience(l.launchpad);
  const claimMode = l.launchpad === "bags" && l.quote ? "external" : (l.capabilities?.claim_mode ?? experience.claimMode);
  const claimUrl = l.launchpad === "bags" && l.quote ? "https://bags.fm/" : experience.claimUrl;
  const claimInApp = claimMode === "in-app";
  const effectiveOp: Op = op === "claim" && !claimInApp ? "burn" : op;
  const opOptions: [Op, string][] = claimInApp
    ? [["claim", "Claim fees"], ["burn", "Burn"], ["send", "Send"], ["distribute", "Distribute"]]
    : [["burn", "Burn"], ["send", "Send"], ["distribute", "Distribute"]];
  const chosenSettings = Object.entries(l.settings ?? {});
  const cur = chain.native;
  const live = l.status === "live";
  // Extra-chain launch plans remain unavailable; Meme.Cooking trades are auction deposits.
  const extra = isExtraChain(l.network);
  const auction = l.launchpad === "meme-cooking";
  const sel = [...selected].filter((wid) => ws.wallets.some((w: any) => w.id === wid));
  const toggle = (wid: string) => setSelected((s) => { const n = new Set(s); n.has(wid) ? n.delete(wid) : n.add(wid); return n; });
  const attached = new Set(ws.wallets.map((w: any) => w.id));
  // "Unavailable ≠ zero": sums are only shown when every attached wallet's balance was actually read.
  const nativeKnown = ws.wallets.length > 0 && ws.wallets.every((w: any) => w.sol != null);
  const tokensKnown = ws.wallets.length > 0 && ws.wallets.every((w: any) => w.tokens != null);
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

  const attach = () => api(`/launches/${id}/wallets`, { body: { wallet_ids: attachId.startsWith("folder:") ? allWallets.filter(w => w.folder_id === attachId.slice(7) && w.network === l.network && !attached.has(w.id)).map(w => w.id) : [attachId] } })
    .then(() => { setAttachId(""); toast("good", "Wallet attached"); load(); }).catch((e) => toast("bad", errMsg(e)));

  // Sections, shared by the terminal layout (tokens with an address) and the draft layout.
  const walletsCard = (
          <Card title="Wallets" icon="wallet" sub={sel.length ? `${sel.length} selected · ${fmt(selSol, 4)} ${cur} · ${compact(selTok)} tokens` : "Select the wallets to trade or operate from"} flush
            actions={<div className="row workspace-wallet-actions">
              {live && !extra && <><button className="btn secondary" onClick={() => setSupplyOpen(true)}><Icon name="send" size={14} />Distribute tokens</button><button className="btn secondary" disabled={ws.wallets.length < 2} onClick={() => setConsolidateOpen(true)}><Icon name="wallet" size={14} />Consolidate tokens</button></>}
              <label className="sr-only" htmlFor="attach-wallet">Attach a wallet</label>
              <select id="attach-wallet" value={attachId} onChange={(e) => setAttachId(e.target.value)} style={{ width: 200 }}>
                <option value="">Attach wallet or folder…</option>
                {folders.map(f => { const count = allWallets.filter(w => w.folder_id === f.id && w.network === l.network && !attached.has(w.id)).length; return count > 0 ? <option key={f.id} value={`folder:${f.id}`} disabled={count > 50}>Folder: {f.name} · {count} wallets</option> : null; })}
                {allWallets.filter((w) => !attached.has(w.id) && w.network === l.network).map((w) => <option key={w.id} value={w.id}>{w.label || short(w.address)}</option>)}
              </select>
              <button className="btn icon secondary" disabled={!attachId} onClick={attach} aria-label="Attach selected wallet"><Icon name="plus" size={14} /></button>
            </div>}>
            {ws.wallets.length === 0 ? (
              <Empty icon="wallet" title="No wallets attached" text={`Attach managed ${chain.name} wallets to trade this token and to count the supply you control.`} />
            ) : (
              <TableWrap><table className="rtable">
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
                <tfoot><tr><td className="m-hide" /><td className="span">Total</td><td className="hide-sm" /><td className="r num">{nativeKnown ? fmt(totalSol, 4) : "—"}</td><td className="r num">{tokensKnown ? compact(totalTok) : "—"}</td>{supply > 0 && tokensKnown && <td className="r num hide-sm">{fmt((totalTok / supply) * 100, 2)}%</td>}</tr></tfoot>
              </table></TableWrap>
            )}
          </Card>
  );
  const ticketCard = live ? (
            <Card id="order-ticket" className="terminal-ticket" title={`Trade $${l.symbol}`} icon="zap" sub="Helm execution · review before sending">
              <div className="stack">
                <Seg full className="trade" value={side} onChange={(s) => { setSide(s); setAmount(s === "buy" ? "0.1" : "25"); }} options={auction ? [["buy", "Deposit"], ["sell", "Withdraw"]] : [["buy", "Buy"], ["sell", "Sell"]]} />
                <details className="ticket-wallets">
                  <summary><span className="row"><Icon name="wallet" size={14} />Wallets</span><span className="mono">{sel.length} / {ws.wallets.length} selected</span></summary>
                  <div className="ticket-wallet-actions"><button type="button" className="btn sm ghost" disabled={!ws.wallets.length} onClick={() => setSelected(new Set(ws.wallets.map((w: any) => w.id)))}>Select all</button>
                    <button type="button" className="btn sm ghost" disabled={!sel.length} onClick={() => setSelected(new Set())}>Clear</button></div>
                  <div className="ticket-wallet-list">
                    {ws.wallets.map((w: any) => <label key={w.id} className="ticket-wallet">
                      <input type="checkbox" checked={selected.has(w.id)} onChange={() => toggle(w.id)} />
                      <span className="grow"><b>{w.label || short(w.address)}</b><span className="muted mono small">{short(w.address)}</span></span>
                      <span className="mono small">{w.sol == null ? "—" : fmt(w.sol, 4)} {cur}</span>
                    </label>)}
                    {!ws.wallets.length && <p className="muted small">Attach a wallet in the Wallets section below to start.</p>}
                  </div>
                </details>
                {auction && <p className="muted small">Meme.Cooking is an auction: deposits count until the countdown ends; withdrawing before then costs 2%.</p>}
                <Field label={side === "buy" ? (auction ? "Deposit per wallet" : `Spend per wallet`) : (auction ? "Withdraw from each wallet's deposit" : "Sell from each wallet")} extra={side === "sell" && auction ? "percent of each wallet's deposit" : side === "sell" && sel.length ? `${compact(selTok)} tokens selected` : side === "buy" && sel.length ? `${fmt(selSol, 4)} ${cur} available` : undefined}>
                  <UnitInput unit={side === "buy" ? cur : "%"} value={amount} onChange={(e) => setAmount(e.target.value)} />
                </Field>
                <div className="chips" role="group" aria-label="Preset amounts">
                  {presets.map((p) => <button key={p} type="button" className={`chip ${amount === p ? "on" : ""}`} aria-pressed={amount === p} onClick={() => setAmount(p)}>{p}{side === "sell" ? "%" : ""}</button>)}
                </div>
                <div className="kv small">
                  <div className="k">Wallets</div>
                  <div className="v">{sel.length ? <span className="mono">{sel.length}</span> : <button type="button" className="btn sm ghost" style={{ height: 22, padding: "0 6px" }} onClick={() => setSelected(new Set(ws.wallets.map((w: any) => w.id)))} disabled={!ws.wallets.length}>Select all {ws.wallets.length}</button>}</div>
                  <div className="k">{side === "buy" ? "Spend before fees" : "Selling"}</div>
                  <div className="v mono">{side === "buy" ? `${fmt(sel.length * amt, 6)} ${cur}` : `${fmt(amt, 2)}% ≈ ${compact((selTok * Math.min(amt, 100)) / 100)} tokens`}</div>
                  <div className="k">Execution</div>
                  <div className="v">{mode === "stagger" ? `One at a time · ${stagger} ms` : "All at once"} · {slippage}% slippage</div>
                  <div className="k">Fees & checks</div><div className="v">Shown in review</div>
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
                  title: auction ? `${side === "buy" ? "Deposit into" : "Withdraw from"} the $${l.symbol} auction` : `${side === "buy" ? "Buy" : "Sell"} $${l.symbol}`, path: `/launches/${id}/trade`,
                  body: { side, wallet_ids: sel, amount: amt, mode, stagger_ms: +stagger, slippage_pct: +slippage },
                })}>
                  {sel.length ? `Review ${side} · ${sel.length} wallet${sel.length === 1 ? "" : "s"}` : "Select wallets to trade"}
                </button>
                <p className="muted small">The next step shows the server's preview, fees and any blockers. Nothing is sent until you confirm.</p>
              </div>
            </Card>
  ) : null;
  const transparencyCard = (
    <>
          {ws.transparency ? <TransparencyPanel t={ws.transparency} network={l.network} symbol={l.symbol}
            actions={live && l.mint_address && !extra ? (
              <button className="btn sm secondary" onClick={() => setSupplyOpen(true)} title="Split this token from one wallet across every wallet of a folder">
                <Icon name="send" size={14} />Distribute supply
              </button>
            ) : undefined} /> : (
            <Card title="Supply transparency" icon="pie"><p className="muted small">Measured once the token is live and chain data loads.</p></Card>
          )}
    </>
  );
  const opsCard = live && extra ? (auction ? (
            <Card title="Claim" icon="coins" sub="After the auction ends">
              <div className="stack">
                <p className="muted small">Once the countdown ends, claim your share of the tokens, or your NEAR back if the soft cap wasn't reached.</p>
                <Field label="Wallet">
                  <select value={claimWallet} onChange={(e) => setClaimWallet(e.target.value)}>
                    <option value="">Choose a wallet…</option>
                    {ws.wallets.map((w: any) => <option key={w.id} value={w.id}>{w.label || short(w.address)}</option>)}
                  </select>
                </Field>
                <button className="btn block" disabled={!claimWallet} onClick={() => setDialog({ title: "Claim tokens or refund", path: `/launches/${id}/claim-fees`, body: { wallet_id: claimWallet } })}>
                  <Icon name="coins" size={15} />Review claim</button>
              </div>
            </Card>) : null) : live ? (
            <Card title="Token operations" icon="layers" sub={claimInApp ? "Claim, burn or send from your wallets" : "Burn or send from your wallets"}>
              <div className="stack">
                {claimMode === "external" && <Callout><b>{experience.rewardTitle}.</b> {experience.claimCopy} {claimUrl && <a href={claimUrl} target="_blank" rel="noreferrer">Open {claimUrl.includes("bags") ? "Bags" : "launchpad"}</a>}</Callout>}
                {claimMode === "automatic" && <Callout><b>{experience.rewardTitle}.</b> {experience.claimCopy}</Callout>}
                <Seg full value={effectiveOp} onChange={setOp} options={opOptions} />
                {effectiveOp === "claim" && claimInApp && (
                  <>
                    {l.launchpad === "pons" && <PonsRewards launchId={l.id} />}
                    <p className="muted small">{l.pons_rewards
                      ? "This launch's ETH rewards are split permanently: 94% to the creator and 6% to Helm. The selected wallet pays gas; it cannot change either recipient. First claim also deploys the reward contract."
                      : experience.claimCopy}</p>
                    {l.pons_rewards && <div className="stack tight small">
                      <span>Creator (94%) <Address address={l.pons_rewards.creator} network={l.network} /></span>
                      <span>Helm (6%) <Address address={l.pons_rewards.treasury} network={l.network} /></span>
                      <span>Reward address <Address address={l.pons_rewards.splitter} network={l.network} /></span>
                      <span className="muted">Each party can withdraw independently. Failed payments stay claimable at the reward address. Pons retains its timelocked authority over future reward routing.</span>
                    </div>}
                    <Field label={l.pons_rewards ? "Wallet paying gas" : "Creator wallet"}>
                      <select value={claimWallet} onChange={(e) => setClaimWallet(e.target.value)}>
                        <option value="">Choose…</option>
                        {ws.wallets.map((w: any) => <option key={w.id} value={w.id}>{w.label || short(w.address)}{w.role === "dev" ? " (dev)" : ""}</option>)}
                      </select>
                    </Field>
                    <button className="btn block" disabled={!claimWallet} onClick={() => setDialog({ title: "Claim creator fees", path: `/launches/${id}/claim-fees`, body: { wallet_id: claimWallet } })}>
                      <Icon name="coins" size={15} />Review claim</button>
                  </>
                )}
                {effectiveOp === "burn" && (
                  <>
                    <Callout tone="warn">Burning permanently destroys tokens. It can't be undone.</Callout>
                    <Field label="Share of the wallet's tokens" hint={firstSel ? `From ${firstSel.label || short(firstSel.address)} (first selected wallet) · ${compact(firstSel.tokens)} tokens` : "Select a wallet in the table first"}>
                      <UnitInput unit="%" value={burnPct} onChange={(e) => setBurnPct(e.target.value)} />
                    </Field>
                    <button className="btn danger-ghost block" disabled={!firstSel || !(+burnPct > 0) || +burnPct > 100} onClick={() => setDialog({ title: "Burn tokens (irreversible)", path: `/launches/${id}/burn`, body: { wallet_id: firstSel.id, percent: +burnPct } })}>
                      <Icon name="flame" size={15} />Review burn</button>
                  </>
                )}
                {effectiveOp === "send" && (
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
                {effectiveOp === "distribute" && (
                  <>
                    <p className="muted small">Split ${l.symbol} from one wallet across every managed wallet of a folder — for example your holder wallets. Receiving wallets stay counted in the transparency panel, and locked allocation lines are checked in review.</p>
                    <button className="btn block" disabled={!ws.wallets.length} onClick={() => setSupplyOpen(true)}>
                      <Icon name="send" size={15} />Choose folder and amounts</button>
                    {!ws.wallets.length && <p className="muted small">Attach wallets first.</p>}
                  </>
                )}
              </div>
            </Card>
  ) : null;
  const perfCard = (
          <Card title="Performance" icon="trend" actions={<button className="btn sm secondary" onClick={() => downloadPnl(launchPnlFacts(l, ws.pnl), `${l.symbol}-pnl.png`).catch(e => toast("bad", errMsg(e)))}><Icon name="download" size={13} />Share card</button>}>
            <PnlImage facts={launchPnlFacts(l, ws.pnl)} alt={`P&L card for ${l.name}`} />
            <div className={`num ${flow > 0 ? "good" : flow < 0 ? "bad" : ""}`} style={{ font: "500 26px var(--mono)" }}>
              {signed(flow)} <span className="muted" style={{ fontSize: 14 }}>{cur}</span>
            </div>
            <p className="muted small" style={{ marginTop: 6 }}>{ws.pnl.note}</p>
          </Card>
  );
  const txCard = (
      <Card title="Transactions" icon="activity" flush sub="Latest 50 for this launch, newest first">
        {ws.transactions.length === 0 ? <Empty icon="activity" title="No transactions yet" text="Deploys, trades, claims and transfers for this token appear here." /> : (
          <TableWrap><table>
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
          </table></TableWrap>
        )}
      </Card>
  );
  const position = (
      <Metrics label="Launch figures" items={[
        { label: `Net ${cur} flow`, value: signed(flow), unit: cur, tone: flow > 0 ? "good" : flow < 0 ? "bad" : undefined, hint: `${ws.pnl.confirmed_txs} confirmed tx${ws.pnl.confirmed_txs === 1 ? "" : "s"}` },
        { label: "Supply you hold", value: ws.transparency ? `${fmt(ws.transparency.controlled_pct, 2)}%` : "—", tone: ws.transparency?.warnings?.length ? "warn" : undefined, hint: ws.transparency ? "across attached wallets" : "once the token is live" },
        { label: "Tokens held", value: tokensKnown ? compact(totalTok) : "—", hint: tokensKnown ? `$${l.symbol}` : ws.wallets.length ? "load balances to total" : "attach wallets to total" },
        { label: `${cur} in wallets`, value: nativeKnown ? fmt(totalSol, 4) : "—", unit: nativeKnown ? cur : undefined, hint: nativeKnown ? "for trades and gas" : ws.wallets.length ? "balances not read yet" : "attach wallets to trade and track" },
        { label: "Wallets", value: String(ws.wallets.length), hint: sel.length ? `${sel.length} selected` : "attached" },
      ]} />
  );
  const allocationsCard = <Allocations launch={l} wallets={ws.wallets} onChanged={load} />;
  const terminal = !!l.mint_address;
  // A copied draft shows the market of the token it was copied from, above the usual draft layout.
  const sourceMarket = !l.mint_address && l.status === "draft" && !!l.cloned_from;

  return (
    <>
      <Link to="/launches" className="btn sm ghost workspace-back" style={{ alignSelf: "flex-start", marginLeft: -8, marginBottom: -8 }}><Icon name="arrowleft" size={14} />All launches</Link>

      <header className="page-head workspace-head">
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
              {!l.mint_address && l.cloned_from && <><span aria-hidden="true">·</span><span>copied from <Address address={l.cloned_from.address} type="token" chars={5} network={l.cloned_from.network} /></span></>}
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
          {live && <button className="btn jump-trade" onClick={() => { setMobileView("trade"); requestAnimationFrame(() => { const el = document.getElementById("order-ticket"); el?.scrollIntoView({ behavior: "smooth", block: "start" }); el?.focus({ preventScroll: true }); }); }}><Icon name="zap" size={14} />Trade</button>}
          <button className="btn icon secondary" onClick={load} aria-label="Refresh now" title="Refresh now"><Icon name="refresh" size={14} /></button>
          {(l.status === "draft" || l.imported) && <DeleteDraft id={l.id} name={l.name} onDeleted={() => { toast("good", "Draft deleted"); nav("/launches"); }} />}
          <button className="btn secondary" onClick={() => api(`/launches/${id}/${l.status === "archived" ? "restore" : "archive"}`, { method: "POST", body: {} })
            .then(() => { toast("good", l.status === "archived" ? "Launch restored" : "Launch archived"); load(); }).catch((e) => toast("bad", errMsg(e)))}>
            <Icon name="archive" size={14} />{l.status === "archived" ? "Restore" : "Archive"}
          </button>
        </div>
      </header>

      {!terminal && <Pipeline status={l.status} />}

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
      {l.status === "failed" && l.deploy_error && <Callout tone="err">{l.deploy_error}.</Callout>}
      {l.status === "deploying" && (l.deploy_note
        ? <Callout tone="warn"><b>Launch setup needs attention.</b> {l.deploy_note}</Callout>
        : l.mint_address || l.deploy_tx
        ? <Callout tone="warn"><b>Sent, not confirmed yet.</b> The create transaction didn't confirm in time, so it may still land. Helm checks for the token every 10 seconds. Don't deploy again until this resolves; that would create a second token.
          {l.deploy_tx && <> <a href={chain.explorer.tx + l.deploy_tx} target="_blank" rel="noreferrer noopener">View the deploy transaction<Icon name="ext" size={11} /></a></>}</Callout>
        : <Callout><b>Deploying.</b> Waiting for the chain to confirm; this page refreshes every 10 seconds.</Callout>)}

      {terminal ? (
        <>
          <nav className="terminal-mobile-nav" aria-label="Terminal view">
            {(["chart", ...(live ? ["trade"] : []), "activity"] as const).map((view) => <button type="button" key={view} aria-pressed={mobileView === view}
              className={mobileView === view ? "on" : ""} onClick={() => { setMobileView(view as "chart" | "trade" | "activity"); if (view === "activity") setTab("tx"); }}>
              <Icon name={view === "chart" ? "trend" : view === "trade" ? "zap" : "activity"} size={15} />{view[0].toUpperCase() + view.slice(1)}
            </button>)}
          </nav>
          <MarketTerminal key={`${l.id}:${l.network}:${l.mint_address}`} launchId={l.id} network={l.network} token={l.mint_address} symbol={l.symbol} ticket={ticketCard} mobileView={mobileView} />
          <div className={`terminal-records terminal-records-${mobileView}`}>
          <section className="your-position" aria-labelledby="pos-h">
            <h2 id="pos-h" className="sec-kicker"><Icon name="wallet" size={12} />Your position · from your Helm wallets</h2>
            {position}
          </section>
          <WsTabs on={tab} setOn={setTab} tabs={[
            ["wallets", `Wallets${ws.wallets.length ? ` · ${ws.wallets.length}` : ""}`, walletsCard],
            ...(extra ? [] : [["allocations", "Allocation & vesting", allocationsCard]] as [string, string, React.ReactNode][]),
            ["tx", `Transactions${ws.transactions.length ? ` · ${ws.transactions.length}` : ""}`, txCard],
            ["supply", "Holders", transparencyCard],
            ["protocol", "Launch details", <Card title={experience.mode}><p>{experience.description}</p><div className="chips">{(chosenSettings.length ? chosenSettings.map(([key, value]) => launchSetting(key, value)) : experience.features).map(f => <span className="chip static" key={f}>{f}</span>)}</div></Card>],
            ...(opsCard ? [["ops", "Token operations", opsCard] as [string, string, React.ReactNode]] : []),
            ["perf", "Performance", perfCard],
          ]} />
          </div>
        </>
      ) : (
        <>
          {sourceMarket && <MarketTerminal key={`${l.id}:${l.cloned_from.network}:${l.cloned_from.address}`} launchId={l.id} network={l.cloned_from.network} token={l.cloned_from.address} symbol="Copied token" />}
          {position}
          <div className="grid g-main">
            <div className="col">{walletsCard}</div>
            <div className="col">{ticketCard}{transparencyCard}{opsCard}{perfCard}</div>
          </div>
          <section aria-labelledby="alloc-h" className="stack">
            <h2 id="alloc-h" className="sec-kicker"><Icon name="pie" size={12} />Allocation, vesting and launch protections</h2>
            {extra ? <p className="muted small">Allocation plans aren't available on {chain.name} yet.</p> : allocationsCard}
          </section>
          {txCard}
        </>
      )}

      {dialog && <SpendDialog {...dialog} network={l.network} onClose={() => setDialog(null)} onDone={load} />}
      {consolidateOpen && <Distribute mode="consolidate" network={l.network} wallets={allWallets.filter(w => attached.has(w.id))} selected={sel.length ? sel : ws.wallets.filter((w:any) => w.role !== "dev").map((w:any) => w.id)} initialAsset={l.mint_address ?? "native"} onClose={() => setConsolidateOpen(false)} onReview={r => { setConsolidateOpen(false); setDialog(r); }} />}
      {supplyOpen && <DistributeSupply launch={l} wallets={allWallets} attachedIds={ws.wallets.map((w:any) => w.id)}
        devWalletId={ws.wallets.find((w: any) => w.role === "dev")?.id}
        onClose={() => setSupplyOpen(false)}
        onReview={(r) => { setSupplyOpen(false); setDialog(r); }} />}
    </>
  );
}
