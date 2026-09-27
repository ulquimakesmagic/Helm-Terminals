import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errMsg, fmt, usd } from "../api";
import AreaChart from "../components/Chart";
import ReferralCard from "../components/Referrals";
import RouteFunds from "../components/RouteFunds";
import SpendDialog from "../components/SpendDialog";
import type { SpendRequest } from "../components/Distribute";
import { CHAIN_ORDER, CHAINS, Pad } from "../chains";
import { coverage } from "../components/Launchpads";
import { ActivityRow, FeedItem } from "../components/feed";
import { Callout, Card, Empty, Icon, Loading, Seg, Skeleton, Stat, StatusBadge, ChainDot } from "../components/ui";
import { useSession } from "../session";

type Period = "all" | "1y" | "1m" | "7d" | "1d";
const PERIODS: [Period, string][] = [["1d", "24H"], ["7d", "7D"], ["1m", "30D"], ["1y", "1Y"], ["all", "All"]];

export default function Dashboard() {
  const { me, tg } = useSession();
  const [period, setPeriod] = useState<Period>("all");
  const [data, setData] = useState<any>(null);
  const [wallets, setWallets] = useState<any[] | null>(null);
  const [launches, setLaunches] = useState<any[] | null>(null);
  const [feed, setFeed] = useState<ActivityRow[] | null>(null);
  const [automations, setAutomations] = useState<any[] | null>(null);
  const [pads, setPads] = useState<Pad[]>([]);
  const [folio, setFolio] = useState<any>(null);
  const [folioErr, setFolioErr] = useState("");
  const [routing, setRouting] = useState(false);
  const [dialog, setDialog] = useState<SpendRequest | null>(null);
  const [error, setError] = useState("");

  const loadFolio = (fresh = false) => api(`/portfolio${fresh ? "?fresh=true" : ""}`)
    .then((p) => { setFolio(p); setFolioErr(""); }).catch((e) => setFolioErr(errMsg(e)));

  const load = () => api(`/dashboard?period=${period}`).then((d) => { setData(d); setError(""); }).catch((e) => setError(errMsg(e)));
  useEffect(() => { load(); }, [period]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    api<any[]>("/wallets").then(setWallets).catch(() => setWallets([]));
    api<any[]>("/launches").then(setLaunches).catch(() => setLaunches([]));
    api<Pad[]>("/launchpads").then(setPads).catch(() => undefined);
    api<ActivityRow[]>("/activity?limit=7").then(setFeed).catch(() => setFeed([]));
    api<any[]>("/automations").then(setAutomations).catch(() => setAutomations([]));
    loadFolio();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const managed = wallets?.filter((w) => w.kind === "managed") ?? [];
  const live = launches?.filter((l) => l.status === "live") ?? [];
  const activePlans = automations?.filter((a) => ["scheduled", "executing", "paused"].includes(a.status)) ?? [];

  const steps: [boolean, string, string, string][] = [
    [managed.length > 0, "Create your first wallets", "/wallets", "Wallets"],
    [!!me?.totp_enabled, "Turn on two-factor authentication", "/settings", "Settings"],
    [(launches?.length ?? 0) > 0, "Create or import a launch", "/launches", "Launches"],
    [!!tg?.linked, "Link the Telegram bot", "/settings", "Settings"],
  ];
  const done = steps.filter((s) => s[0]).length;

  return (
    <>
      <section className="dash-hero">
        <div className="dash-hero-copy">
          <div className="eyebrow"><span className="dot" />MULTICHAIN CONTROL ROOM</div>
          <h1>Your launch desk,<br /><span className="grad-text">at a glance.</span></h1>
          <p>Keep your balance in USDC, route it to whichever chain you launch on, and track every chain from one workspace.</p>
          <div className="row dash-hero-actions">
            <Link to="/launches" className="btn"><Icon name="plus" size={15} />New launch</Link>
            <button className="btn secondary" onClick={() => setRouting(true)}><Icon name="globe" size={15} />Route USDC to a chain</button>
            <Link to="/automation" className="btn secondary"><Icon name="calendar" size={15} />Automation</Link>
          </div>
        </div>
        <div className="dash-orbit" aria-hidden="true">
          <div className="orbit-ring ring-one" /><div className="orbit-ring ring-two" />
          <span className="orbit-core"><Icon name="globe" size={28} /></span>
          <span className="orbit-node node-a"><Icon name="rocket" size={14} /></span>
          <span className="orbit-node node-b"><Icon name="wallet" size={14} /></span>
          <span className="orbit-node node-c"><Icon name="activity" size={14} /></span>
        </div>
        <div className="dash-hero-footer"><span><strong>{live.length}</strong> live launches</span><span><strong>{activePlans.length}</strong> active plans</span><span><strong>{wallets?.length ?? "—"}</strong> wallets</span></div>
      </section>
      {error && <Callout tone="err">{error}</Callout>}

      <div className="grid g-stats">
        {folio ? (
          <Stat accent label="Portfolio · all chains" icon="coins" value={usd(folio.totals.usd)}
            hint={folio.priced ? `coins and stablecoins in ${folio.wallets} managed wallets` : "coin prices unavailable; stablecoins only"} />
        ) : <div className="card stat"><Skeleton h={12} w={90} /><Skeleton h={26} w={140} /></div>}
        <Stat label="Dollar balance" icon="shield" value={folio ? usd(folio.totals.stable_usd) : "–"} hint="USDC, and USDG on Robinhood Chain" />
        <Stat label="Launches" icon="rocket" value={data ? data.total_launches : "–"} hint={`${live.length} live now`} />
        <Stat label="Managed wallets" icon="wallet" value={wallets ? managed.length : "–"} hint={`${(wallets?.length ?? 0) - managed.length} watch-only`} />
        <Stat label="Automation plans" icon="calendar" value={automations ? activePlans.length : "–"} hint={`${automations?.filter((a) => a.status === "completed").length ?? 0} completed`} />
      </div>

      <div className="grid g-main">
        <div className="col">
          <Card title="Balances by chain" icon="globe" sub="Live, across your managed wallets"
            actions={<div className="row" style={{ gap: 6 }}>
              <button className="btn sm ghost" onClick={() => loadFolio(true)} title="Refresh balances"><Icon name="refresh" size={13} /></button>
              <button className="btn sm secondary" onClick={() => setRouting(true)}><Icon name="globe" size={13} />Top up a chain</button>
            </div>} foot={folio?.note} flush>
            {folioErr ? <div style={{ padding: 16 }}><Callout tone="err">{folioErr}</Callout></div> : !folio ? <Loading /> : (
              <div className="feed">
                {folio.chains.map((c: any) => {
                  const share = folio.totals.usd > 0 ? (c.usd / folio.totals.usd) * 100 : 0;
                  const act = data?.by_chain?.[c.network];
                  return (
                    <div key={c.network} className="feed-item" style={{ alignItems: "center" }}>
                      <ChainDot network={c.network} size={26} />
                      <div className="grow" style={{ minWidth: 0 }}>
                        <div className="row between" style={{ gap: 8 }}>
                          <span style={{ fontWeight: 550 }}>{c.name}</span>
                          <span className="num" style={{ fontWeight: 600 }}>{folio.priced || c.stable ? usd(c.usd) : "–"}</span>
                        </div>
                        <div className="row between muted small" style={{ gap: 8 }}>
                          <span>{fmt(c.native, 4)} {c.coin} · {fmt(c.stable, 2)} {c.stable_symbol}</span>
                          <span>{c.wallets} wallet{c.wallets === 1 ? "" : "s"}{act ? ` · ${act.launches} launch${act.launches === 1 ? "" : "es"}` : ""}</span>
                        </div>
                        <div className="bar thin" style={{ marginTop: 6 }}><div style={{ width: `${share}%`, background: CHAINS[c.network as keyof typeof CHAINS]?.color }} /></div>
                        {c.error && <div className="warn-t small">{c.error}</div>}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </Card>

          <Card title="Your launches" icon="rocket" actions={<Link to="/launches" className="btn sm ghost">View all<Icon name="arrowright" size={13} /></Link>} flush>
            {!launches ? <Loading /> : launches.length === 0 ? (
              <Empty icon="rocket" title="No launches yet" text="Create a token on any supported chain and launchpad, or import one you already hold."
                action={<Link to="/launches" className="btn sm"><Icon name="plus" size={14} />Create launch</Link>} />
            ) : (
              <div className="table-wrap"><table>
                <thead><tr><th>Token</th><th>Chain</th><th>Status</th><th className="hide-sm">Launchpad</th><th /></tr></thead>
                <tbody>
                  {launches.slice(0, 5).map((l) => (
                    <tr key={l.id}>
                      <td><Link to={`/launches/${l.id}`} style={{ color: "var(--text)", fontWeight: 600 }}>{l.name}</Link> <span className="muted">${l.symbol}</span></td>
                      <td><ChainDot network={l.network} size={18} /></td>
                      <td><StatusBadge status={l.status} /></td>
                      <td className="hide-sm text-2">{l.launchpad}</td>
                      <td className="r"><Link to={`/launches/${l.id}`} className="btn sm secondary">Open</Link></td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            )}
          </Card>
          <Card title="Net flow · Solana" icon="trend" sub="In SOL · confirmed transactions, fees included. EVM launches show their flows in each workspace."
            actions={<Seg value={period} onChange={setPeriod} options={PERIODS} />}
            foot={data?.note}>
            {!data ? <Skeleton h={240} /> : data.series.length === 0 ? (
              <Empty icon="trend" title="No confirmed transactions in this period" text="Once you deploy, trade or move funds, your net flow shows up here." />
            ) : <AreaChart points={data.series} />}
          </Card>
        </div>

        <div className="col">
          {done < steps.length && (
            <Card title="Get set up" icon="zap" actions={<span className="muted small num">{done}/{steps.length}</span>}>
              <div className="bar thin" style={{ marginBottom: 6 }}><div style={{ width: `${(done / steps.length) * 100}%` }} /></div>
              {steps.map(([ok, label, to, cta]) => (
                <div key={label} className={`check ${ok ? "done" : ""}`}>
                  <span className="tick"><Icon name="check" size={13} stroke={3} /></span>
                  <span className="label grow">{label}</span>
                  {!ok && <Link to={to} className="btn sm secondary">{cta}</Link>}
                </div>
              ))}
            </Card>
          )}

          <Card title="Chain coverage" icon="globe" sub={pads.length ? `${coverage(pads).pads} launchpads on ${coverage(pads).chains} chains` : undefined}
            actions={<Link to="/launches" className="btn sm ghost">Launchpads<Icon name="arrowright" size={13} /></Link>} flush>
            <div className="feed">
              {CHAIN_ORDER.map((c) => {
                const list = pads.filter((p) => p.network === c);
                const live = list.filter((p) => p.status === "available").length;
                return (
                  <div key={c} className="feed-item" style={{ alignItems: "center" }}>
                    <ChainDot network={c} size={26} />
                    <div className="grow">
                      <div style={{ fontWeight: 550 }}>{CHAINS[c].name}</div>
                      <div className="muted small">{list.length} launchpad{list.length === 1 ? "" : "s"}</div>
                    </div>
                    <span className={`badge ${live ? "good" : ""}`}><span className="dot" />{live ? `${live} live` : "Rolling out"}</span>
                  </div>
                );
              })}
            </div>
          </Card>

          <Card title="Recent activity" icon="activity" flush actions={<Link to="/activity" className="btn sm ghost">All<Icon name="arrowright" size={13} /></Link>}>
            {!feed ? <Loading /> : feed.length === 0 ? <Empty icon="activity" title="Nothing yet" /> : (
              <div className="feed">{feed.map((a) => <FeedItem key={a.id} a={a} />)}</div>
            )}
          </Card>

          <Card title="Automation" icon="calendar" sub="Finite plans with visible limits" actions={<Link to="/automation" className="btn sm ghost">Open desk<Icon name="arrowright" size={13} /></Link>}>
            {!automations ? <Loading rows={2} /> : activePlans.length === 0 ? (
              <div className="automation-empty-row"><span className="rule-icon"><Icon name="calendar" size={15} /></span><span className="grow"><strong>No active plans</strong><small>Build a capped buy or sell schedule for any live launch.</small></span><Link to="/automation" className="btn sm secondary">Create</Link></div>
            ) : activePlans.slice(0, 2).map((a) => (
              <div className="automation-empty-row" key={a.id}>
                <span className="rule-icon"><Icon name="zap" size={15} /></span>
                <span className="grow"><strong>{a.launch_name || "Launch"} <span className="muted">${a.launch_symbol}</span></strong><small>{planLine(a)}</small></span>
                <StatusBadge status={a.status} />
              </div>
            ))}
          </Card>

          <ReferralCard data={data?.referral ?? null} onChange={load} />
        </div>
      </div>
      {routing && <RouteFunds onClose={() => setRouting(false)} onReview={(r) => { setRouting(false); setDialog(r); }} />}
      {dialog && <SpendDialog {...dialog} onClose={() => setDialog(null)} onDone={() => loadFolio(true)} />}
    </>
  );
}

function planLine(a: any): string {
  const done = `${a.runs_completed ?? 0} / ${a.total_orders ?? 0}`;
  if (a.side === "sell") {
    const exit = typeof a.planned_position_exit_pct === "number" ? ` · up to ${fmt(a.planned_position_exit_pct, 1)}% of holdings` : "";
    return `${done} sells · ${a.sell_percent ?? "?"}% each${exit}`;
  }
  const planned = typeof a.planned_trade_sol === "number" ? ` · ${fmt(a.planned_trade_sol, 3)} ${a.coin ?? "SOL"} planned` : "";
  return `${done} buys${planned}`;
}
