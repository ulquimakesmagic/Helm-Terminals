import { useEffect, useMemo, useState } from "react";
import { api, errMsg, fmt, short } from "../api";
import { chainOf, stableOf } from "../chains";
import { Callout, Field, Icon, Modal, Spinner, UnitInput } from "./ui";
import type { SpendRequest } from "./Distribute";

interface Wal { id: string; network: string; address: string; kind: string; label: string; balance_sol?: number | null; stable?: number | null }
type Quote = {
  max_send?: number; send_asset?: string; receive_asset?: string;
  quote?: { receive_estimate?: number; receive_minimum?: number; receive_symbol?: string; time_estimate_s?: number;
    fees_usd?: { network_gas?: number; relayer?: number; helm_route_fee?: number } } | null;
  warnings?: string[];
};

/**
 * Bridge / cross-chain swap through Relay (relay.link): send a chain's dollar stablecoin or its native coin from
 * one of your wallets and receive the destination chain's stablecoin or native coin in another of yours. Same-chain
 * swaps between the stablecoin and the native coin are allowed too (that's how EVM wallets convert without Jupiter).
 *
 * The dialog quotes live while you type, so fees and the receiving amount are visible before the review step;
 * the review (SpendDialog) re-quotes at execution time, which is the number you actually confirm.
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
  const [quote, setQuote] = useState<Quote | null>(null);
  const [quoting, setQuoting] = useState(false);
  const [quoteError, setQuoteError] = useState("");

  // Balances for MAX and the % readout: loaded once here so the dialog is self-contained even when the
  // wallets table behind it hasn't loaded balances yet.
  const [bals, setBals] = useState<Record<string, { native?: number | null; stable?: number | null }>>({});
  useEffect(() => {
    api<any[]>("/wallets?with_balances=true").then((ws) => setBals(Object.fromEntries(
      ws.map((w) => [w.id, { native: w.balance_sol, stable: w.stable }])))).catch(() => setBals({}));
  }, []);

  const from = managed.find((w) => w.id === fromId);
  const to = managed.find((w) => w.id === toId);
  const sameChain = !!from && !!to && from.network === to.network;
  const sameAsset = fromAsset === toAsset;
  const slip = +slippage;
  const amt = +amount;
  const inputsOk = !!from && !!to && amt > 0 && slip >= 0.1 && slip <= 5 && (!sameChain || !sameAsset);

  // Live quote while you type (debounced): the server prices the route, computes the fee reserve and
  // returns max_send, the largest amount that would pass its balance checks right now.
  useEffect(() => {
    if (!from || !to || !(amt > 0) || !(slip >= 0.1) || !(slip <= 5) || (sameChain && sameAsset)) {
      setQuote(null); setQuoteError(""); return;
    }
    let live = true;
    const body = { from_wallet_id: from.id, to_wallet_id: to.id, from_asset: fromAsset, to_asset: toAsset,
                   amount: amt, slippage_pct: slip };
    setQuoting(true);
    const t = setTimeout(() => {
      api<any>("/bridge", { body })
        .then((pre) => { if (live) { setQuote(pre.preflight ?? pre); setQuoteError(""); } })
        .catch((e) => { if (live) { setQuote(null); setQuoteError(errMsg(e)); } })
        .finally(() => { if (live) setQuoting(false); });
    }, 600);
    return () => { live = false; clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fromId, toId, fromAsset, toAsset, amount, slippage]);

  const assetSym = (w: Wal | undefined, asset: "stable" | "native") =>
    !w ? "" : asset === "native" ? chainOf(w.network).native : stableOf(w.network);
  const balOf = (w: Wal | undefined, asset: "stable" | "native") =>
    !w ? null : asset === "native" ? bals[w.id]?.native ?? null : bals[w.id]?.stable ?? null;
  // Native sends keep the fee reserve back; its exact size comes from the quote (max_send), with a static
  // headroom estimate before the first quote arrives. Stablecoin sends pay gas in the native coin, so the
  // full balance can go.
  const NATIVE_HEADROOM: Record<string, number> = { solana: 0.02, base: 0.001, bsc: 0.005, ethereum: 0.02, robinhood: 0.001 };
  const maxBalance = balOf(from, fromAsset);
  const maxSend = quote?.max_send != null && quote.send_asset === assetSym(from, fromAsset)
    ? quote.max_send
    : maxBalance == null ? null
      : fromAsset === "stable" ? maxBalance
        : Math.max(0, maxBalance - (NATIVE_HEADROOM[from?.network ?? ""] ?? 0.005));
  const pctOfMax = maxSend != null && maxSend > 0 && amt > 0 ? Math.min(999, (amt / maxSend) * 100) : null;
  const label = (w: Wal) => `${w.label || "Wallet"} · ${short(w.address)}`;
  const balanceNote = (w: Wal, asset: "stable" | "native") => {
    const b = balOf(w, asset);
    return b != null ? ` · ${fmt(b, asset === "native" ? 4 : 2)} ${assetSym(w, asset)}` : "";
  };
  const setShare = (pct: number) => { if (maxSend != null) setAmount(String(+(maxSend * pct / 100).toFixed(6))); };
  const ready = inputsOk && maxSend != null ? amt <= maxSend + 1e-12 : inputsOk;

  function review() {
    if (!from || !to) return;
    onReview({
      title: sameChain
        ? `Swap ${assetSym(from, fromAsset)} for ${assetSym(to, toAsset)} on ${chainOf(from.network).name}`
        : `Bridge ${assetSym(from, fromAsset)} to ${chainOf(to.network).name}`,
      path: "/bridge", network: from.network,
      body: { from_wallet_id: from.id, to_wallet_id: to.id, from_asset: fromAsset, to_asset: toAsset,
              amount: amt, slippage_pct: slip },
    });
  }

  const q = quote?.quote;
  const fees = q?.fees_usd;

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
      <Field label="Amount" extra={pctOfMax != null ? `${fmt(pctOfMax, 1)}% of what this wallet can send` : undefined}>
        <UnitInput unit={from ? assetSym(from, fromAsset) : ""} value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" placeholder="0.0" />
      </Field>
      <div className="chips" role="group" aria-label="Amount share">
        {([25, 50, 75] as const).map((p) => <button key={p} type="button" className="chip" disabled={maxSend == null}
          onClick={() => setShare(p)}>{p}%</button>)}
        <button type="button" className="chip" disabled={maxSend == null} onClick={() => setShare(100)}>Max</button>
        {maxSend != null && <span className="muted small" style={{ alignSelf: "center" }}>Max {fmt(maxSend, 6)} {from ? assetSym(from, fromAsset) : ""} · fees kept back</span>}
      </div>
      <Field label="Max slippage" hint="Maximum difference from the quoted output you accept.">
        <UnitInput unit="%" value={slippage} onChange={(e) => setSlippage(e.target.value)} inputMode="decimal" />
      </Field>

      {quoting && <p className="muted small row"><Spinner /> Getting a live quote…</p>}
      {quoteError && <Callout tone="err">{quoteError}</Callout>}
      {q && !quoting && (
        <div className="kv">
          <div className="k">Estimated receive</div><div className="v num">≈ {fmt(q.receive_estimate ?? 0, 6)} {q.receive_symbol}</div>
          <div className="k">At least</div><div className="v num">{fmt(q.receive_minimum ?? 0, 6)} {q.receive_symbol}</div>
          <div className="k">Relay + gas fee</div><div className="v num">${fmt((fees?.network_gas ?? 0) + (fees?.relayer ?? 0), 2)}</div>
          <div className="k">Helm route fee</div><div className="v">{fees?.helm_route_fee != null && fees.helm_route_fee > 0 ? `$${fmt(fees.helm_route_fee, 2)}` : "None"}</div>
          {q.time_estimate_s != null && <><div className="k">Arrives in</div><div className="v num">about {Math.max(1, Math.round(q.time_estimate_s))}s</div></>}
        </div>
      )}
      {(quote?.warnings?.length ?? 0) > 0 && !quoting && (
        <Callout tone="warn">{quote!.warnings!.map((w, i) => <span key={i} style={{ display: "block" }}>{w}</span>)}</Callout>
      )}
      <p className="muted small">The review step re-quotes at execution time — that is the number you confirm. Nothing is sent until then.</p>
    </Modal>
  );
}
