import { useCallback, useEffect, useMemo, useState } from "react";
import { api, errMsg, fmt, short } from "../api";
import { chainOf } from "../chains";
import SpendDialog from "./SpendDialog";
import { Address, Callout, Card, Empty, Field, Icon, Loading, Seg, Spinner, useToast } from "./ui";

/* Allocation & vesting plan, supply dispersal to a folder, and the launchpad's real launch protections. */

interface Row {
  id?: string; category: string; label: string; recipient: string; amount: string; unlock: string;
  unlock_start: string; unlock_end: string; conditions: string; lock: boolean;
  // read-only, from the server
  pct?: number | null; unlocked_now_pct?: number; lock_enforced?: boolean; source?: string; tags?: string[];
  balance_now?: string | null; category_label?: string;
}
interface View {
  supply: string | null; supply_source: string; allocated: string; unallocated: string | null; reconciled: boolean;
  rows: (Row & { recipient: string | null; unlock_start: string | null; unlock_end: string | null })[];
  warnings: string[]; categories: Record<string, string>; note: string;
}

const day = (s: string | null | undefined) => (s ? s.slice(0, 10) : "");
const toRow = (r: View["rows"][number]): Row => ({
  ...r, recipient: r.recipient ?? "", label: r.label ?? "", conditions: r.conditions ?? "",
  unlock_start: day(r.unlock_start), unlock_end: day(r.unlock_end),
});
const blank = (category = "team"): Row => ({ category, label: "", recipient: "", amount: "", unlock: "immediate", unlock_start: "", unlock_end: "", conditions: "", lock: false });
/** Exact decimal subtraction on strings (amounts can exceed float precision). */
function subtract(a: string, b: string): string {
  const scale = Math.max((a.split(".")[1] ?? "").length, (b.split(".")[1] ?? "").length);
  const big = (x: string) => BigInt((x.split(".")[0] || "0") + (x.split(".")[1] ?? "").padEnd(scale, "0"));
  const v = big(a) - big(b);
  const neg = v < 0n; let s = (neg ? -v : v).toString().padStart(scale + 1, "0");
  s = scale ? `${s.slice(0, -scale)}.${s.slice(-scale)}`.replace(/\.?0+$/, "") : s;
  return (neg ? "-" : "") + s;
}
const sum = (xs: string[]) => xs.reduce((acc, x) => subtract(acc, subtract("0", x || "0")), "0");

export default function Allocations({ launch, wallets, onChanged }: {
  launch: { id: string; network: string; symbol: string; launchpad: string; mint_address: string | null; dev_wallet_id: string | null; settings?: Record<string, unknown> };
  wallets: { id: string; address: string; label: string; tokens: number }[];
  onChanged: () => void;
}) {
  const toast = useToast();
  const [v, setV] = useState<View | null>(null);
  const [err, setErr] = useState("");
  const [rows, setRows] = useState<Row[] | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    api<View>(`/launches/${launch.id}/allocations`).then((x) => { setV(x); setErr(""); }).catch((e) => setErr(errMsg(e)));
  }, [launch.id]);
  useEffect(load, [load]);
  const total = useMemo(() => rows ? sum(rows.map((r) => r.amount)) : v?.allocated ?? "0", [rows, v]);
  const remaining = v?.supply ? subtract(v.supply, total) : null;

  const save = () => {
    if (!rows) return;
    setBusy(true);
    api<View>(`/launches/${launch.id}/allocations`, { method: "PUT", body: { rows: rows.map((r) => ({
      id: r.id, category: r.category, label: r.label, recipient: r.recipient.trim(), amount: r.amount.trim(), unlock: r.unlock,
      unlock_start: r.unlock_start ? `${r.unlock_start}T00:00:00Z` : null, unlock_end: r.unlock_end ? `${r.unlock_end}T00:00:00Z` : null,
      conditions: r.conditions, lock: r.lock,
    })) } }).then((x) => { setV(x); setRows(null); toast("good", "Allocation plan saved"); })
      .catch((e) => toast("bad", errMsg(e))).finally(() => setBusy(false));
  };
  const set = (i: number, patch: Partial<Row>) => setRows((rs) => rs!.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  if (err && !v) return <Callout tone="err">{err}</Callout>;
  if (!v) return <Loading />;
  const editing = rows !== null;
  const cats = Object.entries(v.categories);
  return (
    <div className="stack">
      <Callout tone="info" icon="info">
        Your declared allocation, from Helm's records and the chain, not market data. {v.note}
      </Callout>
      <Card title="Allocation & vesting" icon="pie" flush
        sub={v.supply ? `Supply ${Number(v.supply).toLocaleString()} $${launch.symbol} (${v.supply_source})` : `Supply: ${v.supply_source}`}
        actions={editing ? <>
          <button className="btn sm ghost" onClick={() => setRows(null)} disabled={busy}>Discard</button>
          <button className="btn sm" onClick={save} disabled={busy}>{busy ? <Spinner /> : <Icon name="check" size={13} />}Save plan</button>
        </> : <button className="btn sm secondary" onClick={() => setRows(v.rows.map(toRow))}><Icon name="edit" size={13} />Edit plan</button>}
        foot={<div className="row between small">
          <span>Allocated <b className="mono">{Number(total).toLocaleString()}</b>{v.supply && <> of {Number(v.supply).toLocaleString()}</>}</span>
          {remaining === null ? <span className="muted">Reconciles once the supply is known</span>
            : remaining === "0" ? <span className="badge good"><Icon name="check" size={11} />Reconciles exactly to supply</span>
            : <span className={`badge ${remaining.startsWith("-") ? "bad" : "warn"}`}>{remaining.startsWith("-") ? `Over by ${Number(remaining.slice(1)).toLocaleString()}` : `${Number(remaining).toLocaleString()} unallocated`}</span>}
        </div>}>
        {!editing ? (v.rows.length === 0 ? <Empty icon="pie" title="No allocation plan yet" text="List where the supply goes: public sale or curve, team, treasury and so on, with unlock schedules." /> : (
          <div className="table-wrap"><table className="rtable">
            <caption className="sr-only">Supply allocation</caption>
            <thead><tr><th>Allocation</th><th className="r">Amount</th><th className="r">Supply</th><th>Unlock</th><th className="hide-sm">Holds now</th></tr></thead>
            <tbody>{v.rows.map((r) => (
              <tr key={r.id}>
                <td className="span"><b>{r.label || r.category_label}</b> <span className="muted small">{r.label ? r.category_label : ""}</span>
                  <div className="row small" style={{ gap: 6 }}>{r.recipient ? <Address address={r.recipient} network={launch.network} /> : <span className="muted">held by the launchpad</span>}
                    {r.tags?.map((t) => <span key={t} className="badge info">{t}</span>)}
                    {r.source === "dispersal" && <span className="badge">dispersed</span>}</div>
                  {r.conditions && <div className="muted small">{r.conditions}</div>}</td>
                <td className="r mono" data-label="Amount">{Number(r.amount).toLocaleString(undefined, { maximumFractionDigits: 6 })}</td>
                <td className="r mono" data-label="Supply">{r.pct == null ? "—" : `${fmt(r.pct, 2)}%`}</td>
                <td data-label="Unlock" className="small">{r.unlock === "immediate" ? "Unlocked" : r.unlock === "date" ? `On ${day(r.unlock_start)}` : `Linear ${day(r.unlock_start)} → ${day(r.unlock_end)}`}
                  <div className="muted">{fmt(r.unlocked_now_pct ?? 0, 1)}% unlocked now{r.lock ? (r.lock_enforced ? " · locked by Helm" : " · lock (not enforceable)") : " · declared"}</div></td>
                <td className="hide-sm mono small">{r.balance_now == null ? "—" : Number(r.balance_now).toLocaleString(undefined, { maximumFractionDigits: 4 })}</td>
              </tr>))}</tbody>
          </table></div>
        )) : (
          <div className="stack" style={{ padding: 14 }}>
            {rows!.map((r, i) => {
              const fixed = r.source === "dispersal";
              const locked = !!v.rows.find((x) => x.id === r.id && x.lock);
              return (
                <fieldset key={r.id ?? `n${i}`} className="card" style={{ padding: 12, margin: 0 }}>
                  <legend className="sr-only">Allocation line {i + 1}</legend>
                  <div className="grid g-form">
                    <Field label="Category"><select value={r.category} onChange={(e) => set(i, { category: e.target.value, ...(["public", "liquidity"].includes(e.target.value) ? { recipient: "", lock: false } : {}) })}>
                      {cats.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
                    <Field label="Label"><input value={r.label} maxLength={80} onChange={(e) => set(i, { label: e.target.value })} placeholder="e.g. Core team" /></Field>
                    {!["public", "liquidity"].includes(r.category) && <Field label="Recipient" hint={fixed ? "From a dispersal; can't change" : undefined}>
                      <input className="mono" value={r.recipient} disabled={fixed || locked} onChange={(e) => set(i, { recipient: e.target.value })} placeholder={`${chainOf(launch.network).name} address`} spellCheck={false} /></Field>}
                    <Field label={`Amount ($${launch.symbol})`}><input className="mono" inputMode="decimal" value={r.amount} disabled={fixed} onChange={(e) => set(i, { amount: e.target.value.replace(/[^\d.]/g, "") })} /></Field>
                    <Field label="Unlock"><select value={r.unlock} onChange={(e) => set(i, { unlock: e.target.value })}>
                      <option value="immediate">Immediately</option><option value="date">On a date</option><option value="linear">Linear vesting</option></select></Field>
                    {r.unlock !== "immediate" && <Field label={r.unlock === "date" ? "Unlock date (UTC)" : "Cliff / start (UTC)"}><input type="date" value={r.unlock_start} onChange={(e) => set(i, { unlock_start: e.target.value })} /></Field>}
                    {r.unlock === "linear" && <Field label="Fully vested (UTC)"><input type="date" value={r.unlock_end} onChange={(e) => set(i, { unlock_end: e.target.value })} /></Field>}
                    <Field label="Claim or vesting conditions"><input value={r.conditions} maxLength={280} onChange={(e) => set(i, { conditions: e.target.value })} placeholder="optional" /></Field>
                  </div>
                  <div className="row between" style={{ marginTop: 8 }}>
                    <label className="row small" style={{ gap: 6 }}>
                      <input type="checkbox" checked={r.lock} disabled={locked || r.unlock === "immediate"} onChange={(e) => set(i, { lock: e.target.checked })} />
                      Have Helm enforce this lock (your own Helm wallets only; can be extended, never shortened)
                    </label>
                    {!fixed && !locked && <button className="btn sm ghost" onClick={() => setRows(rows!.filter((_, j) => j !== i))}><Icon name="trash" size={13} />Remove</button>}
                  </div>
                </fieldset>
              );
            })}
            <div className="row">
              <button className="btn sm secondary" onClick={() => setRows([...rows!, blank()])}><Icon name="plus" size={13} />Add line</button>
              {remaining && !remaining.startsWith("-") && remaining !== "0" && (
                <button className="btn sm ghost" onClick={() => {
                  const pub = rows!.findIndex((r) => r.category === "public");
                  if (pub >= 0) set(pub, { amount: sum([rows![pub].amount, remaining]) });
                  else setRows([...rows!, { ...blank("public"), amount: remaining }]);
                }}>Assign the remaining {Number(remaining).toLocaleString()} to the public sale</button>
              )}
            </div>
          </div>
        )}
      </Card>
      {v.warnings.map((w) => <Callout key={w} tone="warn">{w}</Callout>)}
      {launch.mint_address && <Disperse launch={launch} wallets={wallets} onDone={() => { load(); onChanged(); }} />}
      <Protections launchpad={launch.launchpad} params={launch.settings ?? {}} />
    </div>
  );
}

function Disperse({ launch, wallets, onDone }: {
  launch: { id: string; network: string; symbol: string; mint_address: string | null; dev_wallet_id: string | null };
  wallets: { id: string; address: string; label: string; tokens: number }[]; onDone: () => void;
}) {
  const [folders, setFolders] = useState<{ id: string; name: string; wallet_count: number }[]>([]);
  const [all, setAll] = useState<{ id: string; address: string; label: string; folder_id: string | null; network: string; kind: string }[]>([]);
  const [from, setFrom] = useState(launch.dev_wallet_id ?? "");
  const [folder, setFolder] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [split, setSplit] = useState<"even" | "custom">("even");
  const [total, setTotal] = useState("");
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const [dialog, setDialog] = useState<any>(null);
  useEffect(() => {
    api<any[]>("/folders").then(setFolders).catch(() => undefined);
    api<any[]>("/wallets").then(setAll).catch(() => undefined);
  }, []);
  const inFolder = all.filter((w) => w.folder_id === folder && w.network === launch.network && w.kind === "managed" && w.id !== from);
  const src = wallets.find((w) => w.id === from);
  const ids = inFolder.filter((w) => picked.has(w.id)).map((w) => w.id);
  const ok = from && ids.length > 0 && (split === "even" ? +total > 0 : ids.every((id) => +amounts[id] > 0));
  return (
    <Card title="Disperse supply to a folder" icon="send" sub="Move tokens from one of your wallets to wallets in one of your folders">
      <div className="stack">
        <Callout tone="warn">These are your own Helm wallets. Spreading tokens across them doesn't widen ownership: the
          allocation plan records every one as creator-controlled, and the transparency panel keeps counting them as yours.</Callout>
        <div className="grid g-form">
          <Field label="From" hint={src ? `Holds ${fmt(src.tokens, 4)} $${launch.symbol}` : undefined}>
            <select value={from} onChange={(e) => setFrom(e.target.value)}><option value="">Choose…</option>
              {wallets.map((w) => <option key={w.id} value={w.id}>{w.label || short(w.address)}{w.id === launch.dev_wallet_id ? " (dev)" : ""}</option>)}</select></Field>
          <Field label="Folder"><select value={folder} onChange={(e) => { setFolder(e.target.value); setPicked(new Set()); }}><option value="">Choose…</option>
            {folders.map((f) => <option key={f.id} value={f.id}>{f.name} ({f.wallet_count})</option>)}</select></Field>
          <Field label="Split"><Seg full value={split} onChange={setSplit} options={[["even", "Evenly"], ["custom", "Per wallet"]]} /></Field>
          {split === "even" && <Field label={`Total ($${launch.symbol})`}><input inputMode="decimal" value={total} onChange={(e) => setTotal(e.target.value)} /></Field>}
        </div>
        {folder && (inFolder.length === 0 ? <p className="muted small">No {chainOf(launch.network).name} wallets in this folder.</p> : (
          <div className="stack tight">
            <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={ids.length === inFolder.length} onChange={(e) => setPicked(new Set(e.target.checked ? inFolder.map((w) => w.id) : []))} />Select all</label>
            {inFolder.map((w) => (
              <div key={w.id} className="row between small">
                <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={picked.has(w.id)} onChange={() => setPicked((s) => { const n = new Set(s); n.has(w.id) ? n.delete(w.id) : n.add(w.id); return n; })} />
                  {w.label || "Wallet"} <span className="mono muted">{short(w.address)}</span></label>
                {split === "custom" && picked.has(w.id) && <input inputMode="decimal" style={{ width: 140 }} aria-label={`Amount for ${w.label || w.address}`} value={amounts[w.id] ?? ""} onChange={(e) => setAmounts({ ...amounts, [w.id]: e.target.value })} />}
              </div>))}
          </div>))}
        <button className="btn secondary" disabled={!ok} onClick={() => setDialog({
          title: `Disperse $${launch.symbol}`, path: "/wallets/disperse",
          body: { from_wallet_id: from, to_wallet_ids: ids, asset: launch.mint_address, split,
            ...(split === "even" ? { total: +total } : { amounts: ids.map((id) => +amounts[id]) }) },
        })}><Icon name="send" size={15} />Review dispersal</button>
      </div>
      {dialog && <SpendDialog {...dialog} network={launch.network} onClose={() => setDialog(null)} onDone={() => { setDialog(null); onDone(); }} />}
    </Card>
  );
}

function Protections({ launchpad, params }: { launchpad: string; params: Record<string, unknown> }) {
  const [p, setP] = useState<{ items: { id: string; label: string; available: boolean; effect: string; fee: string | null; options?: string[] }[]; summary: string } | null>(null);
  useEffect(() => { api(`/launchpads/${launchpad}/protections`).then(setP).catch(() => undefined); }, [launchpad]);
  if (!p) return null;
  return (
    <Card title="Launch protections" icon="shield" sub={p.summary}>
      <div className="stack tight">
        {p.items.map((i) => (
          <div key={i.id} className="row between small" style={{ alignItems: "flex-start", gap: 12 }}>
            <div style={{ minWidth: 0 }}><b>{i.label}</b>
              <div className="muted">{i.effect}{i.fee ? ` ${i.fee}` : ""}</div>
              {i.available && i.options && <div className="mono small">{i.options.map((o) => `${o.replace(/_/g, " ")}: ${params[o] ?? "default"}`).join(" · ")}</div>}
            </div>
            {i.available ? <span className="badge good">Available</span> : <span className="badge">Not offered</span>}
          </div>))}
        <span className="muted small">Settings are chosen when you create the launch and shown again in the deploy preview.</span>
      </div>
    </Card>
  );
}
