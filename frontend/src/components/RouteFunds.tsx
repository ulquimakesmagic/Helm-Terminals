import { useEffect, useMemo, useState } from "react";
import { api, errMsg, short } from "../api";
import { CHAIN_ORDER, chainOf, stableOf } from "../chains";
import { ChainLabel } from "./marks";
import { Callout, Field, Icon, Modal, Spinner, UnitInput } from "./ui";
import type { SpendRequest } from "./Distribute";

interface Wal { id: string; network: string; address: string; kind: string; label: string }

/**
 * Move value between chains with Relay: typically USDC (your main balance) into the gas coin of the chain you're
 * about to launch on. Opens the spend dialog, which shows the live quote, Relay's fees and Helm's route fee.
 */
export default function RouteFunds({ wallets, toWalletId, toAsset: initialTo = "native", onClose, onReview }: {
  wallets?: Wal[]; toWalletId?: string; toAsset?: "native" | "stable"; onClose: () => void; onReview: (r: SpendRequest) => void;
}) {
  const [all, setAll] = useState<Wal[] | null>(wallets ?? null);
  const [err, setErr] = useState("");
  const managed = useMemo(() => (all ?? []).filter((w) => w.kind === "managed"), [all]);
  const [to, setTo] = useState(toWalletId ?? "");
  const [from, setFrom] = useState("");
  const [fromAsset, setFromAsset] = useState<"stable" | "native">("stable");
  const [toAsset, setToAsset] = useState<"native" | "stable">(initialTo);
  const [amount, setAmount] = useState("20");
  const [slip, setSlip] = useState("1");

  useEffect(() => { if (!wallets) api<Wal[]>("/wallets").then(setAll).catch((e) => setErr(errMsg(e))); }, [wallets]);

  const toW = managed.find((w) => w.id === to);
  const fromW = managed.find((w) => w.id === from);
  const fromUnit = fromW ? (fromAsset === "stable" ? stableOf(fromW.network) : chainOf(fromW.network).native) : "";
  const sameAsset = fromW && toW && fromW.network === toW.network && fromAsset === toAsset;
  const byChain = (ws: Wal[]) => CHAIN_ORDER.map((c) => [c, ws.filter((w) => w.network === c)] as const).filter(([, l]) => l.length);
  const label = (w: Wal) => `${w.label || "Wallet"} · ${short(w.address)}`;

  return (
    <Modal title="Route funds across chains" sub="Keep your balance in USDC and top up gas wherever you launch" onClose={onClose}
      foot={<><button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn" disabled={!fromW || !toW || !(+amount > 0) || !!sameAsset}
          onClick={() => onReview({ title: `Route ${amount} ${fromUnit} to ${chainOf(toW!.network).name}`, path: "/bridge", network: fromW!.network,
            body: { from_wallet_id: from, to_wallet_id: to, from_asset: fromAsset, to_asset: toAsset, amount: +amount, slippage_pct: +slip } })}>
          Get quote<Icon name="arrowright" size={14} /></button></>}>
      {!all ? <div className="row" style={{ gap: 8 }}><Spinner />Loading wallets…</div> : err ? <Callout tone="err">{err}</Callout> : (
        <>
          <div className="grid g-form">
            <Field label="From wallet">
              <select value={from} onChange={(e) => setFrom(e.target.value)}>
                <option value="">Choose…</option>
                {byChain(managed).map(([c, ws]) => <optgroup key={c} label={chainOf(c).name}>{ws.map((w) => <option key={w.id} value={w.id}>{label(w)}</option>)}</optgroup>)}
              </select>
            </Field>
            <Field label="Pay with">
              <select value={fromAsset} onChange={(e) => setFromAsset(e.target.value as "stable" | "native")}>
                <option value="stable">{fromW ? stableOf(fromW.network) : "USDC"}</option>
                <option value="native">{fromW ? chainOf(fromW.network).native : "Network coin"}</option>
              </select>
            </Field>
            <Field label="To wallet">
              <select value={to} onChange={(e) => setTo(e.target.value)}>
                <option value="">Choose…</option>
                {byChain(managed).map(([c, ws]) => <optgroup key={c} label={chainOf(c).name}>{ws.map((w) => <option key={w.id} value={w.id}>{label(w)}</option>)}</optgroup>)}
              </select>
            </Field>
            <Field label="Receive as">
              <select value={toAsset} onChange={(e) => setToAsset(e.target.value as "native" | "stable")}>
                <option value="native">{toW ? `${chainOf(toW.network).native} (gas for launching)` : "Network coin (gas)"}</option>
                <option value="stable">{toW ? stableOf(toW.network) : "Stablecoin"}</option>
              </select>
            </Field>
          </div>
          {fromW && toW && (
            <div className="row small" style={{ gap: 8 }}>
              <ChainLabel network={fromW.network} size={16} /><Icon name="arrowright" size={13} /><ChainLabel network={toW.network} size={16} />
            </div>
          )}
          <div className="grid g-form">
            <Field label="Amount"><UnitInput unit={fromUnit || "—"} value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
            <Field label="Max slippage"><UnitInput unit="%" value={slip} onChange={(e) => setSlip(e.target.value)} /></Field>
          </div>
          {sameAsset && <Callout tone="warn">That's the same coin on the same chain. Use Disperse or Consolidate to move it between wallets.</Callout>}
          {toW?.network === "robinhood" && toAsset === "stable" && <Callout icon="info">Robinhood Chain uses USDG (Paxos) as its dollar stablecoin; there's no native USDC there.</Callout>}
          <Callout icon="info">
            Routes go through Relay. The next step shows the live quote: what should arrive, the minimum after slippage, Relay's fees
            and Helm's route fee. Both wallets are yours.
          </Callout>
        </>
      )}
    </Modal>
  );
}
