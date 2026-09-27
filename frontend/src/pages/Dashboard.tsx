import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errMsg, fmt, usd } from "../api";
import AreaChart from "../components/Chart";
import ReferralCard from "../components/Referrals";
import RouteFunds from "../components/RouteFunds";
import SpendDialog from "../components/SpendDialog";
import type { SpendRequest } from "../components/Distribute";
import { CHAIN_ORDER, CHAINS, Pad, padName } from "../chains";
import { coverage } from "../components/Launchpads";
import { ActivityRow, FeedItem } from "../components/feed";
import {
  Callout, Card, ChainDot, Empty, Icon, IconName, Loading, Metrics, Seg, Skeleton, StatusBadge, TokenAvatar, relTime,
} from "../components/ui";
import { useSession } from "../session";

type Period = "all" | "1y" | "1m" | "7d" | "1d";
const PERIODS: [Period, string][] = [["1d", "24H"], ["7d", "7D"], ["1m", "30D"], ["1y", "1Y"], ["all", "All"]];

interface Attention { key: string; icon: IconName; tone: "info" | "warn" | "bad"; title: string; text: string; to: string; cta: string }

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
  const [folioBusy, setFolioBusy] = useState(false);
  const [routing, setRouting] = useState(false);
  const [dialog, setDialog] = useState<SpendRequest | null>(null);
  const [error, setError] = useState("");

  const loadFolio = (fresh = false) => {
    setFolioBusy(true);
    return api(`/portfolio${fresh ? "?fresh=true" : ""}`)
      .then((p) => { setFolio(p); setFolioErr(""); }).catch((e) => setFolioErr(errMsg(e))).finally(() => setFolioBusy(false));
  };
  const load = () => api(`/dashboard?period=${period}`).then((d) => { setData(d); setError(""); }).catch((e) => setError(errMsg(e)));
  useEffect(() => { load(); }, [period]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    api<any[]>("/wallets").then(setWallets).catch(() => setWallets([]));
    api<any[]>("/launches").then(setLaunches).catch(() => setLaunches([]));
    api<Pad[]>("/launchpads").then(setPads).catch(() => undefined);
    api<ActivityRow[]>("/activity?limit=8").then(setFeed).catch(() => setFeed([]));
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

  // Things that need a decision, straight from launch and plan states.
  const attention: Attention[] = [
    ...(launches ?? []).filter((l) => l.status === "failed").map((l): Attention => ({
      key: `f${l.id}`, icon: "alert", tone: "bad", title: `${l.name} failed to deploy`, text: "Review the error and retry from its workspace.", to: `/launches/${l.id}`, cta: "Open",
    })),
    ...(launches ?? []).filter((l) => l.status === "draft").slice(0, 3).map((l): Attention => ({
      key: `d${l.id}`, icon: "rocket", tone: "info", title: `${l.name} is ready to deploy`, text: `Draft on ${padName(l.launchpad)}.`, to: `/launches/${l.id}`, cta: "Review",
    })),
    ...(automations ?? []).filter((a) => a.status === "failed" && a.error).slice(0, 3).map((a): Attention => ({
      key: `a${a.id}`, icon: "calendar", tone: "warn", title: `Plan on $${a.launch_symbol ?? "token"} stopped`, text: a.error, to: "/automation", cta: "View",
    })),
    ...(automations ?? []).filter((a) => a.status === "paused").slice(0, 3).map((a): Attention => ({
      key: `p${a.id}`, icon: "calendar", tone: "warn", title: `Plan on $${a.launch_symbol ?? "token"} is paused`, text: `${a.runs_completed} of ${a.total_orders} orders done.`, to: "/automation", cta: "Resume",
    })),
  ];

  const chains = (folio?.chains ?? []) as any[];
  const total = folio?.totals?.usd ?? 0;
  const funded = chains.filter((c) => c.usd > 0);

  return (
    <>
      <section className="command" aria-label="Portfolio summary">
        <div>
          <div className="eyebrow">Portfolio · all chains</div>
          {folio ? (
            <div className="figure" aria-live="polite">{usd(total)}</div>
          ) : folioErr ? <div className="figure muted">—</div> : <Skeleton h={34} w={220} style={{ marginTop: 10 }} />}
          <p className="sub">
            {folio ? <>{usd(folio.totals.stable_usd)} in stablecoins · {folio.wallets} managed wallet{folio.wallets === 1 ? "" : "s"} · {live.length} live launch{live.length === 1 ? "" : "es"}{!folio.priced && " · coin prices unavailable, stablecoins only"}</>
              : folioErr ? "Balances couldn't be loaded right now." : "Reading balances across chains…"}
          </p>
        </div>
        <div className="actions">
          <Link to="/launches" className="btn"><Icon name="plus" size={15} />New launch</Link>
          <button className="btn secondary" onClick={() => setRouting(true)}><Icon name="globe" size={15} />Route USDC</button>
          <Link to="/automation" className="btn secondary"><Icon name="calendar" size={15} />Automation</Link>
        </div>
      </section>
      {error && <Callout tone="err">{error}</Callout>}

      <Metrics label="Key figures" items={[
        { label: "Stablecoins", value: folio ? usd(folio.totals.stable_usd) : "—", hint: "USDC, USDG on Robinhood" },
        { label: "Network coins", value: folio ? (folio.priced ? usd(folio.totals.native_usd) : "unpriced") : "—", hint: "SOL, ETH, BNB at market" },
        { label: "Launches", value: data ? String(data.total_launches) : "—", hint: `${live.length} live` },
        { label: "Active plans", value: automations ? String(activePlans.length) : "—", hint: `${automations?.filter((a) => a.status === "completed").length ?? 0} completed` },
        { label: "Wallets", value: wallets ? String(managed.length) : "—", hint: `${(wallets?.length ?? 0) - managed.length} watch-only` },
      ]} />

      {attention.length > 0 && (
        <Card title="Needs attention" icon="alert" sub={`${attention.length} item${attention.length === 1 ? "" : "s"} waiting on you`} flush>
          <div className="feed">
            {attention.map((a) => (
              <div key={a.key} className="feed-item" style={{ alignItems: "center" }}>
                <div className={`feed-ic ${a.tone}`}><Icon name={a.icon} size={14} /></div>
                <div className="grow">
                  <div style={{ fontWeight: 550 }}>{a.title}</div>
                  <div className="muted small truncate" title={a.text}>{a.text}</div>
                </div>
                <Link to={a.to} className="btn sm secondary">{a.cta}</Link>
              </div>
            ))}
          </div>
        </Card>
      )}

      <div className="grid g-main">
        <div className="col">
          <Card title="Balances by chain" icon="globe" sub="Coins and stablecoins in your managed wallets, read live"
            actions={<>
              <button className="btn sm ghost" onClick={() => loadFolio(true)} disabled={folioBusy} aria-label="Refresh balances">
                <Icon name="refresh" size={13} />{folioBusy ? "Refreshing" : "Refresh"}
              </button>
              <button className="btn sm secondary" onClick={() => setRouting(true)}><Icon name="globe" size={13} />Top up a chain</button>
            </>} foot={folio?.note} flush>
            {folioErr ? <div style={{ padding: 16 }}><Callout tone="err">{folioErr}</Callout></div> : !folio ? <Loading rows={5} /> : (
              <>
                {total > 0 && (
                  <div className="stack tight" style={{ padding: "14px 16px", borderBottom: "1px solid var(--line)" }}>
                    <div className="alloc" role="img" aria-label={`Allocation: ${funded.map((c) => `${c.name} ${fmt((c.usd / total) * 100, 1)}%`).join(", ")}`}>
                      {funded.map((c) => <span key={c.network} style={{ width: `${(c.usd / total) * 100}%`, background: CHAINS[c.network as keyof typeof CHAINS]?.color }} />)}
                    </div>
                    <div className="legend">
                      {funded.map((c) => <span key={c.network}><i style={{ background: CHAINS[c.network as keyof typeof CHAINS]?.color }} />{c.name} <b className="num">{fmt((c.usd / total) * 100, 1)}%</b></span>)}
                    </div>
                  </div>
                )}
                <div className="chain-rows" role="table" aria-label="Balances by chain">
                  <div className="chain-row head" role="row">
                    <span role="columnheader" aria-label="Chain" /><span role="columnheader">Chain</span>
                    <span role="columnheader" style={{ textAlign: "right" }}>Coin</span><span role="columnheader" style={{ textAlign: "right" }}>Stablecoin</span>
                    <span role="columnheader" style={{ textAlign: "right" }}>Value</span>
                  </div>
                  {chains.map((c) => {
                    const act = data?.by_chain?.[c.network];
                    return (
                      <div key={c.network} className={`chain-row ${c.wallets === 0 ? "empty-row" : ""}`} role="row">
                        <ChainDot network={c.network} size={24} />
                        <div style={{ minWidth: 0 }} role="cell">
                          <div style={{ fontWeight: 600 }}>{c.name}</div>
                          <div className="muted small truncate">
                            {c.wallets} wallet{c.wallets === 1 ? "" : "s"}{act?.launches ? ` · ${act.launches} launch${act.launches === 1 ? "" : "es"}` : ""}
                            {c.error && <span className="warn-t"> · some balances unreadable</span>}
                          </div>
                        </div>
                        <div className="amt c-native" role="cell">{fmt(c.native, 4)}<small>{c.coin}</small></div>
                        <div className="amt c-stable" role="cell">{fmt(c.stable, 2)}<small>{c.stable_symbol}</small></div>
                        <div className="usd" role="cell">{folio.priced || c.stable ? usd(c.usd) : "—"}</div>
                      </div>
                    );
                  })}
                </div>
              </>
            )}
          </Card>

          <Card title="Launches" icon="rocket" sub={launches ? `${launches.length} total · ${live.length} live` : undefined}
            actions={<Link to="/launches" className="btn sm ghost">View all<Icon name="arrowright" size={13} /></Link>} flush>
            {!launches ? <Loading /> : launches.length === 0 ? (
              <Empty icon="rocket" title="No launches yet" text="Create a token on any supported chain and launchpad, or import one you already hold."
                action={<Link to="/launches" className="btn sm"><Icon name="plus" size={14} />Create launch</Link>} />
            ) : (
              <div className="table-wrap"><table>
                <thead><tr><th>Token</th><th>Chain</th><th className="hide-sm">Launchpad</th><th>Status</th><th className="hide-sm">Created</th><th><span className="sr-only">Action</span></th></tr></thead>
                <tbody>
                  {launches.slice(0, 6).map((l) => (
                    <tr key={l.id}>
                      <td>
                        <div className="row nowrap" style={{ gap: 10 }}>
                          <TokenAvatar symbol={l.symbol} size={28} />
                          <div style={{ minWidth: 0 }}>
                            <Link to={`/launches/${l.id}`} style={{ color: "var(--text)", fontWeight: 600 }}>{l.name}</Link>
                            <div className="muted mono" style={{ fontSize: 11.5 }}>${l.symbol}</div>
                          </div>
                        </div>
                      </td>
                      <td><ChainDot network={l.network} size={18} /></td>
                      <td className="hide-sm text-2">{padName(l.launchpad)}</td>
                      <td><StatusBadge status={l.status} /></td>
                      <td className="hide-sm muted nowrap">{relTime(l.created_at)}</td>
                      <td className="r"><Link to={`/launches/${l.id}`} className="btn sm secondary" aria-label={`Open ${l.name}`}>{l.status === "draft" || l.status === "failed" ? "Deploy" : l.status === "live" ? "Trade" : "Open"}</Link></td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            )}
          </Card>

          <Card title="Net flow · Solana" icon="trend" sub="SOL in and out of your wallets from confirmed transactions, fees included"
            actions={<Seg value={period} onChange={setPeriod} options={PERIODS} />}
            foot={<>{data?.note} EVM launches show their flows in each launch workspace.</>}>
            {!data ? <Skeleton h={220} /> : data.series.length === 0 ? (
              <Empty icon="trend" title="No confirmed Solana transactions in this period" text="Deploys, trades and transfers on Solana show up here once confirmed." />
            ) : <AreaChart points={data.series} height={220} />}
          </Card>
        </div>

        <div className="col">
          {done < steps.length && (
            <Card title="Get set up" icon="zap" actions={<span className="muted small num">{done}/{steps.length}</span>}>
              <div className="bar thin" style={{ marginBottom: 6 }} role="progressbar" aria-valuemin={0} aria-valuemax={steps.length} aria-valuenow={done} aria-label="Setup progress">
                <div style={{ width: `${(done / steps.length) * 100}%` }} />
              </div>
              {steps.map(([ok, label, to, cta]) => (
                <div key={label} className={`check ${ok ? "done" : ""}`}>
                  <span className="tick" aria-hidden="true"><Icon name="check" size={12} stroke={3} /></span>
                  <span className="label grow">{label}<span className="sr-only">{ok ? " (done)" : " (to do)"}</span></span>
                  {!ok && <Link to={to} className="btn sm secondary">{cta}</Link>}
                </div>
              ))}
            </Card>
          )}

          <Card title="Scheduled plans" icon="calendar" sub="Finite buys and staged sells" actions={<Link to="/automation" className="btn sm ghost">Open<Icon name="arrowright" size={13} /></Link>}>
            {!automations ? <Loading rows={2} /> : activePlans.length === 0 ? (
              <div className="automation-empty-row"><span className="rule-icon"><Icon name="calendar" size={14} /></span><span className="grow"><strong>No active plans</strong><small>Schedule a capped set of buys or staged sells on any live launch.</small></span><Link to="/automation" className="btn sm secondary">Create</Link></div>
            ) : activePlans.slice(0, 3).map((a, i) => {
              const pct = a.total_orders ? Math.min(100, (a.runs_completed / a.total_orders) * 100) : 0;
              return (
                <div className="stack tight" key={a.id} style={{ padding: "10px 0", borderTop: i ? "1px solid var(--line)" : 0 }}>
                  <div className="row between nowrap">
                    <strong className="truncate">{a.launch_name || "Launch"} <span className="muted mono">${a.launch_symbol}</span></strong>
                    <StatusBadge status={a.status} />
                  </div>
                  <div className="bar thin" aria-hidden="true"><div style={{ width: `${pct}%` }} /></div>
                  <div className="muted small">{planLine(a)}</div>
                </div>
              );
            })}
          </Card>

          <Card title="Recent activity" icon="activity" flush actions={<Link to="/activity" className="btn sm ghost">All<Icon name="arrowright" size={13} /></Link>}>
            {!feed ? <Loading /> : feed.length === 0 ? <Empty icon="activity" title="Nothing yet" text="Launches, trades, transfers and account changes are logged here." /> : (
              <div className="feed">{feed.map((a) => <FeedItem key={a.id} a={a} />)}</div>
            )}
          </Card>

          <Card title="Chain coverage" icon="layers" sub={pads.length ? `${coverage(pads).live} of ${coverage(pads).pads} launchpads live on ${coverage(pads).chains} chains` : undefined}
            actions={<Link to="/launches" className="btn sm ghost">Launchpads<Icon name="arrowright" size={13} /></Link>} flush>
            {!pads.length ? <Loading rows={3} /> : (
              <div className="feed">
                {CHAIN_ORDER.map((c) => {
                  const list = pads.filter((p) => p.network === c);
                  const n = list.filter((p) => p.status === "available").length;
                  return (
                    <div key={c} className="feed-item" style={{ alignItems: "center", padding: "9px 16px" }}>
                      <ChainDot network={c} size={22} />
                      <div className="grow">
                        <div style={{ fontWeight: 550 }}>{CHAINS[c].name}</div>
                        <div className="muted small truncate">{list.map((p) => padName(p.id)).join(" · ") || "—"}</div>
                      </div>
                      <span className={`badge ${n ? "good" : ""}`}><span className="dot" />{n ? `${n} live` : "Rolling out"}</span>
                    </div>
                  );
                })}
              </div>
            )}
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
  const done = `${a.runs_completed ?? 0} of ${a.total_orders ?? 0}`;
  if (a.side === "sell") {
    const exit = typeof a.planned_position_exit_pct === "number" ? ` · up to ${fmt(a.planned_position_exit_pct, 1)}% of holdings` : "";
    return `${done} sells · ${a.sell_percent ?? "?"}% each${exit}`;
  }
  const planned = typeof a.planned_trade_sol === "number" ? ` · ${fmt(a.planned_trade_sol, 3)} ${a.coin ?? "SOL"} planned` : "";
  return `${done} buys${planned}`;
}
