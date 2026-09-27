import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, errMsg, fmt, short } from "../api";
import { Address, Callout, Card, Empty, Field, Icon, Loading, LoadError, Metrics, PageHeader, Seg, StatusBadge, useToast } from "../components/ui";
import { chainOf, native } from "../chains";

interface Launch { id: string; name: string; symbol: string; network: string; status: string; mint_address: string | null }
interface Wallet { id: string; address: string; label: string; sol: number; tokens: number; role: string }
interface Automation {
  id: string; launch_id: string; launch_name: string | null; launch_symbol: string | null; network: string;
  coin?: string; status: string; side: "buy" | "sell"; wallet_count: number; order_sol: number; sell_percent: number;
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
      if (!launchId) setLaunchId(launchRows.find((l) => l.status === "live")?.id ?? "");
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
  const coin = native(launch?.network);
  const plannedTrade = side === "buy" ? (+amount || 0) * selected.size * (+orders || 0) : 0;
  const plannedExitPercent = side === "sell" ? (1 - (1 - Math.min(100, +amount || 0) / 100) ** (+orders || 0)) * 100 : 0;
  const activePlans = useMemo(() => automations?.filter((a) => active.has(a.status)) ?? [], [automations]);
  const completed = useMemo(() => automations?.filter((a) => a.status === "completed").length ?? 0, [automations]);
  const nextRun = useMemo(() => automations?.filter((a) => a.status === "scheduled" && a.next_run_at)
    .sort((a, b) => a.next_run_at.localeCompare(b.next_run_at))[0], [automations]);
  const [planView, setPlanView] = useState<"active" | "history">("active");
  const shownPlans = useMemo(() => (automations ?? []).filter((a) => planView === "active" ? active.has(a.status) : !active.has(a.status)), [automations, planView]);
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
      toast("good", side === "buy" ? "Scheduled buy plan created" : "Scheduled sell plan created");
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
      <Metrics label="Plan summary" items={[
        { label: "Active plans", value: automations ? String(activePlans.length) : "—", hint: `${automations?.filter((a) => a.status === "paused").length ?? 0} paused` },
        { label: "Next order", value: nextRun ? displayTime(nextRun.next_run_at) : "—", hint: nextRun ? `${nextRun.side === "buy" ? "Buy" : "Sell"} · $${nextRun.launch_symbol ?? ""}` : "nothing scheduled" },
        { label: "Orders filled", value: automations ? String(automations.reduce((s, a) => s + a.runs_completed, 0)) : "—", hint: "across all plans" },
        { label: "Completed", value: automations ? String(completed) : "—", hint: `${automations?.filter((a) => a.status === "failed").length ?? 0} stopped on failure` },
      ]} />

      <Callout tone="info" icon="shield">
        Plans are one-way and finite: a fixed number of buys, or a fixed number of staged sells. Helm never creates buy/sell round trips or trades between your wallets to make volume. Each sell applies to the wallet's remaining balance at that moment.
      </Callout>

      <div className="grid automation-layout">
        <Card title="Build a trade schedule" icon="zap" sub={`${launch ? chainOf(launch.network).name : "Any chain"} · one launch · selected managed wallets`}>
          {error && !launches ? <LoadError what="Launches" error={error} onRetry={() => void load()} /> : !launches ? <Loading rows={4} /> : (
            <div className="automation-form">
              <div className="automation-direction" role="group" aria-label="Schedule direction">
                <button type="button" className={`btn sm ${side === "buy" ? "" : "secondary"}`} aria-pressed={side === "buy"}
                  onClick={() => { setSide("buy"); setAmount("0.05"); clearPreview(); }}>Scheduled buys</button>
                <button type="button" className={`btn sm ${side === "sell" ? "" : "secondary"}`} aria-pressed={side === "sell"}
                  onClick={() => { setSide("sell"); setAmount("25"); clearPreview(); }}>Staged sells</button>
              </div>
              {launches.every((l) => l.status !== "live") && (
                <Callout>You have no live launches yet. Plans run on a live token: <Link to="/launches">deploy or import one</Link> first.</Callout>
              )}
              <Field label="Live launch" hint="Works on Solana, Base, BNB Chain, Robinhood Chain and Ethereum launches.">
                <select value={launchId} onChange={(e) => { setLaunchId(e.target.value); clearPreview(); }}>
                  <option value="">Choose a live launch…</option>
                  {launches.filter((l) => l.status === "live").map((l) => (
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
                        <span className="grow"><strong>{w.label || "Untitled wallet"}</strong><span><Address address={w.address} network={launch?.network ?? "solana"} /></span></span>
                        <span className="wallet-balance">{side === "buy" ? `${fmt(w.sol, 3)} ${coin}` : `${fmt(w.tokens, 2)} $${launch?.symbol ?? "tokens"}`}</span>
                      </label>
                    ))}
                  </div>
                )}
              </div>

              <div className="grid automation-fields">
                <Field label={side === "buy" ? "Buy size · per wallet, each time" : "Sell from remaining balance · each time"}>
                  <div className="input-wrap"><input inputMode="decimal" min="0" max={side === "buy" ? 10 : 100} value={amount} onChange={(e) => { setAmount(e.target.value); clearPreview(); }} /><span className="unit">{side === "buy" ? coin : "%"}</span></div>
                </Field>
                <Field label="Number of orders"><select value={orders} onChange={(e) => { setOrders(e.target.value); clearPreview(); }}>{[2, 3, 4, 6, 8, 12].map((n) => <option key={n} value={n}>{n} {side === "buy" ? "buys" : "sells"}</option>)}</select></Field>
                <Field label="Time between orders"><select value={interval} onChange={(e) => { setInterval(e.target.value); clearPreview(); }}>{[5, 10, 15, 30, 60, 240, 1440].map((n) => <option key={n} value={n}>{n < 60 ? `${n} minutes` : n < 1440 ? `${n / 60} hours` : "24 hours"}</option>)}</select></Field>
                <Field label="Start after"><select value={startDelay} onChange={(e) => { setStartDelay(e.target.value); clearPreview(); }}>{[1, 5, 15, 30, 60, 240, 1440].map((n) => <option key={n} value={n}>{n < 60 ? `${n} minutes` : n < 1440 ? `${n / 60} hours` : "24 hours"}</option>)}</select></Field>
                <Field label="Maximum slippage"><select value={slippage} onChange={(e) => { setSlippage(e.target.value); clearPreview(); }}>{[1, 2, 3, 5, 10, 15, 20].map((n) => <option key={n} value={n}>{n}%</option>)}</select></Field>
              </div>

              <div className="automation-budget">
                {side === "buy" ? <>
                  <div className="row between"><span>Planned trade amount</span><strong>{fmt(plannedTrade, 4)} {coin}</strong></div>
                  <div className="budget-equation">{fmt(+amount || 0, 4)} {coin} × {selected.size} wallet{selected.size === 1 ? "" : "s"} × {+orders || 0} buys</div>
                  <div className="muted small">Network fees vary and are additional. {coin} funding is checked before each order.</div>
                </> : <>
                  <div className="row between"><span>Maximum planned position exit</span><strong>{fmt(plannedExitPercent, 2)}%</strong></div>
                  <div className="budget-equation">{fmt(+amount || 0, 2)}% of each wallet’s remaining tokens × {+orders || 0} sells</div>
                  <div className="muted small">The percentage applies to the balance at each execution. Token prices and proceeds vary; {coin} for network fees is checked before each order. The plan can finish early when no sellable tokens remain.</div>
                </>}
              </div>

              {preview && (
                <div className="automation-review">
                  <div className="row between"><div><div className="eyebrow">REVIEW BEFORE SCHEDULING</div><strong>{preview.preflight.launch}</strong></div><span className="badge info">{preview.preflight.strategy}</span></div>
                  <div className="automation-review-grid">
                    <div><span>Orders</span><strong>{preview.preflight.number_of_orders}</strong></div>
                    <div><span>Per order / wallet</span><strong>{side === "buy" ? `${fmt(preview.preflight.amount_sol_per_wallet_per_order ?? 0, 4)} ${coin}` : `${fmt(preview.preflight.sell_percent_of_remaining_per_order ?? 0, 2)}% of remaining`}</strong></div>
                    <div><span>{side === "buy" ? "Total planned buys" : "Maximum position exit"}</span><strong>{side === "buy" ? `${fmt(preview.preflight.planned_trade_sol ?? 0, 4)} ${coin}` : `${fmt(preview.preflight.maximum_position_exit_percent ?? 0, 2)}%`}</strong></div>
                    <div><span>First order</span><strong>In {preview.preflight.first_order_in_minutes} min</strong></div>
                  </div>
                  <div className="stack tight">
                    {preview.preflight.wallets.map((w) => <div key={w.address} className="row between small"><span>{w.label} · <span className="mono">{short(w.address)}</span>{side === "sell" && <span className="muted"> · {fmt(w.token_balance ?? 0, 2)} ${launch?.symbol}</span>}</span><Address address={w.address} network={launch?.network ?? "solana"} /></div>)}
                  </div>
                  <div className="kv small">
                    <div className="k">Platform fee</div><div className="v">{preview.preflight.platform_fee}</div>
                    <div className="k">Network fees</div><div className="v">{preview.preflight.network_fees}</div>
                  </div>
                  <div className="stack tight">
                    <h3>Schedule · approximate, from now</h3>
                    <OrderTimeline first={preview.preflight.first_order_in_minutes} every={preview.preflight.interval_minutes} count={preview.preflight.number_of_orders} side={side} />
                  </div>
                  {preview.preflight.warnings.length === 0 && (
                    <div className="stack tight">
                      <h3>What happens next</h3>
                      <ol className="next-steps">
                        <li>The plan is saved. Nothing is sent until the first order time.</li>
                        <li>Before each order Helm re-checks {side === "buy" ? `each wallet's ${coin} balance` : `token balances and ${coin} for fees`}, simulates, then sends. Each order is a normal job in the launch's history.</li>
                        <li>If an order fails or its outcome is uncertain, the plan stops. Nothing is retried automatically.</li>
                        <li>You can pause or cancel the remaining orders at any time.</li>
                      </ol>
                    </div>
                  )}
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
          <Card title="Your plans" icon="calendar" sub="Each plan is finite and linked to its execution jobs" actions={<>
            <Seg value={planView} onChange={setPlanView} options={[["active", <>Active <span className="muted num">{activePlans.length}</span></>], ["history", <>History <span className="muted num">{(automations?.length ?? 0) - activePlans.length}</span></>]]} />
            <button className="btn icon sm secondary" onClick={() => void load()} aria-label="Refresh plans"><Icon name="refresh" size={13} /></button>
          </>}>
            {error && !automations ? <LoadError what="Plans" error={error} onRetry={() => void load()} /> : !automations ? <Loading rows={3} /> : shownPlans.length === 0 ? (
              <Empty icon="calendar" title={planView === "active" ? "No active plans" : "No finished plans yet"} text={planView === "active" ? "Build a schedule on the left. Active plans and their next order time show here." : "Completed, cancelled and stopped plans are kept here for reference."} />
            ) : (
              <div className="automation-plan-list">
                {shownPlans.map((a) => {
                  const pct = a.total_orders ? Math.min(100, a.runs_completed / a.total_orders * 100) : 0;
                  return (
                    <article className="automation-plan" key={a.id}>
                      <div className="row between nowrap">
                        <div className="grow"><strong>{a.launch_name || "Launch"} <span className="muted">${a.launch_symbol}</span></strong><div className="muted small">{a.wallet_count} wallet{a.wallet_count === 1 ? "" : "s"} · {a.side === "buy" ? `${fmt(a.order_sol, 4)} ${a.coin ?? native(a.network)} per buy` : `${fmt(a.sell_percent, 2)}% of remaining per sell`}</div></div>
                        <StatusBadge status={a.status} />
                      </div>
                      <div className="bar thin"><div style={{ width: `${pct}%` }} /></div>
                      <div className="row between small muted"><span>{a.runs_completed} of {a.total_orders} {a.side === "buy" ? "buys" : "sells"} complete{a.side === "sell" && a.status === "completed" && a.runs_completed < a.total_orders ? " · position exhausted" : ""}</span><span>{a.side === "buy" ? `${fmt(a.planned_trade_sol ?? 0, 4)} ${a.coin ?? native(a.network)} planned` : `up to ${fmt(a.planned_position_exit_pct ?? 0, 2)}% of starting holdings`}</span></div>
                      {a.status === "scheduled" && <div className="small text-2 row" style={{ gap: 6 }}><Icon name="calendar" size={12} />Next order <span className="mono">{displayTime(a.next_run_at)}</span></div>}
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

/** Approximate order times for a plan being reviewed, counted from now. Shows the first few and the last. */
function OrderTimeline({ first, every, count, side }: { first: number; every: number; count: number; side: "buy" | "sell" }) {
  const start = Date.now() + first * 60_000;
  const at = (i: number) => new Date(start + i * every * 60_000);
  const label = (d: Date) => d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const idx = count <= 4 ? Array.from({ length: count }, (_, i) => i) : [0, 1, 2, count - 1];
  return (
    <ol className="timeline">
      {idx.map((i, n) => (
        <li key={i} className={count > 4 && n === 3 ? "more" : ""}>
          <span>{side === "buy" ? "Buy" : "Sell"} {i + 1} of {count}{count > 4 && n === 3 ? ` · ${count - 4} more before this` : ""}</span>
          <time dateTime={at(i).toISOString()}>{label(at(i))}</time>
        </li>
      ))}
    </ol>
  );
}
