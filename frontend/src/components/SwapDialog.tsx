/*
 * Same-chain swaps from any managed wallet. Solana routes through Jupiter's aggregator; EVM chains
 * (native coin, dollar stablecoin, or any token address) route through Relay's same-chain routing.
 * The dialog only collects the trade and shows the live estimate; confirmation goes through the
 * standard SpendDialog pipeline, which re-quotes at execution time. Cross-chain conversion is the
 * separate Bridge dialog, which this dialog points to rather than imitating.
 */
import { useEffect, useMemo, useState } from "react";
import { api, errMsg, fmt } from "../api";
import { chainOf, stableOf } from "../chains";
import { isEvmAddr, isSolanaAddr } from "../validate";
import { Callout, Field, Icon, Modal, Spinner } from "./ui";

export type SwapRequest = { title: string; path: string; network: string; body: Record<string, unknown> };
type TokenOpt = { mint: string; symbol: string; network?: string };
type WalletOpt = { id: string; network: string; label?: string; address: string; balance_sol?: number | null };

const CUSTOM = "__custom__";
// Native coin kept back so the swap's own transactions can still pay gas (client-side estimate only;
// the server's preview has the exact fee reserve).
const NATIVE_HEADROOM: Record<string, number> = { solana: 0.01, base: 0.001, bsc: 0.005, ethereum: 0.02, robinhood: 0.001 };

export default function SwapDialog({ wallets, tokens, initialWallet, onClose, onSwap }: {
  wallets: WalletOpt[];
  tokens: TokenOpt[];                     // native + stablecoins + the user's launch tokens (per network)
  initialWallet?: string;
  onClose: () => void;
  onSwap: (req: SwapRequest) => void;     // hands off to SpendDialog
}) {
  const [walletId, setWalletId] = useState(initialWallet && wallets.some((w) => w.id === initialWallet) ? initialWallet : wallets[0]?.id ?? "");
  const wallet = wallets.find((w) => w.id === walletId);
  const network = wallet?.network ?? "solana";
  const evm = network !== "solana";
  const options = useMemo(() => {
    const list: TokenOpt[] = evm
      ? [{ mint: "native", symbol: chainOf(network).native, network },
         { mint: "stable", symbol: stableOf(network), network }]
      : [{ mint: "native", symbol: "SOL", network }];
    for (const t of tokens) {
      if ((t.network ?? "solana") !== network || t.mint === "native" || (evm && t.mint === "stable")) continue;
      if (!list.some((o) => o.mint === t.mint)) list.push({ ...t, network });
    }
    return list;
  }, [network, tokens, evm]);

  const [fromMint, setFromMint] = useState<string>(options[0]?.mint ?? "native");
  const [toMint, setToMint] = useState<string>(options[1]?.mint ?? options[0]?.mint ?? "native");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [amount, setAmount] = useState("");
  const [slippage, setSlippage] = useState("1");
  const [quote, setQuote] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setFromMint(options[0]?.mint ?? "native");
    setToMint(options[1]?.mint ?? options[0]?.mint ?? "native");
    setCustomFrom(""); setCustomTo("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [network]);

  const addrOk = (a: string) => (evm ? isEvmAddr(a) : isSolanaAddr(a));
  const resolve = (sel: string, custom: string): { mint: string; symbol: string } | null => {
    if (sel === CUSTOM) return addrOk(custom.trim()) ? { mint: custom.trim(), symbol: `${custom.trim().slice(0, 6)}…` } : null;
    const opt = options.find((o) => o.mint === sel);
    return opt ? { mint: opt.mint, symbol: opt.symbol } : null;
  };
  const from = resolve(fromMint, customFrom);
  const to = resolve(toMint, customTo);

  useEffect(() => {
    const a = Number(amount);
    if (!walletId || !from || !to || !a || a <= 0 || from.mint.toLowerCase() === to.mint.toLowerCase()) { setQuote(null); return; }
    let live = true;
    setBusy(true);
    api<any>("/wallets/swap", { body: { wallet_id: walletId, input_mint: from.mint, output_mint: to.mint, amount: a, slippage_pct: Number(slippage) || 1 } })
      .then((pre) => { if (live) { setQuote(pre.preflight ?? pre); setError(""); } })
      .catch((e) => { if (live) { setQuote(null); setError(errMsg(e)); } })
      .finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [walletId, from?.mint, to?.mint, amount, slippage]);

  const maxNative = wallet && typeof wallet.balance_sol === "number"
    ? Math.max(0, wallet.balance_sol - (NATIVE_HEADROOM[network] ?? 0.005)) : null;
  const ready = !!walletId && !!from && !!to && from.mint.toLowerCase() !== to.mint.toLowerCase()
    && Number(amount) > 0 && !!quote;
  const q = quote?.quote;
  const fees = q?.fees_usd;

  return (
    <Modal title="Swap" sub={`Convert on ${chainOf(network).name} — ${evm ? "through Relay's routing" : "through Jupiter's aggregator"}. Nothing is sent until you review and confirm.`} onClose={onClose}>
      <Field label="Wallet">
        <select value={walletId} onChange={(e) => setWalletId(e.target.value)}>
          {wallets.map((w) => <option key={w.id} value={w.id}>{w.label || "Wallet"} · {chainOf(w.network).name} · {w.address.slice(0, 8)}…</option>)}
        </select>
      </Field>
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <Field label="From">
          <select value={fromMint} onChange={(e) => setFromMint(e.target.value)}>
            {options.map((t) => <option key={t.mint} value={t.mint}>{t.symbol}</option>)}
            {evm && <option value={CUSTOM}>Custom token address…</option>}
          </select>
        </Field>
        <Field label="To">
          <select value={toMint} onChange={(e) => setToMint(e.target.value)}>
            {options.map((t) => <option key={t.mint} value={t.mint}>{t.symbol}</option>)}
            {evm && <option value={CUSTOM}>Custom token address…</option>}
          </select>
        </Field>
      </div>
      {fromMint === CUSTOM && (
        <Field label="From token address"><input className="mono" value={customFrom} onChange={(e) => setCustomFrom(e.target.value)}
          placeholder={evm ? "0x… token contract" : "Mint address"} autoComplete="off" spellCheck={false} /></Field>
      )}
      {toMint === CUSTOM && (
        <Field label="To token address"><input className="mono" value={customTo} onChange={(e) => setCustomTo(e.target.value)}
          placeholder={evm ? "0x… token contract" : "Mint address"} autoComplete="off" spellCheck={false} /></Field>
      )}
      <Field label={`Amount${from ? ` (${from.symbol})` : ""}`}>
        <div className="row" style={{ gap: 8 }}>
          <input className="mono" value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal"
            placeholder="0.0" style={{ flex: 1 }} />
          {from?.mint === "native" && maxNative != null &&
            <button className="btn sm ghost" onClick={() => setAmount(String(maxNative))}>MAX {fmt(maxNative, 3)}</button>}
        </div>
      </Field>
      <Field label="Slippage %" hint="Maximum difference from the quoted output you accept.">
        <input className="mono" value={slippage} onChange={(e) => setSlippage(e.target.value)} inputMode="decimal" style={{ maxWidth: 120 }} />
      </Field>

      {busy && <p className="muted small row"><Spinner /> Getting a quote…</p>}
      {error && <Callout tone="err">{error}</Callout>}
      {quote && !busy && (fees ? (
        <div className="kv">
          <div className="k">Estimated receive</div><div className="v num">≈ {fmt(q.receive_estimate ?? 0, 6)} {q.receive_symbol}</div>
          <div className="k">At least</div><div className="v num">{fmt(q.receive_minimum ?? 0, 6)} {q.receive_symbol}</div>
          <div className="k">Relay + gas fee</div><div className="v num">${fmt((fees.network_gas ?? 0) + (fees.relayer ?? 0), 2)}</div>
          <div className="k">Helm route fee</div><div className="v">{fees.helm_route_fee ? `$${fmt(fees.helm_route_fee, 2)}` : "None"}</div>
        </div>
      ) : (
        <div className="kv">
          <div className="k">Estimated output</div><div className="v num">{quote.estimated_output}</div>
          <div className="k">At least</div><div className="v num">{quote.output}</div>
          <div className="k">Route</div><div className="v">{quote.route}</div>
          <div className="k">Price impact</div><div className="v num">{quote.price_impact}</div>
        </div>
      ))}

      <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn" disabled={!ready}
          onClick={() => onSwap({
            title: `Swap ${amount} ${from?.symbol ?? ""} for ${to?.symbol ?? ""}`,
            path: "/wallets/swap", network,
            body: { wallet_id: walletId, input_mint: from!.mint, output_mint: to!.mint, amount: Number(amount), slippage_pct: Number(slippage) || 1 },
          })}>
          <Icon name="check" size={14} />Review swap
        </button>
      </div>
      <p className="muted small" style={{ margin: 0 }}>
        Converting <b>between chains</b>, or into a coin on another chain? Use the <b>Bridge</b> button in the
        selection bar — cross-chain conversion runs on Relay, not Jupiter.
      </p>
    </Modal>
  );
}
