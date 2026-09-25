import { useEffect, useState } from "react";
import { api } from "../api";

interface A { id: string; kind: string; ref_type: string | null; ref_id: string | null; payload: Record<string, unknown>; created_at: string }

export default function Activity() {
  const [rows, setRows] = useState<A[]>([]);
  const [error, setError] = useState("");
  useEffect(() => { api<A[]>("/activity").then(setRows).catch((e) => setError(e.detail)); }, []);

  return (
    <>
      <h1>Activity</h1>
      {error && <div className="banner err">{error}</div>}
      <div className="card">
        <table>
          <thead><tr><th>Time</th><th>Event</th><th>Reference</th><th>Details</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="muted">{new Date(r.created_at + "Z").toLocaleString()}</td>
                <td>{r.kind}</td>
                <td className="mono">{r.ref_type ? `${r.ref_type}:${r.ref_id?.slice(0, 8)}` : ""}</td>
                <td className="mono muted">{Object.keys(r.payload).length ? JSON.stringify(r.payload) : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <p className="muted">No activity yet.</p>}
      </div>
    </>
  );
}
