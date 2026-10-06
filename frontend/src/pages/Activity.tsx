import { useCallback, useEffect, useMemo, useState } from "react";
import { api, errMsg } from "../api";
import { ActivityRow, FeedItem, groupRepeats, kindMeta } from "../components/feed";
import { Card, Empty, Icon, Loading, PageHeader, Seg, parseDate } from "../components/ui";

type G = "all" | "transactions" | "launches" | "wallets" | "security";
const GROUPS: [G, string][] = [["all", "All"], ["transactions", "Transactions"], ["launches", "Launches"], ["wallets", "Wallets"], ["security", "Security"]];

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
  const [busy, setBusy] = useState(false);
  const [g, setG] = useState<G>("all");

  const load = useCallback(() => {
    setBusy(true);
    api<ActivityRow[]>("/activity?limit=300")
      .then((r) => { setRows(r); setError(""); })
      .catch((e) => setError(errMsg(e)))
      .finally(() => setBusy(false));
  }, []);
  useEffect(() => { load(); }, [load]);

  const count = (k: G) => (rows ?? []).filter((r) => k === "all" || kindMeta(r.kind).group === k).length;
  const days = useMemo(() => {
    const out: [string, ActivityRow[]][] = [];
    for (const r of (rows ?? []).filter((r) => g === "all" || kindMeta(r.kind).group === g)) {
      const label = dayLabel(parseDate(r.created_at));
      const last = out[out.length - 1];
      if (last && last[0] === label) last[1].push(r); else out.push([label, [r]]);
    }
    return out.map(([day, items]) => [day, groupRepeats(items), items.length] as const);
  }, [rows, g]);

  return (
    <>
      <PageHeader title="Activity" sub="An audit trail of your account, newest first. Repeated entries are grouped; expand one to see each time." />
      <Card flush title="Account log" icon="activity" sub={rows ? `${rows.length} entries` : undefined} actions={<>
        <Seg className="wrap-sm" value={g} onChange={setG} options={GROUPS.map(([k, l]) => [k, <>{l}{rows && <span className="muted num"> {count(k)}</span>}</>])} />
        <button className="btn icon sm secondary" onClick={load} disabled={busy} aria-label="Reload activity"><Icon name="refresh" size={13} /></button>
      </>}>
        {error && !rows ? (
          <div className="load-error" role="alert">
            <Icon name="xcircle" size={16} />
            <span className="grow"><b>Activity couldn't be loaded.</b> {error}</span>
            <button className="btn sm secondary" onClick={load} disabled={busy}><Icon name="refresh" size={13} />Try again</button>
          </div>
        ) : !rows ? <Loading rows={6} /> : days.length === 0 ? (
          <Empty icon="activity" title={g === "all" ? "No activity yet" : `No ${GROUPS.find((x) => x[0] === g)?.[1].toLowerCase()} activity yet`}
            text={g === "all" ? "Sign-ins, wallet changes, launches and transactions are recorded here as they happen." : "Nothing in this category yet. Try All."} />
        ) : (
          <div className="feed">
            {error && <div className="load-error" role="alert"><Icon name="alert" size={16} /><span className="grow">Latest refresh failed: {error}. Showing what was loaded before.</span></div>}
            {days.map(([day, groups, n]) => (
              <section key={day} aria-label={day}>
                <h3 className="feed-day">{day} <span className="muted">· {n}</span></h3>
                {groups.map((grp) => <FeedItem key={grp.head.id} a={grp.head} items={grp.items} timeStyle="clock" />)}
              </section>
            ))}
          </div>
        )}
      </Card>
    </>
  );
}
