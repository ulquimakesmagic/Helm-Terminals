import { useState } from "react";
import { api, errMsg } from "../api";
import { Address, Callout, Spinner } from "./ui";

type Row = { label: string; asset: string; amount_raw: string | null; scope: string; unavailable?: string };
type Snapshot = { source: string; checked_at: string; recipient: string; rows: Row[]; routing_matches_splitter: boolean | null; note: string; sweep: string; payouts: string };
const NATIVE = "0x0000000000000000000000000000000000000000";

function quantity(row: Row) {
  if (row.amount_raw === null) return "Unavailable";
  if (row.asset.toLowerCase() !== NATIVE) return `${row.amount_raw} base units`;
  const n = BigInt(row.amount_raw), unit = 10n ** 18n;
  const fraction = (n % unit).toString().padStart(18, "0").replace(/0+$/, "");
  return `${n / unit}${fraction ? `.${fraction}` : ""} ETH`;
}

export default function PonsRewards({ launchId }: { launchId: string }) {
  const [data, setData] = useState<Snapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function load() {
    setBusy(true); setError("");
    try { setData(await api(`/launches/${launchId}/pons-rewards`)); }
    catch (e) { setError(errMsg(e)); }
    finally { setBusy(false); }
  }
  return <section className="stack tight" aria-label="Pons reward stages" aria-busy={busy}>
    <button className="btn secondary sm" disabled={busy} onClick={load}>{busy ? <Spinner /> : null}{data ? "Refresh reward balances" : "Load reward balances"}</button>
    {error && <Callout tone="warn">{error}{data && " Previous readings below are stale."}</Callout>}
    {data && <>
      <span className="small muted">{data.source} · checked {new Date(data.checked_at).toLocaleString()}</span>
      {data.routing_matches_splitter === false && <Callout tone="warn">Pons' current recipient differs from this launch's configured reward address. Future fees may bypass the split. Existing splitter funds are shown separately.</Callout>}
      <span className="small">Current recipient <Address address={data.recipient} network="robinhood" /></span>
      <dl className="stack tight" style={{ margin: 0 }}>
        {data.rows.map((row, i) => <div key={i} style={{ padding: "8px 0", borderBottom: "1px solid var(--line)", minWidth: 0 }}>
          <dt className="small">{row.label}</dt>
          <dd className="mono small" style={{ margin: "4px 0", overflowWrap: "anywhere" }}>{quantity(row)}</dd>
          <span className="muted small">{row.unavailable ?? row.scope}</span>
          {row.asset.toLowerCase() !== NATIVE && <Address address={row.asset} network="robinhood" />}
        </div>)}
      </dl>
      <p className="small muted">{data.note}</p>
      <p className="small muted">{data.sweep}</p>
      <p className="small muted">{data.payouts}</p>
    </>}
  </section>;
}
