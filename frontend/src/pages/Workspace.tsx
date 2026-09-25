import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError, fmt, short, solscan } from "../api";
import SpendDialog from "../components/SpendDialog";

type Dialog = { title: string; path: string; body: Record<string, unknown> } | null;

function TransparencyPanel({ t }: { t: any }) {
  const hot = t.warnings.length > 0;
  return (
    <div className="card">
      <h2>Supply transparency</h2>
      <p className="muted" style={{ marginTop: 0 }}>How much of this token your own wallets control, next to the real holder distribution.</p>
      <div className="row between">
        <span>Your wallets hold</span>
        <strong className={hot ? "bad" : ""}>{fmt(t.controlled_pct, 2)}% of supply</strong>
      </div>
      <div className={`bar ${hot ? "hot" : ""}`} style={{ margin: "6px 0 12px" }}><div style={{ width: `${Math.min(t.controlled_pct, 100)}%` }} /></div>
      {t.warnings.map((w: string) => <div key={w} className="banner warn">{w}</div>)}
      <h3>Top holders</h3>
      <table>
        <thead><tr><th>Owner</th><th>Amount</th><th>% supply</th><th /></tr></thead>
        <tbody>
          {t.top_holders.map((h: any) => (
            <tr key={h.owner}>
              <td className="mono">{short(h.owner)}</td>
              <td>{fmt(h.amount, 0)}</td>
              <td>{fmt(h.pct, 2)}%</td>
              <td>{h.is_yours && <span className="badge yours">yours</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function downloadPnlCard(launch: any, pnl: any) {
  const c = document.createElement("canvas");
  c.width = 1200; c.height = 630;
  const g = c.getContext("2d")!;
  g.fillStyle = "#0d0f14"; g.fillRect(0, 0, 1200, 630);
  g.fillStyle = "#e6e8ee"; g.font = "700 56px Inter, sans-serif"; g.fillText(`${launch.name}  $${launch.symbol}`, 60, 130);
  g.fillStyle = pnl.net_sol_flow >= 0 ? "#35d39a" : "#ff6b81"; g.font = "700 120px Inter, sans-serif";
  g.fillText(`${pnl.net_sol_flow >= 0 ? "+" : ""}${fmt(pnl.net_sol_flow, 3)} SOL`, 60, 320);
  g.fillStyle = "#8b93a7"; g.font = "28px Inter, sans-serif";
  g.fillText(`Net SOL flow · ${pnl.confirmed_txs} confirmed transactions`, 60, 380);
  g.fillText("Unsold tokens are not valued. Fees included.", 60, 420);
  g.fillText("Made with Helm Terminals", 60, 570);
  const a = document.createElement("a");
  a.download = `${launch.symbol}-pnl.png`; a.href = c.toDataURL("image/png"); a.click();
}

export default function Workspace() {
  const { id } = useParams();
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
  const [claimWallet, setClaimWallet] = useState("");
  const [burnPct, setBurnPct] = useState("10");
  const [sendTo, setSendTo] = useState("");
  const [sendPct, setSendPct] = useState("10");

  const load = useCallback(async () => {
    try {
      const [w, all] = await Promise.all([api(`/launches/${id}/workspace`), api<any[]>("/wallets")]);
      setWs(w);
      setAllWallets(all.filter((x) => x.kind === "managed"));
      setError("");
    } catch (e) { setError((e as ApiError).detail); }
  }, [id]);

  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);

  if (!ws) return <>{error ? <div className="banner err">{error}</div> : <p className="muted">Loading…</p>}</>;
  const l = ws.launch;
  const live = l.status === "live";
  const sel = [...selected];
  const toggle = (wid: string) => setSelected((s) => { const n = new Set(s); n.has(wid) ? n.delete(wid) : n.add(wid); return n; });
  const attached = new Set(ws.wallets.map((w: any) => w.id));

  return (
    <>
      <div className="row between">
        <h1>{l.name} <span className="muted">${l.symbol}</span> <span className={`badge ${l.status}`}>{l.status}</span></h1>
        <div className="row">
          {l.mint_address && <a className="mono" href={`https://solscan.io/token/${l.mint_address}`} target="_blank" rel="noreferrer">{short(l.mint_address)}</a>}
          <button className="ghost" onClick={() => api(`/launches/${id}/${l.status === "archived" ? "restore" : "archive"}`, { method: "POST", body: {} }).then(load)}>
            {l.status === "archived" ? "Restore" : "Archive"}
          </button>
        </div>
      </div>
      {error && <div className="banner err">{error}</div>}
      {ws.rpc_error && <div className="banner warn">Chain data is partly unavailable: {ws.rpc_error}</div>}

      {(l.status === "draft" || l.status === "failed") && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2>Deploy</h2>
          <p className="muted">
            {l.dev_wallet_id ? `Dev buy: ${l.dev_buy_sol} SOL.` : "Set a dev wallet on the launch before deploying."} You will see a full preflight, including estimated cost, before anything is sent.
          </p>
          <button disabled={!l.dev_wallet_id} onClick={() => setDialog({ title: `Deploy ${l.name}`, path: `/launches/${id}/deploy`, body: {} })}>Review deployment</button>
        </div>
      )}

      {l.status === "deploying" && <div className="banner info">Deploying… this page refreshes automatically.</div>}

      <div className="grid cols2">
        <div className="stack" style={{ gap: 16 }}>
          <div className="card">
            <div className="row between">
              <h2>Wallets</h2>
              <div className="row">
                <select value={attachId} onChange={(e) => setAttachId(e.target.value)}>
                  <option value="">Attach wallet…</option>
                  {allWallets.filter((w) => !attached.has(w.id)).map((w) => <option key={w.id} value={w.id}>{w.label || short(w.address)}</option>)}
                </select>
                <button className="ghost" disabled={!attachId} onClick={() => api(`/launches/${id}/wallets`, { body: { wallet_ids: [attachId] } }).then(() => { setAttachId(""); load(); }).catch((e) => setError(e.detail))}>Attach</button>
              </div>
            </div>
            <table>
              <thead><tr><th /><th>Wallet</th><th>Role</th><th>SOL</th><th>Tokens</th></tr></thead>
              <tbody>
                {ws.wallets.map((w: any) => (
                  <tr key={w.id}>
                    <td><input type="checkbox" checked={selected.has(w.id)} onChange={() => toggle(w.id)} /></td>
                    <td>{w.label || short(w.address)}</td>
                    <td><span className="badge">{w.role}</span></td>
                    <td>{fmt(w.sol)}</td>
                    <td>{fmt(w.tokens, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {ws.wallets.length === 0 && <p className="muted">No wallets attached yet.</p>}
          </div>

          {live && (
            <div className="card stack">
              <h2>Trade</h2>
              <div className="banner info" style={{ marginBottom: 0 }}>Manual trades from the selected wallets. You approve each order.</div>
              <div className="row">
                <label>Side<select value={side} onChange={(e) => setSide(e.target.value as "buy" | "sell")}><option value="buy">Buy</option><option value="sell">Sell</option></select></label>
                <label>{side === "buy" ? "SOL per wallet" : "% of tokens"}<input value={amount} onChange={(e) => setAmount(e.target.value)} style={{ width: 90 }} /></label>
                <label>Mode<select value={mode} onChange={(e) => setMode(e.target.value as "stagger" | "parallel")}><option value="stagger">Stagger</option><option value="parallel">Parallel</option></select></label>
                {mode === "stagger" && <label>Delay (ms)<input value={stagger} onChange={(e) => setStagger(e.target.value)} style={{ width: 90 }} /></label>}
                <label>Slippage %<input value={slippage} onChange={(e) => setSlippage(e.target.value)} style={{ width: 70 }} /></label>
              </div>
              <button disabled={!sel.length} onClick={() => setDialog({
                title: `${side === "buy" ? "Buy" : "Sell"} ${l.symbol}`, path: `/launches/${id}/trade`,
                body: { side, wallet_ids: sel, amount: +amount, mode, stagger_ms: +stagger, slippage_pct: +slippage },
              })}>Review {side} for {sel.length} wallet{sel.length === 1 ? "" : "s"}</button>
            </div>
          )}
        </div>

        <div className="stack" style={{ gap: 16 }}>
          {ws.transparency ? <TransparencyPanel t={ws.transparency} /> : (
            <div className="card"><h2>Supply transparency</h2><p className="muted">Available once the token is live.</p></div>
          )}

          {live && (
            <div className="card stack">
              <h2>Token operations</h2>
              <div className="row">
                <select value={claimWallet} onChange={(e) => setClaimWallet(e.target.value)}>
                  <option value="">Creator wallet…</option>
                  {ws.wallets.map((w: any) => <option key={w.id} value={w.id}>{w.label || short(w.address)}</option>)}
                </select>
                <button className="ghost" disabled={!claimWallet} onClick={() => setDialog({ title: "Claim creator fees", path: `/launches/${id}/claim-fees`, body: { wallet_id: claimWallet } })}>Claim creator fees</button>
              </div>
              <div className="row">
                <input value={burnPct} onChange={(e) => setBurnPct(e.target.value)} style={{ width: 70 }} /> <span className="muted">% of first selected wallet's tokens</span>
                <button className="danger" disabled={!sel.length} onClick={() => setDialog({ title: "Burn tokens (irreversible)", path: `/launches/${id}/burn`, body: { wallet_id: sel[0], percent: +burnPct } })}>Burn</button>
              </div>
              <div className="row">
                <input placeholder="Recipient address" value={sendTo} onChange={(e) => setSendTo(e.target.value)} style={{ flex: 1 }} />
                <input value={sendPct} onChange={(e) => setSendPct(e.target.value)} style={{ width: 70 }} /><span className="muted">%</span>
                <button className="ghost" disabled={!sel.length || !sendTo} onClick={() => setDialog({ title: "Send tokens", path: `/launches/${id}/send`, body: { wallet_id: sel[0], recipient: sendTo, percent: +sendPct } })}>Send</button>
              </div>
              <button className="ghost" disabled title="Planned: Streamflow integration">Lock supply (planned)</button>
            </div>
          )}

          <div className="card">
            <div className="row between">
              <h2>Performance</h2>
              <button className="ghost" onClick={() => downloadPnlCard(l, ws.pnl)}>Export P&amp;L card</button>
            </div>
            <div className="stat"><div className={`v ${ws.pnl.net_sol_flow >= 0 ? "good" : "bad"}`}>{fmt(ws.pnl.net_sol_flow)} SOL</div><div className="l">{ws.pnl.note}</div></div>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Transactions</h2>
        <table>
          <thead><tr><th>Time</th><th>Type</th><th>Status</th><th>SOL</th><th>Signature</th></tr></thead>
          <tbody>
            {ws.transactions.map((t: any) => (
              <tr key={t.id}>
                <td className="muted">{new Date(t.created_at + "Z").toLocaleTimeString()}</td>
                <td>{t.kind}</td>
                <td><span className={`badge ${t.status}`}>{t.status}</span></td>
                <td>{fmt(t.sol_delta)}</td>
                <td className="mono">{t.signature ? <a href={solscan(t.signature)} target="_blank" rel="noreferrer">{short(t.signature)}</a> : <span className="bad">{t.error}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {ws.transactions.length === 0 && <p className="muted">No transactions yet.</p>}
      </div>

      {dialog && <SpendDialog {...dialog} onClose={() => setDialog(null)} onDone={load} />}
    </>
  );
}
