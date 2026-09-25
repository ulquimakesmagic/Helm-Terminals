import { useEffect, useMemo, useState } from "react";
import { api, errMsg, fmt, short } from "../api";
import SpendDialog from "../components/SpendDialog";
import {
  Address, Callout, Empty, Field, Icon, Loading, Modal, PageHeader, SearchInput, Spinner, Stat, StatusBadge, UnitInput, useToast,
} from "../components/ui";

interface W { id: string; address: string; kind: string; label: string; folder_id: string | null; balance_sol?: number }
interface Folder { id: string; name: string; color: string }
interface Funder { id: string; wallet_id: string; label: string; is_default: boolean }
type Dialog = null | { title: string; path: string; body: Record<string, unknown> };

function CreateWallets({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [tab, setTab] = useState<"managed" | "watch">("managed");
  const [count, setCount] = useState(1);
  const [prefix, setPrefix] = useState("Wallet");
  const [addr, setAddr] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function go() {
    setBusy(true); setError("");
    try {
      if (tab === "managed") await api("/wallets", { body: { count, label_prefix: prefix } });
      else await api("/wallets/watch", { body: { address: addr.trim() } });
      toast("good", tab === "managed" ? `Created ${count} wallet${count === 1 ? "" : "s"}` : "Watch-only address added");
      onDone(); onClose();
    } catch (e) { setError(errMsg(e)); setBusy(false); }
  }
  return (
    <Modal title="Add wallets" onClose={onClose}
      foot={<><button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn" onClick={go} disabled={busy || (tab === "watch" ? !addr.trim() : count < 1 || count > 50)}>{busy && <Spinner />}{tab === "managed" ? `Create ${count}` : "Add address"}</button></>}>
      <div className="seg full">
        <button className={tab === "managed" ? "on" : ""} onClick={() => setTab("managed")}>Managed wallets</button>
        <button className={tab === "watch" ? "on" : ""} onClick={() => setTab("watch")}>Watch-only</button>
      </div>
      {error && <Callout tone="err">{error}</Callout>}
      {tab === "managed" ? (
        <>
          <p className="muted small">New keypairs are created by the signing service. Keys are never shown to you or stored by this app; you can move funds out with a 2FA-protected withdrawal.</p>
          <div className="row nowrap" style={{ alignItems: "flex-start" }}>
            <Field label="How many" hint="1 to 50" style={{ width: 120 }}><input type="number" min={1} max={50} value={count} onChange={(e) => setCount(+e.target.value)} autoFocus /></Field>
            <Field label="Label prefix" hint={`e.g. ${prefix || "Wallet"} 1, ${prefix || "Wallet"} 2`} style={{ flex: 1 }}><input value={prefix} onChange={(e) => setPrefix(e.target.value)} /></Field>
          </div>
        </>
      ) : (
        <>
          <p className="muted small">Track any Solana address. Watch-only wallets can't sign, so they can't trade or be funded from here.</p>
          <Field label="Solana address"><input className="mono" value={addr} onChange={(e) => setAddr(e.target.value)} autoFocus /></Field>
        </>
      )}
    </Modal>
  );
}

export default function Wallets() {
  const toast = useToast();
  const [wallets, setWallets] = useState<W[] | null>(null);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [funders, setFunders] = useState<Funder[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [folderFilter, setFolderFilter] = useState<string>("all");
  const [q, setQ] = useState("");
  const [balances, setBalances] = useState(false);
  const [loadingBal, setLoadingBal] = useState(false);
  const [modal, setModal] = useState<null | "create" | "fund" | "withdraw" | "folder">(null);
  const [fundFrom, setFundFrom] = useState("");
  const [fundAmount, setFundAmount] = useState("0.05");
  const [dest, setDest] = useState("");
  const [folderName, setFolderName] = useState("");
  const [dialog, setDialog] = useState<Dialog>(null);

  const load = async (withBal = balances) => {
    if (withBal) setLoadingBal(true);
    try {
      const [w, f, fu] = await Promise.all([
        api<W[]>(`/wallets${withBal ? "?with_balances=true" : ""}`), api<Folder[]>("/folders"), api<Funder[]>("/funders"),
      ]);
      setWallets(w); setFolders(f); setFunders(fu);
      if (withBal) setBalances(true);
    } catch (e) { toast("bad", errMsg(e)); setWallets((x) => x ?? []); }
    finally { setLoadingBal(false); }
  };
  useEffect(() => { load(false); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const all = wallets ?? [];
  const managed = all.filter((w) => w.kind === "managed");
  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    return all.filter((w) =>
      (folderFilter === "all" || (folderFilter === "none" ? !w.folder_id : w.folder_id === folderFilter)) &&
      (!s || w.label.toLowerCase().includes(s) || w.address.toLowerCase().includes(s)));
  }, [all, folderFilter, q]);
  const totalSol = all.reduce((s, w) => s + (w.balance_sol ?? 0), 0);
  const sel = [...selected];
  const selManaged = sel.filter((id) => managed.some((w) => w.id === id));
  const toggle = (id: string) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const allShownSelected = shown.length > 0 && shown.every((w) => selected.has(w.id));
  const funderOf = (id: string) => funders.find((f) => f.wallet_id === id);
  const folderOf = (id: string | null) => folders.find((f) => f.id === id);

  const makeFunder = (w: W) => api("/funders", { body: { wallet_id: w.id, label: w.label || "Funder", is_default: funders.length === 0 } })
    .then(() => { toast("good", `${w.label || short(w.address)} is now a funder`); load(); }).catch((e) => toast("bad", errMsg(e)));

  return (
    <>
      <PageHeader title="Wallets" sub="Managed wallets you trade and launch from, plus addresses you only watch."
        actions={<>
          <button className="btn secondary" onClick={() => load(true)} disabled={loadingBal}>{loadingBal ? <Spinner /> : <Icon name="refresh" size={15} />}{balances ? "Refresh balances" : "Load balances"}</button>
          <button className="btn" onClick={() => setModal("create")}><Icon name="plus" size={15} />Add wallets</button>
        </>} />

      <div className="grid g-stats">
        <Stat accent label="Total balance" icon="coins" value={balances ? fmt(totalSol) : "—"} unit={balances ? "SOL" : undefined} hint={balances ? "across all wallets" : "Load balances to see totals"} />
        <Stat label="Managed" icon="key" value={wallets ? managed.length : "–"} hint="can sign transactions" />
        <Stat label="Watch-only" icon="eye" value={wallets ? all.length - managed.length : "–"} hint="read-only tracking" />
        <Stat label="Funders" icon="arrowdown" value={funders.length} hint={funders.find((f) => f.is_default) ? `default: ${funders.find((f) => f.is_default)!.label}` : "none set"} />
      </div>

      <div className="card">
        <div className="card-head">
          <div className="chips">
            <button className={`chip ${folderFilter === "all" ? "on" : ""}`} onClick={() => setFolderFilter("all")}>All <span className="num">{all.length}</span></button>
            {folders.map((f) => (
              <button key={f.id} className={`chip ${folderFilter === f.id ? "on" : ""}`} onClick={() => setFolderFilter(f.id)}>
                <span className="dot" style={{ background: f.color || "var(--accent)" }} />{f.name} <span className="num">{all.filter((w) => w.folder_id === f.id).length}</span>
              </button>
            ))}
            {folders.length > 0 && <button className={`chip ${folderFilter === "none" ? "on" : ""}`} onClick={() => setFolderFilter("none")}>No folder</button>}
            <button className="chip" onClick={() => setModal("folder")}><Icon name="plus" size={12} />Folder</button>
          </div>
          <div style={{ width: 260, maxWidth: "100%" }}><SearchInput placeholder="Search label or address" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        </div>
        {!wallets ? <Loading rows={5} /> : all.length === 0 ? (
          <Empty icon="wallet" title="No wallets yet" text="Create managed wallets to launch and trade from, or add an address to watch."
            action={<button className="btn sm" onClick={() => setModal("create")}><Icon name="plus" size={14} />Add wallets</button>} />
        ) : shown.length === 0 ? <Empty icon="search" title="No wallets match" /> : (
          <div className="table-wrap"><table>
            <thead><tr>
              <th className="cb"><input type="checkbox" checked={allShownSelected} aria-label="Select all"
                onChange={() => setSelected(allShownSelected ? new Set() : new Set(shown.map((w) => w.id)))} /></th>
              <th>Wallet</th><th>Address</th><th className="hide-sm">Type</th><th className="hide-sm">Folder</th><th className="r">Balance</th><th className="r" />
            </tr></thead>
            <tbody>
              {shown.map((w) => {
                const fu = funderOf(w.id);
                return (
                  <tr key={w.id} className={`clickable ${selected.has(w.id) ? "sel" : ""}`} onClick={() => toggle(w.id)}>
                    <td className="cb"><input type="checkbox" checked={selected.has(w.id)} onChange={() => toggle(w.id)} onClick={(e) => e.stopPropagation()} /></td>
                    <td><div className="row nowrap" style={{ gap: 8 }}>
                      <span style={{ fontWeight: 550 }}>{w.label || "Untitled"}</span>
                      {fu && <span className="badge info">funder{fu.is_default ? " · default" : ""}</span>}
                    </div></td>
                    <td><Address address={w.address} /></td>
                    <td className="hide-sm"><StatusBadge status={w.kind} label={w.kind === "managed" ? "managed" : "watch-only"} /></td>
                    <td className="hide-sm text-2">{folderOf(w.folder_id)?.name ?? <span className="muted">—</span>}</td>
                    <td className="r num">{w.balance_sol !== undefined ? <>{fmt(w.balance_sol)} <span className="muted">SOL</span></> : <span className="muted">—</span>}</td>
                    <td className="r" onClick={(e) => e.stopPropagation()}>
                      {w.kind === "managed" && !fu && <button className="btn sm ghost" onClick={() => makeFunder(w)}>Make funder</button>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table></div>
        )}
      </div>

      {sel.length > 0 && (
        <div className="selbar">
          <b className="num">{sel.length} selected</b>
          {selManaged.length !== sel.length && <span className="muted small">({sel.length - selManaged.length} watch-only ignored)</span>}
          <div className="grow" />
          <button className="btn sm buy" disabled={!selManaged.length} onClick={() => setModal("fund")}><Icon name="arrowdown" size={14} />Fund</button>
          <button className="btn sm secondary" disabled={!selManaged.length} onClick={() => setModal("withdraw")}><Icon name="arrowup" size={14} />Withdraw</button>
          <button className="btn sm ghost" onClick={() => setSelected(new Set())}>Clear</button>
        </div>
      )}

      <p className="muted small row" style={{ gap: 6 }}><Icon name="lock" size={13} />Keys are held by the signing service. They are never shown in the browser or stored by Helm Terminals.</p>

      {modal === "create" && <CreateWallets onClose={() => setModal(null)} onDone={() => load()} />}

      {modal === "folder" && (
        <Modal title="New folder" onClose={() => setModal(null)}
          foot={<><button className="btn ghost" onClick={() => setModal(null)}>Cancel</button>
            <button className="btn" disabled={!folderName.trim()} onClick={() => api("/folders", { body: { name: folderName.trim() } })
              .then(() => { toast("good", "Folder created"); setFolderName(""); setModal(null); load(); }).catch((e) => toast("bad", errMsg(e)))}>Create folder</button></>}>
          <Field label="Folder name"><input value={folderName} onChange={(e) => setFolderName(e.target.value)} placeholder="e.g. Launch wallets" autoFocus /></Field>
        </Modal>
      )}

      {modal === "fund" && (
        <Modal title="Fund wallets" sub={`Send SOL from one wallet to ${selManaged.filter((id) => id !== fundFrom).length} selected wallet(s)`} onClose={() => setModal(null)}
          foot={<><button className="btn ghost" onClick={() => setModal(null)}>Cancel</button>
            <button className="btn" disabled={!fundFrom || !(+fundAmount > 0) || selManaged.filter((id) => id !== fundFrom).length === 0}
              onClick={() => { setModal(null); setDialog({ title: "Fund wallets", path: "/wallets/fund",
                body: { funder_wallet_id: fundFrom, allocations: selManaged.filter((id) => id !== fundFrom).map((id) => ({ wallet_id: id, sol: +fundAmount })) } }); }}>
              Review<Icon name="arrowright" size={14} /></button></>}>
          <Field label="Send from">
            <select value={fundFrom} onChange={(e) => setFundFrom(e.target.value)}>
              <option value="">Choose a wallet…</option>
              {[...managed].sort((a, b) => Number(!!funderOf(b.id)) - Number(!!funderOf(a.id))).map((w) => (
                <option key={w.id} value={w.id}>{funderOf(w.id) ? "★ " : ""}{w.label || "Wallet"} · {short(w.address)}{w.balance_sol !== undefined ? ` · ${fmt(w.balance_sol)} SOL` : ""}</option>
              ))}
            </select>
          </Field>
          <Field label="Amount per wallet"><UnitInput unit="SOL" value={fundAmount} onChange={(e) => setFundAmount(e.target.value)} /></Field>
          <Callout>Total: <b className="num">{fmt(+fundAmount * selManaged.filter((id) => id !== fundFrom).length)} SOL</b> plus network fees. You'll see the full preview before anything is sent.</Callout>
        </Modal>
      )}

      {modal === "withdraw" && (
        <Modal title="Withdraw SOL" sub={`Sweep all SOL from ${selManaged.length} wallet(s), keeping a little for fees`} onClose={() => setModal(null)}
          foot={<><button className="btn ghost" onClick={() => setModal(null)}>Cancel</button>
            <button className="btn" disabled={dest.trim().length < 32}
              onClick={() => { setModal(null); setDialog({ title: "Withdraw SOL", path: "/wallets/withdraw", body: { wallet_ids: selManaged, destination: dest.trim(), all: true } }); }}>
              Review<Icon name="arrowright" size={14} /></button></>}>
          <Field label="Destination address" hint="Double-check this. Transfers can't be reversed."><input className="mono" value={dest} onChange={(e) => setDest(e.target.value)} placeholder="Your main wallet address" autoFocus /></Field>
          <Callout tone="warn" icon="shield">Withdrawals need your 2FA code on the next step.</Callout>
        </Modal>
      )}

      {dialog && <SpendDialog {...dialog} onClose={() => setDialog(null)} onDone={() => { setSelected(new Set()); load(true); }} />}
    </>
  );
}
