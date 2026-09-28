import { useEffect, useState } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import { api, errMsg } from "../api";
import { Callout, Card, Empty, Icon, Loading, PageHeader, StatusBadge } from "../components/ui";

/** Old /automation links. With ?launch= they go straight to that launch's Launchdesk automation tab; otherwise this
 * lists your plans by launch so every one is one click from where it's managed now. */
export default function AutomationMoved() {
  const [search] = useSearchParams();
  const launch = search.get("launch");
  const [plans, setPlans] = useState<any[] | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { if (!launch) api<any[]>("/automations").then(setPlans).catch((e) => setErr(errMsg(e))); }, [launch]);
  if (launch) return <Navigate to={`/launches/${encodeURIComponent(launch)}?tab=automation`} replace />;
  const byLaunch = new Map<string, any[]>();
  (plans ?? []).forEach((p) => byLaunch.set(p.launch_id, [...(byLaunch.get(p.launch_id) ?? []), p]));
  return (
    <>
      <PageHeader title="Automation moved" sub="Plans now live inside each launch's Launchdesk, next to that token's wallets and position." />
      <Callout tone="info" icon="info">Existing one-wallet plans keep running; older plans that traded several wallets at once are paused and need to be recreated per wallet. Open a launch below to pause, cancel or review its plans, or to create a new one.</Callout>
      {err ? <Callout tone="err">{err}</Callout> : !plans ? <Loading /> : byLaunch.size === 0 ? (
        <Empty icon="calendar" title="No plans yet" text="Open a live launch and use its Automation tab." action={<Link to="/launches" className="btn"><Icon name="rocket" size={15} />Launches</Link>} />
      ) : (
        <Card title="Your plans by launch" icon="calendar" flush>
          <div className="stack tight" style={{ padding: 14 }}>
            {[...byLaunch.entries()].map(([id, ps]) => (
              <div key={id} className="row between">
                <span><b>{ps[0].launch_name ?? "Launch"}</b> <span className="muted mono">${ps[0].launch_symbol}</span>
                  <span className="row small" style={{ gap: 6 }}>{ps.slice(0, 4).map((p) => <StatusBadge key={p.id} status={p.status} />)}</span></span>
                <Link to={`/launches/${id}?tab=automation`} className="btn sm secondary">Open Launchdesk<Icon name="arrowright" size={13} /></Link>
              </div>
            ))}
          </div>
        </Card>
      )}
    </>
  );
}
