import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errMsg, fmt } from "../api";
import AreaChart from "../components/Chart";
import ReferralCard from "../components/Referrals";
import { CHAIN_ORDER, CHAINS, Pad } from "../chains";
import { coverage } from "../components/Launchpads";
import { ActivityRow, FeedItem } from "../components/feed";
import { Callout, Card, Empty, Icon, Loading, PageHeader, Seg, Skeleton, Stat, StatusBadge, ChainDot } from "../components/ui";
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
  const [pads, setPads] = useState<Pad[]>([]);
  const [error, setError] = useState("");

  const load = () => api(`/dashboard?period=${period}`).then((d) => { setData(d); setError(""); }).catch((e) => setError(errMsg(e)));
  useEffect(() => { load(); }, [period]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    api<any[]>("/wallets").then(setWallets).catch(() => setWallets([]));
    api<any[]>("/launches").then(setLaunches).catch(() => setLaunches([]));
    api<Pad[]>("/launchpads").then(setPads).catch(() => undefined);
    api<ActivityRow[]>("/activity?limit=7").then(setFeed).catch(() => setFeed([]));
  }, []);

  const managed = wallets?.filter((w) => w.kind === "managed") ?? [];
  const live = launches?.filter((l) => l.status === "live") ?? [];

  const steps: [boolean, string, string, string][] = [
    [managed.length > 0, "Create your first wallets", "/wallets", "Wallets"],
    [!!me?.totp_enabled, "Turn on two-factor authentication", "/settings", "Settings"],
    [(launches?.length ?? 0) > 0, "Create or import a launch", "/launches", "Launches"],
    [!!tg?.linked, "Link the Telegram bot", "/settings", "Settings"],
  ];
  const done = steps.filter((s) => s[0]).length;

  return (
    <>
      <PageHeader
        title="Dashboard"
        sub="Your launches across every chain, net flow and recent account activity."
        actions={<>
          <Link to="/launches" className="btn"><Icon name="plus" size={15} />New launch</Link>
        </>}
      />
      {error && <Callout tone="err">{error}</Callout>}

      <div className="grid g-stats">
        {data ? (
          <Stat accent label="Net flow · Solana" icon="trend" value={`${data.net_sol_flow > 0 ? "+" : ""}${fmt(data.net_sol_flow)}`} unit="SOL"
            tone={data.net_sol_flow > 0 ? "good" : data.net_sol_flow < 0 ? "bad" : undefined} hint={PERIODS.find((p) => p[0] === period)?.[1] + " · confirmed txs"} />
        ) : <div className="card stat"><Skeleton h={12} w={90} /><Skeleton h={26} w={140} /></div>}
        <Stat label="Launches" icon="rocket" value={data ? data.total_launches : "–"} hint={`${live.length} live now`} />
        <Stat label="Managed wallets" icon="wallet" value={wallets ? managed.length : "–"} hint={`${(wallets?.length ?? 0) - managed.length} watch-only`} />
        <Stat label="Referral earnings" icon="gift" value={data ? fmt(data.referral.earned_sol + data.referral.owed_sol, 4) : "–"} unit={data ? "SOL" : undefined} hint={`${data?.referral.referred_users ?? 0} ${data?.referral.referred_users === 1 ? "person" : "people"} referred`} />
      </div>

      <div className="grid g-main">
        <div className="col">
          <Card title="Cumulative net flow" icon="trend" sub="Solana, in SOL · confirmed transactions, fees included"
            actions={<Seg value={period} onChange={setPeriod} options={PERIODS} />}
            foot={data?.note}>
            {!data ? <Skeleton h={240} /> : data.series.length === 0 ? (
              <Empty icon="trend" title="No confirmed transactions in this period" text="Once you deploy, trade or move funds, your net flow shows up here." />
            ) : <AreaChart points={data.series} />}
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

          <ReferralCard data={data?.referral ?? null} onChange={load} />
        </div>
      </div>
    </>
  );
}
