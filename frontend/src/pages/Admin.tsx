import { useCallback, useEffect, useState } from "react";
import { api, errMsg } from "../api";
import {
  Callout, Card, CopyButton, Empty, Icon, LoadError, Loading, Metrics, Modal, PageHeader, SearchInput, Seg, Spinner, StatusBadge, TableWrap, useToast,
} from "../components/ui";

/* Admin dashboard. The server checks the admin role on every request; this page only decides what to show. */

type Tab = "revenue" | "treasury" | "fees" | "users" | "audit" | "operations";
interface AdminMe { id: string; role: "superadmin" | "viewer"; can_change: boolean }
interface ConfigDiagnostics {
  signer: string; superadmin_id_configured: boolean; trusted_proxy_ranges_configured: boolean;
  rpc_configured: Record<string, boolean>; external_provider_keys_configured: Record<string, boolean>;
}

const SOURCE_LABEL: Record<string, string> = {
  trade_fee: "Trading fees (Solana)", dex_trade_fee: "Trading fees (EVM DEX)", route_fee: "Cross-chain route fees (Relay)",
  creator_share: "Creator-reward shares", partner: "Launchpad / partner revenue",
};
const NETWORKS = ["solana", "evm", "base", "bsc", "ethereum", "robinhood"];
const NET_LABEL: Record<string, string> = { solana: "Solana", evm: "All EVM chains (default)", base: "Base", bsc: "BNB Chain", ethereum: "Ethereum", robinhood: "Robinhood Chain" };

/** Raw integer amount as a decimal string, exact (no float rounding). */
function units(raw: string | number | null | undefined, decimals: number, max = 6): string {
  if (raw === null || raw === undefined) return "amount not reported";
  let s = String(raw);
  const neg = s.startsWith("-");
  if (neg) s = s.slice(1);
  s = s.padStart(decimals + 1, "0");
  const whole = s.slice(0, s.length - decimals) || "0";
  let frac = decimals ? s.slice(s.length - decimals).slice(0, max).replace(/0+$/, "") : "";
  if (!frac && decimals && /[1-9]/.test(s.slice(s.length - decimals))) frac = "0".repeat(max - 1) + "1";
  return `${neg ? "−" : ""}${Number(whole).toLocaleString()}${frac ? "." + frac : ""}`;
}
const utc = (s: string | null | undefined) => (s ? s.replace("T", " ").replace(/\.\d+/, "").replace("Z", " UTC") : "—");

/* ------------------------------------------------------------------ step-up */
interface Proof { password: string; totp: string }

function StepUpDialog({ title, detail, onCancel, onConfirm, needsReason }: {
  title: string; detail: React.ReactNode; onCancel: () => void; needsReason?: boolean;
  onConfirm: (proof: Proof, reason: string) => Promise<void>;
}) {
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async () => {
    setBusy(true); setError("");
    try { await onConfirm({ password, totp }, reason); } catch (e) { setError(errMsg(e).replace(/^totp_required: ?/, "")); setBusy(false); }
  };
  const ok = password && /^\d{6}$/.test(totp) && (!needsReason || reason.trim().length >= 5);
  return (
    <Modal title={title} sub="Confirm with your password and a fresh 2FA code" onClose={onCancel} lock={busy}
      foot={<><button className="btn ghost" onClick={onCancel} disabled={busy}>Cancel</button>
        <button className="btn" onClick={submit} disabled={!ok || busy}>{busy && <Spinner />}Confirm</button></>}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); if (ok) submit(); }}>
        <div className="muted small">{detail}</div>
        {needsReason && (
          <label className="field"><span>Reason (recorded in the audit log)</span>
            <textarea rows={2} value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} autoFocus /></label>
        )}
        <label className="field"><span>Password</span>
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus={!needsReason} /></label>
        <label className="field"><span>2FA code</span>
          <input inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={totp} onChange={(e) => setTotp(e.target.value.replace(/\D/g, ""))} /></label>
        {error && <Callout tone="err">{error}</Callout>}
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}

/* ------------------------------------------------------------------ revenue */
interface Bucket { network: string; asset: string; decimals: number; received: number; pending: number; claimable: number; failed: number; estimated: number; estimated_by_status: Record<string, number>; unreconciled: number; entries: number; estimated_entries: number; unknown_amount_entries: number }
interface Summary { totals: Bucket[]; by_source: { source: string; entries: number; by_asset: { network: string; asset: string; decimals: number; received: number; pending: number; claimable: number; failed: number; estimated: number; estimated_by_status: Record<string, number> }[] }[]; referral_liabilities: { network: string; asset: string; amount_raw: string; decimals: number; entries: number }[]; valuation: { method: string; as_of: string }; notes: Record<string, string>; warnings: string[] }
interface LedgerItem { id: string; source: string; network: string; asset: string; amount_raw: string | null; decimals: number; estimated: boolean; status: string; reconciled_at: string | null; destination: string | null; reference: string | null; created_at: string; launch: { id: string; name: string; symbol: string; token: string | null } | null; note: string }

interface RelayFees { wallet: string | null; claim_url: string; as_of: string; source: string; unavailable?: string; total_usd?: number; available_usd?: number;
  balances?: { asset: string; chain_id: number; amount: string; amount_usd: string; minimum: string }[] }

/** What Relay actually holds for the treasury, read from Relay, plus a check of ledger entries against it. */
function RelayCard({ me, onVerified }: { me: AdminMe; onVerified: () => void }) {
  const toast = useToast();
  const [d, setD] = useState<RelayFees | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { api<RelayFees>("/admin/relay-fees").then(setD).catch((e) => setD({ wallet: null, claim_url: "", as_of: "", source: "", unavailable: errMsg(e) })); }, []);
  useEffect(load, [load]);
  const verify = () => {
    setBusy(true);
    api<Record<string, number>>("/admin/revenue/verify-relay", { body: {} })
      .then((r) => {
        const parts = [r.confirmed && `${r.confirmed} confirmed`, r.not_collected && `${r.not_collected} not collected`, r.pending && `${r.pending} still pending`, r.unknown && `${r.unknown} unknown to Relay`, r.error && `${r.error} couldn't be checked`].filter(Boolean);
        toast("good", r.checked ? `Checked ${r.checked}: ${parts.join(", ")}` : "No unchecked route fees");
        onVerified(); load();
      }).catch((e) => toast("bad", errMsg(e))).finally(() => setBusy(false));
  };
  return (
    <Card title="Relay app fees" icon="coins" sub={d?.as_of ? `Read from Relay · ${utc(d.as_of)}` : undefined}
      actions={<>{me.can_change && <button className="btn sm secondary" onClick={verify} disabled={busy}>{busy ? <Spinner /> : <Icon name="check" size={14} />}Verify with Relay</button>}
        <a className="btn sm ghost" href="https://relay.link/claim-app-fees" target="_blank" rel="noreferrer noopener">Claim <Icon name="ext" size={13} /></a></>}>
      {!d ? <Loading rows={1} /> : d.unavailable ? <span className="muted small">{d.unavailable}</span> : (
        <div className="stack tight">
          <div className="row between"><span className="muted small">Available to claim</span><b className="mono">${(d.available_usd ?? 0).toFixed(2)}</b></div>
          {(d.balances ?? []).map((b) => (
            <div key={`${b.asset}-${b.chain_id}`} className="row between small"><span className="muted">{b.asset} · chain {b.chain_id}</span><span className="mono">{b.amount} (${Number(b.amount_usd || 0).toFixed(2)})</span></div>))}
          <span className="muted small">Relay holds these fees until you claim them with the treasury wallet (free to Base). "Verify with Relay" marks each route-fee entry confirmed, or sets it to 0 when Relay took no fee.</span>
        </div>
      )}
    </Card>
  );
}

function Revenue({ me }: { me: AdminMe }) {
  const toast = useToast();
  const [f, setF] = useState({ start: "", end: "", network: "", asset: "", source: "", status: "" });
  const [page, setPage] = useState(1);
  const [sum, setSum] = useState<Summary | null>(null);
  const [led, setLed] = useState<{ total: number; items: LedgerItem[] } | null>(null);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState<string[]>([]);
  const qs = (extra: Record<string, string | number> = {}) => {
    const p = new URLSearchParams();
    Object.entries({ ...f, ...extra }).forEach(([k, v]) => { if (v !== "" && v !== undefined) p.set(k, String(v)); });
    return p.toString();
  };
  const load = useCallback(() => {
    setErr("");
    const { status, ...rest } = f;
    const p = new URLSearchParams(Object.entries(rest).filter(([, v]) => v) as [string, string][]);
    api<Summary>(`/admin/revenue/summary?${p}`).then(setSum).catch((e) => setErr(errMsg(e)));
    api<{ total: number; items: LedgerItem[] }>(`/admin/revenue/ledger?${qs({ page, size: 50 })}`).then(setLed).catch((e) => setErr(errMsg(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [f, page]);
  useEffect(load, [load]);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => { setPage(1); setF({ ...f, [k]: e.target.value }); };
  const reconcile = () => {
    api<{ reconciled: number }>("/admin/revenue/reconcile", { body: { ids: sel, note: "" } })
      .then((r) => { toast("good", `${r.reconciled} entries marked reconciled`); setSel([]); load(); })
      .catch((e) => toast("bad", errMsg(e)));
  };

  if (err && !sum) return <LoadError what="Revenue" error={err} onRetry={load} />;
  return (
    <div className="stack">
      <Card title="Filters" icon="search">
        <div className="grid g-form">
          <label className="field"><span>From (UTC)</span><input type="date" value={f.start} onChange={set("start")} /></label>
          <label className="field"><span>To (UTC)</span><input type="date" value={f.end} onChange={set("end")} /></label>
          <label className="field"><span>Chain</span><select value={f.network} onChange={set("network")}><option value="">All</option>
            {NETWORKS.filter((n) => n !== "evm").map((n) => <option key={n} value={n}>{NET_LABEL[n]}</option>)}</select></label>
          <label className="field"><span>Asset</span><input value={f.asset} placeholder="SOL, ETH, USDC…" onChange={set("asset")} /></label>
          <label className="field"><span>Source</span><select value={f.source} onChange={set("source")}><option value="">All</option>
            {Object.entries(SOURCE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label className="field"><span>Status (ledger)</span><select value={f.status} onChange={set("status")}><option value="">All</option>
            <option value="received">Received</option><option value="pending">Pending</option><option value="claimable">Claimable</option><option value="configured">Configured (not revenue)</option><option value="failed">Failed</option><option value="unreconciled">Unreconciled</option></select></label>
        </div>
      </Card>

      {!sum ? <Loading /> : (
        <>
          <Card title="Totals by asset" icon="coins" sub={`Shown in each asset's own units, never converted or added across assets · as of ${utc(sum.valuation.as_of)}`} flush>
            {sum.totals.length === 0 ? <Empty icon="coins" title="No revenue recorded yet" text="Entries appear here as fees are earned." /> : (
              <TableWrap><table className="rtable">
                <caption className="sr-only">Revenue totals by asset and chain</caption>
                <thead><tr><th>Asset</th><th className="r">Received</th><th className="r">Pending</th><th className="r">Claimable</th><th className="r">Failed</th><th className="r">Estimated</th><th className="r">Unreconciled</th><th className="r hide-sm">Entries</th></tr></thead>
                <tbody>{sum.totals.map((b) => (
                  <tr key={b.network + b.asset}>
                    <td className="span"><b>{b.asset}</b> <span className="muted">on {NET_LABEL[b.network] ?? b.network}</span>
                      {b.estimated_entries > 0 && <span className="badge warn" style={{ marginLeft: 6 }}>{b.estimated_entries} estimated</span>}
                      {b.unknown_amount_entries > 0 && <span className="badge" style={{ marginLeft: 6 }}>{b.unknown_amount_entries} amount unknown</span>}</td>
                    <td className="r mono" data-label="Received">{units(b.received, b.decimals)}</td>
                    <td className="r mono" data-label="Pending">{units(b.pending, b.decimals)}</td>
                    <td className="r mono" data-label="Claimable">{units(b.claimable, b.decimals)}</td>
                    <td className="r mono" data-label="Failed">{units(b.failed, b.decimals)}</td>
                    <td className="r mono" data-label="Estimated">{units(b.estimated, b.decimals)}
                      {b.estimated > 0 && <span className="muted small" style={{ display: "block" }}>{Object.entries(b.estimated_by_status).filter(([, amount]) => amount > 0).map(([status, amount]) => `${units(amount, b.decimals)} ${status}`).join(" · ")}</span>}</td>
                    <td className="r mono" data-label="Unreconciled">{units(b.unreconciled, b.decimals)}</td>
                    <td className="r hide-sm muted">{b.entries}</td>
                  </tr>))}</tbody>
              </table></TableWrap>
            )}
          </Card>
          <Callout tone="info">
            <b>Received</b>: {sum.notes.received} <b>Pending</b>: {sum.notes.pending} <b>Claimable</b>: {sum.notes.claimable} <b>Configured</b>: {sum.notes.configured} <b>Failed</b>: {sum.notes.failed} <b>Estimated</b>: {sum.notes.estimated} Valuation: {sum.valuation.method}.
          </Callout>

          <Card title="Referral liabilities · excluded from Helm revenue" icon="gift">
            <p className="muted small">{sum.notes.referral_liabilities} This is a read-only total; no treasury payout controls are available here.</p>
            {sum.referral_liabilities.length === 0 ? <span className="muted small">No confirmed referral shares are currently owed.</span> : (
              <div className="stack tight">{sum.referral_liabilities.map((liability) => (
                <div key={liability.network} className="row between"><span>{liability.asset} · {NET_LABEL[liability.network] ?? liability.network} · {liability.entries} entries</span>
                  <b className="mono">{units(liability.amount_raw, liability.decimals)} {liability.asset}</b></div>))}</div>
            )}
          </Card>

          <Card title="By source" icon="pie">
            <div className="grid g-2">
              {sum.by_source.map((s) => (
                <div key={s.source} className="stack tight">
                  <div className="row between"><b>{SOURCE_LABEL[s.source] ?? s.source}</b><span className="muted small">{s.entries} entries</span></div>
                  {s.by_asset.length === 0 ? <span className="muted small">Nothing yet</span> : s.by_asset.map((a) => (
                    <div key={a.network + a.asset} className="row between small">
                      <span className="muted">{a.asset} · {NET_LABEL[a.network] ?? a.network}</span>
                      <span className="mono">{units(a.received, a.decimals)} received{a.claimable ? ` · ${units(a.claimable, a.decimals)} claimable` : ""}{a.pending ? ` · ${units(a.pending, a.decimals)} pending` : ""}{a.failed ? ` · ${units(a.failed, a.decimals)} failed` : ""}{a.estimated ? ` · ${units(a.estimated, a.decimals)} estimated (${Object.entries(a.estimated_by_status).filter(([, amount]) => amount > 0).map(([status]) => status).join(", ")})` : ""}</span>
                    </div>))}
                </div>))}
            </div>
          </Card>

          <RelayCard me={me} onVerified={load} />

          {sum.warnings.length > 0 && (
            <Callout tone="warn" icon="alert"><div className="stack tight"><b>Gaps and provider-held revenue</b>
              {sum.warnings.map((w) => <span key={w} className="small">{w}</span>)}</div></Callout>
          )}
        </>
      )}

      <Card title="Ledger" icon="layers" sub={led ? `${led.total} entries · times in UTC` : undefined} flush
        actions={me.can_change && sel.length > 0 ? <button className="btn sm" onClick={reconcile}><Icon name="check" size={14} />Mark {sel.length} reconciled</button> : undefined}
        foot={led && led.total > 50 ? <div className="row between"><button className="btn sm ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
          <span className="muted small">Page {page} of {Math.ceil(led.total / 50)}</span>
          <button className="btn sm ghost" disabled={page * 50 >= led.total} onClick={() => setPage(page + 1)}>Next</button></div> : undefined}>
        {!led ? <Loading /> : led.items.length === 0 ? <Empty icon="layers" title="No entries match" /> : (
          <TableWrap><table className="rtable">
            <caption className="sr-only">Revenue ledger</caption>
            <thead><tr>{me.can_change && <th><span className="sr-only">Select</span></th>}<th>When (UTC)</th><th>Source</th><th>Launch</th><th className="r">Amount</th><th>Status</th><th className="hide-sm">Destination</th><th className="hide-sm">Reference</th></tr></thead>
            <tbody>{led.items.map((r) => (
              <tr key={r.id} className={me.can_change ? "has-cb" : ""}>
                {me.can_change && <td className="cb"><input type="checkbox" aria-label="Select entry" disabled={!!r.reconciled_at} checked={sel.includes(r.id)}
                  onChange={(e) => setSel(e.target.checked ? [...sel, r.id] : sel.filter((x) => x !== r.id))} /></td>}
                <td className="muted small mono" data-label="When">{utc(r.created_at)}</td>
                <td data-label="Source" title={r.note}>{SOURCE_LABEL[r.source] ?? r.source}<div className="muted small">{NET_LABEL[r.network] ?? r.network}</div></td>
                <td data-label="Launch">{r.launch ? <>{r.launch.name} <span className="muted">${r.launch.symbol}</span></> : <span className="muted">—</span>}</td>
                <td className="r mono" data-label="Amount" title={r.amount_raw ? `${r.amount_raw} raw · ${r.decimals} decimals` : r.note}>
                  {r.status === "configured" ? "Not measured" : <>{units(r.amount_raw, r.decimals)} {r.asset}{r.estimated && <div className="muted small">estimated</div>}</>}</td>
                <td data-label="Status"><div><StatusBadge status={r.status === "received" ? "confirmed" : r.status === "failed" ? "failed" : r.status === "claimable" ? "scheduled" : "pending"} label={r.status} /></div>
                  <div className="muted small">{r.reconciled_at ? `reconciled ${utc(r.reconciled_at)}` : "unreconciled"}</div></td>
                <td className="hide-sm mono small">{r.destination ? <span className="row nowrap">{r.destination.slice(0, 6)}…{r.destination.slice(-4)}<CopyButton text={r.destination} /></span> : "—"}</td>
                <td className="hide-sm mono small">{r.reference ? <span className="row nowrap">{r.reference.slice(0, 8)}…<CopyButton text={r.reference} /></span> : "—"}</td>
              </tr>))}</tbody>
          </table></TableWrap>
        )}
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ treasury */
interface TRow { network: string; address: string | null; label: string; source: string | null; updated_at: string | null; effective: string | null; used_for: string[]; explorer: string | null;
  balances?: { network: string; explorer: string; native?: { asset: string; amount: number }; stable?: { asset: string; amount: number }; unavailable?: string }[] }

function Treasury({ me }: { me: AdminMe }) {
  const toast = useToast();
  const [rows, setRows] = useState<TRow[] | null>(null);
  const [custody, setCustody] = useState("");
  const [err, setErr] = useState("");
  const [bal, setBal] = useState(false);
  const [edit, setEdit] = useState<{ network: string; address: string; label: string } | null>(null);
  const [confirm, setConfirm] = useState(false);
  const load = useCallback(() => {
    setErr("");
    api<{ items: TRow[]; custody: string }>(`/admin/treasury${bal ? "?balances=true" : ""}`).then((r) => { setRows(r.items); setCustody(r.custody); }).catch((e) => setErr(errMsg(e)));
  }, [bal]);
  useEffect(load, [load]);
  if (err && !rows) return <LoadError what="Treasury" error={err} onRetry={load} />;
  if (!rows) return <Loading />;
  return (
    <div className="stack">
      <Callout tone="info" icon="lock">{custody} Changing an address affects new fees only; it never moves funds already received.</Callout>
      <div className="row"><button className="btn sm secondary" onClick={() => setBal(!bal)}><Icon name="refresh" size={14} />{bal ? "Hide balances" : "Load balances"}</button></div>
      <div className="grid g-2">
        {rows.map((t) => (
          <Card key={t.network} title={NET_LABEL[t.network] ?? t.network} icon="wallet"
            sub={t.address ? `${t.source === "admin" ? "Set in the dashboard" : "From the environment"}${t.updated_at ? ` · ${utc(t.updated_at)}` : ""}` : t.effective ? "Uses the EVM default" : "Not set: fee paths paying here are off"}
            actions={me.can_change ? <button className="btn sm ghost" onClick={() => setEdit({ network: t.network, address: t.address ?? "", label: t.label })}><Icon name="edit" size={13} />Change</button> : undefined}>
            <div className="stack tight">
              {t.address ? <span className="row nowrap mono small" style={{ wordBreak: "break-all" }}>{t.address}<CopyButton text={t.address} />
                {t.explorer && <a className="icon-btn" href={t.explorer} target="_blank" rel="noreferrer noopener" aria-label="Open in explorer"><Icon name="ext" size={13} /></a>}</span>
                : <span className="muted small">{t.effective ? `Effective: ${t.effective}` : "No address"}</span>}
              {t.label && <span className="small">{t.label}</span>}
              {t.used_for.length > 0 && <span className="muted small">Receives: {t.used_for.join(" · ")}</span>}
              {t.balances?.map((b) => (
                <div key={b.network} className="row between small">
                  <a href={b.explorer} target="_blank" rel="noreferrer noopener" className="muted">{NET_LABEL[b.network] ?? b.network} <Icon name="ext" size={11} /></a>
                  <span className="mono">{b.unavailable ? <span className="muted">{b.unavailable}</span> : `${b.native?.amount.toLocaleString(undefined, { maximumFractionDigits: 6 })} ${b.native?.asset} · ${b.stable?.amount.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${b.stable?.asset}`}</span>
                </div>))}
            </div>
          </Card>))}
      </div>
      {edit && !confirm && (
        <Modal title={`Treasury: ${NET_LABEL[edit.network]}`} sub="Receive-only address; paste a multisig or hardware-wallet address" onClose={() => setEdit(null)}
          foot={<><button className="btn ghost" onClick={() => setEdit(null)}>Cancel</button><button className="btn" onClick={() => setConfirm(true)}>Continue</button></>}>
          <div className="stack">
            <label className="field"><span>Address</span><input className="mono" value={edit.address} onChange={(e) => setEdit({ ...edit, address: e.target.value })} placeholder={edit.network === "solana" ? "Solana address" : "0x…"} autoFocus /></label>
            <label className="field"><span>Label (optional)</span><input value={edit.label} maxLength={80} onChange={(e) => setEdit({ ...edit, label: e.target.value })} /></label>
            <span className="muted small">Leave the address empty to remove the dashboard entry (the environment value, if any, applies again). Never paste a private key or seed phrase anywhere in Helm.</span>
          </div>
        </Modal>
      )}
      {edit && confirm && (
        <StepUpDialog title="Change treasury address" detail={<>New {NET_LABEL[edit.network]} treasury: <span className="mono">{edit.address || "(removed)"}</span>. Check every character before confirming.</>}
          onCancel={() => { setConfirm(false); setEdit(null); }}
          onConfirm={async (proof) => {
            await api(`/admin/treasury/${edit.network}`, { method: "PUT", body: { address: edit.address, label: edit.label, confirm: proof } });
            toast("good", "Treasury address saved"); setConfirm(false); setEdit(null); load();
          }} />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ fees */
interface SettingRow { key: string; bps: number; source: string; updated_at?: string; min: number; max: number; treasury_network: string | null; treasury: string | null; paths: { id: string; label: string; supported: boolean }[] }
interface FeePath { id: string; label: string; networks: string[]; supported: boolean; lands: string | null; how: string }
const SETTING_LABEL: Record<string, string> = {
  trade_fee_bps_solana: "Trading fee: Solana launchpads", trade_fee_bps_evm_dex: "Trading fee: EVM trades routed through a DEX",
  route_fee_bps: "Cross-chain route fee (Relay)", creator_share_bps: "Share of new launches' creator rewards",
};

function Fees({ me }: { me: AdminMe }) {
  const toast = useToast();
  const [data, setData] = useState<{ settings: SettingRow[]; paths: FeePath[]; creator_share_launchpads: Record<string, { bps: number; supported: boolean; note: string }> } | null>(null);
  const [err, setErr] = useState("");
  const [edit, setEdit] = useState<{ key: string; bps: string } | null>(null);
  const load = useCallback(() => { setErr(""); api("/admin/settings").then(setData).catch((e) => setErr(errMsg(e))); }, []);
  useEffect(load, [load]);
  if (err && !data) return <LoadError what="Fee settings" error={err} onRetry={load} />;
  if (!data) return <Loading />;
  return (
    <div className="stack">
      <Callout tone="warn" icon="alert">Rate changes apply to new previews only. Existing launches keep the reward split they were created with. A fee can't be turned on until its treasury address is set and the provider supports it.</Callout>
      <Card title="Rates" icon="coins" flush>
        <TableWrap><table className="rtable">
          <caption className="sr-only">Fee settings</caption>
          <thead><tr><th>Setting</th><th className="r">Rate</th><th>Allowed</th><th>Pays to</th><th className="r"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{data.settings.map((s) => (
            <tr key={s.key}>
              <td className="span"><b>{SETTING_LABEL[s.key] ?? s.key}</b><div className="muted small">{s.source === "admin" ? `Set in the dashboard ${utc(s.updated_at)}` : "From the environment (unchanged)"}</div></td>
              <td className="r mono" data-label="Rate">{(s.bps / 100).toFixed(2)}%</td>
              <td className="muted small" data-label="Allowed">{(s.min / 100).toFixed(2)}–{(s.max / 100).toFixed(2)}%</td>
              <td className="small" data-label="Pays to">{s.key === "creator_share_bps" ? "Each launchpad's chain treasury" : s.treasury ? <span className="mono">{s.treasury.slice(0, 6)}…{s.treasury.slice(-4)}</span> : <span className="badge warn">No treasury: off</span>}</td>
              <td className="r act">{me.can_change && <button className="btn sm ghost" onClick={() => setEdit({ key: s.key, bps: String(s.bps) })}>Change…</button>}</td>
            </tr>))}</tbody>
        </table></TableWrap>
      </Card>
      <Card title="Creator-reward share by launchpad" icon="rocket">
        <div className="stack tight">{Object.entries(data.creator_share_launchpads).map(([lp, c]) => (
          <div key={lp} className="row between small"><b>{lp}</b><span className="muted">{c.note}</span></div>))}
          <span className="muted small">Other launchpads (pump.fun, LetsBonk, Meteora DBC, Four.meme, Flap, Pons, Uniswap v2): not available. Helm doesn't set up their reward splits.</span></div>
      </Card>
      <Card title="Supported revenue paths" icon="layers" flush>
        <TableWrap><table className="rtable">
          <caption className="sr-only">Revenue paths</caption>
          <thead><tr><th>Path</th><th>Status</th><th>Lands as</th><th className="hide-sm">How it works</th></tr></thead>
          <tbody>{data.paths.map((p) => (
            <tr key={p.id}>
              <td className="span"><b>{p.label}</b><div className="muted small">{p.networks.join(", ") || "—"}</div></td>
              <td data-label="Status">{p.supported ? <span className="badge good">Supported</span> : <span className="badge">Unavailable</span>}</td>
              <td data-label="Lands as">{p.lands ?? "—"}</td>
              <td className="hide-sm muted small">{p.how}</td>
            </tr>))}</tbody>
        </table></TableWrap>
      </Card>
      {edit && (
        <StepUpDialog title={SETTING_LABEL[edit.key] ?? edit.key}
          detail={<label className="field"><span>New rate in basis points (100 = 1%)</span>
            <input inputMode="numeric" value={edit.bps} onChange={(e) => setEdit({ ...edit, bps: e.target.value.replace(/\D/g, "") })} /></label>}
          onCancel={() => setEdit(null)}
          onConfirm={async (proof) => {
            await api(`/admin/settings/${edit.key}`, { method: "PUT", body: { bps: Number(edit.bps), confirm: proof } });
            toast("good", "Rate saved"); setEdit(null); load();
          }} />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ users */
interface UserRow { id: string; email: string; display_name: string; registered_at: string; role: string; status: string; last_login: string | null; last_activity: string | null; plan: null; referred_by_code: string | null; launches: number; trades: number; automations: number; wallets: number; two_factor: boolean; flags: { flag: string; reason: string; at: string }[] }

function Users({ me }: { me: AdminMe }) {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("created_at");
  const [order, setOrder] = useState<"desc" | "asc">("desc");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<{ total: number; items: UserRow[] } | null>(null);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<any>(null);
  const load = useCallback(() => {
    setErr("");
    const p = new URLSearchParams({ sort, order, page: String(page), size: "25", ...(query ? { q: query } : {}), ...(status ? { status } : {}) });
    api(`/admin/users?${p}`).then(setData).catch((e) => setErr(errMsg(e)));
  }, [query, sort, order, status, page]);
  useEffect(load, [load]);
  useEffect(() => { api("/admin/metrics?days=30").then(setMetrics).catch(() => undefined); }, []);
  useEffect(() => { const t = setTimeout(() => { setPage(1); setQuery(q.trim()); }, 300); return () => clearTimeout(t); }, [q]);
  const th = (key: string, label: string, cls = "") => (
    <th className={cls} aria-sort={sort === key ? (order === "asc" ? "ascending" : "descending") : "none"}>
      <button className="linklike" onClick={() => { if (sort === key) setOrder(order === "asc" ? "desc" : "asc"); else { setSort(key); setOrder("desc"); } setPage(1); }}>
        {label}{sort === key ? (order === "asc" ? " ↑" : " ↓") : ""}</button></th>);
  const jobs = metrics?.trade_and_deploy_jobs;
  return (
    <div className="stack">
      {metrics && <Metrics label="Platform metrics, last 30 days" items={[
        { label: "Accounts", value: metrics.users_total },
        { label: "New in 30 days", value: metrics.registrations_by_day.reduce((a: number, d: any) => a + d.count, 0) },
        { label: "Signed in, 30 days", value: metrics.active_users.count, hint: metrics.active_users.definition },
        { label: "Launches, 30 days", value: Object.values(metrics.launches_by_status as Record<string, number>).reduce((a, b) => a + b, 0) },
        { label: "Trade & deploy success", value: jobs?.success_rate == null ? "—" : `${(jobs.success_rate * 100).toFixed(1)}%`, hint: jobs?.definition },
      ]} />}
      <Card title="Users" icon="user" sub={data ? `${data.total} accounts · times in UTC${me.can_change ? "" : " · emails masked for viewers"}` : undefined} flush
        actions={<div className="row">
          <SearchInput placeholder="Email, name or account ID" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search users" />
          <select value={status} style={{ width: "auto" }} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label="Filter by status"><option value="">All</option><option value="active">Active</option><option value="suspended">Suspended</option></select>
        </div>}
        foot={data && data.total > 25 ? <div className="row between"><button className="btn sm ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
          <span className="muted small">Page {page} of {Math.ceil(data.total / 25)}</span>
          <button className="btn sm ghost" disabled={page * 25 >= data.total} onClick={() => setPage(page + 1)}>Next</button></div> : undefined}>
        {err && !data ? <LoadError what="Users" error={err} onRetry={load} /> : !data ? <Loading /> : data.items.length === 0 ? <Empty icon="user" title="No users match" /> : (
          <TableWrap><table className="rtable">
            <caption className="sr-only">Users</caption>
            <thead><tr>{th("email", "Account")}{th("created_at", "Registered")}{th("last_login", "Last sign-in", "hide-sm")}<th>Status</th>{th("launches", "Launches", "r")}{th("trades", "Trades", "r hide-sm")}<th className="r hide-sm">Automations</th>{th("wallets", "Wallets", "r")}<th className="hide-sm">Referral</th></tr></thead>
            <tbody>{data.items.map((u) => (
              <tr key={u.id} onClick={() => setOpen(u.id)} style={{ cursor: "pointer" }}>
                <td className="span"><button className="linklike" onClick={() => setOpen(u.id)} style={{ textAlign: "left" }}><b>{u.display_name || u.email}</b></button>
                  <div className="muted small">{u.email} · <span className="mono">{u.id}</span></div>
                  <div className="row small" style={{ gap: 4 }}>{u.role !== "user" && <span className="badge violet">{u.role}</span>}{!u.two_factor && <span className="badge">no 2FA</span>}</div></td>
                <td className="muted small mono" data-label="Registered">{utc(u.registered_at)}</td>
                <td className="muted small mono hide-sm">{utc(u.last_login)}</td>
                <td data-label="Status">{u.status === "suspended" ? <span className="badge bad" title={u.flags[0]?.reason}>Suspended</span> : <span className="badge good">Active</span>}</td>
                <td className="r" data-label="Launches">{u.launches}</td>
                <td className="r hide-sm">{u.trades}</td>
                <td className="r hide-sm">{u.automations}</td>
                <td className="r" data-label="Wallets">{u.wallets}</td>
                <td className="hide-sm muted small">{u.referred_by_code ?? "—"}</td>
              </tr>))}</tbody>
          </table></TableWrap>
        )}
      </Card>
      <span className="muted small">Helm has no plans or subscriptions, so there's no plan column.</span>
      {open && <UserDetail id={open} me={me} onClose={() => setOpen(null)} onChanged={load} />}
    </div>
  );
}

function UserDetail({ id, me, onClose, onChanged }: { id: string; me: AdminMe; onClose: () => void; onChanged: () => void }) {
  const toast = useToast();
  const [u, setU] = useState<any>(null);
  const [err, setErr] = useState("");
  const [action, setAction] = useState<"suspend" | "restore" | "viewer" | "revoke" | null>(null);
  const load = useCallback(() => { api(`/admin/users/${id}`).then(setU).catch((e) => setErr(errMsg(e))); }, [id]);
  useEffect(load, [load]);
  if (action) {
    const path = action === "viewer" ? `/admin/roles/${id}/viewer` : action === "revoke" ? `/admin/roles/${id}/revoke` : `/admin/users/${id}/${action}`;
    const titles = { suspend: "Suspend account", restore: "Restore account", viewer: "Grant read-only admin (viewer)", revoke: "Remove admin role" };
    return <StepUpDialog title={titles[action]} needsReason detail={action === "suspend" ? "Signs the account out everywhere, blocks sign-in and stops its scheduled trades. Funds stay in the user's wallets." : "Recorded in the audit log with your reason."}
      onCancel={() => setAction(null)}
      onConfirm={async (proof, reason) => { await api(path, { body: { reason, confirm: proof } }); toast("good", "Done"); setAction(null); load(); onChanged(); }} />;
  }
  return (
    <Modal title={u ? (u.display_name || u.email) : "Account"} sub={u ? <span className="mono">{u.id}</span> : undefined} onClose={onClose} wide
      foot={me.can_change && u && u.role !== "superadmin" ? <>
        {u.role === "viewer" ? <button className="btn ghost" onClick={() => setAction("revoke")}>Remove admin role</button> : <button className="btn ghost" onClick={() => setAction("viewer")}>Make viewer</button>}
        {u.status === "suspended" ? <button className="btn" onClick={() => setAction("restore")}>Restore access</button> : <button className="btn danger-ghost" onClick={() => setAction("suspend")}>Suspend…</button>}
      </> : undefined}>
      {err ? <Callout tone="err">{err}</Callout> : !u ? <Loading /> : (
        <div className="stack">
          <div className="grid g-form small">
            <div><div className="muted">Email</div>{u.email}</div>
            <div><div className="muted">Registered</div><span className="mono">{utc(u.registered_at)}</span></div>
            <div><div className="muted">Last sign-in</div><span className="mono">{utc(u.last_login)}</span></div>
            <div><div className="muted">Role</div>{u.role}</div>
            <div><div className="muted">Status</div>{u.status}{u.status_reason && <div className="muted">{u.status_reason} · {utc(u.status_changed_at)}</div>}</div>
            <div><div className="muted">2FA</div>{u.two_factor ? "On" : "Off"}</div>
            <div><div className="muted">Referred with</div>{u.referred_by_code ?? "—"}</div>
            <div><div className="muted">Jobs</div>{Object.entries(u.jobs).map(([k, v]) => `${v} ${k}`).join(" · ") || "none"}</div>
          </div>
          <div className="stack tight"><b>Wallets ({u.wallets.total})</b><span className="muted small">{u.wallets.note}</span>
            {u.wallets.by_network.map((n: any) => (
              <div key={n.network} className="small">{NET_LABEL[n.network] ?? n.network}: {n.managed} managed, {n.watch} watch-only
                <span className="muted mono"> {n.sample.map((s: any) => s.address).join(", ")}</span></div>))}</div>
          <div className="stack tight"><b>Launches ({u.launches.length})</b>
            {u.launches.length === 0 ? <span className="muted small">None</span> : u.launches.map((l: any) => (
              <div key={l.id} className="row between small"><span>{l.name} <span className="muted">${l.symbol} · {l.launchpad}</span></span><StatusBadge status={l.status} /></div>))}</div>
          {u.history.length > 0 && <div className="stack tight"><b>Access history</b>{u.history.map((h: any, i: number) => (
            <div key={i} className="small muted">{utc(h.at)} · {h.action} · {h.changes?.reason?.[1] ?? ""}</div>))}</div>}
        </div>
      )}
    </Modal>
  );
}

/* ------------------------------------------------------------------ audit */
function Audit() {
  const [data, setData] = useState<{ total: number; items: any[] } | null>(null);
  const [err, setErr] = useState("");
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const load = useCallback(() => {
    api(`/admin/audit?page=${page}&size=50${action ? `&action=${action}` : ""}`).then(setData).catch((e) => setErr(errMsg(e)));
  }, [page, action]);
  useEffect(load, [load]);
  return (
    <Card title="Audit log" icon="shield" sub="Append-only · every admin sign-in and change · times in UTC" flush
      actions={<select value={action} style={{ width: "auto" }} onChange={(e) => { setAction(e.target.value); setPage(1); }} aria-label="Filter by action">
        <option value="">All actions</option><option value="admin.session">Admin sign-ins</option><option value="treasury">Treasury</option>
        <option value="setting">Fee settings</option><option value="user.">User access</option><option value="role">Roles</option><option value="revenue">Reconciliation</option></select>}
      foot={data && data.total > 50 ? <div className="row between"><button className="btn sm ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
        <span className="muted small">Page {page}</span><button className="btn sm ghost" disabled={page * 50 >= data.total} onClick={() => setPage(page + 1)}>Next</button></div> : undefined}>
      {err && !data ? <LoadError what="Audit log" error={err} onRetry={load} /> : !data ? <Loading /> : data.items.length === 0 ? <Empty icon="shield" title="Nothing recorded yet" /> : (
        <TableWrap><table className="rtable">
          <caption className="sr-only">Audit log</caption>
          <thead><tr><th>When</th><th>Action</th><th>Target</th><th>Changes</th><th className="hide-sm">Actor · IP</th></tr></thead>
          <tbody>{data.items.map((e) => (
            <tr key={e.id}>
              <td className="mono small muted" data-label="When">{utc(e.at)}</td>
              <td data-label="Action"><b>{e.action}</b></td>
              <td className="mono small" data-label="Target">{e.target ?? "—"}</td>
              <td className="small span">{Object.entries(e.changes ?? {}).map(([k, v]: any) => <div key={k}><span className="muted">{k}:</span> {String(v?.[0] ?? "—")} → {String(v?.[1] ?? "—")}</div>)}</td>
              <td className="hide-sm mono small muted">{e.actor?.slice(0, 8)} · {e.context?.ip ?? "—"}</td>
            </tr>))}</tbody>
        </table></TableWrap>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ page */
export default function Admin() {
  const [me, setMe] = useState<AdminMe | null>(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState<Tab>("revenue");
  useEffect(() => { api<AdminMe>("/admin/me").then(setMe).catch((e) => setErr(errMsg(e))); }, []);
  if (err) return <Empty icon="lock" title="Admin access only" text={err === "not allowed" ? "This account doesn't have admin access." : err} />;
  if (!me) return <Loading />;
  return (
    <>
      <PageHeader title="Admin" sub={me.role === "superadmin" ? "Superadmin · changes need your password and a fresh 2FA code" : "Viewer · read-only"} />
      <Seg<Tab> options={[["revenue", "Revenue"], ["treasury", "Treasury"], ["fees", "Fees"], ["users", "Users"], ["audit", "Audit log"], ["operations", "Operations"]]} value={tab} onChange={setTab} />
      {tab === "revenue" && <Revenue me={me} />}
      {tab === "treasury" && <Treasury me={me} />}
      {tab === "fees" && <Fees me={me} />}
      {tab === "users" && <Users me={me} />}
      {tab === "audit" && <Audit />}
      {tab === "operations" && <Operations />}
    </>
  );
}

function Operations() {
  const [config, setConfig] = useState<ConfigDiagnostics | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api<ConfigDiagnostics>("/admin/config").then(setConfig).catch((e) => setError(errMsg(e))); }, []);
  if (error) return <LoadError what="Operational configuration" error={error} onRetry={() => {
    setError(""); api<ConfigDiagnostics>("/admin/config").then(setConfig).catch((e) => setError(errMsg(e)));
  }} />;
  if (!config) return <Loading />;
  const list = (title: string, values: Record<string, boolean>) => (
    <Card title={title} icon="settings">
      <div className="stack tight">{Object.entries(values).map(([name, ready]) => (
        <div className="row between" key={name}><span className="mono small">{name.replace(/_/g, " ")}</span>
          <StatusBadge status={ready ? "available" : "failed"} label={ready ? "configured" : "not configured"} /></div>
      ))}</div>
    </Card>
  );
  return <div className="stack">
    <Callout tone="info">Only configuration presence is shown. Secret values, endpoint URLs, and treasury addresses are never returned here.</Callout>
    <Card title="Runtime safeguards" icon="shield">
      <div className="stack tight">
        <div className="row between"><span>Signer mode</span><StatusBadge status={config.signer === "turnkey" ? "available" : "pending"} label={config.signer} /></div>
        <div className="row between"><span>Superadmin user ID</span><StatusBadge status={config.superadmin_id_configured ? "available" : "failed"} label={config.superadmin_id_configured ? "configured" : "missing · admin access disabled"} /></div>
        <div className="row between"><span>Trusted proxy ranges</span><StatusBadge status={config.trusted_proxy_ranges_configured ? "available" : "pending"} label={config.trusted_proxy_ranges_configured ? "configured" : "unset · forwarded IPs ignored"} /></div>
      </div>
    </Card>
    <div className="grid g-2">{list("RPC endpoints", config.rpc_configured)}{list("Provider keys", config.external_provider_keys_configured)}</div>
  </div>;
}
