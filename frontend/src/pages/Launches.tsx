import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, short } from "../api";

interface Launch { id: string; name: string; symbol: string; status: string; launchpad: string; mint_address: string | null; imported: boolean }
interface Pad { id: string; network: string; status: string }
interface W { id: string; label: string; address: string; kind: string }

function toBase64(file: File): Promise<string> {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(String(r.result).split(",")[1] ?? "");
    r.onerror = rej;
    r.readAsDataURL(file);
  });
}

export default function Launches() {
  const nav = useNavigate();
  const [launches, setLaunches] = useState<Launch[]>([]);
  const [archived, setArchived] = useState<Launch[]>([]);
  const [pads, setPads] = useState<Pad[]>([]);
  const [wallets, setWallets] = useState<W[]>([]);
  const [error, setError] = useState("");
  const [f, setF] = useState({ launchpad: "pumpfun", name: "", symbol: "", description: "", twitter: "", telegram: "", website: "", dev_wallet_id: "", dev_buy_sol: "0" });
  const [image, setImage] = useState<File | null>(null);
  const [mint, setMint] = useState("");
  const [showArchived, setShowArchived] = useState(false);

  const load = async () => {
    try {
      const [l, a, p, w] = await Promise.all([
        api<Launch[]>("/launches"), api<Launch[]>("/launches?status=archived"), api<Pad[]>("/launchpads"), api<W[]>("/wallets"),
      ]);
      setLaunches(l); setArchived(a); setPads(p); setWallets(w.filter((x) => x.kind === "managed"));
    } catch (e) { setError((e as ApiError).detail); }
  };
  useEffect(() => { load(); }, []);

  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value });

  async function create() {
    setError("");
    try {
      const l = await api<Launch>("/launches", { body: {
        launchpad: f.launchpad, name: f.name, symbol: f.symbol, description: f.description,
        socials: { twitter: f.twitter, telegram: f.telegram, website: f.website },
        dev_wallet_id: f.dev_wallet_id || null, dev_buy_sol: +f.dev_buy_sol,
        image_b64: image ? await toBase64(image) : null,
      } });
      nav(`/launches/${l.id}`);
    } catch (e) { setError((e as ApiError).detail); }
  }

  const tryIt = (p: Promise<Launch>) => p.then((l) => nav(`/launches/${l.id}`)).catch((e) => setError((e as ApiError).detail));

  return (
    <>
      <h1>Launches</h1>
      {error && <div className="banner err">{error}</div>}
      <div className="grid cols2" style={{ marginBottom: 16 }}>
        <div className="card stack">
          <h2>Create a launch</h2>
          <label>Launchpad
            <select value={f.launchpad} onChange={set("launchpad")}>
              {pads.map((p) => <option key={p.id} value={p.id} disabled={p.status !== "available"}>{p.id} ({p.network}){p.status !== "available" ? " — planned" : ""}</option>)}
            </select>
          </label>
          <div className="row">
            <label style={{ flex: 2 }}>Name<input value={f.name} onChange={set("name")} maxLength={32} /></label>
            <label style={{ flex: 1 }}>Symbol<input value={f.symbol} onChange={set("symbol")} maxLength={10} /></label>
          </div>
          <label>Description<textarea value={f.description} onChange={set("description")} rows={2} maxLength={500} /></label>
          <div className="row">
            <label style={{ flex: 1 }}>X<input value={f.twitter} onChange={set("twitter")} placeholder="x.com/…" /></label>
            <label style={{ flex: 1 }}>Telegram<input value={f.telegram} onChange={set("telegram")} placeholder="t.me/…" /></label>
            <label style={{ flex: 1 }}>Website<input value={f.website} onChange={set("website")} /></label>
          </div>
          <label>Image (max 2 MB)<input type="file" accept="image/*" onChange={(e) => setImage(e.target.files?.[0] ?? null)} /></label>
          <div className="row">
            <label style={{ flex: 2 }}>Dev wallet
              <select value={f.dev_wallet_id} onChange={set("dev_wallet_id")}>
                <option value="">Choose…</option>
                {wallets.map((w) => <option key={w.id} value={w.id}>{w.label || short(w.address)}</option>)}
              </select>
            </label>
            <label style={{ flex: 1 }}>Dev buy (SOL)<input value={f.dev_buy_sol} onChange={set("dev_buy_sol")} /></label>
          </div>
          <button disabled={!f.name || !f.symbol} onClick={create}>Create draft</button>
          <p className="muted" style={{ margin: 0 }}>The draft is not sent on-chain until you review and confirm the deployment.</p>
        </div>

        <div className="card stack">
          <h2>Clone or import a token</h2>
          <label>Token mint address<input value={mint} onChange={(e) => setMint(e.target.value)} className="mono" /></label>
          <div className="row">
            <button className="ghost" disabled={!mint} onClick={() => tryIt(api<Launch>("/launches/clone", { body: { mint, dev_wallet_id: f.dev_wallet_id || null } }))}>Clone metadata into a draft</button>
            <button className="ghost" disabled={!mint} onClick={() => tryIt(api<Launch>("/launches/import", { body: { mint } }))}>Import existing token</button>
          </div>
          <p className="muted" style={{ margin: 0 }}>
            Clone copies name, symbol and description (not the image). Import lets you attach your own wallets to an existing token to trade it and see the supply transparency panel.
          </p>
          <h3 style={{ marginTop: 12 }}>Launchpad roadmap</h3>
          <div className="row">
            {pads.map((p) => <span key={p.id} className={`badge ${p.status === "available" ? "live" : "planned"}`}>{p.id}</span>)}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="row between"><h2>Your launches</h2><button className="ghost" onClick={() => setShowArchived(!showArchived)}>{showArchived ? "Hide" : "Show"} archived ({archived.length})</button></div>
        <table>
          <thead><tr><th>Token</th><th>Status</th><th>Launchpad</th><th>Mint</th></tr></thead>
          <tbody>
            {[...launches, ...(showArchived ? archived : [])].map((l) => (
              <tr key={l.id}>
                <td><Link to={`/launches/${l.id}`}>{l.name} <span className="muted">${l.symbol}</span></Link></td>
                <td><span className={`badge ${l.status}`}>{l.status}</span>{l.imported && <span className="badge"> imported</span>}</td>
                <td>{l.launchpad}</td>
                <td className="mono">{l.mint_address ? short(l.mint_address) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {launches.length === 0 && <p className="muted">No launches yet.</p>}
      </div>
    </>
  );
}
