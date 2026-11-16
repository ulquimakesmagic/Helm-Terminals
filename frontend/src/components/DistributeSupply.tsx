import { useEffect, useMemo, useState } from "react";
import { api, fmt, short } from "../api";
import { Callout, Field, Icon, Modal, UnitInput } from "./ui";
import type { SpendRequest } from "./Distribute";
import FeeWalletSelect from './FeeWalletSelect';
import SplitChoice, {SplitMode} from './SplitChoice';

interface Wal { id: string; network: string; address: string; kind: string; label: string; folder_id: string | null }
interface Folder { id: string; name: string; wallet_count?: number }

/**
 * Post-launch supply dispersal: send one of your launch tokens from a source wallet (default: the dev
 * wallet) to every managed wallet of a chosen folder on the launch's chain, split evenly by a fixed
 * total or a share of the source balance. The request goes through /wallets/disperse, so allocation
 * locks, wallet attachment and the transparency panel all apply as usual.
 */
export default function DistributeSupply({ launch, wallets, devWalletId, attachedIds = [], onClose, onReview }: {
  launch: { id: string; network: string; mint_address: string | null; symbol: string };
  wallets: Wal[]; devWalletId?: string; attachedIds?: string[];
  onClose: () => void; onReview: (r: SpendRequest) => void;
}) {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [folderId, setFolderId] = useState("");
  const [hub, setHub] = useState("");
  const [targetMode, setTargetMode] = useState<"attached" | "folder">(attachedIds.length ? "attached" : "folder");
  const [each, setEach] = useState<Record<string,string>>({});
  const [split, setSplit] = useState<SplitMode>('total');
  const [weights,setWeights]=useState<Record<string,string>>({});
  const [seed,setSeed]=useState(()=>crypto.randomUUID());
  const [feeWallet,setFeeWallet]=useState('');
  const [total, setTotal] = useState("");
  const [percent, setPercent] = useState("100");

  useEffect(() => { api<Folder[]>("/folders").then(setFolders).catch(() => setFolders([])); }, []);
  const onChain = useMemo(() => wallets.filter((w) => w.network === launch.network && w.kind === "managed"), [wallets, launch.network]);
  useEffect(() => {
    if (!hub && devWalletId && onChain.some((w) => w.id === devWalletId)) setHub(devWalletId);
  }, [hub, devWalletId, onChain]);
  const targets = onChain.filter((w) => (targetMode === "attached" ? attachedIds.includes(w.id) : !!folderId && w.folder_id === folderId) && w.id !== hub);
  const folder = folders.find((f) => f.id === folderId);
  const label = (w: Wal) => `${w.label || "Wallet"} · ${short(w.address)}`;
  const ready = !!hub && targets.length > 0 && targets.length<=50 && (split === "custom" ? targets.every(w => Number.isFinite(+each[w.id]) && +each[w.id] > 0) : split === 'percent' ? +percent > 0 && +percent <= 100 : +total > 0 && (split!=='weighted' || targets.every((w,i)=>Number.isInteger(+(weights[w.id] ?? i+1)) && +(weights[w.id] ?? i+1)>0 && +(weights[w.id] ?? i+1)<=10000)));

  function review() {
    const body: Record<string, unknown> = { from_wallet_id: hub, to_wallet_ids: targets.map((w) => w.id), asset: launch.mint_address,fee_wallet_id:feeWallet || null };
    if (split === "custom") { body.split = "custom"; body.amounts = targets.map(w => each[w.id]); } else if (split === 'percent') body.percent=percent; else {body.total=total;if(split==='weighted')Object.assign(body,{split,weights:targets.map((w,i)=>+(weights[w.id] ?? i+1))});if(split==='random')Object.assign(body,{split,split_seed:seed});}
    onReview({
      title: `Distribute $${launch.symbol} to ${targets.length} wallets`,
      path: "/wallets/disperse", network: launch.network, body,
    });
  }

  return (
    <Modal title={`Distribute $${launch.symbol} supply`} onClose={onClose}
      sub={`Distribute tokens to attached launch wallets or a wallet folder on this chain`}
      foot={<><button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn" disabled={!ready} onClick={review}>Review<Icon name="arrowright" size={14} /></button></>}>
      <Field label="Send from">
        <select value={hub} onChange={(e) => setHub(e.target.value)}>
          <option value="">Choose a wallet…</option>
          {onChain.map((w) => <option key={w.id} value={w.id}>{label(w)}{w.id === devWalletId ? " — dev" : ""}</option>)}
        </select>
      </Field>
      <Field label="Recipients"><div className="seg"><button type="button" className={targetMode === "attached" ? "on" : ""} onClick={() => setTargetMode("attached")}>Launch wallets</button><button type="button" className={targetMode === "folder" ? "on" : ""} onClick={() => setTargetMode("folder")}>Wallet folder</button></div></Field>
      {targetMode === "folder" && <Field label="Distribute to folder">
        <select value={folderId} onChange={(e) => setFolderId(e.target.value)}>
          <option value="">Choose a folder…</option>
          {folders.map((f) => <option key={f.id} value={f.id}>{f.name}{typeof f.wallet_count === "number" ? ` · ${f.wallet_count} wallet${f.wallet_count === 1 ? "" : "s"}` : ""}</option>)}
        </select>
      </Field>}
      {targetMode === "folder" && folders.length === 0 && <Callout icon="info">You have no wallet folders yet. Create one on the Wallets page first.</Callout>}
      {folderId && targets.length === 0 && <Callout tone="warn">No managed wallets from “{folder?.name}” on this chain (or the folder only holds the source wallet).</Callout>}
      {targets.length > 0 && <p className="muted small">{targets.length} recipient wallet{targets.length === 1 ? "" : "s"}{targetMode === "folder" ? ` in “${folder?.name}”` : " attached to this launch"}. {split === "custom" ? "Set an explicit amount for each recipient." : ['weighted','random'].includes(split)?'Variable allocations are shown before confirmation.':'The total is split equally.'}</p>}
      {targets.length>50 && <Callout tone="warn">Choose up to 50 recipients per distribution.</Callout>}
      <Field label="Split">
        <SplitChoice value={split} onChange={setSplit}/>
      </Field>
      {split === "custom" ? <div className="split-recipient-list">{targets.map(w => <Field key={w.id} label={label(w)}><UnitInput unit={launch.symbol} value={each[w.id] ?? ""} inputMode="decimal" onChange={e => setEach(old => ({...old,[w.id]:e.target.value}))} /></Field>)}</div> : split !== "percent"
        ? <Field label={`Total ${launch.symbol} to split`} hint={split==='total' && targets.length ? `${fmt((+total || 0) / targets.length, 6)} ${launch.symbol} each` : 'The exact total is preserved. Review every recipient before confirming.'}>
            <UnitInput unit={launch.symbol} value={total} onChange={(e) => setTotal(e.target.value)} inputMode="decimal" /></Field>
        : <Field label="Share of the source wallet's token balance">
            <UnitInput unit="%" value={percent} onChange={(e) => setPercent(e.target.value)} inputMode="decimal" /></Field>}
      {split==='weighted' && <div className="split-recipient-list">{targets.map((w,i)=><Field key={w.id} label={`${label(w)} · weight`}><input type="number" min={1} max={10000} step={1} value={weights[w.id] ?? String(i+1)} onChange={e=>setWeights(old=>({...old,[w.id]:e.target.value}))}/></Field>)}</div>}
      {split==='random' && <button type="button" className="btn sm secondary" onClick={()=>setSeed(crypto.randomUUID())}>Reroll split</button>}
      <FeeWalletSelect network={launch.network} wallets={wallets} value={feeWallet} onChange={setFeeWallet}/>
      <Callout icon="info">Receiving wallets are attached to the launch, so its supply-transparency panel keeps counting this supply as yours. Allocation locks are checked in review.</Callout>
    </Modal>
  );
}
