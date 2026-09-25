import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, errMsg, short } from "../api";
import { CHAIN_ORDER, CHAINS, ChainId, chainOf, Pad, padName } from "../chains";
import { LaunchpadDirectory } from "../components/Launchpads";
import {
  Address, Callout, Card, ChainBadge, ChainDot, Empty, Field, Icon, Loading, Modal, PageHeader, SearchInput, Seg, Spinner, StatusBadge, TokenAvatar, UnitInput, relTime, useToast,
} from "../components/ui";

interface Launch {
  id: string; network: string; name: string; symbol: string; status: string; launchpad: string; mint_address: string | null;
  imported: boolean; created_at: string; dev_buy_sol: number; description: string;
}
interface W { id: string; label: string; address: string; kind: string; network: string }

type Filter = "all" | "live" | "draft" | "archived";

function toBase64(file: File): Promise<string> {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(String(r.result).split(",")[1] ?? "");
    r.onerror = rej;
    r.readAsDataURL(file);
  });
}

const EMPTY_FORM = { launchpad: "pumpfun", name: "", symbol: "", description: "", twitter: "", telegram: "", website: "", dev_wallet_id: "", dev_buy_sol: "0" };

function CreateLaunch({ pads, wallets, onClose }: { pads: Pad[]; wallets: W[]; onClose: () => void }) {
  const nav = useNavigate();
  const [f, setF] = useState(EMPTY_FORM);
  const [image, setImage] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pad = pads.find((p) => p.id === f.launchpad);
  const chain = chainOf(pad?.network);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setF({ ...f, [k]: k === "symbol" ? e.target.value.toUpperCase() : e.target.value });

  useEffect(() => {
    if (!image) { setPreview(null); return; }
    const u = URL.createObjectURL(image);
    setPreview(u);
    return () => URL.revokeObjectURL(u);
  }, [image]);

  async function create() {
    setError("");
    if (image && image.size > 2_000_000) { setError("Image must be 2 MB or smaller."); return; }
    setBusy(true);
    try {
      const l = await api<Launch>("/launches", { body: {
        launchpad: f.launchpad, name: f.name.trim(), symbol: f.symbol.trim(), description: f.description,
        socials: { twitter: f.twitter, telegram: f.telegram, website: f.website },
        dev_wallet_id: f.dev_wallet_id || null, dev_buy_sol: +f.dev_buy_sol || 0,
        image_b64: image ? await toBase64(image) : null,
      } });
      nav(`/launches/${l.id}`);
    } catch (e) { setError(errMsg(e)); setBusy(false); }
  }

  return (
    <Modal wide title="New launch" sub="Pick a chain and launchpad, then fill in the token. Nothing is sent on-chain until you confirm the deployment." onClose={onClose}
      foot={<>
        <span className="muted small grow">{!f.dev_wallet_id && "You can pick a dev wallet later, before deploying."}</span>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn" disabled={!f.name || !f.symbol || busy} onClick={create}>{busy ? <Spinner /> : <Icon name="check" size={15} />}Create draft</button>
      </>}>
      {error && <Callout tone="err">{error}</Callout>}
      <div>
        <h3 style={{ marginBottom: 10 }}>1 · Chain and launchpad</h3>
        <LaunchpadDirectory pads={pads} picked={f.launchpad} onPick={(p) => setF({ ...f, launchpad: p.id })} />
      </div>
      <div className="divider" />
      <h3>2 · Token</h3>
      <div className="row" style={{ alignItems: "stretch", gap: 16, flexWrap: "nowrap" }}>
        <label style={{ cursor: "pointer", flex: "none" }} title="Upload image">
          <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden onChange={(e) => setImage(e.target.files?.[0] ?? null)} />
          {preview ? (
            <img src={preview} alt="" style={{ width: 112, height: 112, borderRadius: 18, objectFit: "cover", border: "1px solid var(--line-2)" }} />
          ) : (
            <div style={{ width: 112, height: 112, borderRadius: 18, border: "1.5px dashed var(--line-2)", display: "grid", placeItems: "center", color: "var(--muted)", textAlign: "center", fontSize: 11.5 }}>
              <div><Icon name="image" size={22} /><div style={{ marginTop: 6 }}>Add image<br />max 2 MB</div></div>
            </div>
          )}
        </label>
        <div className="stack grow">
          <div className="row nowrap" style={{ alignItems: "flex-start" }}>
            <Field label="Token name" extra={`${f.name.length}/32`} style={{ flex: 2 }}><input value={f.name} onChange={set("name")} maxLength={32} placeholder="Helm Cat" autoFocus /></Field>
            <Field label="Ticker" style={{ flex: 1 }}><input value={f.symbol} onChange={set("symbol")} maxLength={10} placeholder="HCAT" className="mono" /></Field>
          </div>
          <div className="row small muted" style={{ gap: 8 }}>
            Launching on <ChainBadge network={chain.id} /> via <b className="text-2">{padName(f.launchpad)}</b>
          </div>
        </div>
      </div>
      <Field label="Description" extra={`${f.description.length}/500`}>
        <textarea value={f.description} onChange={set("description")} rows={3} maxLength={500} placeholder="What is this token about?" />
      </Field>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10 }}>
        <Field label="X / Twitter"><input value={f.twitter} onChange={set("twitter")} placeholder="https://x.com/…" /></Field>
        <Field label="Telegram"><input value={f.telegram} onChange={set("telegram")} placeholder="https://t.me/…" /></Field>
        <Field label="Website"><input value={f.website} onChange={set("website")} placeholder="https://…" /></Field>
      </div>
      <div className="divider" />
      <div className="row nowrap" style={{ alignItems: "flex-start" }}>
        <Field label="Dev wallet" style={{ flex: 2 }} hint={wallets.length === 0 ? "Create a managed wallet on the Wallets page first." : "Signs the create transaction and makes the dev buy."}>
          <select value={f.dev_wallet_id} onChange={set("dev_wallet_id")}>
            <option value="">Choose later…</option>
            {wallets.filter((w) => w.network === chain.id).map((w) => <option key={w.id} value={w.id}>{w.label || "Wallet"} · {short(w.address)}</option>)}
          </select>
        </Field>
        <Field label="Dev buy" style={{ flex: 1 }}><UnitInput unit={chain.native} value={f.dev_buy_sol} onChange={set("dev_buy_sol")} /></Field>
      </div>
    </Modal>
  );
}

function ImportLaunch({ wallets, onClose }: { wallets: W[]; onClose: () => void }) {
  const nav = useNavigate();
  const [mode, setMode] = useState<"import" | "clone">("import");
  const [mint, setMint] = useState("");
  const [dev, setDev] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function go() {
    setBusy(true); setError("");
    try {
      const l = await api<Launch>(mode === "import" ? "/launches/import" : "/launches/clone", { body: mode === "import" ? { mint: mint.trim() } : { mint: mint.trim(), dev_wallet_id: dev || null } });
      nav(`/launches/${l.id}`);
    } catch (e) { setError(errMsg(e)); setBusy(false); }
  }
  return (
    <Modal title="Import or clone a token" onClose={onClose}
      foot={<><button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn" disabled={!mint.trim() || busy} onClick={go}>{busy && <Spinner />}{mode === "import" ? "Import token" : "Clone into draft"}</button></>}>
      <Seg full value={mode} onChange={setMode} options={[["import", "Import existing"], ["clone", "Clone metadata"]]} />
      <p className="muted small">
        {mode === "import"
          ? "Attach your own wallets to a token that already exists so you can trade it here and see the supply transparency panel."
          : "Copies the name, ticker and description of an existing token into a new draft. The image is not copied."}
      </p>
      {error && <Callout tone="err">{error}</Callout>}
      <Field label="Token mint address"><input value={mint} onChange={(e) => setMint(e.target.value)} className="mono" placeholder="e.g. 7xKX…pump" autoFocus /></Field>
      {mode === "clone" && (
        <Field label="Dev wallet (optional)">
          <select value={dev} onChange={(e) => setDev(e.target.value)}>
            <option value="">Choose later…</option>
            {wallets.map((w) => <option key={w.id} value={w.id}>{w.label || "Wallet"} · {short(w.address)}</option>)}
          </select>
        </Field>
      )}
    </Modal>
  );
}

export default function Launches() {
  const nav = useNavigate();
  const toast = useToast();
  const [launches, setLaunches] = useState<Launch[] | null>(null);
  const [archived, setArchived] = useState<Launch[]>([]);
  const [pads, setPads] = useState<Pad[]>([]);
  const [wallets, setWallets] = useState<W[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [chainF, setChainF] = useState<"all" | ChainId>("all");
  const [modal, setModal] = useState<null | "create" | "import">(null);

  useEffect(() => {
    Promise.all([api<Launch[]>("/launches"), api<Launch[]>("/launches?status=archived"), api<Pad[]>("/launchpads"), api<W[]>("/wallets")])
      .then(([l, a, p, w]) => { setLaunches(l); setArchived(a); setPads(p); setWallets(w.filter((x) => x.kind === "managed")); })
      .catch((e) => { toast("bad", errMsg(e)); setLaunches([]); });
  }, [toast]);

  const counts = useMemo(() => ({
    all: launches?.length ?? 0,
    live: launches?.filter((l) => l.status === "live").length ?? 0,
    draft: launches?.filter((l) => l.status !== "live").length ?? 0,
    archived: archived.length,
  }), [launches, archived]);

  const shown = useMemo(() => {
    const base = filter === "archived" ? archived : (launches ?? []).filter((l) => filter === "all" || (filter === "live" ? l.status === "live" : l.status !== "live"));
    const s = q.trim().toLowerCase();
    const byChain = chainF === "all" ? base : base.filter((l) => (l.network ?? "solana") === chainF);
    return s ? byChain.filter((l) => l.name.toLowerCase().includes(s) || l.symbol.toLowerCase().includes(s) || (l.mint_address ?? "").toLowerCase().includes(s)) : byChain;
  }, [launches, archived, filter, q, chainF]);

  return (
    <>
      <PageHeader title="Launches" sub="Create tokens on any supported chain and launchpad, import ones you hold, and open a workspace to trade and manage each one."
        actions={<>
          <button className="btn secondary" onClick={() => setModal("import")}><Icon name="download" size={15} />Import / clone</button>
          <button className="btn" onClick={() => setModal("create")}><Icon name="plus" size={15} />New launch</button>
        </>} />

      <div className="row between">
        <Seg value={filter} onChange={setFilter} options={[
          ["all", <>All <span className="muted num">{counts.all}</span></>],
          ["live", <>Live <span className="muted num">{counts.live}</span></>],
          ["draft", <>Drafts <span className="muted num">{counts.draft}</span></>],
          ["archived", <>Archived <span className="muted num">{counts.archived}</span></>],
        ]} />
        <div className="chips">
          <button className={`chip ${chainF === "all" ? "on" : ""}`} onClick={() => setChainF("all")}>All chains</button>
          {CHAIN_ORDER.map((c) => (
            <button key={c} className={`chip ${chainF === c ? "on" : ""}`} onClick={() => setChainF(c)}><ChainDot network={c} size={14} />{CHAINS[c].name}</button>
          ))}
        </div>
        <div style={{ width: 280, maxWidth: "100%" }}><SearchInput placeholder="Search name, ticker or mint" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      </div>

      {!launches ? <div className="card"><Loading rows={4} /></div> : shown.length === 0 && (q || filter !== "all") ? (
        <div className="card"><Empty icon="search" title="No launches match" text="Try a different filter or search." /></div>
      ) : (
        <div className="grid g-cards">
          {filter !== "archived" && !q && (
            <button className="card lcard-new" onClick={() => setModal("create")}>
              <span className="empty" style={{ padding: 0 }}><span className="ic"><Icon name="plus" size={22} /></span></span>
              <b style={{ color: "var(--text)" }}>New launch</b>
              <span className="small">Pick a chain and launchpad</span>
            </button>
          )}
          {shown.map((l) => (
            <div key={l.id} className="card lcard" role="link" tabIndex={0} onClick={() => nav(`/launches/${l.id}`)} onKeyDown={(e) => e.key === "Enter" && nav(`/launches/${l.id}`)}>
              <div className="row nowrap" style={{ gap: 12 }}>
                <TokenAvatar symbol={l.symbol} size={44} />
                <div className="grow">
                  <div className="truncate" style={{ fontWeight: 650, fontSize: 15 }}>{l.name}</div>
                  <div className="muted mono">${l.symbol}</div>
                </div>
                <StatusBadge status={l.status} />
              </div>
              {l.description && <p className="muted small" style={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{l.description}</p>}
              <div className="meta">
                <div><span className="k">Chain</span><span className="row nowrap" style={{ gap: 6 }}><ChainDot network={l.network} size={14} />{chainOf(l.network).name}</span></div>
                <div><span className="k">Launchpad</span>{padName(l.launchpad)}{l.imported && <span className="badge violet" style={{ marginLeft: 6 }}>imported</span>}</div>
                <div><span className="k">Created</span>{relTime(l.created_at)}</div>
                <div><span className="k">Token address</span>{l.mint_address ? <Address address={l.mint_address} type="token" network={l.network} /> : <span className="muted">Not deployed</span>}</div>
              </div>
            </div>
          ))}
        </div>
      )}

      {pads.length > 0 && (
        <Card title="Launchpads" icon="globe" sub={`${pads.length} launchpads across ${new Set(pads.map((p) => p.network)).size} chains · ${pads.filter((p) => p.status === "available").length} live today, the rest rolling out`}>
          <LaunchpadDirectory pads={pads} />
        </Card>
      )}

      {modal === "create" && <CreateLaunch pads={pads} wallets={wallets} onClose={() => setModal(null)} />}
      {modal === "import" && <ImportLaunch wallets={wallets} onClose={() => setModal(null)} />}
    </>
  );
}
