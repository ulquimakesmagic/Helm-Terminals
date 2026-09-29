import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errMsg, fmt, usd } from "../api";
import AreaChart from "../components/Chart";
import ReferralCard from "../components/Referrals";
import SpendDialog from "../components/SpendDialog";
import type { SpendRequest } from "../components/Distribute";
import { ALL_CHAIN_ORDER, chainOf, Pad, padName } from "../chains";
import { coverage } from "../components/Launchpads";
import { ChainMark, PadLabel, PadMark } from "../components/marks";
import { ActivityRow, FeedItem, groupRepeats, isRoutine } from "../components/feed";
import {
  Callout, Card, Empty, Icon, IconName, LoadError, Loading, Metrics, parseDate, relTime, Seg, Skeleton, Spinner, StatusBadge, TableWrap, TokenImage,
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
      key: `a${a.id}`, icon: "calendar", tone: "bad", title: `Plan on $${a.launch_symbol ?? "token"} stopped`, text: a.error, to: `/launches/${a.launch_id}?tab=automation`, cta: "View",
    })),
    ...(plans.data ?? []).filter((a) => a.status === "paused").slice(0, 3).map((a): NextAction => ({
      key: `p${a.id}`, icon: "calendar", tone: "warn", title: `Plan on $${a.launch_symbol ?? "token"} is paused`, text: `${a.runs_completed} of ${a.total_orders} orders done. Resume or cancel the rest.`, to: `/launches/${a.launch_id}?tab=automation`, cta: "Review",
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
  const total = folio?.totals?.usd;
  const funded = chains.filter((c) => c.usd > 0);
  const incomplete = chains.filter((c) => ["partial", "unavailable"].includes(statusOf(c)));
  const unpriced = folio?.unpriced_chains?.length ?? 0;
  const totalComplete = folio?.complete !== false && incomplete.length === 0 && !unpriced;
  const stableComplete = folio?.stable_complete ?? incomplete.length === 0;
  const nativeComplete = folio?.native_complete ?? (incomplete.length === 0 && folio?.priced);
  const checkedAt = folio?.checked_at ? parseDate(folio.checked_at) : null;
  const stale = !!checkedAt && Date.now() - checkedAt.getTime() > STALE_MS;
  const noWallets = !!wallets.data && managed.length === 0;
  const recent = groupRepeats((feed.data ?? []).filter((r) => !isRoutine(r))).slice(0, 6);

  return (
    <>
      <h1 className="sr-only">Dashboard</h1>
      <section className="command" aria-label="Portfolio summary">
        <div style={{ minWidth: 0 }}>
          <div className="row" style={{ gap: 10 }}>
            <span className="eyebrow">Portfolio · managed wallets</span>
            {folio && !folioErr && folio.wallets > 0 && (!totalComplete ? (
              <span className="state partial"><Icon name="alert" size={12} />{incomplete.length
                ? `Incomplete · ${incomplete.length} chain${incomplete.length === 1 ? "" : "s"} not fully read`
                : "Incomplete · some coin prices unavailable"}</span>
            ) : <span className="state none"><Icon name="checkcircle" size={12} />Tracked balances read</span>)}
          </div>
          {folio ? (
            folio.wallets === 0 ? <div className="figure figure-text muted">No wallets yet</div>
              : <div className="figure" aria-live="polite">{total == null ? "Unavailable" : usd(total)}{total != null && !totalComplete && <small>at least</small>}</div>
          ) : folioErr ? <div className="figure figure-text muted">Unavailable</div> : <Skeleton h={34} w={220} style={{ marginTop: 10 }} />}
          <p className="sub">
            {folioErr ? <>Balances couldn't be read: {folioErr}</>
              : folio && folio.wallets === 0 ? <>Create a managed wallet on any chain, fund it, and its balances show up here.</>
              : folio ? <>{folio.wallets} managed wallet{folio.wallets === 1 ? "" : "s"} · {totalComplete ? "Tracked assets valued below" : "Known balances only; unread amounts are excluded"}</>
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
        </div>
      </section>
      {error && <Callout tone="err">Dashboard figures couldn't be loaded: {error}</Callout>}

      <Metrics label="Key figures" items={[
        { label: "Tracked stablecoins", value: folio?.wallets > 0 ? <PortfolioValue value={folio.totals.stable_usd} complete={stableComplete} /> : folioErr ? "Unavailable" : "—", tone: folio && !stableComplete ? "warn" : undefined, hint: folio?.wallets === 0 ? "no wallets yet" : "USDC / USDG · valued at $1" },
        { label: "Network coins", value: folio?.wallets > 0 ? <PortfolioValue value={folio.totals.native_usd} complete={nativeComplete} /> : folioErr ? "Unavailable" : "—", tone: folio && !nativeComplete ? "warn" : undefined, hint: folio?.wallets === 0 ? "no wallets yet" : "Priced native balances only" },
        { label: "Launches", value: launches.data ? String(launches.data.length) : launches.error ? "Unavailable" : "—", hint: launches.data ? `${live.length} live` : " " },
        { label: "Managed wallets", value: wallets.data ? String(managed.length) : wallets.error ? "Unavailable" : "—", hint: wallets.data ? `${wallets.data.length - managed.length} connected / watch-only excluded` : " " },
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
            foot={folio && <>{folio.note}{!totalComplete && " Totals only include amounts that could be read and valued."}</>} flush>
            {folioErr ? <LoadError what="Balances" error={folioErr} onRetry={() => loadFolio(true)} busy={folioBusy} /> : !folio ? <Loading rows={5} />
              : folio.wallets === 0 ? <Empty icon="wallet" title="No managed wallets yet" text="Balances appear per chain once you create and fund a wallet. Use Create wallets above." /> : (
              <>
                {total > 0 && (
                  <div className="stack tight" style={{ padding: "14px 16px", borderBottom: "1px solid var(--line)" }}>
                    <div className="alloc" role="img" aria-label={`Allocation of balances that were read: ${funded.map((c) => `${c.name} ${fmt((c.usd / total) * 100, 1)}%`).join(", ")}`}>
                      {funded.map((c) => <span key={c.network} style={{ width: `${(c.usd / total) * 100}%`, background: chainOf(c.network).color }} />)}
                    </div>
                    <div className="legend" aria-hidden="true">
                      {funded.map((c) => <span key={c.network}><i style={{ background: chainOf(c.network).color }} />{c.name} <b className="num">{fmt((c.usd / total) * 100, 1)}%</b></span>)}
                    </div>
                  </div>
                )}
                <div className="chain-rows" role="table" aria-label="Balances by chain">
                  <div className="chain-row head" role="row">
                    <span role="columnheader"><span className="sr-only">Mark</span></span><span role="columnheader">Chain</span>
                    <span role="columnheader" style={{ textAlign: "right" }}>Coin</span><span role="columnheader" style={{ textAlign: "right" }}>Stablecoin</span>
                    <span role="columnheader" style={{ textAlign: "right" }}>Value</span>
                  </div>
                  {chains.map((c) => <ChainRow key={c.network} c={c} launches={data?.by_chain?.[c.network]?.launches ?? 0}
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
                <TableWrap><table className="rtable">
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
                </table></TableWrap>
              )}
          </Card>

          <Card title="Net flow" icon="trend" sub="Value in and out of your wallets from confirmed transactions, in USD, fees included"
            actions={<Seg value={period} onChange={setPeriod} options={PERIODS} />}
            foot={<>{data?.note}{data && data.usd_priced === false && " Some days couldn't be priced and count as $0."}</>}>
            {error && !data ? <LoadError what="Net flow" error={error} onRetry={load} /> : !data ? <Skeleton h={220} /> : data.series.length === 0 ? (
              <Empty icon="trend" title="No confirmed transactions in this period" text="Deploys, trades and transfers show up here once confirmed." />
            ) : <AreaChart points={data.series.map((p: any) => ({ date: p.date, total: p.cumulative_usd ?? 0, day: p.daily_usd ?? 0 }))} height={220} />}
          </Card>
        </div>

        <div className="col">
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
                {ALL_CHAIN_ORDER.map((c) => {
                  const list = pads.filter((p) => p.network === c);
                  if (!list.length) return null;
                  return (
                    <div key={c} className="stack tight">
                      <div className="row" style={{ gap: 8 }}><ChainMark network={c} size={18} /><b className="small-strong">{chainOf(c).name}</b>
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

function PortfolioValue({ value, complete }: { value: number | null; complete: boolean }) {
  return value == null ? <>Unavailable</> : <>{usd(value)}{!complete && <small>partial</small>}</>;
}

/** Keep provider diagnostics out of the main balance table, including with an older API response. */
function noteText(c: any, st: ChainStatus): string {
  if (c.message) return c.message;
  return st === "partial" ? "Some balances are unavailable. Amounts shown cover successful reads only."
    : "The balance provider is temporarily unavailable. Try again shortly.";
}

/** One chain's balances. Unread data is never shown as a number: unavailable chains say so and offer a retry. */
function ChainRow({ c, launches, onRetry, busy }: { c: any; launches: number; onRetry: () => void; busy: boolean }) {
  const st = statusOf(c);
  const meta = `${c.wallets} wallet${c.wallets === 1 ? "" : "s"}${launches ? ` · ${launches} launch${launches === 1 ? "" : "es"}` : ""}`;
  const cls = st === "unavailable" ? "unavail" : st === "partial" ? "partial" : st === "no_wallets" ? "empty-row" : "";
  return (
    <div className={`chain-row ${cls}`} role="row">
      <ChainMark network={c.network} size={24} />
      <div style={{ minWidth: 0 }} role="cell">
        <div style={{ fontWeight: 600 }}>{c.name}</div>
        <div className="muted small">{st === "no_wallets" ? "No wallets on this chain" : meta}</div>
        {c.wallets > 0 && c.scope && <div className="muted small portfolio-scope">{c.scope}</div>}
      </div>
      {st === "unavailable" ? (
        <>
          <div className="amt c-native" role="cell"><span className="unknown">not read</span></div>
          <div className="amt c-stable" role="cell"><span className="unknown">{c.stable_tracked === false ? "Not tracked" : "not read"}</span></div>
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
          <div className="amt c-native" role="cell">{c.native_read === 0 ? <span className="unknown">not read</span> : <>{fmt(c.native, 4)}<small>{c.coin}</small>{c.native_read < c.wallets && <span className="balance-partial">partial</span>}</>}</div>
          <div className="amt c-stable" role="cell">{c.native_is_stable ? <span className="unknown">Included in coin</span> : c.stable_tracked === false ? <span className="unknown">Not tracked</span> : c.stable_read === 0 ? <span className="unknown">not read</span> : <>{fmt(c.stable, 2)}<small>{c.stable_symbol}</small>{c.stable_read < c.wallets && <span className="balance-partial">partial</span>}</>}</div>
          <div className="usd" role="cell">{c.usd == null ? <span className="unknown">unpriced</span> : usd(c.usd)}{c.usd != null && (c.value_complete === false || st === "partial") && <div className="muted small" style={{ fontFamily: "var(--font)" }}>at least</div>}</div>
        </>
      )}
      {(st === "unavailable" || st === "partial") && (
        <div className="note" role="cell">
          <span className={`state ${st}`}><Icon name={st === "unavailable" ? "xcircle" : "alert"} size={12} />{st === "unavailable" ? "Couldn't read balances" : "Partly read"}</span>
          <span className="muted">{noteText(c, st)}</span>
          <button className="btn sm ghost" onClick={onRetry} disabled={busy} style={{ height: 26 }}>{busy ? <Spinner /> : <Icon name="refresh" size={12} />}Retry</button>
        </div>
      )}
      {c.wallets > 0 && c.price_complete === false && <div className="note muted" role="cell">{c.coin} price unavailable; unpriced amounts are excluded from the total.</div>}
    </div>
  );
}

