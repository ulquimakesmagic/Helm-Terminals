import { useMemo, useState } from "react";
import { short } from "../api";
import { chainOf } from "../chains";
import { Callout, Field, Icon, Modal } from "./ui";
import type { SpendRequest } from "./Distribute";

interface Wal { id: string; network: string; address: string; kind: string; label: string }

/**
 * Sweep dust: move everything from the selected wallets into one — their full stablecoin balance and
 * their whole native coin (fees kept back). Stablecoins are sent first, then the coin, so every
 * wallet still has gas for its own sends. One confirmation covers all of it.
 */
export default function SweepDialog({ wallets, selectedIds, network, onClose, onReview }: {
  wallets: Wal[]; selectedIds: string[]; network: string;
  onClose: () => void; onReview: (r: SpendRequest) => void;
}) {
  const chain = chainOf(network);
  const candidates = useMemo(
    () => wallets.filter((w) => w.network === network && w.kind === "managed" && !selectedIds.includes(w.id)),
    [wallets, network, selectedIds]);
  const [destId, setDestId] = useState(candidates[0]?.id ?? "");
  const label = (w: Wal) => `${w.label || "Wallet"} · ${short(w.address)}`;
  const dest = candidates.find((w) => w.id === destId);

  return (
    <Modal title={`Sweep dust on ${chain.name}`} onClose={onClose}
      sub={`Empty ${selectedIds.length} wallet${selectedIds.length === 1 ? "" : "s"} into one — stablecoins and network coin, fees kept back`}
      foot={<><button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn" disabled={!dest}
          onClick={() => onReview({
            title: `Sweep ${selectedIds.length} wallet${selectedIds.length === 1 ? "" : "s"} on ${chain.name}`,
            path: "/wallets/sweep", network,
            body: { from_wallet_ids: selectedIds, to_wallet_id: dest!.id },
          })}>Review sweep<Icon name="arrowright" size={14} /></button></>}>
      {candidates.length === 0
        ? <Callout tone="warn">Every managed wallet on {chain.name} is selected — deselect the one that should receive the dust.</Callout>
        : <Field label="Sweep into">
            <select value={destId} onChange={(e) => setDestId(e.target.value)}>
              {candidates.map((w) => <option key={w.id} value={w.id}>{label(w)}</option>)}
            </select>
          </Field>}
      <Callout icon="info">Each selected wallet sends its whole dollar balance and everything but its fee reserve. Wallets with nothing to move are listed as skipped, not failed.</Callout>
      <p className="muted small">The preview shows every amount and the fee estimate before anything is confirmed.</p>
    </Modal>
  );
}
