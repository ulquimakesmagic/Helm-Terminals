import { useMemo, useState } from "react";
import { fmt, short } from "../api";
import { chainOf, stableOf } from "../chains";
import { Callout, Field, Icon, Modal, UnitInput } from "./ui";
import type { SpendRequest } from "./Distribute";

interface Wal { id: string; network: string; address: string; kind: string; label: string; balance_sol?: number; stable?: number }

/**
 * Bridge / cross-chain swap through Relay (relay.link): send a chain's dollar stablecoin or its native coin from
 * one of your wallets and receive the destination chain's stablecoin or native coin in another of yours. Same-chain
 * swaps between the stablecoin and the native coin are allowed too (that's how EVM wallets convert without Jupiter).
 * The request goes to /bridge, so the SpendDialog shows Relay's fresh quote — what should arrive, the minimum after
 * slippage, fees and timing — before anything is confirmed.
 */
export default function BridgeDialog({ wallets, initialFromId, onClose, onReview }: {
  wallets: Wal[]; initialFromId?: string;
  onClose: () => void; onReview: (r: SpendRequest) => void;
}) {
  const managed = useMemo(() => wallets.filter((w) => w.kind === "managed"), [wallets]);
  const [fromId, setFromId] = useState(initialFromId && managed.some((w) => w.id === initialFromId) ? initialFromId : "");
  const [toId, setToId] = useState("");
  const [fromAsset, setFromAsset] = useState<"stable" | "native">("stable");
  const [toAsset, setToAsset] = useState<"native" | "stable">("native");
  const [amount, setAmount] = useState("");
  const [slippage, setSlippage] = useState("1");

  const from = managed.find((w) => w.id === fromId);
  const to = managed.find((w) => w.id === toId);
  const sameChain = !!from && !!to && from.network === to.network;
  const sameAsset = fromAsset === toAsset;
  const assetSym = (w: Wal | undefined, asset: "stable" | "native") =>
    !w ? "" : asset === "native" ? chainOf(w.network).native : stableOf(w.network);
  const ready = !!from && !!to && +amount > 0 && +slippage > 0 && (!sameChain || !sameAsset);

  const label = (w: Wal) => `${w.label || "Wallet"} · ${short(w.address)}`;
  const balanceNote = (w: Wal | undefined, asset: "stable" | "native") => {
    if (!w) return "";
    if (asset === "native") return w.balance_sol != null ? ` · ${fmt(w.balance_sol, 4)} ${chainOf(w.network).native}` : "";
    return w.stable != null ? ` · ${fmt(w.stable, 2)} ${stableOf(w.network)}` : "";
  };

  function review() {
    if (!from || !to) return;
    onReview({
      title: sameChain
        ? `Swap ${assetSym(from, fromAsset)} for ${assetSym(to, toAsset)} on ${chainOf(from.network).name}`
        : `Bridge ${assetSym(from, fromAsset)} to ${chainOf(to.network).name}`,
      path: "/bridge", network: from.network,
      body: { from_wallet_id: from.id, to_wallet_id: to.id, from_asset: fromAsset, to_asset: toAsset,
              amount: +amount, slippage_pct: +slippage },
    });
  }

  return (
    <Modal title="Bridge between chains" sub="Move value between your wallets on any chain · Relay (relay.link)" onClose={onClose} wide
      foot={<><button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn" disabled={!ready} onClick={review}>Review<Icon name="arrowright" size={14} /></button></>}>
      <Field label="Send from">
        <select value={fromId} onChange={(e) => setFromId(e.target.value)}>
          <option value="">Choose a wallet…</option>
          {managed.map((w) => <option key={w.id} value={w.id}>{label(w)}{balanceNote(w, fromAsset)}</option>)}
        </select>
      </Field>
      <Field label="What to send">
        <div className="seg full" role="group" aria-label="Asset to send">
          <button type="button" className={fromAsset === "stable" ? "on" : ""} onClick={() => setFromAsset("stable")}>
            Dollar stablecoin{from && ` · ${stableOf(from.network)}`}</button>
          <button type="button" className={fromAsset === "native" ? "on" : ""} onClick={() => setFromAsset("native")}>
            Network coin{from && ` · ${chainOf(from.network).native}`}</button>
        </div>
      </Field>
      <Field label="Receive into">
        <select value={toId} onChange={(e) => setToId(e.target.value)}>
          <option value="">Choose a wallet…</option>
          {managed.filter((w) => !from || w.id !== from.id).map((w) => <option key={w.id} value={w.id}>{label(w)}{balanceNote(w, toAsset)}</option>)}
        </select>
      </Field>
      <Field label="What to receive">
        <div className="seg full" role="group" aria-label="Asset to receive">
          <button type="button" className={toAsset === "stable" ? "on" : ""} onClick={() => setToAsset("stable")}>
            Dollar stablecoin{to && ` · ${stableOf(to.network)}`}</button>
          <button type="button" className={toAsset === "native" ? "on" : ""} onClick={() => setToAsset("native")}>
            Network coin{to && ` · ${chainOf(to.network).native}`}</button>
        </div>
      </Field>
      {from && to && !sameChain && <Callout icon="info">{assetSym(from, fromAsset)} leaves <b>{chainOf(from.network).name}</b> from {label(from)} and arrives on <b>{chainOf(to.network).name}</b> as {assetSym(to, toAsset)} in {label(to)}. If Relay's fill fails, the amount is refunded to the source wallet.</Callout>}
      {sameChain && !sameAsset && <Callout icon="info">Same-chain conversion on {chainOf(from!.network).name}: {assetSym(from, fromAsset)} → {assetSym(to, toAsset)} through Relay's routing. For Solana coins, the Swap dialog runs on Jupiter instead.</Callout>}
      {sameChain && sameAsset && <Callout tone="warn">Both sides are the same asset on {chainOf(from!.network).name} — that's a plain transfer; use Send instead.</Callout>}
      <div className="row nowrap" style={{ gap: 10 }}>
        <Field label="Amount" style={{ flex: 1 }}>
          <UnitInput unit={from ? assetSym(from, fromAsset) : ""} value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" placeholder="0.0" />
        </Field>
        <Field label="Max slippage" style={{ flex: 1 }}>
          <UnitInput unit="%" value={slippage} onChange={(e) => setSlippage(e.target.value)} inputMode="decimal" />
        </Field>
      </div>
      <p className="muted small">The next step shows Relay's live quote — what should arrive, the guaranteed minimum, fees and timing — before anything is sent.</p>
    </Modal>
  );
}
