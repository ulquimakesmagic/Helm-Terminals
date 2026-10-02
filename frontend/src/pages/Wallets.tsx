import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, errMsg, fmt, short, usd } from "../api";
import { useSession } from "../session";
import Distribute from "../components/Distribute";
import { ChainLabel, ChainMark } from "../components/marks";
import SpendDialog from "../components/SpendDialog";
import { CHAIN_ORDER, CHAINS, ChainId, chainOf, isExtraChain } from "../chains";
import { isEvmAddr, isSolanaAddr } from "../validate";
import {
  Address, Callout, Empty, Field, Icon, LoadError, Loading, Metrics, Modal, PageHeader, SearchInput, Spinner, StatusBadge, TableWrap, UnitInput, useToast,
} from "../components/ui";

interface W { id: string; network: string; address: string; kind: string; label: string; folder_id: string | null; balance_sol?: number; balance_native?: string | null; stable?: number; stable_symbol?: string; usd?: number; coin_usd?: number | null; is_main?: boolean }
interface Folder { id: string; name: string; color: string; wallet_count?: number }
const NEW_FOLDER = "__new__";
const FOLDER_COLORS = ["#6366f1", "#38bdf8", "#34d399", "#fbbf24", "#f87171", "#a78bfa", "#f472b6", "#94a3b8"];

/** Create a folder, or reuse an existing one with the same name (the API refuses duplicates). */
async function ensureFolder(name: string, folders: Folder[]): Promise<Folder> {
  const clean = name.trim().replace(/\s+/g, " ");
  const existing = folders.find((f) => f.name.toLowerCase() === clean.toLowerCase());
  if (existing) return existing;
  return api<Folder>("/folders", { body: { name: clean, color: FOLDER_COLORS[folders.length % FOLDER_COLORS.length] } });
}

function ManageFolders({ folders, onClose, onChanged }: { folders: Folder[]; onClose: () => void; onChanged: (deletedId?: string) => void }) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [color, setColor] = useState(FOLDER_COLORS[folders.length % FOLDER_COLORS.length]);
  const [busy, setBusy] = useState<string | null>(null); // "create" | "merge" | folder id being deleted
  const [confirm, setConfirm] = useState<string | null>(null);
  const [error, setError] = useState("");
  const dupes = folders.length - new Set(folders.map((f) => f.name.trim().toLowerCase())).size;
  const taken = folders.some((f) => f.name.toLowerCase() === name.trim().replace(/\s+/g, " ").toLowerCase());

  const create = () => {
    if (busy || !name.trim() || taken) return; // one request at a time: no more duplicate folders
    setBusy("create"); setError("");
    api("/folders", { body: { name: name.trim(), color } })
      .then(() => { toast("good", `Folder "${name.trim()}" created`); setName(""); onChanged(); })
      .catch((e) => setError(errMsg(e))).finally(() => setBusy(null));
  };
  const remove = (f: Folder) => {
    setBusy(f.id); setError("");
    api<{ wallets_unfiled: number }>(`/folders/${f.id}`, { method: "DELETE" })
      .then((r) => { toast("good", `Deleted "${f.name}"${r.wallets_unfiled ? `, ${r.wallets_unfiled} wallet${r.wallets_unfiled === 1 ? "" : "s"} moved to No folder` : ""}`); setConfirm(null); onChanged(f.id); })
      .catch((e) => setError(errMsg(e))).finally(() => setBusy(null));
  };
  const merge = () => {
    setBusy("merge"); setError("");
    api<{ removed: number; wallets_moved: number }>("/folders/merge-duplicates", { body: {} })
      .then((r) => { toast("good", `Merged ${r.removed} duplicate folder${r.removed === 1 ? "" : "s"}`); onChanged(); })
      .catch((e) => setError(errMsg(e))).finally(() => setBusy(null));
  };

  return (
    <Modal title="Folders" sub="Group wallets for launches and trading. Deleting a folder never deletes its wallets." onClose={onClose}
      foot={<button className="btn secondary" onClick={onClose}>Done</button>}>
      {error && <Callout tone="err">{error}</Callout>}
      {dupes > 0 && (
        <Callout tone="warn">
          <div className="row" style={{ justifyContent: "space-between", gap: 10 }}>
            <span>You have {dupes} duplicate folder{dupes === 1 ? "" : "s"} (same name). Merge them into one each; wallets move along.</span>
            <button className="btn sm" onClick={merge} disabled={!!busy}>{busy === "merge" && <Spinner />}Merge duplicates</button>
          </div>
        </Callout>
      )}
      <form className="row nowrap" style={{ alignItems: "flex-end", gap: 8 }} onSubmit={(e) => { e.preventDefault(); create(); }}>
        <Field label="New folder" style={{ flex: 1 }} hint={taken ? "You already have a folder with this name." : undefined}>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Launch wallets" maxLength={80} autoFocus />
        </Field>
        <button type="submit" className="btn" disabled={!name.trim() || taken || !!busy} style={{ marginBottom: taken ? 22 : 0 }}>
          {busy === "create" ? <Spinner /> : <Icon name="plus" size={14} />}Create
        </button>
      </form>
      <div className="hue-row">
        {FOLDER_COLORS.map((c) => (
          <button key={c} type="button" className={`hue ${color === c ? "on" : ""}`} style={{ background: c, width: 24, height: 24 }}
            onClick={() => setColor(c)} aria-label={`Folder colour ${c}`} aria-pressed={color === c} />
        ))}
      </div>
      {folders.length === 0 ? <p className="muted small">No folders yet.</p> : (
        <div className="stack" style={{ gap: 0 }}>
          {folders.map((f) => (
            <div key={f.id} className="row nowrap" style={{ gap: 10, padding: "10px 0", borderBottom: "1px solid var(--line)" }}>
              <span className="dot" style={{ width: 10, height: 10, borderRadius: 3, background: f.color || "var(--accent)", flex: "none" }} />
              <span className="grow truncate" style={{ fontWeight: 550 }}>{f.name}</span>
              <span className="muted small num">{f.wallet_count ?? 0} wallet{f.wallet_count === 1 ? "" : "s"}</span>
              {confirm === f.id ? (
                <>
                  <button className="btn sm danger" onClick={() => remove(f)} disabled={!!busy}>{busy === f.id && <Spinner />}Delete</button>
                  <button className="btn sm ghost" onClick={() => setConfirm(null)}>Keep</button>
                </>
              ) : (
                <button className="icon-btn" title={`Delete ${f.name}`} aria-label={`Delete ${f.name}`} onClick={() => setConfirm(f.id)} disabled={!!busy}>
                  <Icon name="trash" size={15} />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}

function FolderSelect({ folders, value, onChange, newName, onNewName }: {
  folders: Folder[]; value: string; onChange: (v: string) => void; newName: string; onNewName: (v: string) => void;
}) {
  return (
    <div className="row nowrap" style={{ gap: 8, alignItems: "flex-start" }}>
      <Field label="Folder" style={{ flex: 1 }}>
        <select value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">No folder</option>
          {folders.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          <option value={NEW_FOLDER}>+ New folder…</option>
        </select>
      </Field>
      {value === NEW_FOLDER && (
        <Field label="New folder name" style={{ flex: 1 }}>
          <input value={newName} onChange={(e) => onNewName(e.target.value)} placeholder="e.g. Snipers" maxLength={80} autoFocus />
        </Field>
      )}
    </div>
  );
}
interface Funder { id: string; wallet_id: string; label: string; is_default: boolean }
type Dialog = null | { title: string; path: string; body: Record<string, unknown>; network?: string };

export interface ExtraNet { id: string; name: string; family: string; chain_id: number | null; enabled: boolean }
type AddTab = "managed" | "import" | "connect" | "watch";
const OWN_APP: Record<string, string> = { tron: "TronLink", evm: "your EVM wallet", near: "your NEAR wallet", ton: "TON Connect" };

/** Why a chain can't be used on a tab (null when it can). Solana and the core EVM chains are Helm-managed or imported;
 * TRON, NEAR and Arc can also link your own wallet; TON only connects through TON Connect (Helm never holds TON keys). */
function unavailable(tab: AddTab, id: string, extras: ExtraNet[]): string | null {
  const x = extras.find((n) => n.id === id);
  if (tab === "watch") return null;
  if (tab === "connect") return x ? null : "Solana and EVM wallets are created or imported in Helm";
  if (x?.family === "ton") return "Helm doesn't hold TON keys: use Connect";
  return null;
}

function CreateWallets({ onClose, onDone, folders, defaultFolder, extras }: {
  onClose: () => void; onDone: () => void; folders: Folder[]; defaultFolder: string; extras: ExtraNet[];
}) {
  const toast = useToast();
  const [folder, setFolder] = useState(defaultFolder);
  const [newFolder, setNewFolder] = useState("");
  const [tab, setTab] = useState<AddTab>("managed");
  const [secret, setSecret] = useState("");
  const [importLabel, setImportLabel] = useState("Imported");
  const [allEvm, setAllEvm] = useState(true);
  const [password, setPassword] = useState("");
  const [importCode, setImportCode] = useState("");
  const { me } = useSession();
  const [count, setCount] = useState(1);
  const [prefix, setPrefix] = useState("Wallet");
  const [addr, setAddr] = useState("");
  const [network, setNetwork] = useState<string>("solana");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const extra = extras.find((n) => n.id === network) ?? null;
  const fam = extra ? extra.family : network === "solana" ? "solana" : "evm";
  const name = chainOf(network).name;
  const coreEvm = !extra && network !== "solana";

  const switchTab = (t: AddTab) => {
    setTab(t); setError(""); setSecret("");
    if (unavailable(t, network, extras)) {
      const first = [...CHAIN_ORDER, ...extras.map((n) => n.id)].find((id) => !unavailable(t, id, extras));
      if (first) setNetwork(first);
    }
  };

  async function go() {
    if (busy) return;
    setBusy(true); setError("");
    try {
      if (tab === "connect") {
        if (!me || !extra) return;
        const c = await import("../components/walletConnectors");
        await c.linkOwnWallet({ id: extra.id, name: extra.name, family: extra.family, chain_id: extra.chain_id }, me.id);
        toast("good", `${name} wallet connected`);
        onDone(); onClose();
        return;
      }
      const folder_id = folder === NEW_FOLDER ? (await ensureFolder(newFolder, folders)).id : folder || null;
      let imported = 0;
      if (tab === "managed") await api("/wallets", { body: { network, count, label_prefix: prefix, folder_id } });
      else if (tab === "import") {
        const r = await api<unknown[]>("/wallets/import", { body: { network, private_key: secret.trim(), label: importLabel.trim() || "Imported", all_evm: coreEvm && allEvm, folder_id, password, totp: importCode || null } });
        imported = r.length;
        setSecret(""); setPassword(""); setImportCode("");
      } else await api("/wallets/watch", { body: { network, address: addr.trim(), folder_id } });
      const where = folder_id ? ` in ${folder === NEW_FOLDER ? newFolder.trim() : folders.find((f) => f.id === folder_id)?.name}` : "";
      toast("good", tab === "managed" ? `Created ${count} ${name} wallet${count === 1 ? "" : "s"}${where}`
        : tab === "import" ? `Wallet imported${imported > 1 ? ` on ${imported} chains` : ""}${where}` : `Watch-only address added${where}`);
      onDone(); onClose();
    } catch (e: any) { setError(e?.declined !== undefined ? e.message : errMsg(e)); } finally { setBusy(false); }
  }

  const chip = (id: string) => {
    const why = unavailable(tab, id, extras);
    const x = extras.find((n) => n.id === id);
    return (
      <button key={id} type="button" disabled={!!why} className={`chip ${network === id ? "on" : ""}`} aria-pressed={network === id}
        style={why ? { opacity: 0.45, cursor: "not-allowed" } : undefined} onClick={() => { setNetwork(id); setError(""); }} title={why ?? undefined}>
        <ChainMark network={id} size={16} />{x ? x.name : CHAINS[id as ChainId].name}
      </button>
    );
  };
  const importHint = fam === "solana" ? "The base58 key your wallet app exports, or a solana-keygen JSON array"
    : fam === "tron" ? "The 64-character hex private key your TRON wallet (e.g. TronLink) exports"
    : fam === "near" ? "The ed25519:… private key your NEAR wallet exports"
    : "64 hex characters, with or without 0x";
  const ready = tab === "connect" ? !!extra && extra.family !== "ton"
    : tab === "watch" ? !!addr.trim()
    : tab === "import" ? !!secret.trim() && !!password && (!me?.totp_enabled || importCode.length === 6)
    : count >= 1 && count <= 50;
  const primary = tab === "connect" && extra?.family === "ton"
    ? <Link className="btn" to={`/networks?network=${extra.id}`} onClick={onClose}><Icon name="plus" size={14} />Open TON Connect</Link>
    : <button className="btn" onClick={go} disabled={busy || !ready || (tab !== "connect" && folder === NEW_FOLDER && !newFolder.trim())}>
        {busy && <Spinner />}{tab === "managed" ? `Create ${count}` : tab === "import" ? "Import wallet" : tab === "connect" ? `Connect ${OWN_APP[fam]}` : "Add address"}</button>;

  return (
    <Modal title="Add wallets" onClose={onClose} lock={busy && tab === "connect"}
      foot={<><button className="btn ghost" onClick={onClose}>Cancel</button>{primary}</>}>
      <div className="seg full" role="tablist" aria-label="How to add">
        {([["managed", "New"], ["import", "Import"], ["connect", "Connect"], ["watch", "Watch-only"]] as [AddTab, string][]).map(([t, label]) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => switchTab(t)}>{label}</button>
        ))}
      </div>
      <div className="field">
        <span>Chain</span>
        <div className="chips">{CHAIN_ORDER.map(chip)}</div>
        {extras.length > 0 && <div className="chips" style={{ marginTop: 6 }}>{extras.map((n) => chip(n.id))}</div>}
      </div>
      {extra && tab !== "watch" && <Callout tone="warn" icon="alert">{extra.name} is a mainnet: transfers from this wallet move real money.</Callout>}
      {error && <Callout tone="err">{error}</Callout>}
      {tab === "managed" ? (
        <>
          <p className="muted small">New keypairs are created by the signing service. Keys are never shown to you or stored by this app; you can move funds out with a 2FA-protected withdrawal.
            {fam === "tron" && " A new TRON address activates when it first receives TRX."}
            {fam === "near" && " A new NEAR wallet is an implicit account (64 hex characters); it exists on-chain once it first receives NEAR."}
            {fam === "evm" && extra && ` Gas on ${extra.name} is paid in USDC.`}</p>
          <div className="row nowrap" style={{ alignItems: "flex-start" }}>
            <Field label="How many" hint="1 to 50" style={{ width: 120 }}><input type="number" min={1} max={50} value={count} onChange={(e) => setCount(+e.target.value)} /></Field>
            <Field label="Label prefix" hint={`e.g. ${prefix || "Wallet"} 1, ${prefix || "Wallet"} 2`} style={{ flex: 1, minWidth: 0 }}><input value={prefix} onChange={(e) => setPrefix(e.target.value)} /></Field>
          </div>
          <FolderSelect folders={folders} value={folder} onChange={setFolder} newName={newFolder} onNewName={setNewFolder} />
        </>
      ) : tab === "import" ? (
        <>
          <p className="muted small">Bring a wallet you already have into Helm so you can launch and trade from it. Helm's server encrypts the key for the signing service's secure enclave as soon as it arrives. Helm never stores it or shows it again. Anyone with this key controls the wallet, so only paste it here, never in a chat or email.</p>
          <Field label={`${name} private key`} hint={importHint}>
            <input className="mono" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="off" spellCheck={false} autoCapitalize="off" autoCorrect="off" />
          </Field>
          {fam === "near" && <Callout icon="info">Helm adds this key as its own implicit account (64 hex characters). For a named account such as alice.near, use <b>Connect</b> instead.</Callout>}
          <Field label="Label"><input value={importLabel} onChange={(e) => setImportLabel(e.target.value)} maxLength={80} /></Field>
          <div className="row" style={{ alignItems: "flex-start" }}>
            <Field label="Your Helm password" hint="Imported wallets can receive funds without a 2FA code, so Helm checks it's you" style={{ flex: "1 1 220px", minWidth: 0 }}>
              <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </Field>
            {me?.totp_enabled && (
              <Field label="2FA code" style={{ width: 130 }}>
                <input className="mono" inputMode="numeric" value={importCode} onChange={(e) => setImportCode(e.target.value.replace(/\D/g, "").slice(0, 6))} placeholder="123456" />
              </Field>
            )}
          </div>
          {coreEvm && (
            <label className="small" style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
              <input type="checkbox" checked={allEvm} onChange={(e) => setAllEvm(e.target.checked)} style={{ marginTop: 2, flex: "none" }} />
              <span>Add it on every EVM chain (the same address works on Base, BNB Chain, Ethereum and Robinhood Chain)</span>
            </label>
          )}
          <FolderSelect folders={folders} value={folder} onChange={setFolder} newName={newFolder} onNewName={setNewFolder} />
        </>
      ) : tab === "connect" ? (
        !extra ? <Callout icon="info">Connect your own wallet on TRON, NEAR, Arc or TON. {extras.length === 0 && "None of these networks is enabled on this server yet."}</Callout>
        : extra.family === "ton" ? (
          <p className="muted small">TON wallets connect through TON Connect on the Networks page. Your wallet proves the address is yours with ton_proof; Helm never sees its keys.</p>
        ) : (
          <>
            <p className="muted small">Helm asks {OWN_APP[fam]} to sign a message naming this site, {extra.name} and a one-time code that expires in 15 minutes. That proves the address is yours. It doesn't approve any transaction, and Helm never sees the wallet's keys.</p>
            <p className="muted small">Every send from a connected wallet is reviewed in Helm first, then approved in {OWN_APP[fam]}.
              {fam === "tron" && " Install TronLink and switch it to " + extra.name + " first."}
              {fam === "evm" && ` Your wallet will be asked to switch to ${extra.name} (chain ID ${extra.chain_id}).`}
              {fam === "near" && " You'll choose a NEAR wallet in the next window."}</p>
          </>
        )
      ) : (
        <>
          <p className="muted small">Track any {name} address. Watch-only wallets can't sign, so they can't trade or be funded from here.</p>
          <Field label={`${name} ${fam === "near" ? "account" : "address"}`} hint={fam === "near" ? "e.g. alice.near, or a 64-character implicit account" : undefined}>
            <input className="mono" value={addr} onChange={(e) => setAddr(e.target.value)} spellCheck={false} autoCapitalize="off" autoCorrect="off" />
          </Field>
          <FolderSelect folders={folders} value={folder} onChange={setFolder} newName={newFolder} onNewName={setNewFolder} />
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
  const [chainF, setChainF] = useState<string>("all");
  const [balances, setBalances] = useState(false);
  const [loadingBal, setLoadingBal] = useState(false);
  const [modal, setModal] = useState<null | "create" | "fund" | "withdraw" | "folders" | "move" | "disperse" | "consolidate">(null);
  const [moveTo, setMoveTo] = useState("");
  const [moveNew, setMoveNew] = useState("");
  const [moving, setMoving] = useState(false);
  const [fundFrom, setFundFrom] = useState("");
  const [fundAmount, setFundAmount] = useState("0.05");
  const [dest, setDest] = useState("");
  const [dialog, setDialog] = useState<Dialog>(null);
  const [folio, setFolio] = useState<any>(null);
  const [settingMain, setSettingMain] = useState(false);
  const [extras, setExtras] = useState<ExtraNet[]>([]);
  useEffect(() => { api<{ extra: ExtraNet[] }>("/chains").then((c) => setExtras(c.extra.filter((n) => n.enabled))).catch(() => setExtras([])); }, []);

  const [loadErr, setLoadErr] = useState("");
  const load = async (withBal = balances) => {
    setLoadErr("");
    if (withBal) setLoadingBal(true);
    try {
      const [w, f, fu] = await Promise.all([
        api<W[]>(`/wallets${withBal ? "?with_balances=true" : ""}`), api<Folder[]>("/folders"), api<Funder[]>("/funders"),
      ]);
      setWallets(w); setFolders(f); setFunders(fu);
      if (withBal) { setBalances(true); api("/portfolio?fresh=true").then(setFolio).catch(() => setFolio(null)); }
    } catch (e) { if (wallets) toast("bad", errMsg(e)); setLoadErr(errMsg(e)); }
    finally { setLoadingBal(false); }
  };
  useEffect(() => { load(false); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const all = wallets ?? [];
  const managed = all.filter((w) => w.kind === "managed");
  const hasMain = all.some((w) => w.is_main);
  const setupMain = () => {
    setSettingMain(true);
    api<unknown[]>("/wallets/main", { method: "POST" }).then(() => { toast("good", "Main wallet ready"); load(); })
      .catch((e) => toast("bad", errMsg(e))).finally(() => setSettingMain(false));
  };
  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    return all.filter((w) =>
      (chainF === "all" || (w.network ?? "solana") === chainF) &&
      (folderFilter === "all" || (folderFilter === "none" ? !w.folder_id : w.folder_id === folderFilter)) &&
      (!s || w.label.toLowerCase().includes(s) || w.address.toLowerCase().includes(s)));
  }, [all, folderFilter, q, chainF]);
  const unread = balances ? all.filter((w) => w.kind === "managed" && typeof w.balance_sol !== "number") : [];
  const sel = [...selected];
  const selManaged = sel.filter((id) => managed.some((w) => w.id === id));
  const selNets = [...new Set(selManaged.map((id) => managed.find((w) => w.id === id)?.network ?? "solana"))];
  const selNet = selNets.length === 1 ? selNets[0] : null;
  const coin = chainOf(selNet).native;
  const evmSel = !!selNet && selNet !== "solana";
  const extraSel = isExtraChain(selNet);  // TRON, NEAR, Arc and TON move funds through their own transfer
  const toggle = (id: string) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const allShownSelected = shown.length > 0 && shown.every((w) => selected.has(w.id));
  const funderOf = (id: string) => funders.find((f) => f.wallet_id === id);
  const folderOf = (id: string | null) => folders.find((f) => f.id === id);

  const makeFunder = (w: W) => api("/funders", { body: { wallet_id: w.id, label: w.label || "Funder", is_default: funders.length === 0 } })
    .then(() => { toast("good", `${w.label || short(w.address)} is now a funder`); load(); }).catch((e) => toast("bad", errMsg(e)));

  return (
    <>
      <PageHeader title="Wallets" sub="Managed wallets you trade and launch from, plus addresses you watch, on every chain."
        actions={<>
          <button className="btn secondary" onClick={() => load(true)} disabled={loadingBal}>{loadingBal ? <Spinner /> : <Icon name="refresh" size={15} />}{balances ? "Refresh balances" : "Load balances"}</button>
          <button className="btn" onClick={() => setModal("create")}><Icon name="plus" size={15} />Add wallets</button>
        </>} />

      <Metrics label="Wallet totals" items={[
        { label: "Total across chains", value: folio ? usd(folio.totals.usd) : "—", tone: folio && folio.complete === false ? "warn" : undefined,
          hint: folio ? (folio.complete === false ? `at least · ${folio.incomplete_chains.length} chain${folio.incomplete_chains.length === 1 ? "" : "s"} not fully read` : folio.priced ? "coins and stablecoins" : "coin prices unavailable") : "load balances to see" },
        { label: "Stablecoins", value: folio ? usd(folio.totals.stable_usd) : "—", hint: "USDC, USDG on Robinhood" },
        { label: "Managed", value: wallets ? String(managed.length) : "—", hint: `on ${new Set(managed.map((w) => w.network)).size} chain${new Set(managed.map((w) => w.network)).size === 1 ? "" : "s"}` },
        { label: "Watch-only", value: wallets ? String(all.filter((w) => w.kind === "watch").length) : "—",
          hint: all.some((w) => w.kind === "connected") ? `tracked · plus ${all.filter((w) => w.kind === "connected").length} connected` : "tracked, can't sign" },
        { label: "Folders", value: String(folders.length), hint: funders.length ? `${funders.length} funder${funders.length === 1 ? "" : "s"}` : "no funders set" },
      ]} />

      {wallets && !hasMain && (
        <Callout tone="info">
          <div className="row between" style={{ gap: 10 }}>
            <span><b>Your account has no main wallet yet.</b> It's one Solana wallet and one EVM address (on Base, BNB Chain, Ethereum and Robinhood Chain) that you can keep USDC in; launches on any chain route from it when the dev wallet is short.</span>
            <button className="btn sm" onClick={setupMain} disabled={settingMain}>{settingMain ? <Spinner /> : <Icon name="plus" size={13} />}Set up main wallet</button>
          </div>
        </Callout>
      )}

      {balances && unread.length > 0 && (
        <Callout tone="warn">
          <div className="row between" style={{ gap: 10 }}>
            <span><b>{unread.length} wallet balance{unread.length === 1 ? "" : "s"} couldn't be read</b> ({[...new Set(unread.map((w) => chainOf(w.network).name))].join(", ")}). Those rows say "Unavailable"; they are not zero.</span>
            <button className="btn sm secondary" onClick={() => load(true)} disabled={loadingBal}>{loadingBal ? <Spinner /> : <Icon name="refresh" size={13} />}Retry</button>
          </div>
        </Callout>
      )}

      <div className="card">
        <div className="card-head">
          <div className="chips">
            <button className={`chip ${folderFilter === "all" ? "on" : ""}`} onClick={() => setFolderFilter("all")}>All <span className="num">{all.length}</span></button>
            {folders.map((f) => (
              <button key={f.id} className={`chip ${folderFilter === f.id ? "on" : ""}`} onClick={() => setFolderFilter(f.id)}>
                <span className="dot" style={{ background: f.color || "var(--accent)" }} />{f.name} <span className="num">{all.filter((w) => w.folder_id === f.id).length}</span>
              </button>
            ))}
            {folders.length > 0 && <button className={`chip ${folderFilter === "none" ? "on" : ""}`} onClick={() => setFolderFilter("none")}>No folder <span className="num">{all.filter((w) => !w.folder_id).length}</span></button>}
            {shown.length > 0 && <button className="chip show-sm" aria-pressed={allShownSelected}
              onClick={() => setSelected(allShownSelected ? new Set() : new Set(shown.map((w) => w.id)))}>{allShownSelected ? "Clear selection" : `Select all ${shown.length}`}</button>}
            <button className="chip" onClick={() => setModal("folders")}><Icon name="folder" size={12} />{folders.length ? "Manage folders" : "New folder"}</button>
          </div>
          <div className="row filters" style={{ flex: "1 1 320px", justifyContent: "flex-end" }}>
            <select value={chainF} onChange={(e) => setChainF(e.target.value)} style={{ flex: "0 1 190px" }} aria-label="Filter by chain">
              <option value="all">All chains ({all.length})</option>
              {[...CHAIN_ORDER, ...new Set([...extras.map((n) => n.id), ...all.map((w) => w.network).filter(isExtraChain)])].map((c) => (
                <option key={c} value={c}>{chainOf(c).name} ({all.filter((w) => (w.network ?? "solana") === c).length})</option>))}
            </select>
          <div style={{ flex: "1 1 200px", maxWidth: 280 }}><SearchInput aria-label="Search wallets" placeholder="Search label or address" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          </div>
        </div>
        {loadErr && !wallets ? <LoadError what="Wallets" error={loadErr} onRetry={() => load(false)} busy={loadingBal} /> : !wallets ? <Loading rows={5} /> : all.length === 0 ? (
          <Empty icon="wallet" title="No wallets yet" text="Create managed wallets to launch and trade from, or add an address to watch."
            action={<button className="btn sm" onClick={() => setModal("create")}><Icon name="plus" size={14} />Add wallets</button>} />
        ) : shown.length === 0 ? <Empty icon="search" title="No wallets match" /> : (
          <TableWrap><table className="rtable">
            <caption className="sr-only">Your wallets</caption>
            <thead><tr>
              <th className="cb"><input type="checkbox" checked={allShownSelected} aria-label="Select all"
                onChange={() => setSelected(allShownSelected ? new Set() : new Set(shown.map((w) => w.id)))} /></th>
              <th>Wallet</th><th>Chain</th><th>Address</th><th className="hide-sm">Type</th><th className="hide-sm">Folder</th><th className="r">Balance</th><th className="r"><span className="sr-only">Actions</span></th>
            </tr></thead>
            <tbody>
              {shown.map((w) => {
                const fu = funderOf(w.id);
                return (
                  <tr key={w.id} className={`clickable has-cb ${selected.has(w.id) ? "sel" : ""}`} onClick={() => toggle(w.id)}>
                    <td className="cb"><input type="checkbox" checked={selected.has(w.id)} onChange={() => toggle(w.id)} onClick={(e) => e.stopPropagation()} aria-label={`Select ${w.label || w.address}`} /></td>
                    <td className="span"><div className="row" style={{ gap: 8 }}>
                      <span style={{ fontWeight: 550 }}>{w.label || "Untitled"}</span>
                      {w.is_main ? <span className="badge good" title="Your account's main wallet. Launches draw USDC from it first.">main</span>
                        : fu && <span className="badge info">funder{fu.is_default ? " · default" : ""}</span>}
                      {w.kind !== "managed" && <span className="badge show-sm">{w.kind === "connected" ? "your wallet" : "watch-only"}</span>}
                    </div></td>
                    <td data-label="Chain"><ChainLabel network={w.network} size={16} /></td>
                    <td data-label="Address"><Address address={w.address} network={w.network} /></td>
                    <td className="hide-sm"><StatusBadge status={w.kind} label={w.kind === "managed" ? "managed" : w.kind === "connected" ? `your wallet (${OWN_APP[extras.find((n) => n.id === w.network)?.family ?? (w.network.startsWith("ton") ? "ton" : "evm")]})` : "watch-only"} /></td>
                    <td className="hide-sm text-2">{folderOf(w.folder_id)?.name ?? <span className="muted">—</span>}</td>
                    <td className="r num" data-label="Balance">{isExtraChain(w.network) ? (
                      <span className="stack tight" style={{ alignItems: "flex-end", gap: 1 }}>
                        {w.balance_native != null ? <span style={{ fontWeight: 600 }}>{w.balance_native} <span className="muted">{chainOf(w.network).native}</span></span>
                          : balances ? <span className="state unavailable"><Icon name="xcircle" size={12} />Unavailable</span> : <span className="muted">—</span>}
                        <Link to={`/networks?network=${w.network}`} className="small" onClick={(e) => e.stopPropagation()}>Tokens, send, history</Link>
                      </span>
                    ) : typeof w.balance_sol === "number" ? (
                      <div className="stack tight" style={{ alignItems: "flex-end", gap: 1 }}>
                        <span style={{ fontWeight: 600 }}>{typeof w.usd === "number" ? usd(w.usd) : <>{fmt(w.balance_sol, 4)} <span className="muted">{chainOf(w.network).native}</span></>}</span>
                        {typeof w.usd === "number" && <span className="muted small">{fmt(w.balance_sol, 4)} {chainOf(w.network).native} · {fmt(w.stable ?? 0, 2)} {w.stable_symbol}</span>}
                        {typeof w.usd !== "number" && typeof w.stable === "number" && <span className="muted small">{fmt(w.stable, 2)} {w.stable_symbol}</span>}
                      </div>)
                      : balances ? <span className="state unavailable" title="This balance couldn't be read. Refresh balances to retry."><Icon name="xcircle" size={12} />Unavailable</span>
                      : <span className="muted" title="Load balances to see">—<span className="sr-only"> not loaded</span></span>}</td>
                    <td className="r" onClick={(e) => e.stopPropagation()}>
                      {w.kind === "managed" && !fu && <button className="btn sm ghost row-action" onClick={() => makeFunder(w)} aria-label={`Make ${w.label || w.address} a funder`}>Make funder</button>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table></TableWrap>
        )}
      </div>

      {sel.length > 0 && (
        <div className="selbar" role="region" aria-label="Actions for selected wallets">
          <b className="num" aria-live="polite">{sel.length} selected</b>
          {selNet && <span className="row nowrap small text-2" style={{ gap: 6 }}><ChainMark network={selNet} size={16} />{chainOf(selNet).name}</span>}
          {selManaged.length !== sel.length && <span className="muted small">({sel.length - selManaged.length} watch-only ignored)</span>}
          <div className="grow" />
          {selNets.length > 1 && <span className="warn-t small">Money actions need wallets on one chain</span>}
          {extraSel && <Link className="small" to={`/networks?network=${selNet}`}>Send on {chainOf(selNet).name} from Networks</Link>}
          <button className="btn sm buy" disabled={!selNet || extraSel} onClick={() => setModal("fund")}><Icon name="arrowdown" size={14} />Fund</button>
          <button className="btn sm secondary" disabled={!selNet || extraSel} onClick={() => setModal("withdraw")}><Icon name="arrowup" size={14} />Withdraw</button>
          <button className="btn sm secondary" disabled={!selNet || extraSel} onClick={() => setModal("disperse")} title="Split a coin or token from one wallet across the selected wallets"><Icon name="send" size={14} />Disperse</button>
          <button className="btn sm secondary" disabled={!selNet || extraSel} onClick={() => setModal("consolidate")} title="Gather a coin or token from the selected wallets into one"><Icon name="download" size={14} />Consolidate</button>
          <button className="btn sm secondary" onClick={() => { setMoveTo(""); setMoveNew(""); setModal("move"); }}><Icon name="folder" size={14} />Move to folder</button>
          <button className="btn sm ghost" onClick={() => setSelected(new Set())}>Clear</button>
        </div>
      )}

      <p className="muted small row" style={{ gap: 6 }}><Icon name="lock" size={13} />Keys are held by the signing service. They are never shown in the browser or stored by Helm Terminals.</p>

      {modal === "create" && <CreateWallets onClose={() => setModal(null)} onDone={() => load()} folders={folders} extras={extras}
        defaultFolder={folders.some((f) => f.id === folderFilter) ? folderFilter : ""} />}

      {modal === "folders" && <ManageFolders folders={folders} onClose={() => setModal(null)}
        onChanged={(deletedId) => { if (deletedId && folderFilter === deletedId) setFolderFilter("all"); load(); }} />}

      {modal === "move" && (
        <Modal title="Move to folder" sub={`${sel.length} wallet${sel.length === 1 ? "" : "s"} selected`} onClose={() => setModal(null)} lock={moving}
          foot={<><button className="btn ghost" onClick={() => setModal(null)} disabled={moving}>Cancel</button>
            <button className="btn" disabled={moving || (moveTo === NEW_FOLDER && !moveNew.trim())} onClick={async () => {
              if (moving) return;
              setMoving(true);
              try {
                const folder_id = moveTo === NEW_FOLDER ? (await ensureFolder(moveNew, folders)).id : moveTo || null;
                await api("/wallets/move", { body: { wallet_ids: sel, folder_id } });
                toast("good", `Moved ${sel.length} wallet${sel.length === 1 ? "" : "s"}`);
                setModal(null); setSelected(new Set()); load();
              } catch (e) { toast("bad", errMsg(e)); } finally { setMoving(false); }
            }}>{moving && <Spinner />}Move</button></>}>
          <FolderSelect folders={folders} value={moveTo} onChange={setMoveTo} newName={moveNew} onNewName={setMoveNew} />
        </Modal>
      )}

      {modal === "fund" && (
        <Modal title="Fund wallets" sub={`Send ${coin} on ${chainOf(selNet).name} from one wallet to ${selManaged.filter((id) => id !== fundFrom).length} selected wallet(s)`} onClose={() => setModal(null)}
          foot={<><button className="btn ghost" onClick={() => setModal(null)}>Cancel</button>
            <button className="btn" disabled={!fundFrom || !(+fundAmount > 0) || selManaged.filter((id) => id !== fundFrom).length === 0}
              onClick={() => { setModal(null); setDialog({ title: "Fund wallets", path: "/wallets/fund", network: selNet ?? "solana",
                body: { funder_wallet_id: fundFrom, allocations: selManaged.filter((id) => id !== fundFrom).map((id) => ({ wallet_id: id, sol: +fundAmount })) } }); }}>
              Review<Icon name="arrowright" size={14} /></button></>}>
          <Field label="Send from">
            <select value={fundFrom} onChange={(e) => setFundFrom(e.target.value)}>
              <option value="">Choose a wallet…</option>
              {managed.filter((w) => w.network === selNet).sort((a, b) => Number(!!funderOf(b.id)) - Number(!!funderOf(a.id))).map((w) => (
                <option key={w.id} value={w.id}>{funderOf(w.id) ? "★ " : ""}{w.label || "Wallet"} · {short(w.address)}{w.balance_sol !== undefined ? ` · ${fmt(w.balance_sol)} ${coin}` : ""}</option>
              ))}
            </select>
          </Field>
          <Field label="Amount per wallet"><UnitInput unit={coin} value={fundAmount} onChange={(e) => setFundAmount(e.target.value)} /></Field>
          <Callout>Total: <b className="num">{fmt(+fundAmount * selManaged.filter((id) => id !== fundFrom).length)} {coin}</b> plus network fees. You'll see the full preview before anything is sent.</Callout>
        </Modal>
      )}

      {modal === "withdraw" && (
        <Modal title={`Withdraw ${coin}`} sub={`Sweep all ${coin} on ${chainOf(selNet).name} from ${selManaged.length} wallet(s), keeping a little for fees`} onClose={() => setModal(null)}
          foot={<><button className="btn ghost" onClick={() => setModal(null)}>Cancel</button>
            <button className="btn" disabled={evmSel ? !isEvmAddr(dest.trim()) : !isSolanaAddr(dest.trim())}
              onClick={() => { setModal(null); setDialog({ title: `Withdraw ${coin}`, path: "/wallets/withdraw", network: selNet ?? "solana", body: { wallet_ids: selManaged, destination: dest.trim(), all: true } }); }}>
              Review<Icon name="arrowright" size={14} /></button></>}>
          <Field label="Destination address" hint="Double-check this. Transfers can't be reversed."><input className="mono" value={dest} onChange={(e) => setDest(e.target.value)} placeholder={evmSel ? "0x… your main wallet on " + chainOf(selNet).name : "Your main wallet address"} autoFocus /></Field>
          {evmSel && <Callout icon="info">Send to an address on {chainOf(selNet).name}. The same 0x address works on other EVM chains, but the {coin} only arrives on {chainOf(selNet).name}.</Callout>}
          <Callout tone="warn" icon="shield">Withdrawals need your 2FA code on the next step.</Callout>
        </Modal>
      )}

      {(modal === "disperse" || modal === "consolidate") && selNet && (
        <Distribute mode={modal} network={selNet} wallets={all} selected={selManaged} onClose={() => setModal(null)}
          onReview={(r) => { setModal(null); setDialog(r); }} />
      )}


      {dialog && <SpendDialog {...dialog} onClose={() => setDialog(null)} onDone={() => { setSelected(new Set()); load(true); }} />}
    </>
  );
}
