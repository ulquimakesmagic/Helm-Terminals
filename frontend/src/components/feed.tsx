import { Link } from "react-router-dom";
import { short } from "../api";
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
};

export function kindMeta(kind: string): Meta {
  return KINDS[kind] ?? { icon: "activity", tone: "", label: kind.replace(/[._]/g, " "), group: kind.endsWith(".confirmed") ? "transactions" : "other" };
}

function detail(a: ActivityRow): string {
  const p = a.payload ?? {};
  if (a.kind === "wallets.created" && p.count) return `${p.count} wallet${p.count === 1 ? "" : "s"} on ${p.network ?? "solana"}`;
  if (a.kind === "launch.created" && p.launchpad) return `on ${p.launchpad}`;
  if (a.kind === "launch.imported" && typeof p.mint === "string") return `mint ${short(p.mint)}`;
  return "";
}

export function FeedItem({ a }: { a: ActivityRow }) {
  const m = kindMeta(a.kind);
  const d = detail(a);
  return (
    <div className="feed-item">
      <div className={`feed-ic ${m.tone}`}><Icon name={m.icon} size={15} /></div>
      <div className="grow">
        <div style={{ fontWeight: 500 }}>{m.label}</div>
        <div className="muted small row" style={{ gap: 6 }}>
          {d && <span>{d}</span>}
          {a.ref_type === "launch" && a.ref_id && <Link to={`/launches/${a.ref_id}`}>Open launch</Link>}
        </div>
      </div>
      <span className="muted small nowrap" title={parseDate(a.created_at).toLocaleString()}>{relTime(a.created_at)}</span>
    </div>
  );
}
