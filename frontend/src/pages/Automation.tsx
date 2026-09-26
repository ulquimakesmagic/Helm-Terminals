import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, errMsg, fmt, short } from "../api";
import { Address, Callout, Card, Empty, Field, Icon, Loading, PageHeader, StatusBadge, useToast } from "../components/ui";

interface Launch { id: string; name: string; symbol: string; network: string; status: string; mint_address: string | null }
interface Wallet { id: string; address: string; label: string; sol: number; tokens: number; role: string }
interface Automation {
  id: string; launch_id: string; launch_name: string | null; launch_symbol: string | null; network: string;
  status: string; side: "buy" | "sell"; wallet_count: number; order_sol: number; sell_percent: number;
  total_orders: number; interval_seconds: number; slippage_pct: number; planned_trade_sol: number | null;
  planned_position_exit_pct: number | null; runs_completed: number; next_run_at: string;
  last_job_id: string | null; error: string | null; created_at: string;
}
interface Preview {
  preflight_hash: string;
  preflight: {
    launch: string; strategy: string; side?: "buy" | "sell";
    wallets: { label: string; address: string; token_balance?: number }[];
    amount_sol_per_wallet_per_order?: number; sell_percent_of_remaining_per_order?: number;
    maximum_position_exit_percent?: number; number_of_orders: number; interval_minutes: number;
    first_order_in_minutes: number; planned_trade_sol?: number; slippage_pct: number;
    platform_fee: string; network_fees: string; warnings: string[];
  };
}

const active = new Set(["scheduled", "executing", "paused"]);

function idempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ?? `schedule-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function displayTime(value: string) {
  const date = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
  return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export default function Automation() {
  const [search] = useSearchParams();
  const toast = useToast();
  const [launches, setLaunches] = useState<Launch[] | null>(null);
  const [automations, setAutomations] = useState<Automation[] | null>(null);
  const [wallets, setWallets] = useState<Wallet[]>([]);
  const [launchId, setLaunchId] = useState(() => search.get("launch") ?? "");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [amount, setAmount] = useState("0.05");
  const [orders, setOrders] = useState("3");
  const [interval, setInterval] = useState("5");
  const [startDelay, setStartDelay] = useState("1");
  const [slippage, setSlippage] = useState("5");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionId, setActionId] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [launchRows, autoRows] = await Promise.all([api<Launch[]>("/launches"), api<Automation[]>("/automations")]);
      setLaunches(launchRows);
      setAutomations(autoRows);
      setError("");
      if (!launchId) setLaunchId(launchRows.find((l) => l.status === "live" && l.network === "solana")?.id ?? "");
    } catch (e) { setError(errMsg(e)); }
  }, [launchId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => { void load(); }, 15_000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    setWallets([]);
    setSelected(new Set());
    setPreview(null);
    if (!launchId) return;
    api<{ wallets: Wallet[] }>(`/launches/${launchId}/workspace`)
      .then((workspace) => setWallets(workspace.wallets ?? []))
      .catch((e) => setError(errMsg(e)));
  }, [launchId]);

  const launch = launches?.find((l) => l.id === launchId);
  const plannedTrade = side === "buy" ? (+amount || 0) * selected.size * (+orders || 0) : 0;
  const plannedExitPercent = side === "sell" ? (1 - (1 - Math.min(100, +amount || 0) / 100) ** (+orders || 0)) * 100 : 0;
  const activePlans = useMemo(() => automations?.filter((a) => active.has(a.status)) ?? [], [automations]);
  const completed = useMemo(() => automations?.filter((a) => a.status === "completed").length ?? 0, [automations]);
  const clearPreview = () => setPreview(null);

  function formBody() {
    return {
      side, wallet_ids: [...selected], ...(side === "buy" ? { amount_sol: +amount } : { sell_percent: +amount }),
      total_orders: +orders, interval_minutes: +interval,
      start_in_minutes: +startDelay, slippage_pct: +slippage,
    };
  }

  async function reviewPlan() {
    if (!launch || !selected.size) return;
    setBusy(true); setError("");
    try {
      const next = await api<Preview>(`/launches/${launchId}/automations`, { body: formBody() });
      setPreview(next);
    } catch (e) { setError(errMsg(e)); }
    finally { setBusy(false); }
  }

  async function confirmPlan() {
    if (!preview) return;
    setBusy(true); setError("");
    try {
      await api(`/launches/${launchId}/automations`, {
        body: { ...formBody(), confirm: true, preflight_hash: preview.preflight_hash, idempotency_key: idempotencyKey() },
      });
      setPreview(null);
      toast("good", "Scheduled buy plan created");
      await load();
    } catch (e) { setError(errMsg(e)); setPreview(null); }
    finally { setBusy(false); }
  }

  async function control(id: string, action: "pause" | "resume" | "cancel") {
    setActionId(id); setError("");
    try {
      await api(`/automations/${id}/${action}`, { body: {} });
      await load();
      toast(action === "cancel" ? "info" : "good", action === "cancel" ? "Remaining orders cancelled" : `Plan ${action}d`);
    } catch (e) { setError(errMsg(e)); }
    finally { setActionId(""); }
  }

  function toggleWallet(id: string) {
    clearPreview();
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  return (
    <>
      <PageHeader title="Automation" sub="Set a finite buying or staged-selling schedule, keep its limits visible, and stop between orders."
        actions={<Link to="/launches" className="btn secondary"><Icon name="rocket" size={15} />Launches</Link>} />

      {error && <Callout tone="err">{error}</Callout>}
      <div className="automation-hero">
        <div className="automation-hero-mark"><Icon name="calendar" size={23} /></div>
        <div className="grow">
          <div className="eyebrow">CONTROLLED EXECUTION</div>
          <h2>Automate a plan. Keep the limits.</h2>
          <p>Schedule a fixed number of buys or staged sells from wallets you control. Every tranche uses Helm’s normal transaction simulation and audit trail.</p>
        </div>
        <div className="automation-hero-stats">
          <div><strong>{automations ? activePlans.length : "—"}</strong><span>active plans</span></div>
          <div><strong>{automations ? completed : "—"}</strong><span>completed</span></div>
        </div>
      </div>

      <Callout tone="info" icon="shield">
        Plans are one-way and finite: schedule buys or staged sells. Helm does not create buy/sell round trips or trade between your wallets to inflate activity. A sell tranche applies to each wallet’s remaining balance at execution time.
      </Callout>

      <div className="grid automation-layout">
        <Card title="Build a trade schedule" icon="zap" sub="Solana · one launch · selected managed wallets">
          {!launches ? <Loading rows={4} /> : (
            <div className="automation-form">
              <div className="automation-direction" role="group" aria-label="Schedule direction">
                <button type="button" className={`btn sm ${side === "buy" ? "" : "secondary"}`} aria-pressed={side === "buy"}
                  onClick={() => { setSide("buy"); setAmount("0.05"); clearPreview(); }}>Scheduled buys</button>
                <button type="button" className={`btn sm ${side === "sell" ? "" : "secondary"}`} aria-pressed={side === "sell"}
                  onClick={() => { setSide("sell"); setAmount("25"); clearPreview(); }}>Staged sells</button>
              </div>
              <Field label="Live Solana launch" hint="Scheduled buys currently support Solana launches.">
                <select value={launchId} onChange={(e) => { setLaunchId(e.target.value); clearPreview(); }}>
                  <option value="">Choose a live launch…</option>
                  {launches.filter((l) => l.status === "live" && l.network === "solana").map((l) => (
                    <option key={l.id} value={l.id}>{l.name} · ${l.symbol}</option>
                  ))}
                </select>
              </Field>

              <div className="automation-wallets">
                <div className="row between">
                  <div><strong>Execution wallets</strong><div className="muted small">Choose attached wallets. Each is labeled as project controlled.</div></div>
                  {wallets.length > 1 && <button type="button" className="btn sm ghost" onClick={() => {
                    clearPreview(); setSelected(selected.size === wallets.length ? new Set() : new Set(wallets.map((w) => w.id)));
                  }}>{selected.size === wallets.length ? "Clear" : "Select all"}</button>}
                </div>
                {!launchId ? <p className="muted small">Choose a live launch to load its attached wallets.</p> : wallets.length === 0 ? (
                  <p className="muted small">No managed wallets are attached to this launch yet. Attach wallets in its workspace first.</p>
                ) : (
                  <div className="automation-wallet-list">
                    {wallets.map((w) => (
                      <label className={`automation-wallet ${selected.has(w.id) ? "selected" : ""}`} key={w.id}>
                        <input type="checkbox" checked={selected.has(w.id)} onChange={() => toggleWallet(w.id)} />
                        <span className="wallet-avatar"><Icon name="wallet" size={15} /></span>
                        <span className="grow"><strong>{w.label || "Untitled wallet"}</strong><span><Address address={w.address} network="solana" /></span></span>
                        <span className="wallet-balance">{side === "buy" ? `${fmt(w.sol, 3)} SOL` : `${fmt(w.tokens, 2)} $${launch?.symbol ?? "tokens"}`}</span>
                      </label>
                    ))}
                  </div>
                )}
              </div>

              <div className="grid automation-fields">
                <Field label={side === "buy" ? "Buy size · per wallet, each time" : "Sell from remaining balance · each time"}>
                  <div className="input-wrap"><input inputMode="decimal" min="0" max={side === "buy" ? 10 : 100} value={amount} onChange={(e) => { setAmount(e.target.value); clearPreview(); }} /><span className="unit">{side === "buy" ? "SOL" : "%"}</span></div>
                </Field>
                <Field label="Number of orders"><select value={orders} onChange={(e) => { setOrders(e.target.value); clearPreview(); }}>{[2, 3, 4, 6, 8, 12].map((n) => <option key={n} value={n}>{n} {side === "buy" ? "buys" : "sells"}</option>)}</select></Field>
                <Field label="Time between orders"><select value={interval} onChange={(e) => { setInterval(e.target.value); clearPreview(); }}>{[5, 10, 15, 30, 60, 240, 1440].map((n) => <option key={n} value={n}>{n < 60 ? `${n} minutes` : n < 1440 ? `${n / 60} hours` : "24 hours"}</option>)}</select></Field>
                <Field label="Start after"><select value={startDelay} onChange={(e) => { setStartDelay(e.target.value); clearPreview(); }}>{[1, 5, 15, 30, 60, 240, 1440].map((n) => <option key={n} value={n}>{n < 60 ? `${n} minutes` : n < 1440 ? `${n / 60} hours` : "24 hours"}</option>)}</select></Field>
                <Field label="Maximum slippage"><select value={slippage} onChange={(e) => { setSlippage(e.target.value); clearPreview(); }}>{[1, 2, 3, 5, 10, 15, 20].map((n) => <option key={n} value={n}>{n}%</option>)}</select></Field>
              </div>

              <div className="automation-budget">
                {side === "buy" ? <>
                  <div className="row between"><span>Planned trade amount</span><strong>{fmt(plannedTrade, 4)} SOL</strong></div>
                  <div className="budget-equation">{fmt(+amount || 0, 4)} SOL × {selected.size} wallet{selected.size === 1 ? "" : "s"} × {+orders || 0} buys</div>
                  <div className="muted small">Network fees vary and are additional. SOL funding is checked before each order.</div>
                </> : <>
                  <div className="row between"><span>Maximum planned position exit</span><strong>{fmt(plannedExitPercent, 2)}%</strong></div>
                  <div className="budget-equation">{fmt(+amount || 0, 2)}% of each wallet’s remaining tokens × {+orders || 0} sells</div>
                  <div className="muted small">The percentage applies to the balance at each execution. Token prices and proceeds vary; SOL for network fees is checked before each order. The plan can finish early when no sellable tokens remain.</div>
                </>}
              </div>

              {preview && (
                <div className="automation-review">
                  <div className="row between"><div><div className="eyebrow">REVIEW BEFORE SCHEDULING</div><strong>{preview.preflight.launch}</strong></div><span className="badge info">{preview.preflight.strategy}</span></div>
                  <div className="automation-review-grid">
                    <div><span>Orders</span><strong>{preview.preflight.number_of_orders}</strong></div>
                    <div><span>Per order / wallet</span><strong>{side === "buy" ? `${fmt(preview.preflight.amount_sol_per_wallet_per_order ?? 0, 4)} SOL` : `${fmt(preview.preflight.sell_percent_of_remaining_per_order ?? 0, 2)}% of remaining`}</strong></div>
                    <div><span>{side === "buy" ? "Total planned buys" : "Maximum position exit"}</span><strong>{side === "buy" ? `${fmt(preview.preflight.planned_trade_sol ?? 0, 4)} SOL` : `${fmt(preview.preflight.maximum_position_exit_percent ?? 0, 2)}%`}</strong></div>
                    <div><span>First order</span><strong>In {preview.preflight.first_order_in_minutes} min</strong></div>
                  </div>
                  <div className="stack tight">
                    {preview.preflight.wallets.map((w) => <div key={w.address} className="row between small"><span>{w.label} · <span className="mono">{short(w.address)}</span>{side === "sell" && <span className="muted"> · {fmt(w.token_balance ?? 0, 2)} ${launch?.symbol}</span>}</span><Address address={w.address} network="solana" /></div>)}
                  </div>
                  <div className="small text-2">Platform fee: {preview.preflight.platform_fee}<br />Network fees: {preview.preflight.network_fees}</div>
                  {preview.preflight.warnings.map((warning) => <Callout key={warning} tone="warn">{warning}</Callout>)}
                  <div className="row between">
                    <button className="btn ghost" onClick={clearPreview}>Edit plan</button>
                    <button className="btn" disabled={busy || preview.preflight.warnings.length > 0} onClick={confirmPlan}>
                      {busy ? <span className="spinner" /> : <Icon name="check" size={15} />}Schedule {preview.preflight.number_of_orders} {side === "buy" ? "buys" : "sells"}
                    </button>
                  </div>
                </div>
              )}

              {!preview && <button className="btn lg block" disabled={!launch || !selected.size || !(+amount > 0) || (side === "sell" && +amount > 100) || busy} onClick={reviewPlan}>
                {busy ? <span className="spinner" /> : <Icon name="eye" size={15} />}Review schedule
              </button>}
            </div>
          )}
        </Card>

        <div className="col">
          <Card title="Your plans" icon="calendar" sub="Each plan is finite and linked to its execution jobs" actions={<button className="btn icon sm secondary" onClick={() => void load()} aria-label="Refresh plans"><Icon name="refresh" size={13} /></button>}>
            {!automations ? <Loading rows={3} /> : automations.length === 0 ? (
              <Empty icon="calendar" title="No plans yet" text="Your scheduled orders and their status will appear here." />
            ) : (
              <div className="automation-plan-list">
                {automations.map((a) => {
                  const pct = a.total_orders ? Math.min(100, a.runs_completed / a.total_orders * 100) : 0;
                  return (
                    <article className="automation-plan" key={a.id}>
                      <div className="row between nowrap">
                        <div className="grow"><strong>{a.launch_name || "Launch"} <span className="muted">${a.launch_symbol}</span></strong><div className="muted small">{a.wallet_count} wallet{a.wallet_count === 1 ? "" : "s"} · {a.side === "buy" ? `${fmt(a.order_sol, 4)} SOL per buy` : `${fmt(a.sell_percent, 2)}% of remaining per sell`}</div></div>
                        <StatusBadge status={a.status} />
                      </div>
                      <div className="bar thin"><div style={{ width: `${pct}%` }} /></div>
                      <div className="row between small muted"><span>{a.runs_completed} of {a.total_orders} {a.side === "buy" ? "buys" : "sells"} complete{a.side === "sell" && a.status === "completed" && a.runs_completed < a.total_orders ? " · position exhausted" : ""}</span><span>{a.side === "buy" ? `${fmt(a.planned_trade_sol ?? 0, 4)} SOL planned` : `up to ${fmt(a.planned_position_exit_pct ?? 0, 2)}% of starting holdings`}</span></div>
                      {a.status === "scheduled" && <div className="small text-2">Next order {displayTime(a.next_run_at)}</div>}
                      {a.error && <Callout tone="warn">{a.error}</Callout>}
                      <div className="row between">
                        <Link to={`/launches/${a.launch_id}`} className="btn sm ghost">Open workspace<Icon name="arrowright" size={12} /></Link>
                        <div className="row">
                          {a.status === "scheduled" && <button className="btn sm secondary" disabled={actionId === a.id} onClick={() => void control(a.id, "pause")}>Pause</button>}
                          {a.status === "paused" && <button className="btn sm secondary" disabled={actionId === a.id} onClick={() => void control(a.id, "resume")}>Resume</button>}
                          {(a.status === "scheduled" || a.status === "paused") && <button className="btn sm danger-ghost" disabled={actionId === a.id} onClick={() => void control(a.id, "cancel")}>Cancel</button>}
                        </div>
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </Card>
          <Card title="Execution rules" icon="shield">
            <div className="automation-rules">
              <div><span className="rule-icon"><Icon name="check" size={14} /></span><span><strong>Finite by design</strong><small>Every plan has a fixed order count and maximum trade amount.</small></span></div>
              <div><span className="rule-icon"><Icon name="check" size={14} /></span><span><strong>Simulated before sending</strong><small>Each tranche uses the launch adapter and transaction simulator.</small></span></div>
              <div><span className="rule-icon"><Icon name="check" size={14} /></span><span><strong>Stops on failure</strong><small>Uncertain or failed orders are never retried automatically.</small></span></div>
              <div><span className="rule-icon"><Icon name="check" size={14} /></span><span><strong>Auditable</strong><small>Every confirmed tranche is recorded as a regular Helm job.</small></span></div>
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}
