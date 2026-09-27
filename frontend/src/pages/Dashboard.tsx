import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errMsg, fmt, usd } from "../api";
import AreaChart from "../components/Chart";
import ReferralCard from "../components/Referrals";
import RouteFunds from "../components/RouteFunds";
import SpendDialog from "../components/SpendDialog";
import type { SpendRequest } from "../components/Distribute";
import { CHAIN_ORDER, CHAINS, Pad, padName } from "../chains";
import { coverage } from "../components/Launchpads";
import { ChainMark, PadLabel, PadMark } from "../components/marks";
import { ActivityRow, FeedItem, groupRepeats, isRoutine } from "../components/feed";
import {
  Callout, Card, Empty, Icon, IconName, Loading, LoadError, Metrics, Seg, Skeleton, Spinner, StatusBadge, TokenImage, parseDate, relTime,
} from "../components/ui";
import { useSession } from "../session";

type Period = "all" | "1y" | "1m" | "7d" | "1d";
const PERIODS: [Period, string][] = [["1d", "24H"], ["7d", "7D"], ["1m", "30D"], ["1y", "1Y"], ["all", "All"]];
const STALE_MS = 5 * 60_000;

type ChainStatus = "ok" | "partial" | "unavailable" | "no_wallets";
/** Read status for a chain row. Older API responses have no `status`, so infer it from the error text. */
function statusOf(c: any): ChainStatus {
  if (c.status) return c.status;
  if (!c.wallets) return "no_wallets";
  if (!c.error) return "ok";
  return /^\d+ of \d+/.test(c.error) ? "partial" : "unavailable";
}

interface NextAction { key: string; icon: IconName; tone: "info" | "warn" | "bad" | ""; title: string; text: string; to: string; cta: string }

/** A list load that can fail independently of the rest of the page. */
function useList<T>(path: string) {
  const [data, setData] = useState<T[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    setBusy(true);
    return api<T[]>(path).then((d) => { setData(d); setError(""); }).catch((e) => setError(errMsg(e))).finally(() => setBusy(false));
  }, [path]);
  useEffect(() => { load(); }, [load]);
  return { data, error, busy, load };
}

export default function Dashboard() {
  const { me, tg } = useSession();
  const [period, setPeriod] = useState<Period>("all");
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const wallets = useList<any>("/wallets");
  const launches = useList<any>("/launches");
  const feed = useList<ActivityRow>("/activity?limit=40");
  const plans = useList<any>("/automations");
  const [pads, setPads] = useState<Pad[]>([]);
  const [folio, setFolio] = useState<any>(null);
  const [folioErr, setFolioErr] = useState("");
  const [folioBusy, setFolioBusy] = useState(false);
  const [routing, setRouting] = useState(false);
  const [dialog, setDialog] = useState<SpendRequest | null>(null);
  const [, tick] = useState(0);

  const loadFolio = (fresh = false) => {
    setFolioBusy(true);
    return api(`/portfolio${fresh ? "?fresh=true" : ""}`)
      .then((p) => { setFolio(p); setFolioErr(""); }).catch((e) => setFolioErr(errMsg(e))).finally(() => setFolioBusy(false));
  };
  const load = () => api(`/dashboard?period=${period}`).then((d) => { setData(d); setError(""); }).catch((e) => setError(errMsg(e)));
  useEffect(() => { load(); }, [period]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    api<Pad[]>("/launchpads").then(setPads).catch(() => undefined);
    loadFolio();
    const t = setInterval(() => tick((n) => n + 1), 30_000); // keeps the "as of" age honest
    return () => clearInterval(t);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const managed = wallets.data?.filter((w) => w.kind === "managed") ?? [];
  const live = launches.data?.filter((l) => l.status === "live") ?? [];
  const activePlans = plans.data?.filter((a) => ["scheduled", "executing", "paused"].includes(a.status)) ?? [];

  // One list of next actions: things that need a decision first, then remaining setup. Each appears once.
  const setup: NextAction[] = [
    // "Create wallets" is the header's primary action when there are none, so it isn't repeated here.
    ...(me && !me.totp_enabled ? [{ key: "s-2fa", icon: "shield" as IconName, tone: "warn" as const, title: "Turn on two-factor authentication", text: "Withdrawals and token sends stay blocked until 2FA is on.", to: "/settings", cta: "Set up 2FA" }] : []),
    ...(launches.data && launches.data.length === 0 && managed.length > 0 ? [{ key: "s-l", icon: "rocket" as IconName, tone: "" as const, title: "Create or import a launch", text: "Pick a chain and launchpad, or import a token you already hold.", to: "/launches", cta: "Launches" }] : []),
    ...(tg && !tg.linked ? [{ key: "s-tg", icon: "telegram" as IconName, tone: "" as const, title: "Link the Telegram bot", text: "Optional: trade and check status from Telegram with the same previews.", to: "/settings", cta: "Settings" }] : []),
  ];
  const decisions: NextAction[] = [
    ...(launches.data ?? []).filter((l) => l.status === "failed").map((l): NextAction => ({
      key: `f${l.id}`, icon: "alert", tone: "bad", title: `${l.name} failed to deploy`, text: "Review what happened and retry from its workspace.", to: `/launches/${l.id}`, cta: "Open",
    })),
    ...(plans.data ?? []).filter((a) => a.status === "failed" && a.error).slice(0, 3).map((a): NextAction => ({
      key: `a${a.id}`, icon: "calendar", tone: "bad", title: `Plan on $${a.launch_symbol ?? "token"} stopped`, text: a.error, to: "/automation", cta: "View",
    })),
    ...(plans.data ?? []).filter((a) => a.status === "paused").slice(0, 3).map((a): NextAction => ({
      key: `p${a.id}`, icon: "calendar", tone: "warn", title: `Plan on $${a.launch_symbol ?? "token"} is paused`, text: `${a.runs_completed} of ${a.total_orders} orders done. Resume or cancel the rest.`, to: "/automation", cta: "Review",
    })),
    ...(launches.data ?? []).filter((l) => l.status === "draft").slice(0, 3).map((l): NextAction => ({
      key: `d${l.id}`, icon: "rocket", tone: "info", title: `${l.name} is ready to deploy`, text: `Draft on ${padName(l.launchpad)}. The preview shows every cost first.`, to: `/launches/${l.id}`, cta: "Review",
    })),
  ];
  // Only steps whose state is known count toward progress, so a failed load never reads as "not done".
  const setupChecks = [wallets.data ? managed.length > 0 : null, me ? !!me.totp_enabled : null,
    launches.data ? launches.data.length > 0 : null, tg ? !!tg.linked : null].filter((x): x is boolean => x !== null);
  const setupTotal = setupChecks.length;
  const setupDone = setupChecks.filter(Boolean).length;

  const chains = (folio?.chains ?? []) as any[];
  const total = folio?.totals?.usd ?? 0;
  const funded = chains.filter((c) => c.usd > 0);
  const incomplete = chains.filter((c) => ["partial", "unavailable"].includes(statusOf(c)));
  const checkedAt = folio?.checked_at ? parseDate(folio.checked_at) : null;
  const stale = !!checkedAt && Date.now() - checkedAt.getTime() > STALE_MS;
  const noWallets = !!wallets.data && managed.length === 0;
  const recent = groupRepeats((feed.data ?? []).filter((r) => !isRoutine(r))).slice(0, 6);

  return (
    <>
      <section className="command" aria-label="Portfolio summary">
        <div style={{ minWidth: 0 }}>
          <div className="row" style={{ gap: 10 }}>
            <span className="eyebrow">Portfolio · all chains</span>
            {folio && !folioErr && folio.wallets > 0 && (incomplete.length ? (
              <span className="state partial"><Icon name="alert" size={12} />Incomplete · {incomplete.length} chain{incomplete.length === 1 ? "" : "s"} not fully read</span>
            ) : <span className="state none"><Icon name="checkcircle" size={12} />All chains read</span>)}
          </div>
          {folio ? (
            folio.wallets === 0 ? <div className="figure muted">No wallets yet</div>
              : <div className="figure" aria-live="polite">{usd(total)}{incomplete.length > 0 && <small>at least</small>}</div>
          ) : folioErr ? <div className="figure muted">Unavailable</div> : <Skeleton h={34} w={220} style={{ marginTop: 10 }} />}
          <p className="sub">
            {folioErr ? <>Balances couldn't be read: {folioErr}</>
              : folio && folio.wallets === 0 ? <>Create a managed wallet on any chain, fund it, and its balances show up here.</>
              : folio ? <>{usd(folio.totals.stable_usd)} in stablecoins · {folio.wallets} managed wallet{folio.wallets === 1 ? "" : "s"}{!folio.priced && " · coin prices unavailable, stablecoins only"}</>
              : "Reading balances across chains…"}
          </p>
          <div className="row" style={{ gap: 8, marginTop: 8 }}>
            {checkedAt && folio?.wallets > 0 && <span className={`asof ${stale ? "stale" : ""}`}>{stale ? "Stale · " : ""}as of {checkedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} ({relTime(folio.checked_at)})</span>}
            {(folioErr || folio?.wallets > 0) && <button className="btn sm ghost" onClick={() => loadFolio(true)} disabled={folioBusy} style={{ marginLeft: -6 }}>
              {folioBusy ? <Spinner /> : <Icon name="refresh" size={13} />}{folioErr ? "Try again" : "Refresh balances"}
            </button>}
          </div>
        </div>
        <div className="actions">
          {noWallets ? (
            <Link to="/wallets" className="btn"><Icon name="wallet" size={15} />Create wallets</Link>
          ) : <Link to="/launches" className="btn"><Icon name="plus" size={15} />New launch</Link>}
          {!noWallets && <button className="btn secondary" onClick={() => setRouting(true)}><Icon name="globe" size={15} />Route USDC</button>}
          <Link to="/automation" className="btn secondary"><Icon name="calendar" size={15} />Automation</Link>
        </div>
      </section>
      {error && <Callout tone="err">Dashboard figures couldn't be loaded: {error}</Callout>}

      <Metrics label="Key figures" items={[
        { label: "Stablecoins", value: folio?.wallets > 0 ? usd(folio.totals.stable_usd) : folioErr ? "Unavailable" : "—", hint: folio?.wallets === 0 ? "no wallets yet" : "USDC, USDG on Robinhood" },
        { label: "Network coins", value: folio?.wallets > 0 ? (folio.priced ? usd(folio.totals.native_usd) : "Unpriced") : folioErr ? "Unavailable" : "—", hint: folio?.wallets === 0 ? "no wallets yet" : "SOL, ETH, BNB at market" },
        { label: "Launches", value: launches.data ? String(launches.data.length) : launches.error ? "Unavailable" : "—", hint: launches.data ? `${live.length} live` : " " },
        { label: "Active plans", value: plans.data ? String(activePlans.length) : plans.error ? "Unavailable" : "—", hint: plans.data ? `${plans.data.filter((a) => a.status === "completed").length} completed` : " " },
        { label: "Wallets", value: wallets.data ? String(managed.length) : wallets.error ? "Unavailable" : "—", hint: wallets.data ? `${wallets.data.length - managed.length} watch-only` : " " },
      ]} />

      {(decisions.length > 0 || setup.length > 0) && (
        <Card title="Next actions" icon="zap" sub={decisions.length ? `${decisions.length} need${decisions.length === 1 ? "s" : ""} a decision` : "Finish setting up"} flush>
          <div className="next-list">
            {decisions.map((a) => <NextRow key={a.key} a={a} />)}
            {setup.length > 0 && (
              <>
                <div className="next-kicker">
                  <span>Setup {setupDone}/{setupTotal}</span>
                  <span className="bar thin" role="progressbar" aria-valuemin={0} aria-valuemax={setupTotal} aria-valuenow={setupDone} aria-label="Setup progress">
                    <span style={{ display: "block", height: "100%", width: `${(setupDone / setupTotal) * 100}%`, background: "var(--accent)", borderRadius: 3 }} />
                  </span>
                </div>
                {setup.map((a) => <NextRow key={a.key} a={a} />)}
              </>
            )}
          </div>
        </Card>
      )}

      <div className="grid g-main">
        <div className="col">
          <Card title="Balances by chain" icon="globe" sub="Coins and stablecoins in your managed wallets"
            actions={!noWallets && <button className="btn sm secondary" onClick={() => setRouting(true)}><Icon name="globe" size={13} />Top up a chain</button>}
            foot={folio && <>{folio.note}{incomplete.length > 0 && " Totals only count balances that were read."}</>} flush>
            {folioErr ? <LoadError what="Balances" error={folioErr} onRetry={() => loadFolio(true)} busy={folioBusy} /> : !folio ? <Loading rows={5} />
              : folio.wallets === 0 ? <Empty icon="wallet" title="No managed wallets yet" text="Balances appear per chain once you create and fund a wallet. Use Create wallets above." /> : (
              <>
                {total > 0 && (
                  <div className="stack tight" style={{ padding: "14px 16px", borderBottom: "1px solid var(--line)" }}>
                    <div className="alloc" role="img" aria-label={`Allocation of balances that were read: ${funded.map((c) => `${c.name} ${fmt((c.usd / total) * 100, 1)}%`).join(", ")}`}>
                      {funded.map((c) => <span key={c.network} style={{ width: `${(c.usd / total) * 100}%`, background: CHAINS[c.network as keyof typeof CHAINS]?.color }} />)}
                    </div>
                    <div className="legend" aria-hidden="true">
                      {funded.map((c) => <span key={c.network}><i style={{ background: CHAINS[c.network as keyof typeof CHAINS]?.color }} />{c.name} <b className="num">{fmt((c.usd / total) * 100, 1)}%</b></span>)}
                    </div>
                  </div>
                )}
                <div className="chain-rows" role="table" aria-label="Balances by chain">
                  <div className="chain-row head" role="row">
                    <span role="columnheader"><span className="sr-only">Mark</span></span><span role="columnheader">Chain</span>
                    <span role="columnheader" style={{ textAlign: "right" }}>Coin</span><span role="columnheader" style={{ textAlign: "right" }}>Stablecoin</span>
                    <span role="columnheader" style={{ textAlign: "right" }}>Value</span>
                  </div>
                  {chains.map((c) => <ChainRow key={c.network} c={c} launches={data?.by_chain?.[c.network]?.launches ?? 0} priced={folio.priced}
                    onRetry={() => loadFolio(true)} busy={folioBusy} />)}
                </div>
              </>
            )}
          </Card>

          <Card title="Launches" icon="rocket" sub={launches.data ? `${launches.data.length} total · ${live.length} live` : undefined}
            actions={<Link to="/launches" className="btn sm ghost">View all<Icon name="arrowright" size={13} /></Link>} flush>
            {launches.error && !launches.data ? <LoadError what="Launches" error={launches.error} onRetry={launches.load} busy={launches.busy} />
              : !launches.data ? <Loading /> : launches.data.length === 0 ? (
                <Empty icon="rocket" title="No launches yet" text={noWallets ? "Create a managed wallet first; it signs the launch." : "Create a token on any supported chain and launchpad, or import one you already hold."} />
              ) : (
                <div className="table-wrap"><table className="rtable">
                  <caption className="sr-only">Your most recent launches</caption>
                  <thead><tr><th>Token</th><th>Launchpad</th><th>Status</th><th className="hide-sm">Created</th><th><span className="sr-only">Action</span></th></tr></thead>
                  <tbody>
                    {launches.data.slice(0, 6).map((l) => (
                      <tr key={l.id}>
                        <td className="span">
                          <div className="row nowrap" style={{ gap: 10 }}>
                            <TokenImage launch={l} size={28} />
                            <div style={{ minWidth: 0 }}>
                              <Link to={`/launches/${l.id}`} style={{ color: "var(--text)", fontWeight: 600 }}>{l.name}</Link>
                              <div className="muted mono" style={{ fontSize: 11.5 }}>${l.symbol}</div>
                            </div>
                          </div>
                        </td>
                        <td data-label="Launchpad"><PadLabel pad={l.launchpad} network={l.network} showChain /></td>
                        <td data-label="Status"><StatusBadge status={l.status} /></td>
                        <td className="hide-sm muted nowrap">{relTime(l.created_at)}</td>
                        <td className="r span"><Link to={`/launches/${l.id}`} className="btn sm secondary" aria-label={`${l.status === "draft" || l.status === "failed" ? "Deploy" : l.status === "live" ? "Trade" : "Open"} ${l.name}`}>{l.status === "draft" || l.status === "failed" ? "Deploy" : l.status === "live" ? "Trade" : "Open"}</Link></td>
                      </tr>
                    ))}
                  </tbody>
                </table></div>
              )}
          </Card>

          <Card title="Net flow · Solana" icon="trend" sub="SOL in and out of your wallets from confirmed transactions, fees included"
            actions={<Seg value={period} onChange={setPeriod} options={PERIODS} />}
            foot={<>{data?.note} EVM launches show their flows in each launch workspace.</>}>
            {error && !data ? <LoadError what="Net flow" error={error} onRetry={load} /> : !data ? <Skeleton h={220} /> : data.series.length === 0 ? (
              <Empty icon="trend" title="No confirmed Solana transactions in this period" text="Deploys, trades and transfers on Solana show up here once confirmed." />
            ) : <AreaChart points={data.series} height={220} />}
          </Card>
        </div>

        <div className="col">
          <Card title="Scheduled plans" icon="calendar" sub="Finite buys and staged sells" actions={<Link to="/automation" className="btn sm ghost">Open<Icon name="arrowright" size={13} /></Link>}>
            {plans.error && !plans.data ? <LoadError what="Plans" error={plans.error} onRetry={plans.load} busy={plans.busy} />
              : !plans.data ? <Loading rows={2} /> : activePlans.length === 0 ? (
                <p className="muted small">No active plans. {live.length ? "You can schedule a capped set of buys or staged sells on a live launch." : "Plans run on a live launch."}</p>
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

          <Card title="Recent activity" icon="activity" sub="Sign-ins are left out here" flush actions={<Link to="/activity" className="btn sm ghost">All<Icon name="arrowright" size={13} /></Link>}>
            {feed.error && !feed.data ? <LoadError what="Activity" error={feed.error} onRetry={feed.load} busy={feed.busy} />
              : !feed.data ? <Loading /> : recent.length === 0 ? <Empty icon="activity" title="Nothing yet" text="Launches, trades and transfers are logged here." /> : (
                <div className="feed">{recent.map((g) => <FeedItem key={g.head.id} a={g.head} items={g.items} />)}</div>
              )}
          </Card>

          <Card title="Chain coverage" icon="layers" sub={pads.length ? `${coverage(pads).live} of ${coverage(pads).pads} launchpads live on ${coverage(pads).chains} chains` : undefined}
            actions={<Link to="/launches" className="btn sm ghost">Launchpads<Icon name="arrowright" size={13} /></Link>}>
            {!pads.length ? <Loading rows={3} /> : (
              <div className="stack">
                {CHAIN_ORDER.map((c) => {
                  const list = pads.filter((p) => p.network === c);
                  if (!list.length) return null;
                  return (
                    <div key={c} className="stack tight">
                      <div className="row" style={{ gap: 8 }}><ChainMark network={c} size={18} /><b style={{ fontSize: 13 }}>{CHAINS[c].name}</b>
                        <span className="muted small">{list.filter((p) => p.status === "available").length} of {list.length} live</span></div>
                      <ul className="pad-chips" style={{ margin: 0, padding: 0, listStyle: "none" }}>
                        {list.map((p) => {
                          const st = p.status === "available" ? "live" : p.status === "setup_required" ? "setup" : "soon";
                          return (
                            <li key={p.id} className={`pad-chip ${st}`} title={p.reason ?? undefined}>
                              <PadMark pad={p.id} network={p.network} size={20} />{padName(p.id)}
                              <span className="st">{st === "live" ? "Live" : st === "setup" ? "Needs setup" : "Soon"}</span>
                            </li>
                          );
                        })}
                      </ul>
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

function NextRow({ a }: { a: NextAction }) {
  return (
    <div className="next-item">
      <div className={`feed-ic ${a.tone}`} aria-hidden="true"><Icon name={a.icon} size={14} /></div>
      <div style={{ minWidth: 0 }}>
        <div className="t">{a.title}</div>
        <div className="d">{a.text}</div>
      </div>
      <Link to={a.to} className="btn sm secondary">{a.cta}</Link>
    </div>
  );
}

/** One chain's balances. Unread data is never shown as a number: unavailable chains say so and offer a retry. */
function ChainRow({ c, launches, priced, onRetry, busy }: { c: any; launches: number; priced: boolean; onRetry: () => void; busy: boolean }) {
  const st = statusOf(c);
  const meta = `${c.wallets} wallet${c.wallets === 1 ? "" : "s"}${launches ? ` · ${launches} launch${launches === 1 ? "" : "es"}` : ""}`;
  const cls = st === "unavailable" ? "unavail" : st === "partial" ? "partial" : st === "no_wallets" ? "empty-row" : "";
  return (
    <div className={`chain-row ${cls}`} role="row">
      <ChainMark network={c.network} size={24} />
      <div style={{ minWidth: 0 }} role="cell">
        <div style={{ fontWeight: 600 }}>{c.name}</div>
        <div className="muted small">{st === "no_wallets" ? "No wallets on this chain" : meta}</div>
      </div>
      {st === "unavailable" ? (
        <>
          <div className="amt c-native" role="cell"><span className="unknown">not read</span></div>
          <div className="amt c-stable hide-sm" role="cell"><span className="unknown">not read</span></div>
          <div className="usd" role="cell"><span className="unknown">unknown</span></div>
        </>
      ) : st === "no_wallets" ? (
        <>
          <div className="amt c-native" role="cell"><span className="sr-only">No wallets</span>—</div>
          <div className="amt c-stable" role="cell" aria-hidden="true">—</div>
          <div className="usd" role="cell" aria-hidden="true">—</div>
        </>
      ) : (
        <>
          <div className="amt c-native" role="cell">{fmt(c.native, 4)}<small>{c.coin}</small></div>
          <div className="amt c-stable" role="cell">{fmt(c.stable, 2)}<small>{c.stable_symbol}</small></div>
          <div className="usd" role="cell">{priced || c.stable ? usd(c.usd) : "—"}{st === "partial" && <div className="muted small" style={{ fontFamily: "var(--font)" }}>at least</div>}</div>
        </>
      )}
      {(st === "unavailable" || st === "partial") && (
        <div className="note" role="cell">
          <span className={`state ${st}`}><Icon name={st === "unavailable" ? "xcircle" : "alert"} size={12} />{st === "unavailable" ? "Couldn't read balances" : "Partly read"}</span>
          <span className="muted">{st === "partial" && c.wallets_read != null ? `${c.wallets_read} of ${c.wallets} wallets read; figures cover those only.` : c.error}</span>
          <button className="btn sm ghost" onClick={onRetry} disabled={busy} style={{ height: 26 }}>{busy ? <Spinner /> : <Icon name="refresh" size={12} />}Retry</button>
        </div>
      )}
    </div>
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
