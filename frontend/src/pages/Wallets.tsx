import { useEffect, useState } from "react";
import { api, ApiError, fmt, short } from "../api";
import SpendDialog from "../components/SpendDialog";

interface W { id: string; address: string; kind: string; label: string; folder_id: string | null; balance_sol?: number }
interface Folder { id: string; name: string; color: string }
interface Funder { id: string; wallet_id: string; label: string; is_default: boolean }

export default function Wallets() {
  const [wallets, setWallets] = useState<W[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [funders, setFunders] = useState<Funder[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [count, setCount] = useState(1);
  const [prefix, setPrefix] = useState("Wallet");
  const [watchAddr, setWatchAddr] = useState("");
  const [folderName, setFolderName] = useState("");
  const [fundFrom, setFundFrom] = useState("");
  const [fundAmount, setFundAmount] = useState("0.05");
  const [dest, setDest] = useState("");
  const [error, setError] = useState("");
  const [dialog, setDialog] = useState<null | { title: string; path: string; body: Record<string, unknown> }>(null);

  const load = async (balances = false) => {
    try {
      const [w, f, fu] = await Promise.all([
        api<W[]>(`/wallets${balances ? "?with_balances=true" : ""}`),
        api<Folder[]>("/folders"),
        api<Funder[]>("/funders"),
      ]);
      setWallets(w); setFolders(f); setFunders(fu);
    } catch (e) { setError((e as ApiError).detail); }
  };
  useEffect(() => { load(); }, []);

  const run = (p: Promise<unknown>) => p.then(() => { setError(""); return load(); }).catch((e) => setError((e as ApiError).detail));
  const managed = wallets.filter((w) => w.kind === "managed");
  const sel = [...selected];
  const toggle = (id: string) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const folderOf = (id: string | null) => folders.find((f) => f.id === id);

  return (
    <>
      <div className="row between"><h1>My Wallets</h1><button className="ghost" onClick={() => load(true)}>Refresh balances</button></div>
      {error && <div className="banner err">{error}</div>}
      <div className="banner info">
        Wallets are created and held by the signing service (Turnkey in production). Private keys are never shown or stored by this app.
      </div>

      <div className="grid cols2" style={{ marginBottom: 16 }}>
        <div className="card stack">
          <h2>Create wallets</h2>
          <div className="row">
            <label>Count<input type="number" min={1} max={50} value={count} onChange={(e) => setCount(+e.target.value)} style={{ width: 80 }} /></label>
            <label>Label prefix<input value={prefix} onChange={(e) => setPrefix(e.target.value)} /></label>
          </div>
          <button onClick={() => run(api("/wallets", { body: { count, label_prefix: prefix } }))}>Create</button>
          <h3>Watch-only address</h3>
          <div className="row">
            <input placeholder="Solana address" value={watchAddr} onChange={(e) => setWatchAddr(e.target.value)} style={{ flex: 1 }} />
            <button className="ghost" onClick={() => run(api("/wallets/watch", { body: { address: watchAddr } }).then(() => setWatchAddr("")))}>Add</button>
          </div>
        </div>
        <div className="card stack">
          <h2>Fund and withdraw</h2>
          <div className="row">
            <label>Funder wallet
              <select value={fundFrom} onChange={(e) => setFundFrom(e.target.value)}>
                <option value="">Choose…</option>
                {managed.map((w) => <option key={w.id} value={w.id}>{w.label || short(w.address)}</option>)}
              </select>
            </label>
            <label>SOL per wallet<input value={fundAmount} onChange={(e) => setFundAmount(e.target.value)} style={{ width: 90 }} /></label>
          </div>
          <button disabled={!fundFrom || !sel.length} onClick={() => setDialog({
            title: "Fund wallets", path: "/wallets/fund",
            body: { funder_wallet_id: fundFrom, allocations: sel.filter((id) => id !== fundFrom).map((id) => ({ wallet_id: id, sol: +fundAmount })) },
          })}>Fund {sel.length} selected</button>
          <div className="row">
            <input placeholder="Withdraw destination address" value={dest} onChange={(e) => setDest(e.target.value)} style={{ flex: 1 }} />
            <button className="ghost" disabled={!dest || !sel.length} onClick={() => setDialog({
              title: "Withdraw SOL", path: "/wallets/withdraw", body: { wallet_ids: sel, destination: dest, all: true },
            })}>Withdraw all</button>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="row between">
          <h2>Wallets ({wallets.length})</h2>
          <div className="row">
            <input placeholder="New folder" value={folderName} onChange={(e) => setFolderName(e.target.value)} />
            <button className="ghost" onClick={() => run(api("/folders", { body: { name: folderName } }).then(() => setFolderName("")))}>Add folder</button>
          </div>
        </div>
        <table>
          <thead><tr><th /><th>Label</th><th>Address</th><th>Type</th><th>Folder</th><th>Balance</th><th /></tr></thead>
          <tbody>
            {wallets.map((w) => (
              <tr key={w.id}>
                <td><input type="checkbox" checked={selected.has(w.id)} onChange={() => toggle(w.id)} /></td>
                <td>{w.label}</td>
                <td className="mono"><a href={`https://solscan.io/account/${w.address}`} target="_blank" rel="noreferrer">{short(w.address)}</a></td>
                <td><span className="badge">{w.kind}</span></td>
                <td>{folderOf(w.folder_id)?.name ?? ""}</td>
                <td>{w.balance_sol !== undefined ? `${fmt(w.balance_sol)} SOL` : "—"}</td>
                <td>
                  {w.kind === "managed" && !funders.some((f) => f.wallet_id === w.id) && (
                    <button className="ghost" onClick={() => run(api("/funders", { body: { wallet_id: w.id, label: w.label || "Funder", is_default: funders.length === 0 } }))}>Make funder</button>
                  )}
                  {funders.find((f) => f.wallet_id === w.id) && <span className="badge yours">funder{funders.find((f) => f.wallet_id === w.id)?.is_default ? " (default)" : ""}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {wallets.length === 0 && <p className="muted">No wallets yet.</p>}
      </div>

      {dialog && <SpendDialog {...dialog} onClose={() => setDialog(null)} onDone={() => load(true)} />}
    </>
  );
}
