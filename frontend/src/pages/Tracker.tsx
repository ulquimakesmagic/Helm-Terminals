import { useEffect, useState } from "react";
import { api, ApiError, short } from "../api";

interface Item { id: string; address: string; label: string; bookmarked: boolean }

export default function Tracker() {
  const [items, setItems] = useState<Item[]>([]);
  const [address, setAddress] = useState("");
  const [label, setLabel] = useState("");
  const [error, setError] = useState("");

  const load = () => api<Item[]>("/tracker").then(setItems).catch((e) => setError(e.detail));
  useEffect(() => { load(); }, []);

  const act = (p: Promise<unknown>) => p.then(() => { setError(""); return load(); }).catch((e) => setError((e as ApiError).detail));

  return (
    <>
      <h1>Tracker</h1>
      <p className="muted">Keep external wallet addresses in one list, with labels and bookmarks.</p>
      {error && <div className="banner err">{error}</div>}
      <div className="card row" style={{ marginBottom: 16 }}>
        <input placeholder="Solana address" value={address} onChange={(e) => setAddress(e.target.value)} style={{ flex: 2 }} />
        <input placeholder="Label" value={label} onChange={(e) => setLabel(e.target.value)} style={{ flex: 1 }} />
        <button onClick={() => act(api("/tracker", { body: { address, label } }).then(() => { setAddress(""); setLabel(""); }))}>Add</button>
      </div>
      <div className="card">
        <table>
          <thead><tr><th /><th>Label</th><th>Address</th><th /></tr></thead>
          <tbody>
            {items.map((i) => (
              <tr key={i.id}>
                <td><button className="ghost" onClick={() => act(api(`/tracker/${i.id}?bookmarked=${!i.bookmarked}`, { method: "PATCH", body: {} }))}>{i.bookmarked ? "★" : "☆"}</button></td>
                <td>{i.label}</td>
                <td className="mono"><a href={`https://solscan.io/account/${i.address}`} target="_blank" rel="noreferrer">{short(i.address)}</a></td>
                <td><button className="ghost" onClick={() => act(api(`/tracker/${i.id}`, { method: "DELETE" }))}>Remove</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        {items.length === 0 && <p className="muted">Nothing tracked yet.</p>}
      </div>
    </>
  );
}
