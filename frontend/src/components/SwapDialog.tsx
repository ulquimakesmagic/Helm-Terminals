/*
 * Solana swaps through Jupiter. This dialog only collects the trade (wallet, tokens, amount,
 * slippage) and shows the live estimate; confirmation goes through the standard SpendDialog
 * pipeline, so a fresh quote is taken again at execution time. Cross-chain conversion stays on
 * Relay routes (Route funds), which this dialog points to rather than imitating.
 */
import { useEffect, useState } from "react";
import { api, errMsg, fmt } from "../api";
import { Callout, Field, Icon, Modal, Spinner } from "./ui";

type TokenOpt = { mint: string; symbol: string; decimals?: number };

export type SwapRequest = { title: string; path: string; network: string; body: Record<string, unknown> };

export default function SwapDialog({ wallets, tokens, initialWallet, onClose, onSwap }: {
  wallets: { id: string; label: string; address: string; balance_sol?: number | null }[];
  tokens: TokenOpt[];                     // SOL + stablecoins + the user's launch tokens
  initialWallet?: string;
  onClose: () => void;
  onSwap: (req: SwapRequest) => void;     // hands off to SpendDialog
}) {
  const [walletId, setWalletId] = useState(initialWallet ?? wallets[0]?.id ?? "");
  const [from, setFrom] = useState(tokens[0]);
  const [to, setTo] = useState(tokens[1] ?? tokens[0]);
  const [amount, setAmount] = useState("");
  const [slippage, setSlippage] = useState("1");
  const [quote, setQuote] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const a = Number(amount);
    if (!walletId || !from || !to || !a || a <= 0 || from.mint === to.mint) { setQuote(null); return; }
    let live = true;
    setBusy(true);
    api<any>("/wallets/swap", { body: { wallet_id: walletId, input_mint: from.mint, output_mint: to.mint, amount: a, slippage_pct: Number(slippage) || 1 } })
      .then((pre) => { if (live) { setQuote(pre.preflight ?? pre); setError(""); } })
      .catch((e) => { if (live) { setQuote(null); setError(errMsg(e)); } })
      .finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [walletId, from?.mint, to?.mint, amount, slippage]);

  const wallet = wallets.find((w) => w.id === walletId);
  const maxSol = wallet && typeof wallet.balance_sol === "number" ? Math.max(0, wallet.balance_sol - 0.01) : null;
  const ready = !!walletId && !!from && !!to && from.mint !== to.mint && Number(amount) > 0 && !!quote;

  return (
    <Modal title="Swap" sub="Convert tokens on Solana through Jupiter's aggregator. Nothing is sent until you review and confirm." onClose={onClose}>
      <Field label="Wallet">
        <select value={walletId} onChange={(e) => setWalletId(e.target.value)}>
          {wallets.map((w) => <option key={w.id} value={w.id}>{w.label || "Wallet"} · {w.address.slice(0, 8)}…</option>)}
        </select>
      </Field>
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <Field label="From">
          <select value={from?.mint} onChange={(e) => setFrom(tokens.find((t) => t.mint === e.target.value)!)}>
            {tokens.map((t) => <option key={t.mint} value={t.mint}>{t.symbol}</option>)}
          </select>
        </Field>
        <Field label="To">
          <select value={to?.mint} onChange={(e) => setTo(tokens.find((t) => t.mint === e.target.value)!)}>
            {tokens.map((t) => <option key={t.mint} value={t.mint}>{t.symbol}</option>)}
          </select>
        </Field>
      </div>
      <Field label={`Amount${from?.symbol ? ` (${from.symbol})` : ""}`}>
        <div className="row" style={{ gap: 8 }}>
          <input className="mono" value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal"
            placeholder="0.0" style={{ flex: 1 }} />
          {from?.mint === "native" && maxSol != null &&
            <button className="btn sm ghost" onClick={() => setAmount(String(maxSol))}>MAX {fmt(maxSol, 3)}</button>}
        </div>
      </Field>
      <Field label="Slippage %" hint="Maximum difference from the quoted output you accept.">
        <input className="mono" value={slippage} onChange={(e) => setSlippage(e.target.value)} inputMode="decimal" style={{ maxWidth: 120 }} />
      </Field>

      {busy && <p className="muted small row"><Spinner /> Getting a quote…</p>}
      {error && <Callout tone="err">{error}</Callout>}
      {quote && !busy && (
        <div className="kv">
          <div className="k">Estimated output</div><div className="v num">{quote.estimated_output}</div>
          <div className="k">At least</div><div className="v num">{quote.output}</div>
          <div className="k">Route</div><div className="v">{quote.route}</div>
          <div className="k">Price impact</div><div className="v num">{quote.price_impact}</div>
        </div>
      )}

      <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn" disabled={!ready}
          onClick={() => onSwap({
            title: `Swap ${amount} ${from?.symbol ?? ""} for ${to?.symbol ?? ""}`,
            path: "/wallets/swap", network: "solana",
            body: { wallet_id: walletId, input_mint: from!.mint, output_mint: to!.mint, amount: Number(amount), slippage_pct: Number(slippage) || 1 },
          })}>
          <Icon name="check" size={14} />Review swap
        </button>
      </div>
      <p className="muted small" style={{ margin: 0 }}>
        Converting <b>between chains</b>? Use <b>Disperse / Consolidate / Route funds</b> in the
        selection bar above — cross-chain conversion runs on Relay, not Jupiter.
      </p>
    </Modal>
  );
}
