import { ReactNode } from "react";
import { fmt, short } from "../api";
import { chainOf, isExtraChain, padName } from "../chains";

/**
 * Turns a server preflight into the few facts a person must check before confirming: the headline amount, the
 * numbers around it (wallets, fees, gas, timing) and what happens after they press confirm. Everything here is
 * read from the preflight itself; nothing is estimated in the browser. The full preflight is still shown below.
 */
export interface ReviewSummary {
  label: string;
  value: ReactNode;
  unit?: string;
  sub?: ReactNode;
  cells: [string, ReactNode][];
  next: string[];
  tone?: "danger";
}

const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const list = (v: unknown) => (Array.isArray(v) ? v : []);
const sum = (items: unknown[], key: string) => items.reduce((s: number, x: any) => s + (num(x?.[key]) ?? 0), 0);
const fee = (v: unknown) => (typeof v === "string" && v ? v.charAt(0).toUpperCase() + v.slice(1) : "None");

export function summarize(pre: any, network: string): ReviewSummary | null {
  const s = summarizeKind(pre, network);
  if (!s || !pre || pre.kind === "bridge" || (isExtraChain(pre.network) && s.label.startsWith("Send on"))) return s;
  // Decisions depend on where the money moves: the network and (for token actions) the exact token contract lead the
  // cells on every review, so they're never hidden in the full preview.
  const chain = chainOf(pre.network ?? network);
  const lead: [string, ReactNode][] = [["Network", chain.name]];
  if (typeof pre.mint === "string" && pre.mint) lead.push(["Token", <span className="mono" title={pre.mint}>{short(pre.mint)}</span>]);
  return { ...s, cells: [...lead, ...s.cells] };
}

function summarizeKind(pre: any, network: string): ReviewSummary | null {
  if (!pre) return null;
  const chain = chainOf(pre.network ?? network);
  const coin = typeof pre.coin === "string" ? pre.coin : chain.native;
  const gas = num(pre.max_gas_each) ?? num(pre.max_gas) ?? num(pre.network_fees_max);
  const gasCell: [string, ReactNode][] = gas !== null ? [[pre.max_gas_each != null ? "Max gas per tx" : "Max network fees", `${fmt(gas, 6)} ${pre.network_fee_coin ?? coin}`]] : [];

  if (isExtraChain(pre.network) && ["withdraw", "send_tokens", "ton_transfer", "external_transfer"].includes(pre.kind)) return extraTransfer(pre);

  switch (pre.kind) {
    case "deploy": {
      const devUsd = num(pre.dev_buy_usd);
      const cells: [string, ReactNode][] = [["Dev buy", `${fmt(num(pre.dev_buy_sol) ?? 0, 6)} ${coin}${devUsd != null && num(pre.dev_buy_sol) ? ` ≈ $${fmt(devUsd, 2)}` : ""}`]];
      const f = pre.funding && typeof pre.funding === "object" ? pre.funding : null;
      if (f) cells.unshift(["Funded from", `${f.send} on ${f.from_chain}`]);
      if (pre.launch_fee) cells.push(["Launch fee", String(pre.launch_fee)]);
      cells.push(...gasCell, ["Platform fee", fee(pre.platform_fee)]);
      return {
        label: "Create token", value: `$${pre.symbol ?? ""}`, sub: <>{pre.name} on {padName(pre.launchpad)} · {chain.name}</>, cells,
        next: [
          ...(f ? [`Helm routes ${f.send} from ${f.from} on ${f.from_chain} into ${coin} in your dev wallet through Relay (route fee: ${f.route_fee}) and waits for it to arrive. If the route doesn't complete, nothing is deployed. ${f.leftover}.`] : []),
          `Your dev wallet ${pre.dev_wallet ? short(pre.dev_wallet) : ""} signs the create transaction${num(pre.dev_buy_sol) ? " and the dev buy" : ""}.`,
          `Helm simulates it first, then sends it to ${chain.name}.`,
          "The launch turns live in its workspace once the chain confirms it. If it fails, nothing is retried automatically.",
        ],
      };
    }
    case "trade": {
      const wallets = list(pre.wallets).length;
      const amount = num(pre.amount) ?? 0;
      const buy = pre.side === "buy";
      const stagger = pre.mode === "stagger";
      return {
        label: buy ? "Buy" : "Sell",
        value: buy ? fmt(amount * wallets, 6) : `${fmt(amount, 2)}%`,
        unit: buy ? coin : "of holdings",
        sub: buy ? <>{fmt(amount, 6)} {coin} from each of {wallets} wallet{wallets === 1 ? "" : "s"}</> : <>{fmt(amount, 2)}% of each wallet's tokens, across {wallets} wallet{wallets === 1 ? "" : "s"}</>,
        cells: [
          ["Wallets", String(wallets)],
          ["Execution", stagger ? `One at a time, ${fmt(num(pre.stagger_ms) ?? 0, 0)} ms apart` : "All at once"],
          ["Max slippage", `${fmt(num(pre.slippage_pct) ?? 0, 2)}%`],
          ...gasCell,
          ["Platform fee", fee(pre.platform_fee)],
        ],
        next: [
          "Each wallet's order is simulated before it is sent; a failing simulation stops that order.",
          stagger ? "Wallets send one after another, so you can watch each result land." : "All wallets send together.",
          "Results appear in the launch's transactions. Failed orders are never retried automatically.",
        ],
      };
    }
    case "fund": {
      const to = list(pre.to);
      const total = num(pre.total_sol) ?? num(pre.total) ?? (sum(to, "sol") + sum(to, "amount"));
      return {
        label: "Fund wallets", value: fmt(total, 6), unit: coin, sub: <>From {pre.from ? short(pre.from) : "your wallet"} to {to.length} wallet{to.length === 1 ? "" : "s"}</>,
        cells: [["Recipients", String(to.length)], ...gasCell, ["Platform fee", fee(pre.platform_fee)]],
        next: ["One transfer per recipient, sent in order.", "All recipients are wallets on your account."],
      };
    }
    case "swap":
      return {
        label: "Swap", value: `${pre.input} → ${pre.output}`, unit: "", sub: <>Through Jupiter on Solana from {pre.wallet ? short(pre.wallet) : "the wallet"}</>,
        cells: [["Estimated out", pre.estimated_output ?? "—"], ["Route", pre.route ?? "—"], ["Price impact", pre.price_impact ?? "—"],
          ["Slippage", pre.slippage ?? "—"], ["Platform fee", fee(pre.platform_fee)]], tone: "danger",
        next: ["A fresh quote is taken when the swap executes; the final output is bounded by the slippage shown here.",
          "Jupiter builds the transaction; Helm checks its signatures and simulates it before sending."],
      };
    case "withdraw": {
      const from = list(pre.from);
      const total = sum(from, "sol") + sum(from, "amount");
      return {
        label: "Withdraw", value: fmt(total, 6), unit: coin, sub: <>From {from.length} wallet{from.length === 1 ? "" : "s"} to {pre.destination ? short(pre.destination) : "the destination"}</>,
        cells: [["Source wallets", String(from.length)], ...gasCell, ["Platform fee", fee(pre.platform_fee)]], tone: "danger",
        next: ["Funds leave your Helm wallets for the destination address.", "Transfers can't be reversed. Check the destination carefully."],
      };
    }
    case "claim_fees":
      return {
        label: "Claim creator fees", value: pre.wallet ? short(pre.wallet) : "—", sub: "Fees are paid to this creator wallet",
        cells: [["Platform fee", fee(pre.platform_fee)]],
        next: ["The launchpad's claim transaction is simulated, then sent.", "If there is nothing to claim, nothing is sent."],
      };
    case "burn":
      return {
        label: "Burn tokens", value: fmt(num(pre.tokens) ?? 0, 2), unit: "tokens", sub: <>{fmt(num(pre.percent) ?? 0, 2)}% of the balance in {pre.wallet ? short(pre.wallet) : "the wallet"}</>,
        cells: [["Irreversible", "Yes"], ...gasCell, ["Platform fee", fee(pre.platform_fee)]], tone: "danger",
        next: ["The tokens are destroyed permanently and the supply shrinks.", "This can't be undone."],
      };
    case "send_tokens":
      return {
        label: "Send tokens", value: fmt(num(pre.tokens) ?? 0, 2), unit: "tokens", sub: <>To {pre.to ? short(pre.to) : "the recipient"}</>,
        cells: [["From", pre.from ? short(pre.from) : "—"], ...gasCell, ["Platform fee", fee(pre.platform_fee)]], tone: "danger",
        next: ["The tokens leave your wallet for the recipient.", "Transfers can't be reversed."],
      };
    case "disperse": {
      const to = list(pre.to);
      return {
        label: "Disperse", value: fmt(num(pre.total) ?? 0, 6), unit: pre.asset, sub: <>From {pre.from?.label ?? "your wallet"} to {to.length} of your wallets · {pre.split === "custom" ? "custom amounts" : "even split"}</>,
        cells: [["Recipients", String(to.length)], ["Transactions", String(pre.transactions ?? to.length)], ...gasCell, ["Platform fee", fee(pre.platform_fee)]],
        next: [
          "Transfers are sent in order; if one fails, the rest stop so nothing is left half-known.",
          pre.transparency ? "The receiving wallets are attached to the launch, so its transparency panel counts this supply as yours." : "Only wallets on your account receive funds.",
        ],
      };
    }
    case "consolidate": {
      const from = list(pre.from);
      return {
        label: "Consolidate", value: fmt(num(pre.total) ?? 0, 6), unit: pre.asset, sub: <>From {from.length} wallet{from.length === 1 ? "" : "s"} into {pre.to?.label ?? "your wallet"}</>,
        cells: [["Source wallets", String(from.length)], ["Share of each", `${fmt(num(pre.percent) ?? 0, 2)}%`], ...gasCell, ["Platform fee", fee(pre.platform_fee)]],
        next: ["Each source wallet signs its own transfer. One failing doesn't stop the others.", "Wallets with nothing to move are skipped."],
      };
    }
    case "bridge": {
      const q = pre.quote ?? {};
      const f = q.fees_usd ?? {};
      return {
        label: `Route ${pre.from_chain} → ${pre.to_chain}`, value: fmt(num(pre.send) ?? 0, 6), unit: pre.send_asset,
        sub: q.receive_estimate != null ? <>Receive about {fmt(q.receive_estimate, 6)} {q.receive_symbol ?? pre.receive_asset}, at least {fmt(q.receive_minimum ?? 0, 6)}</> : <>Receive {pre.receive_asset} on {pre.to_chain}</>,
        cells: [
          ["Max slippage", `${fmt(num(pre.slippage_pct) ?? 0, 2)}%`],
          ["Relay + gas", q.fees_usd ? `$${fmt((f.network_gas ?? 0) + (f.relayer ?? 0), 2)}` : "In quote"],
          ["Helm route fee", fee(pre.platform_fee)],
          ["Arrives in", q.time_estimate_s ? `about ${Math.max(1, Math.round(q.time_estimate_s))}s` : "Varies"],
        ],
        next: [
          `Confirming fetches a fresh quote and sends from your ${pre.from_chain} wallet.`,
          `Relay fills on ${pre.to_chain}; if the fill fails, the amount is refunded to the source wallet.`,
        ],
      };
    }
    default:
      return null;
  }
}

export function ReviewHero({ s }: { s: ReviewSummary }) {
  return (
    <div className="review-hero" style={s.tone === "danger" ? { borderColor: "rgba(239,107,107,0.35)" } : undefined}>
      <div className="rh-main">
        <span className="rh-label">{s.label}</span>
        <span className="rh-value">{s.value}{s.unit && <small>{s.unit}</small>}</span>
        {s.sub && <span className="rh-sub">{s.sub}</span>}
      </div>
      {s.cells.length > 0 && (
        <div className="rh-cells">
          {s.cells.map(([k, v]) => <div key={k}><span>{k}</span><b>{v}</b></div>)}
        </div>
      )}
    </div>
  );
}

/** TRON / NEAR / Arc transfers: one wallet, one recipient, an exact decimal amount and chain-specific fee estimates. */
function extraTransfer(pre: any): ReviewSummary {
  const cells: [string, ReactNode][] = [["From", short(pre.from)]];
  const est: [string, string, string][] = [
    ["activation_fee_trx", "Account activation", "TRX"], ["bandwidth_fee_trx", "Bandwidth burn", "TRX"],
    ["energy_fee_trx", "Energy burn", "TRX"], ["fee_limit_trx", "Fee limit (max)", "TRX"],
    ["network_fee_trx_about", "Network fee (about)", "TRX"], ["storage_deposit_near", "Storage deposit", "NEAR"],
    ["gas_fee_near_max", "Gas (max)", "NEAR"], ["gas_fee_usdc_max", "Gas (max)", "USDC"], ["ton_attached", "TON attached", "TON"],
  ];
  for (const [k, label, unit] of est) if (typeof pre[k] === "string" && (pre[k] !== "0" || k === "network_fee_trx_about")) cells.push([label, `${pre[k]} ${unit}`]);
  cells.push(["Platform fee", fee(pre.platform_fee)]);
  const next = pre.external_wallet
    ? ["Your wallet shows this transfer for approval and signs it; Helm never holds the key.", "Transfers can't be reversed."]
    : ["Funds leave this wallet for the recipient. Transfers can't be reversed."];
  if (pre.fee_note) next.push(String(pre.fee_note));
  if (pre.recipient_note) next.push(String(pre.recipient_note));
  if (pre.finality) next.push(String(pre.finality));
  return {
    label: `Send on ${pre.chain ?? chainOf(pre.network).name}`, value: String(pre.amount), unit: pre.asset,
    sub: <>To <span className="mono">{short(pre.to)}</span>{pre.token ? <> · token <span className="mono">{short(pre.token)}</span></> : null}</>,
    cells, next, tone: "danger",
  };
}
