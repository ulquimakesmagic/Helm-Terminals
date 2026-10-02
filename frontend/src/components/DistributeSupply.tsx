import { useEffect, useMemo, useState } from "react";
import { api, fmt, short } from "../api";
import { Callout, Field, Icon, Modal, UnitInput } from "./ui";
import type { SpendRequest } from "./Distribute";

interface Wal { id: string; network: string; address: string; kind: string; label: string; folder_id: string | null }
interface Folder { id: string; name: string; wallet_count?: number }

/**
 * Post-launch supply dispersal: send one of your launch tokens from a source wallet (default: the dev
 * wallet) to every managed wallet of a chosen folder on the launch's chain, split evenly by a fixed
 * total or a share of the source balance. The request goes through /wallets/disperse, so allocation
 * locks, wallet attachment and the transparency panel all apply as usual.
 */
export default function DistributeSupply({ launch, wallets, devWalletId, onClose, onReview }: {
  launch: { id: string; network: string; mint_address: string | null; symbol: string };
  wallets: Wal[]; devWalletId?: string;
  onClose: () => void; onReview: (r: SpendRequest) => void;
}) {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [folderId, setFolderId] = useState("");
  const [hub, setHub] = useState("");
  const [split, setSplit] = useState<"total" | "percent">("total");
  const [total, setTotal] = useState("");
  const [percent, setPercent] = useState("100");

  useEffect(() => { api<Folder[]>("/folders").then(setFolders).catch(() => setFolders([])); }, []);
  const onChain = useMemo(() => wallets.filter((w) => w.network === launch.network), [wallets, launch.network]);
  useEffect(() => {
    if (!hub && devWalletId && onChain.some((w) => w.id === devWalletId)) setHub(devWalletId);
  }, [hub, devWalletId, onChain]);
  const targets = onChain.filter((w) => w.folder_id === folderId && w.id !== hub);
  const folder = folders.find((f) => f.id === folderId);
  const label = (w: Wal) => `${w.label || "Wallet"} · ${short(w.address)}`;
  const ready = !!hub && targets.length > 0 && (split === "total" ? +total > 0 : +percent > 0 && +percent <= 100);

  function review() {
    const body: Record<string, unknown> = { from_wallet_id: hub, to_wallet_ids: targets.map((w) => w.id), asset: launch.mint_address };
    if (split === "total") body.total = +total; else body.percent = +percent;
    onReview({
      title: `Distribute $${launch.symbol} supply to “${folder?.name ?? "?"}”`,
      path: "/wallets/disperse", network: launch.network, body,
    });
  }

  return (
    <Modal title={`Distribute $${launch.symbol} supply`} onClose={onClose}
      sub={`Split the token across every managed wallet of a folder on this chain`}
      foot={<><button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn" disabled={!ready} onClick={review}>Review<Icon name="arrowright" size={14} /></button></>}>
      <Field label="Send from">
        <select value={hub} onChange={(e) => setHub(e.target.value)}>
          <option value="">Choose a wallet…</option>
          {onChain.map((w) => <option key={w.id} value={w.id}>{label(w)}{w.id === devWalletId ? " — dev" : ""}</option>)}
        </select>
      </Field>
      <Field label="Distribute to folder">
        <select value={folderId} onChange={(e) => setFolderId(e.target.value)}>
          <option value="">Choose a folder…</option>
          {folders.map((f) => <option key={f.id} value={f.id}>{f.name}{typeof f.wallet_count === "number" ? ` · ${f.wallet_count} wallet${f.wallet_count === 1 ? "" : "s"}` : ""}</option>)}
        </select>
      </Field>
      {folders.length === 0 && <Callout icon="info">You have no wallet folders yet. Create one on the Wallets page first.</Callout>}
      {folderId && targets.length === 0 && <Callout tone="warn">No managed wallets from “{folder?.name}” on this chain (or the folder only holds the source wallet).</Callout>}
      {targets.length > 0 && <p className="muted small">Each of the {targets.length} wallet{targets.length === 1 ? "" : "s"} in “{folder?.name}” receives an even share.</p>}
      <Field label="Split">
        <div className="seg full" role="group" aria-label="Split mode">
          <button type="button" className={split === "total" ? "on" : ""} onClick={() => setSplit("total")}>Even · fixed total</button>
          <button type="button" className={split === "percent" ? "on" : ""} onClick={() => setSplit("percent")}>Even · % of balance</button>
        </div>
      </Field>
      {split === "total"
        ? <Field label={`Total ${launch.symbol} to split`} hint={targets.length ? `${fmt((+total || 0) / targets.length, 6)} ${launch.symbol} each` : undefined}>
            <UnitInput unit={launch.symbol} value={total} onChange={(e) => setTotal(e.target.value)} inputMode="decimal" /></Field>
        : <Field label="Share of the source wallet's token balance">
            <UnitInput unit="%" value={percent} onChange={(e) => setPercent(e.target.value)} inputMode="decimal" /></Field>}
      <Callout icon="info">Receiving wallets are attached to the launch, so its supply-transparency panel keeps counting this supply as yours. Allocation locks are checked in review.</Callout>
    </Modal>
  );
}
