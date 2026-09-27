import { useEffect, useMemo, useState } from "react";
import { api, errMsg } from "../api";
import { ActivityRow, FeedItem, kindMeta } from "../components/feed";
import { Callout, Card, Empty, Loading, PageHeader, Seg, parseDate } from "../components/ui";

type G = "all" | "transactions" | "launches" | "wallets" | "security";

function dayLabel(d: Date) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const x = new Date(d); x.setHours(0, 0, 0, 0);
  const diff = Math.round((today.getTime() - x.getTime()) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: diff > 300 ? "numeric" : undefined });
}

export default function Activity() {
  const [rows, setRows] = useState<ActivityRow[] | null>(null);
  const [error, setError] = useState("");
  const [g, setG] = useState<G>("all");
  useEffect(() => { api<ActivityRow[]>("/activity?limit=300").then(setRows).catch((e) => { setError(errMsg(e)); setRows([]); }); }, []);

  const count = (k: G) => (rows ?? []).filter((r) => k === "all" || kindMeta(r.kind).group === k).length;
  const groups = useMemo(() => {
    const out: [string, ActivityRow[]][] = [];
    for (const r of (rows ?? []).filter((r) => g === "all" || kindMeta(r.kind).group === g)) {
      const label = dayLabel(parseDate(r.created_at));
      const last = out[out.length - 1];
      if (last && last[0] === label) last[1].push(r); else out.push([label, [r]]);
    }
    return out;
  }, [rows, g]);

  return (
    <>
      <PageHeader title="Activity" sub="An audit trail of everything that happened on your account, newest first. Transactions link to their launch." />
      {error && <Callout tone="err">{error}</Callout>}
      <Card flush title="Account log" icon="activity" actions={
        <Seg value={g} onChange={setG} options={(["all", "transactions", "launches", "wallets", "security"] as G[]).map((k) => [k, <>{k === "all" ? "All" : k.charAt(0).toUpperCase() + k.slice(1)} <span className="muted num">{count(k)}</span></>])} />
      }>
        {!rows ? <Loading rows={6} /> : groups.length === 0 ? <Empty icon="activity" title="No activity here yet" text={g === "all" ? "Sign-ins, wallet changes, launches and transactions are recorded here as they happen." : "Nothing in this category yet. Try All."} /> : (
          <div className="feed">
            {groups.map(([day, items]) => (
              <div key={day}>
                <h3 className="feed-day">{day} <span className="faint">· {items.length}</span></h3>
                {items.map((a) => <FeedItem key={a.id} a={a} />)}
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  );
}
