import { useEffect, useState } from "react";
import { api, fmt } from "../api";

const PERIODS = [["all", "All-time"], ["1y", "1 Year"], ["1m", "1 Month"], ["7d", "7 Days"], ["1d", "1 Day"]];

function Line({ points }: { points: { date: string; cumulative_sol: number }[] }) {
  if (!points.length) return <p className="muted" style={{ textAlign: "center", padding: 40 }}>No transactions yet.</p>;
  const w = 560, h = 180, pad = 8;
  const ys = points.map((p) => p.cumulative_sol);
  const min = Math.min(0, ...ys), max = Math.max(0, ...ys) || 1;
  const x = (i: number) => pad + (points.length === 1 ? (w - 2 * pad) / 2 : (i * (w - 2 * pad)) / (points.length - 1));
  const y = (v: number) => h - pad - ((v - min) / (max - min || 1)) * (h - 2 * pad);
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.cumulative_sol)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" role="img" aria-label="Cumulative net SOL flow">
      <line x1={pad} x2={w - pad} y1={y(0)} y2={y(0)} stroke="var(--line)" />
      <path d={d} fill="none" stroke="var(--accent)" strokeWidth="2" />
    </svg>
  );
}

export default function Dashboard() {
  const [period, setPeriod] = useState("all");
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");

  const load = () => api(`/dashboard?period=${period}`).then(setData).catch((e) => setError(e.detail));
  useEffect(() => { load(); }, [period]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div className="row between">
        <h1>Dashboard</h1>
        <div className="row">
          {PERIODS.map(([k, l]) => (
            <button key={k} className={period === k ? "" : "ghost"} onClick={() => setPeriod(k)}>{l}</button>
          ))}
        </div>
      </div>
      {error && <div className="banner err">{error}</div>}
      {data && (
        <div className="stack" style={{ gap: 16 }}>
          <div className="grid cols4">
            <div className="card stat"><div className="l">Net SOL flow</div><div className={`v ${data.net_sol_flow >= 0 ? "good" : "bad"}`}>{fmt(data.net_sol_flow)} SOL</div></div>
            <div className="card stat"><div className="l">Launches</div><div className="v">{data.total_launches}</div></div>
            <div className="card stat"><div className="l">Referral codes</div><div className="v">{data.referral.codes.length}</div></div>
          </div>
          <div className="card">
            <h2>Cumulative net SOL flow</h2>
            <Line points={data.series} />
            <p className="muted" style={{ marginBottom: 0 }}>{data.note}</p>
          </div>
          <div className="card">
            <div className="row between">
              <h2>Referral program</h2>
              <button className="ghost" onClick={() => api("/referrals", { method: "POST", body: {} }).then(load)}>New code</button>
            </div>
            {data.referral.codes.length === 0 ? <p className="muted">No codes yet.</p> : (
              <table><tbody>
                {data.referral.codes.map((c: any) => (
                  <tr key={c.code}><td className="mono">{c.code}</td><td>{c.uses}/{c.max_uses} used</td></tr>
                ))}
              </tbody></table>
            )}
          </div>
        </div>
      )}
    </>
  );
}
