import { Link } from "react-router-dom";
import { fmt, short } from "../api";
import { chainOf, padName } from "../chains";
import { Icon, IconName, parseDate, relTime } from "./ui";

export interface ActivityRow { id: string; kind: string; ref_type: string | null; ref_id: string | null; payload: Record<string, unknown>; created_at: string }

type Meta = { icon: IconName; tone: string; label: string; group: string; routine?: boolean };

/*
 * `*.confirmed` events are logged when you approve a preview and Helm starts the job, not when the chain confirms
 * it. They're named for what you did ("Buy order sent"); the on-chain result lives on the launch's transactions.
 */
const KINDS: Record<string, Meta> = {
  "account.created": { icon: "user", tone: "violet", label: "Account created", group: "security" },
  "session.created": { icon: "key", tone: "", label: "Signed in", group: "security", routine: true },
  "session.revoked": { icon: "logout", tone: "", label: "Session revoked", group: "security" },
  "2fa.enabled": { icon: "shield", tone: "good", label: "Two-factor authentication turned on", group: "security" },
  "telegram.linked": { icon: "telegram", tone: "info", label: "Telegram linked", group: "security" },
  "telegram.unlinked": { icon: "telegram", tone: "", label: "Telegram unlinked", group: "security" },
  "referral.payout_set": { icon: "gift", tone: "info", label: "Referral payout address set", group: "security" },
  "profile.updated": { icon: "user", tone: "", label: "Profile updated", group: "security", routine: true },
  "wallets.created": { icon: "wallet", tone: "info", label: "Wallets created", group: "wallets" },
  "wallets.imported": { icon: "download", tone: "info", label: "Wallet imported", group: "wallets" },
  "wallets.main_created": { icon: "wallet", tone: "good", label: "Main wallet set up", group: "wallets" },
  "launch.created": { icon: "rocket", tone: "info", label: "Launch draft created", group: "launches" },
  "launch.imported": { icon: "download", tone: "violet", label: "Token imported", group: "launches" },
  "launch.deleted": { icon: "trash", tone: "", label: "Draft deleted", group: "launches" },
  "launch.archived": { icon: "archive", tone: "", label: "Launch archived", group: "launches" },
  "deploy.confirmed": { icon: "rocket", tone: "info", label: "Deployment started", group: "transactions" },
  "trade_buy.confirmed": { icon: "trend", tone: "info", label: "Buy order sent", group: "transactions" },
  "trade_sell.confirmed": { icon: "trend", tone: "info", label: "Sell order sent", group: "transactions" },
  "fund.confirmed": { icon: "arrowdown", tone: "info", label: "Wallet funding sent", group: "transactions" },
  "withdraw.confirmed": { icon: "arrowup", tone: "warn", label: "Withdrawal sent", group: "transactions" },
  "claim_fees.confirmed": { icon: "coins", tone: "info", label: "Creator fee claim sent", group: "transactions" },
  "burn.confirmed": { icon: "flame", tone: "warn", label: "Token burn sent", group: "transactions" },
  "send_tokens.confirmed": { icon: "send", tone: "warn", label: "Token transfer sent", group: "transactions" },
  "automation.scheduled": { icon: "calendar", tone: "info", label: "Scheduled plan created", group: "transactions" },
  "automation.order_confirmed": { icon: "checkcircle", tone: "good", label: "Scheduled order confirmed", group: "transactions" },
  "automation.order_failed": { icon: "alert", tone: "bad", label: "Scheduled plan stopped", group: "transactions" },
  "automation.sell_plan_exhausted": { icon: "checkcircle", tone: "", label: "Scheduled sells finished: nothing left to sell", group: "transactions" },
  "automation.paused": { icon: "calendar", tone: "warn", label: "Plan paused", group: "transactions" },
  "automation.resumed": { icon: "calendar", tone: "info", label: "Plan resumed", group: "transactions" },
  "automation.cancelled": { icon: "calendar", tone: "", label: "Plan cancelled", group: "transactions" },
  "automation.edited": { icon: "edit", tone: "", label: "Plan tightened", group: "transactions" },
  "automation.expired": { icon: "calendar", tone: "", label: "Plan expired", group: "transactions" },
  "allocation.updated": { icon: "pie", tone: "info", label: "Allocation plan updated", group: "launches" },
  "supply.dispersed": { icon: "send", tone: "info", label: "Supply dispersed to your wallets", group: "transactions" },
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
  if (SIDED[kind] && side) return { ...base, label: SIDED[kind](side) };
  return base;
}

function kindMeta0(kind: string): Meta {
  if (KINDS[kind]) return KINDS[kind];
  const words = kind.replace(/\.confirmed$/, " sent").replace(/[._]/g, " ").trim();
  return { icon: "activity", tone: "", label: words.charAt(0).toUpperCase() + words.slice(1), group: kind.endsWith(".confirmed") ? "transactions" : "other" };
}

function detail(a: ActivityRow): string {
  const p = a.payload ?? {};
  if (a.kind === "wallets.created" && p.count) return `${p.count} wallet${p.count === 1 ? "" : "s"} on ${chainOf(String(p.network ?? "solana")).name}`;
  if (a.kind === "launch.created" && p.cloned_from && typeof p.cloned_from === "object") {
    const c = p.cloned_from as { network?: string; address?: string };
    return `copied from ${chainOf(String(c.network ?? "solana")).name} token ${short(String(c.address ?? ""))}${p.launchpad ? ` · on ${padName(String(p.launchpad))}` : ""}`;
  }
  if (a.kind === "launch.created" && p.launchpad) return `on ${padName(String(p.launchpad))}`;
  if (a.kind === "wallets.imported" && typeof p.address === "string") return `${short(p.address)} on ${p.count && Number(p.count) > 1 ? `${p.count} chains` : chainOf(String(p.network ?? "solana")).name}`;
  if (a.kind === "launch.deleted" && p.name) return String(p.name);
  if (a.kind === "launch.imported" && typeof p.mint === "string") return `token ${short(p.mint)}`;
  if (a.kind === "automation.scheduled") {
    const side = p.side === "sell" ? "sells" : "buys";
    const size = p.side === "sell" ? (p.sell_percent ? ` · ${p.sell_percent}% each` : "") : typeof p.planned_trade_sol === "number" ? ` · ${fmt(p.planned_trade_sol, 6)} ${p.coin ?? "SOL"}` : "";
    return `${String(p.orders ?? "")} ${side} · ${String(p.wallet_count ?? "")} wallet${p.wallet_count === 1 ? "" : "s"}${size}`;
  }
  if (a.kind.startsWith("automation.order_") && p.order_number) return `Order ${p.order_number}${p.status ? ` ended ${p.status}` : ""}`;
  if (a.kind.endsWith(".confirmed") && a.ref_type === "job") return "You approved the preview · on-chain result in the launch's transactions";
  return "";
}

const clock = (s: string) => parseDate(s).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/** Collapse consecutive entries that say the same thing (e.g. repeated sign-ins) into one row with a count. */
export interface FeedGroup { head: ActivityRow; items: ActivityRow[] }
export function groupRepeats(rows: ActivityRow[]): FeedGroup[] {
  const out: FeedGroup[] = [];
  for (const r of rows) {
    const last = out[out.length - 1];
    // A launch or plan reference is part of what an entry says; a session or job id isn't.
    const sameRef = !["launch", "automation"].includes(r.ref_type ?? "") || last?.head.ref_id === r.ref_id;
    if (last && last.head.kind === r.kind && detail(last.head) === detail(r) && sameRef) last.items.push(r);
    else out.push({ head: r, items: [r] });
  }
  return out;
}

/** Routine account events (sign-ins, profile edits) are left out of compact summaries like the dashboard's. */
export const isRoutine = (r: ActivityRow) => !!kindMeta0(r.kind).routine;

export function FeedItem({ a, items, timeStyle = "relative" }: { a: ActivityRow; items?: ActivityRow[]; timeStyle?: "relative" | "clock" }) {
  const m = kindMeta(a.kind, a.payload);
  const d = detail(a);
  const n = items?.length ?? 1;
  const when = (x: ActivityRow) => timeStyle === "clock" ? clock(x.created_at) : relTime(x.created_at);
  const oldest = items?.[items.length - 1];
  const time = (
    <time className="feed-time" dateTime={parseDate(a.created_at).toISOString()} title={parseDate(a.created_at).toLocaleString()}>
      {n > 1 && oldest ? `${when(oldest)} – ${when(a)}` : when(a)}
    </time>
  );
  const links = <>
    {a.ref_type === "launch" && a.ref_id && <Link className="feed-action" to={`/launches/${a.ref_id}`}>Open launch</Link>}
    {a.ref_type === "automation" && (a.payload as any)?.launch_id && <Link className="feed-action" to={`/launches/${(a.payload as any).launch_id}?tab=tx`}>View historical transactions</Link>}
  </>;
  const body = (linked = true) => (
    <div className="grow">
      <div className="row" style={{ gap: 8 }}>
        <span style={{ fontWeight: 550 }}>{m.label}</span>
        {n > 1 && <span className="feed-count" aria-label={`${n} times`}>×{n}</span>}
      </div>
      {(d || a.ref_type === "launch" || a.ref_type === "automation") && (
        <div className="feed-sub">
          {d && <span>{d}</span>}
          {linked && links}
        </div>
      )}
    </div>
  );
  const icon = <div className={`feed-ic ${m.tone}`} aria-hidden="true"><Icon name={m.icon} size={14} /></div>;
  if (n > 1 && items) {
    return (
      <details className="feed-more">
        <summary className="feed-item" style={{ alignItems: "flex-start" }}>
          {icon}{body(false)}
          <span className="row nowrap" style={{ gap: 6 }}>{time}<span className="feed-toggle" aria-hidden="true">›</span></span>
        </summary>
        <div className="feed-nested">
          <div>{links}</div>
          {items.map((x) => <div key={x.id}><span>{m.label}</span><span className="mono">{parseDate(x.created_at).toLocaleString()}</span></div>)}
        </div>
      </details>
    );
  }
  return <div className="feed-item">{icon}{body()}{time}</div>;
}
