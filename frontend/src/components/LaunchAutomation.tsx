import { useCallback, useEffect, useState } from "react";
import { api, errMsg, fmt, short } from "../api";
import { chainOf, explorerUrl } from "../chains";
import {
  Callout, Card, Empty, Field, Icon, Loading, parseDate, Seg, Spinner, StatusBadge, TableWrap, UnitInput, useToast,
} from "./ui";

/* Position plans for one launch: finite scheduled accumulation or staged exits from one wallet you pick. */

interface Plan {
  id: string; status: string; side: "buy" | "sell"; wallet_id: string | null; legacy_multi_wallet: boolean;
  order_sol: number | null; budget: number | null; sell_amount: number | null; sell_per_order: number | null;
  sell_percent: number | null; total_orders: number; runs_completed: number; interval_seconds: number;
  slippage_pct: number; max_price_impact_pct: number | null; expires_at: string | null; next_run_at: string | null;
  error: string | null; created_at: string; coin: string;
}
interface Detail extends Plan {
  executions: { job_id: string; order_number: number; status: string; error: string | null; at: string;
    transactions: { kind: string; status: string; signature: string | null; error: string | null; at: string }[] }[];
  history: { event: string; at: string; details: Record<string, unknown> }[];
}
interface W { id: string; address: string; label: string; sol: number; tokens: number; role: string }

const ACTIVE = new Set(["scheduled", "executing", "paused"]);
const key = () => globalThis.crypto?.randomUUID?.() ?? `plan-${Date.now()}-${Math.random().toString(36).slice(2)}`;
const when = (s: string | null) => (s ? parseDate(s).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—");
const LABELS: Record<string, string> = {
  strategy: "Plan", wallet: "Wallet", budget: "Total budget", amount_per_order: "Per order", sell_amount: "Total to sell",
  sell_per_order: "Per order", wallet_token_balance: "Wallet holds", maximum_executions: "Maximum executions",
  interval_minutes: "Every (minutes)", first_order_in_minutes: "First order in (minutes)", expires_in_hours: "Expires in (hours)",
  slippage_pct: "Slippage limit (%)", max_price_impact_pct: "Price-impact limit (%)", price_impact_check: "Price-impact check",
  platform_fee: "Helm fee", estimated_helm_fee: "Estimated Helm fee", estimated_helm_fee_max: "Helm fee, at most",
  estimated_network_fees_max: "Network fees, at most", network_fees: "Network fees",
};
const SHOWN = Object.keys(LABELS);

export default function LaunchAutomation({ launchId, network, symbol, wallets, live }: {
  launchId: string; network: string; symbol: string; wallets: W[]; live: boolean;
}) {
  const toast = useToast();
  const coin = chainOf(network).native;
  const [plans, setPlans] = useState<Plan[] | null>(null);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const load = useCallback(() => {
    api<Plan[]>(`/launches/${launchId}/automations`).then((p) => { setPlans(p); setErr(""); }).catch((e) => setErr(errMsg(e)));
  }, [launchId]);
  useEffect(() => { load(); const t = setInterval(() => { if (document.visibilityState === "visible") load(); }, 15_000); return () => clearInterval(t); }, [load]);
  const active = plans?.find((p) => ACTIVE.has(p.status));
  const act = (id: string, verb: "pause" | "resume" | "cancel") =>
    api(`/automations/${id}/${verb}`, { body: {} }).then(() => { toast("good", `Plan ${verb === "cancel" ? "cancelled" : verb + "d"}`); load(); })
      .catch((e) => toast("bad", errMsg(e)));

  return (
    <div className="stack">
      <Callout tone="info" icon="info">
        Automation manages <b>your own position</b>: a finite schedule of buys or sells from one wallet you choose, with a
        budget, limits and an expiry. It never alternates buying and selling, never trades several wallets together, and
        doesn't create volume or holders. One plan per launch at a time; it stops at the first failed order.
      </Callout>
      {err && !plans ? <Callout tone="err">{err}</Callout> : !plans ? <Loading /> : (
        <Card title="Plans" icon="calendar" sub={`${plans.length} for $${symbol}`} flush>
          {plans.length === 0 ? <Empty icon="calendar" title="No plans yet" text={live ? "Create one below." : "Plans start once the token is live."} /> : (
            <TableWrap><table className="rtable">
              <caption className="sr-only">Position plans</caption>
              <thead><tr><th>Plan</th><th>Progress</th><th>Limits</th><th>Next</th><th className="r"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>{plans.map((p) => {
                const w = wallets.find((x) => x.id === p.wallet_id);
                return (
                  <tr key={p.id}>
                    <td className="span">
                      <div className="row nowrap" style={{ gap: 8 }}><StatusBadge status={p.status} /><b>{p.side === "buy" ? "Scheduled accumulation" : "Staged exit"}</b></div>
                      <div className="muted small">
                        {p.legacy_multi_wallet ? "Older plan across several wallets" : w ? (w.label || short(w.address)) : "One wallet"}
                        {" · "}{p.side === "buy" ? `${fmt(p.budget ?? 0, 6)} ${coin} budget, ${fmt(p.order_sol ?? 0, 6)} per order` :
                          p.sell_amount != null ? `${fmt(p.sell_amount, 4)} $${symbol}, ${fmt(p.sell_per_order ?? 0, 4)} per order` : `${p.sell_percent}% of remaining per order`}
                      </div>
                      {p.error && <div className="small" style={{ color: p.status === "failed" ? "var(--bad)" : "var(--warn)" }}>{p.error}</div>}
                    </td>
                    <td data-label="Progress" className="mono">{p.runs_completed} / {p.total_orders}</td>
                    <td data-label="Limits" className="small muted">slip {p.slippage_pct}%{p.max_price_impact_pct != null ? ` · impact ${p.max_price_impact_pct}%` : ""}<br />every {Math.round(p.interval_seconds / 60)} min{p.expires_at ? ` · ends ${when(p.expires_at)}` : ""}</td>
                    <td data-label="Next" className="small">{p.status === "scheduled" ? when(p.next_run_at) : "—"}</td>
                    <td className="r act"><span className="row nowrap" style={{ gap: 4, justifyContent: "flex-end" }}>
                      <button className="btn sm ghost" onClick={() => setOpen(open === p.id ? null : p.id)}>{open === p.id ? "Hide" : "Details"}</button>
                      {p.status === "scheduled" && <button className="btn sm ghost" onClick={() => act(p.id, "pause")}>Pause</button>}
                      {p.status === "paused" && !p.legacy_multi_wallet && <button className="btn sm ghost" onClick={() => act(p.id, "resume")}>Resume</button>}
                      {(p.status === "scheduled" || p.status === "paused") && <button className="btn sm danger-ghost" onClick={() => act(p.id, "cancel")}>Cancel</button>}
                    </span></td>
                  </tr>
                );
              })}</tbody>
            </table></TableWrap>
          )}
        </Card>
      )}
      {open && <PlanDetail id={open} network={network} onChanged={load} />}
      {live && (active ? (
        <Callout tone="info">This launch has an active {active.side === "buy" ? "accumulation" : "exit"} plan. Finish, cancel or let it expire before creating another.</Callout>
      ) : <NewPlan launchId={launchId} network={network} symbol={symbol} wallets={wallets} onCreated={load} />)}
    </div>
  );
}

function PlanDetail({ id, network, onChanged }: { id: string; network: string; onChanged: () => void }) {
  const toast = useToast();
  const [d, setD] = useState<Detail | null>(null);
  const [edit, setEdit] = useState<Record<string, string>>({});
  const load = useCallback(() => { api<Detail>(`/automations/${id}`).then(setD).catch((e) => toast("bad", errMsg(e))); }, [id, toast]);
  useEffect(load, [load]);
  if (!d) return <Loading rows={2} />;
  const editable = d.status === "scheduled" || d.status === "paused";
  const save = () => {
    const body: Record<string, number> = {};
    Object.entries(edit).forEach(([k, v]) => { if (v !== "") body[k] = Number(v); });
    api(`/automations/${id}`, { method: "PATCH", body }).then(() => { toast("good", "Plan updated"); setEdit({}); load(); onChanged(); })
      .catch((e) => toast("bad", errMsg(e)));
  };
  const f = (k: string, label: string, unit: string, hint: string) => (
    <Field label={label} hint={hint}><UnitInput unit={unit} value={edit[k] ?? ""} placeholder="unchanged" onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} /></Field>
  );
  return (
    <Card title="Plan details" icon="layers" sub="Every order, its transactions, and every change, with times">
      <div className="stack">
        {editable && (
          <details>
            <summary className="small" style={{ cursor: "pointer" }}>Tighten this plan</summary>
            <div className="grid g-form" style={{ marginTop: 10 }}>
              {f("total_orders", "Orders", "max", `now ${d.total_orders}; fewer only`)}
              {f("slippage_pct", "Slippage", "%", `now ${d.slippage_pct}; lower only`)}
              {d.max_price_impact_pct != null && f("max_price_impact_pct", "Price impact", "%", `now ${d.max_price_impact_pct}; lower only`)}
              {f("interval_minutes", "Interval", "min", `now ${Math.round(d.interval_seconds / 60)}; longer only`)}
              {d.expires_at && f("expires_in_hours", "Expires in", "h", "earlier only")}
            </div>
            <button className="btn sm" style={{ marginTop: 8 }} disabled={!Object.values(edit).some((v) => v !== "")} onClick={save}>Save changes</button>
          </details>
        )}
        <h3>Executions</h3>
        {d.executions.length === 0 ? <p className="muted small">No orders yet.</p> : d.executions.map((e) => (
          <div key={e.job_id} className="stack tight" style={{ borderTop: "1px solid var(--line)", paddingTop: 8 }}>
            <div className="row between"><span>Order {e.order_number} · <span className="muted small">{when(e.at)}</span></span><StatusBadge status={e.status} /></div>
            {e.transactions.map((t, i) => (
              <div key={i} className="row between small">
                <span className="muted">{t.kind.replace(/_/g, " ")}{t.error ? `: ${t.error}` : ""}</span>
                {t.signature ? <a className="mono" href={explorerUrl(network, "tx", t.signature)} target="_blank" rel="noreferrer">{short(t.signature)} <Icon name="ext" size={11} /></a> : <span className="muted">not sent</span>}
              </div>
            ))}
          </div>
        ))}
        <h3>History</h3>
        <ol className="stack tight" style={{ margin: 0, paddingLeft: 18 }}>
          {d.history.map((h, i) => <li key={i} className="small"><span className="mono muted">{parseDate(h.at).toLocaleString()}</span> · {h.event.replace(/_/g, " ")}{h.details?.changes ? ` (${Object.keys(h.details.changes as object).join(", ")})` : ""}</li>)}
        </ol>
      </div>
    </Card>
  );
}

function NewPlan({ launchId, network, symbol, wallets, onCreated }: {
  launchId: string; network: string; symbol: string; wallets: W[]; onCreated: () => void;
}) {
  const toast = useToast();
  const coin = chainOf(network).native;
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [walletId, setWalletId] = useState("");
  const [budget, setBudget] = useState("0.2");
  const [sellAmount, setSellAmount] = useState("");
  const [orders, setOrders] = useState("4");
  const [interval, setIntervalMin] = useState("30");
  const [start, setStart] = useState("5");
  const [slippage, setSlippage] = useState("5");
  const [impact, setImpact] = useState("5");
  const [expiry, setExpiry] = useState("24");
  const [preview, setPreview] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const wallet = wallets.find((w) => w.id === walletId);
  const body = () => ({
    side, wallet_id: walletId, ...(side === "buy" ? { budget: +budget } : { sell_amount: +sellAmount }),
    total_orders: +orders, interval_minutes: +interval, start_in_minutes: +start, slippage_pct: +slippage,
    max_price_impact_pct: +impact, expires_in_hours: +expiry,
  });
  const review = () => {
    setBusy(true); setError(""); setPreview(null);
    api(`/launches/${launchId}/automations`, { body: body() }).then(setPreview).catch((e) => setError(errMsg(e))).finally(() => setBusy(false));
  };
  const confirm = () => {
    setBusy(true); setError("");
    api(`/launches/${launchId}/automations`, { body: { ...body(), confirm: true, preflight_hash: preview.preflight_hash, idempotency_key: key() } })
      .then(() => { toast("good", "Plan scheduled"); setPreview(null); onCreated(); }).catch((e) => setError(errMsg(e))).finally(() => setBusy(false));
  };
  const reset = () => setPreview(null);
  return (
    <Card title="New plan" icon="plus" sub="Every limit is explicit; nothing runs until you confirm the preview">
      <div className="stack">
        <Seg full className="trade" value={side} onChange={(s) => { setSide(s); reset(); }} options={[["buy", "Scheduled accumulation"], ["sell", "Staged exit"]]} />
        <div className="grid g-form">
          <Field label="Wallet" hint="One wallet you control, attached to this launch">
            <select value={walletId} onChange={(e) => { setWalletId(e.target.value); reset(); }}>
              <option value="">Choose one…</option>
              {wallets.map((w) => <option key={w.id} value={w.id}>{w.label || short(w.address)} · {fmt(w.sol, 4)} {coin} · {fmt(w.tokens, 2)} ${symbol}</option>)}
            </select>
          </Field>
          {side === "buy"
            ? <Field label="Total budget" hint={`Split evenly across the orders`}><UnitInput unit={coin} value={budget} onChange={(e) => { setBudget(e.target.value); reset(); }} /></Field>
            : <Field label="Total to sell" hint={wallet ? `Wallet holds ${fmt(wallet.tokens, 4)} $${symbol}` : "Pick the wallet first"}><UnitInput unit={`$${symbol}`} value={sellAmount} onChange={(e) => { setSellAmount(e.target.value); reset(); }} /></Field>}
          <Field label="Maximum executions" hint="2 to 12 orders"><input inputMode="numeric" value={orders} onChange={(e) => { setOrders(e.target.value); reset(); }} /></Field>
          <Field label="Every"><UnitInput unit="min" value={interval} onChange={(e) => { setIntervalMin(e.target.value); reset(); }} /></Field>
          <Field label="First order in"><UnitInput unit="min" value={start} onChange={(e) => { setStart(e.target.value); reset(); }} /></Field>
          <Field label="Expires in" hint="Nothing runs after this"><UnitInput unit="hours" value={expiry} onChange={(e) => { setExpiry(e.target.value); reset(); }} /></Field>
          <Field label="Slippage limit"><UnitInput unit="%" value={slippage} onChange={(e) => { setSlippage(e.target.value); reset(); }} /></Field>
          <Field label="Price-impact limit"><UnitInput unit="%" value={impact} onChange={(e) => { setImpact(e.target.value); reset(); }} /></Field>
        </div>
        {error && <Callout tone="err">{error}</Callout>}
        {!preview ? (
          <button className="btn" disabled={!walletId || busy || (side === "buy" ? !(+budget > 0) : !(+sellAmount > 0))} onClick={review}>{busy ? <Spinner /> : <Icon name="eye" size={15} />}Review plan</button>
        ) : (
          <div className="stack">
            <div className="kv">
              {SHOWN.filter((k) => preview.preflight[k] !== undefined).map((k) => {
                const v = preview.preflight[k];
                return <div key={k} className="row between small" style={{ gap: 12 }}><span className="muted">{LABELS[k]}</span>
                  <span style={{ textAlign: "right" }}>{typeof v === "object" ? `${v.label} · ${short(v.address)}` : typeof v === "number" ? fmt(v, 9) : String(v)}</span></div>;
              })}
            </div>
            <Callout tone="info">{preview.preflight.what_this_is}</Callout>
            {preview.preflight.warnings.map((w: string) => <Callout key={w} tone="warn">{w}</Callout>)}
            <div className="row">
              <button className="btn ghost" onClick={reset}>Edit</button>
              <button className="btn" disabled={busy || preview.preflight.warnings.length > 0} onClick={confirm}>{busy ? <Spinner /> : <Icon name="check" size={15} />}Confirm and schedule</button>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

