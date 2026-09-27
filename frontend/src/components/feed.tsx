import { Link } from "react-router-dom";
import { fmt, short } from "../api";
import { Icon, IconName, parseDate, relTime } from "./ui";

export interface ActivityRow { id: string; kind: string; ref_type: string | null; ref_id: string | null; payload: Record<string, unknown>; created_at: string }

type Meta = { icon: IconName; tone: string; label: string; group: string };
const KINDS: Record<string, Meta> = {
  "account.created": { icon: "user", tone: "violet", label: "Account created", group: "security" },
  "session.created": { icon: "key", tone: "", label: "Signed in", group: "security" },
  "session.revoked": { icon: "logout", tone: "", label: "Session revoked", group: "security" },
  "2fa.enabled": { icon: "shield", tone: "good", label: "Two-factor authentication turned on", group: "security" },
  "telegram.linked": { icon: "telegram", tone: "info", label: "Telegram linked", group: "security" },
  "telegram.unlinked": { icon: "telegram", tone: "", label: "Telegram unlinked", group: "security" },
  "referral.payout_set": { icon: "gift", tone: "info", label: "Referral payout address set", group: "security" },
  "profile.updated": { icon: "user", tone: "", label: "Profile updated", group: "security" },
  "wallets.created": { icon: "wallet", tone: "info", label: "Wallets created", group: "wallets" },
  "launch.created": { icon: "rocket", tone: "info", label: "Launch draft created", group: "launches" },
  "launch.imported": { icon: "download", tone: "violet", label: "Token imported", group: "launches" },
  "launch.archived": { icon: "archive", tone: "", label: "Launch archived", group: "launches" },
  "deploy.confirmed": { icon: "rocket", tone: "good", label: "Deployment confirmed", group: "transactions" },
  "trade_buy.confirmed": { icon: "trend", tone: "good", label: "Buy confirmed", group: "transactions" },
  "trade_sell.confirmed": { icon: "trend", tone: "warn", label: "Sell confirmed", group: "transactions" },
  "fund.confirmed": { icon: "arrowdown", tone: "good", label: "Wallet funding confirmed", group: "transactions" },
  "withdraw.confirmed": { icon: "arrowup", tone: "warn", label: "Withdrawal confirmed", group: "transactions" },
  "claim_fees.confirmed": { icon: "coins", tone: "good", label: "Creator fee claim confirmed", group: "transactions" },
  "burn.confirmed": { icon: "flame", tone: "bad", label: "Token burn confirmed", group: "transactions" },
  "send_tokens.confirmed": { icon: "send", tone: "warn", label: "Token send confirmed", group: "transactions" },
  "automation.scheduled": { icon: "calendar", tone: "info", label: "Scheduled plan created", group: "transactions" },
  "automation.order_confirmed": { icon: "checkcircle", tone: "good", label: "Scheduled order confirmed", group: "transactions" },
  "automation.order_failed": { icon: "alert", tone: "bad", label: "Scheduled plan stopped", group: "transactions" },
  "automation.sell_plan_exhausted": { icon: "checkcircle", tone: "", label: "Scheduled sells finished: nothing left to sell", group: "transactions" },
  "disperse.confirmed": { icon: "send", tone: "info", label: "Disperse sent", group: "transactions" },
  "consolidate.confirmed": { icon: "download", tone: "info", label: "Consolidate sent", group: "transactions" },
  "bridge.confirmed": { icon: "globe", tone: "info", label: "Cross-chain route sent", group: "transactions" },
};

// Automation events name their side in the payload; older events without one keep the neutral label.
const SIDED: Record<string, (s: string) => string> = {
  "automation.scheduled": (s) => `Scheduled ${s}s created`,
  "automation.order_confirmed": (s) => `Scheduled ${s} confirmed`,
  "automation.order_failed": (s) => `Scheduled ${s}s stopped`,
};

export function kindMeta(kind: string, payload?: Record<string, unknown>): Meta {
  const base = kindMeta0(kind);
  const side = payload?.side === "sell" || payload?.side === "buy" ? payload.side : null;
  if (SIDED[kind] && side) return { ...base, label: SIDED[kind](side), tone: kind === "automation.order_confirmed" && side === "sell" ? "warn" : base.tone };
  return base;
}

function kindMeta0(kind: string): Meta {
  return KINDS[kind] ?? { icon: "activity", tone: "", label: kind.replace(/[._]/g, " "), group: kind.endsWith(".confirmed") ? "transactions" : "other" };
}

function detail(a: ActivityRow): string {
  const p = a.payload ?? {};
  if (a.kind === "wallets.created" && p.count) return `${p.count} wallet${p.count === 1 ? "" : "s"} on ${p.network ?? "solana"}`;
  if (a.kind === "launch.created" && p.launchpad) return `on ${p.launchpad}`;
  if (a.kind === "launch.imported" && typeof p.mint === "string") return `mint ${short(p.mint)}`;
  if (a.kind === "automation.scheduled") {
    const side = p.side === "sell" ? "sells" : "buys";
    const size = p.side === "sell" ? (p.sell_percent ? ` · ${p.sell_percent}% each` : "") : typeof p.planned_trade_sol === "number" ? ` · ${fmt(p.planned_trade_sol, 6)} ${p.coin ?? "SOL"}` : "";
    return `${String(p.orders ?? "")} ${side} · ${String(p.wallet_count ?? "")} wallets${size}`;
  }
  if (a.kind.startsWith("automation.order_") && p.order_number) return `Order ${p.order_number}`;
  return "";
}

export function FeedItem({ a }: { a: ActivityRow }) {
  const m = kindMeta(a.kind, a.payload);
  const d = detail(a);
  return (
    <div className="feed-item">
      <div className={`feed-ic ${m.tone}`} aria-hidden="true"><Icon name={m.icon} size={14} /></div>
      <div className="grow">
        <div style={{ fontWeight: 500 }}>{m.label}</div>
        <div className="muted small row" style={{ gap: 6 }}>
          {d && <span>{d}</span>}
          {a.ref_type === "launch" && a.ref_id && <Link to={`/launches/${a.ref_id}`}>Open launch</Link>}
          {a.ref_type === "automation" && <Link to="/automation">Open automation</Link>}
        </div>
      </div>
      <time className="feed-time" dateTime={parseDate(a.created_at).toISOString()} title={parseDate(a.created_at).toLocaleString()}>{relTime(a.created_at)}</time>
    </div>
  );
}
