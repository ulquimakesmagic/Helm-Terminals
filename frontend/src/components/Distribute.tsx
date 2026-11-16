import { useEffect, useMemo, useState } from "react";
import { api, fmt, short } from "../api";
import { chainOf, stableOf } from "../chains";
import { isEvmAddr, isSolanaAddr } from "../validate";
import { ChainLabel } from "./marks";
import { Callout, Field, Icon, Modal, UnitInput } from "./ui";
import FeeWalletSelect from './FeeWalletSelect';
import SplitChoice, {SplitMode} from './SplitChoice';

interface Wal { id: string; network: string; address: string; kind: string; label: string; balance_sol?: number }
interface Launch { id: string; name: string; symbol: string; network: string; mint_address: string | null }
export type SpendRequest = { title: string; path: string; body: Record<string, unknown>; network?: string };

const CUSTOM = "__custom__";

/**
 * Disperse (one wallet → many of yours) or consolidate (many of yours → one), for the chain's coin, its dollar
 * stablecoin, a launch token or any token address. Hands the request to the spend dialog for preview + confirm.
 */
export default function Distribute({ mode: initial, network, wallets, selected, initialAsset, onClose, onReview }: {
  mode: "disperse" | "consolidate"; network: string; wallets: Wal[]; selected: string[]; initialAsset?: string;
  onClose: () => void; onReview: (r: SpendRequest) => void;
}) {
  const chain = chainOf(network);
  const stable = stableOf(network);
  const [mode, setMode] = useState(initial);
  const [launches, setLaunches] = useState<Launch[]>([]);
  const [asset, setAsset] = useState(initialAsset ? "__custom__" : "native");
  const [customToken, setCustomToken] = useState(initialAsset ?? "");
  const [hub, setHub] = useState(""); // disperse: source; consolidate: destination
  const [split, setSplit] = useState<SplitMode>('total');
  const [weights,setWeights]=useState<Record<string,string>>({});
  const [seed,setSeed]=useState(()=>crypto.randomUUID());
  const [feeWallet,setFeeWallet]=useState('');
  const [total, setTotal] = useState("");
  const [percent, setPercent] = useState("100");
  const [each, setEach] = useState<Record<string, string>>({});

  useEffect(() => { api<Launch[]>("/launches").then(setLaunches).catch(() => setLaunches([])); }, []);
  const onChain = useMemo(() => wallets.filter((w) => w.network === network && w.kind === "managed"), [wallets, network]);
  const tokens = launches.filter((l) => l.network === network && l.mint_address);
  const others = selected.filter((id) => id !== hub && onChain.some((w) => w.id === id));
  const unit = asset === "native" ? chain.native : asset === "stable" ? stable
    : asset === CUSTOM ? "tokens" : `$${tokens.find((l) => l.mint_address === asset)?.symbol ?? "tokens"}`;
  const assetValue = asset === CUSTOM ? customToken.trim() : asset;
  const tokenOk = asset !== CUSTOM || (network === "solana" ? isSolanaAddr(assetValue) : isEvmAddr(assetValue));
  const label = (w?: Wal) => w ? `${w.label || "Wallet"} · ${short(w.address)}` : "";

  const ready = !!hub && others.length > 0 && tokenOk && (mode === "consolidate" ? +percent > 0 && +percent <= 100
    : split === "custom" ? others.every((id) => +(each[id] ?? 0) > 0) : split === "percent" ? +percent > 0 && +percent <= 100 : +total > 0 && (split !== 'weighted' || others.every((id,i)=>Number.isInteger(+(weights[id] ?? i+1)) && +(weights[id] ?? i+1)>0 && +(weights[id] ?? i+1)<=10000)));

  function review() {
    const hubW = onChain.find((w) => w.id === hub);
    if (mode === "disperse") {
      const body: Record<string, unknown> = { from_wallet_id: hub, to_wallet_ids: others, asset: assetValue, fee_wallet_id:feeWallet || null };
      if (split === "custom") Object.assign(body, { split: "custom", amounts: others.map((id) => each[id]) });
      else if (split === 'percent') body.percent=percent;
      else {body.total=total;if(split==='weighted')Object.assign(body,{split,weights:others.map((id,i)=>+(weights[id] ?? i+1))});if(split==='random')Object.assign(body,{split,split_seed:seed});}
      onReview({ title: `Disperse ${unit} from ${label(hubW)}`, path: "/wallets/disperse", network, body });
    } else {
      onReview({ title: `Consolidate ${unit} into ${label(hubW)}`, path: "/wallets/consolidate", network,
        body: { from_wallet_ids: others, to_wallet_id: hub, asset: assetValue, percent: +percent,fee_wallet_id:feeWallet || null } });
    }
  }

  return (
    <Modal title="Move between your wallets" sub={<span className="row nowrap" style={{ gap: 6 }}><ChainLabel network={network} size={14} /><span>· only wallets on your account · no platform fee</span></span>} onClose={onClose} wide
      foot={<><button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn" disabled={!ready} onClick={review}>Review<Icon name="arrowright" size={14} /></button></>}>
      <div className="seg full">
        <button className={mode === "disperse" ? "on" : ""} onClick={() => setMode("disperse")}>Disperse (split)</button>
        <button className={mode === "consolidate" ? "on" : ""} onClick={() => setMode("consolidate")}>Consolidate</button>
      </div>

      <Field label="What to move">
        <select value={asset} onChange={(e) => setAsset(e.target.value)}>
          <option value="native">{chain.native} (network coin)</option>
          <option value="stable">{stable} (dollar balance)</option>
          {tokens.map((l) => <option key={l.id} value={l.mint_address!}>${l.symbol} · {l.name} (your launch)</option>)}
          <option value={CUSTOM}>Another token address…</option>
        </select>
      </Field>
      {asset === CUSTOM && (
        <Field label="Token address"><input className="mono" value={customToken} onChange={(e) => setCustomToken(e.target.value)}
          placeholder={network === "solana" ? "Mint address" : "0x… token contract"} /></Field>
      )}

      <Field label={mode === "disperse" ? "Send from" : "Gather into"}>
        <select value={hub} onChange={(e) => setHub(e.target.value)}>
          <option value="">Choose a wallet…</option>
          {onChain.map((w) => <option key={w.id} value={w.id}>{label(w)}{asset === "native" && w.balance_sol !== undefined ? ` · ${fmt(w.balance_sol)} ${chain.native}` : ""}</option>)}
        </select>
      </Field>

      <div className="muted small" style={{ marginBottom: 10 }}>
        {mode === "disperse" ? "To" : "From"} {others.length} selected wallet{others.length === 1 ? "" : "s"}
        {selected.length !== others.length + (selected.includes(hub) ? 1 : 0) && " (wallets on other chains or watch-only are left out)"}
      </div>

      {mode === "disperse" ? (
        <>
          <SplitChoice value={split} onChange={setSplit}/>
          {['total','weighted','random'].includes(split) && <Field label="Total to split" hint={split==='total' && others.length ? `${fmt((+total || 0) / others.length, 6)} ${unit} each` : 'Exact allocations are shown in review and remain fixed through confirmation.'}>
            <UnitInput unit={unit} value={total} onChange={(e) => setTotal(e.target.value)} /></Field>}
          {split==='weighted' && <div className="split-recipient-list">{others.map((id,i)=><Field key={id} label={`${label(onChain.find(w=>w.id===id))} · weight`}><input type="number" min={1} max={10000} step={1} value={weights[id] ?? String(i+1)} onChange={e=>setWeights(old=>({...old,[id]:e.target.value}))}/></Field>)}</div>}
          {split==='random' && <div className="row"><p className="muted small grow">Unequal shares of this fixed total. Review every recipient before confirming.</p><button type="button" className="btn sm secondary" onClick={()=>setSeed(crypto.randomUUID())}>Reroll split</button></div>}
          {split === "percent" && <Field label="Share of the source balance" hint={asset === "native" ? "Network fees are kept back first." : undefined}>
            <UnitInput unit="%" value={percent} onChange={(e) => setPercent(e.target.value)} /></Field>}
          {split === "custom" && (
            <div className="stack tight">
              {others.map((id) => (
                <div key={id} className="row between" style={{ gap: 10 }}>
                  <span className="small">{label(onChain.find((w) => w.id === id))}</span>
                  <div style={{ width: 170 }}><UnitInput unit={unit} value={each[id] ?? ""} onChange={(e) => setEach({ ...each, [id]: e.target.value })} /></div>
                </div>
              ))}
            </div>
          )}
        </>
      ) : (
        <Field label="Share of each wallet's balance" hint={asset === "native" ? "At 100%, each wallet keeps just enough for the network fee." : undefined}>
          <UnitInput unit="%" value={percent} onChange={(e) => setPercent(e.target.value)} />
        </Field>
      )}

      <FeeWalletSelect network={network} wallets={wallets} value={feeWallet} onChange={setFeeWallet}/>
      <Callout icon="info">
        Amounts are exactly what the preview shows. When you move one of your launch tokens, the receiving wallets are attached
        to that launch so its transparency panel keeps counting the supply you control.
      </Callout>
    </Modal>
  );
}
