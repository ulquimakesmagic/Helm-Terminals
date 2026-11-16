import { ReactNode, useEffect, useRef, useState } from "react";
import { api, errMsg } from "../api";
import { chainOf } from "../chains";
import CandleChart, { Bar, fmtPrice } from "./CandleChart";
import GmgnChart from "./GmgnChart";
import { CHART_INTERVALS, ChartInterval, gmgnChartUrl } from "../gmgn";
import { Address, Callout, Empty, Icon, Seg, Skeleton, Spinner } from "./ui";

type Win = "m5" | "h1" | "h4" | "h12" | "h24";
const WINS: [Win, string][] = [["m5", "5m"], ["h1", "1h"], ["h4", "4h"], ["h12", "12h"], ["h24", "24h"]];
type Res = "1" | "5" | "15" | "60" | "240" | "1D";
const RES: [Res, string][] = [["1", "1m"], ["5", "5m"], ["15", "15m"], ["60", "1H"], ["240", "4H"], ["1D", "1D"]];

interface Pool { address: string; dex: string | null; quote_symbol: string | null; quote_address: string | null; liquidity_usd: number | null; volume_usd_24h?: number | null }
interface Metrics {
  price_usd: number | null; market_cap_usd: number | null; fdv_usd: number | null; liquidity_usd: number | null;
  holder_count: number | null; created_at?: string | null; last_trade_at?: string | null;
  price_change: Record<Win, number | null>; volume_usd: Record<Win, number | null>;
  txns: Record<Win, { buys: number | null; sells: number | null; swaps: number | null }>;
}
interface MarketRes {
  state: "ok" | "no_pool" | "not_indexed" | "no_token" | "unsupported" | "not_configured" | "error";
  network: string; token: string | null; whose: "launch" | "source"; symbol?: string | null; name?: string | null;
  provider?: string; fetched_at?: number; stale?: boolean; error?: string; error_kind?: string; retry_after_s?: number;
  explorer_url?: string; pool: Pool | null; pairs?: Pool[]; metrics: Metrics | null; flags?: { is_scam?: boolean };
  chart?: { available: boolean; resolutions: string[] };
}
interface CandleRes { state: string; bars: Bar[]; bar_seconds?: number; error?: string; error_kind?: string; retry_after_s?: number; fetched_at?: number; stale?: boolean; last_bar_at?: number | null; scope?: string }

const usd = (v: number | null | undefined) => v == null ? null : `$${v.toLocaleString(undefined, { notation: v >= 100000 ? "compact" : "standard", maximumSignificantDigits: 5 })}`;
const count = (v: number | null | undefined) => v == null ? null : v.toLocaleString();
const pct = (v: number | null | undefined) => v == null ? null : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
const ago = (s: number) => s < 60 ? `${Math.round(s)}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`;

function HeadStat({ label, value, tone, title }: { label: string; value: string | null; tone?: string; title?: string }) {
  return <div className="th-stat" title={value == null ? "Not reported by Codex for this token" : title}>
    <span className="k">{label}</span><span className={`v num ${value == null ? "na" : tone ?? ""}`}>{value ?? "—"}{value == null && <span className="sr-only"> not reported</span>}</span>
  </div>;
}

function usePoll<T>(path: string | null, intervalMs: number, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [rev, setRev] = useState(0);
  const nextAt = useRef(0);
  useEffect(() => {
    setData(null); setError(""); nextAt.current = 0;
    if (!path) return;
    let active = true, inFlight = false;
    const load = async () => {
      if (inFlight || Date.now() < nextAt.current) return;
      inFlight = true; setBusy(true);
      try {
        const d = await api<any>(path);
        if (!active) return;
        setData(d); setError("");
        nextAt.current = d?.retry_after_s ? Date.now() + d.retry_after_s * 1000 : 0;
      } catch (e) { if (active) setError(errMsg(e)); }
      finally { inFlight = false; if (active) setBusy(false); }
    };
    void load();
    const t = setInterval(() => { if (document.visibilityState === "visible") void load(); }, intervalMs);
    return () => { active = false; clearInterval(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, rev, ...deps]);
  return { data, error, busy, refresh: () => setRev((r) => r + 1) };
}

/** Indicative market data from Codex, kept separate from balances, quotes and transaction history. The chart is GMGN's
 *  embed on the chains GMGN covers (Solana, Ethereum, BNB Chain) and Codex candles everywhere else. */
export default function MarketTerminal({ launchId, network, token, symbol, ticket, mobileView = "chart" }: {
  launchId: string; network: string; token: string | null; symbol: string; ticket?: ReactNode; mobileView?: string;
}) {
  const [win, setWin] = useState<Win>("h24");
  const [res, setRes] = useState<Res>("15");
  const [gInt, setGInt] = useState<ChartInterval>("15");
  const [chartSource, setChartSource] = useState<"gmgn" | "codex">("gmgn");
  const [pool, setPool] = useState("");
  const [allStats, setAllStats] = useState(false);
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 5000); return () => clearInterval(t); }, []);
  useEffect(() => setPool(""), [launchId, network, token]);
  const m = usePoll<MarketRes>(`/launches/${launchId}/market`, 30000, [network, token]);
  const embedUrl = gmgnChartUrl(m.data?.network ?? network, m.data?.token ?? token, gInt);
  const gmgnUrl = chartSource === "gmgn" ? embedUrl : null;
  const chartOn = !gmgnUrl && !!m.data?.chart?.available && ["ok", "no_pool"].includes(m.data.state);
  const c = usePoll<CandleRes>(chartOn ? `/launches/${launchId}/market/candles?resolution=${res}${pool ? `&pool=${pool}` : ""}` : null,
    res === "1" || res === "5" ? 20000 : 60000, [chartOn]);

  const r = m.data;
  const ageS = r?.fetched_at ? now / 1000 - r.fetched_at : 0;
  const expired = ageS > 300;
  const stale = !!r?.stale || !!m.error || ageS > 90;
  const metrics = expired ? null : r?.metrics ?? null;
  const pools = r?.pairs ?? [];
  const shownPool = pools.find((p) => p.address === pool) ?? r?.pool ?? null;
  const change = metrics?.price_change.h24;
  const tx = metrics?.txns[win];
  const buys = tx?.buys, sells = tx?.sells;
  const split = buys != null && sells != null && buys + sells > 0 ? buys / (buys + sells) : null;
  const net = chainOf(r?.network ?? network).name;

  let notice: ReactNode = null;
  if (m.error) notice = <Callout tone="warn"><b>Market refresh failed.</b> {m.error}{r?.fetched_at ? " The last response is shown and marked stale." : ""}</Callout>;
  else if (r?.state === "not_configured") notice = <Callout icon="info"><b>Market data isn't configured on this server.</b> The operator needs to add a Codex API key. Wallets and trading are unaffected.</Callout>;
  else if (r?.state === "unsupported") notice = <Callout icon="globe">Codex doesn't cover {net}, so Helm shows no market data here. Wallets and Helm trading remain available where supported.</Callout>;
  else if (r?.state === "not_indexed") notice = <Callout icon="radar"><b>Codex hasn't indexed this token yet.</b> New tokens usually appear after their first trades.{r.explorer_url && <> <a href={r.explorer_url} target="_blank" rel="noreferrer">View token on explorer</a></>}</Callout>;
  else if (r?.state === "no_pool") notice = <Callout icon="radar"><b>No pool indexed yet.</b> Codex knows this token but lists no trading pool for it.</Callout>;
  else if (r?.state === "error" || r?.stale) notice = <Callout tone="warn"><b>{r.stale ? "Showing previously fetched Codex data." : "Market data is unavailable right now."}</b> {r.error}</Callout>;

  return <section className={`term terminal-view-${mobileView}`} aria-label="Indicative market data">
    <div className="term-head">
      <div className="th-pool"><span className="term-kicker"><Icon name="trend" size={12} />Codex · indicative market data</span>
        <b className="th-poolname">{r?.symbol ?? symbol} <span className="muted">/ USD · {net}</span></b>
        <button className="btn sm ghost mobile-stats-toggle" type="button" aria-expanded={allStats} onClick={() => setAllStats(!allStats)}>{allStats ? "Fewer stats" : "More stats"}</button>
      </div>
      <div className={`th-stats ${allStats ? "stats-expanded" : ""}`} role="region" aria-label="Market statistics">
        <HeadStat label="Price" value={metrics?.price_usd != null ? `$${fmtPrice(metrics.price_usd)}` : null} title="Codex price in US dollars" />
        <HeadStat label="24h change" value={pct(change)} tone={change != null && change >= 0 ? "good" : "bad"} title="Token price change over 24 hours (Codex)" />
        <HeadStat label="Mkt cap" value={usd(metrics?.market_cap_usd)} title="Circulating market cap: price × circulating supply, as reported by Codex" />
        <HeadStat label="FDV" value={usd(metrics?.fdv_usd)} title="Fully diluted valuation: price × total supply, as reported by Codex" />
        <HeadStat label="Liquidity" value={usd(metrics?.liquidity_usd)} title="Liquidity across the token's indexed pools (Codex)" />
        <HeadStat label="24h volume" value={usd(metrics?.volume_usd.h24)} title="Token-wide volume across indexed pools (Codex)" />
        <HeadStat label="Holders" value={count(metrics?.holder_count)} title="Holder count reported by Codex" />
      </div>
      <div className="th-src">
        <span className={`fresh ${stale || expired ? "stale" : ""}`} role="status">
          {r?.fetched_at ? `${expired ? "Expired · " : stale ? "Stale · " : ""}Codex · updated ${ago(ageS)}` : r ? "Codex" : "Loading market data…"}
        </span>
        <button className="btn sm ghost" onClick={() => { m.refresh(); c.refresh(); }} disabled={m.busy} aria-label="Refresh market data">
          {m.busy ? <Spinner /> : <Icon name="refresh" size={13} />}Refresh
        </button>
      </div>
    </div>
    <div className="market-notices">
      {r?.whose === "source" && <Callout icon="copy">You are viewing the copied token's market. After deployment, this workspace switches to your launch's token.</Callout>}
      {r?.flags?.is_scam && <Callout tone="warn"><b>Codex flags this token as a likely scam.</b> Check the contract before trading.</Callout>}
      {notice}
      {expired && <Callout tone="warn">The last market figures are over five minutes old and are hidden. Refresh to check for new data.</Callout>}
    </div>
    <div className="term-main">
      <div className="term-analysis">
        <section className="term-chart" aria-label="Price chart">
          <div className="chart-head">
            <div className="row small"><span className="term-kicker"><Icon name="trend" size={13} />{gmgnUrl ? "Chart · GMGN" : "Price · USD"}</span>
              <span className="badge">{gmgnUrl ? "Token view" : pool && shownPool ? `${shownPool.dex ?? "Pool"} pool` : "All pools"}</span></div>
            {embedUrl && <select className="chart-source" aria-label="Chart source" value={chartSource} onChange={event=>setChartSource(event.target.value as "gmgn" | "codex")}><option value="gmgn">GMGN chart</option><option value="codex">Codex chart</option></select>}
            {gmgnUrl ? <Seg value={gInt} onChange={setGInt} options={CHART_INTERVALS} className="res-seg" />
              : <Seg value={res} onChange={setRes} options={RES} className="res-seg" />}
          </div>
          {!(r?.token ?? token) ? <div className="chart-unavailable"><Empty icon="trend" title="No token yet" text="The chart appears once this launch has a token address and trades are indexed." /></div>
            : gmgnUrl ? <GmgnChart url={gmgnUrl} symbol={r?.symbol ?? symbol} onUseCodex={()=>setChartSource("codex")} />
            : !r && !m.error ? <Skeleton h={380} />
            : !chartOn ? <div className="chart-unavailable"><Empty icon="globe" title="Chart unavailable" text={r?.state === "unsupported" ? `Codex doesn't cover ${net}.` : r?.state === "not_configured" ? "Market data isn't configured on this server." : "No candles are available for this token yet."} />{r?.explorer_url && <a href={r.explorer_url} target="_blank" rel="noreferrer">View token on explorer <Icon name="ext" size={12} /></a>}</div>
            : !c.data && !c.error ? <Skeleton h={380} />
            : c.data?.bars?.length ? <CandleChart bars={c.data.bars} barSeconds={c.data.bar_seconds ?? 900} label={`${r?.symbol ?? symbol} price in US dollars, ${RES.find(([k]) => k === res)?.[1]} candles from Codex`} />
            : <div className="chart-unavailable"><Empty icon="activity" title={c.data?.state === "error" ? "Chart data unavailable" : "No trades in this range"}
                text={c.data?.state === "error" ? c.data.error : "Codex returned no candles for this timeframe. Try a longer timeframe; candles appear after trades."} /></div>}
          {c.data?.stale && <Callout tone="warn">Showing previously fetched candles; Codex could not be reached just now.</Callout>}
          {gmgnUrl ? <p className="market-note">The chart is GMGN's and loads from GMGN in your browser. GMGN picks the pool and controls its updates, so it can differ from the Codex figures on this page; Helm can't check what the embedded chart shows.</p>
            : <p className="market-note">Candles are Codex OHLCV data in US dollars{pool ? " for the selected pool" : ", aggregated across the token's indexed pools"}. Bars appear only where trades happened; Helm never fills gaps. Times are in your local time zone.</p>}
        </section>
        <div className="term-panel market-pool" aria-label="Pools">
          <div className="tp-head"><b>Pools</b><span className="muted small">Codex · by liquidity</span></div>
          {!r && !m.error ? <Skeleton h={90} /> : pools.length ? <>
            {pools.length > 1 && !gmgnUrl && <label className="field pool-pick">Chart<select value={pool} onChange={(e) => setPool(e.target.value)}>
              <option value="">All pools (token)</option>
              {pools.map((p) => <option key={p.address} value={p.address}>{p.dex ?? "DEX"} · {p.quote_symbol ?? "Quote"} · {p.address.slice(0, 6)}…</option>)}
            </select></label>}
            {shownPool && <dl className="pool-facts">
              <div><dt>DEX</dt><dd>{shownPool.dex ?? "Not reported"}</dd></div>
              <div><dt>Pool</dt><dd><Address address={shownPool.address} network={r?.network ?? network} chars={5} /></dd></div>
              <div><dt>Quote asset</dt><dd>{shownPool.quote_symbol ?? "Not reported"}</dd></div>
              <div><dt>Pool liquidity</dt><dd className="num">{usd(shownPool.liquidity_usd) ?? "Not reported"}</dd></div>
            </dl>}
          </> : <p className="pool-placeholder muted small">{r?.state === "ok" ? "Pool details unavailable." : "No indexed pool yet."}</p>}
        </div>
      </div>
      <div className="term-side">
        {ticket}
        <div className="term-panel market-activity" role="group" aria-label="Token activity">
          <div className="tp-head"><b>Token activity</b><span className="muted small">Codex · all pools</span></div>
          <div className="win-tabs" role="group" aria-label="Activity time window">
            {WINS.map(([key, label]) => <button key={key} type="button" aria-pressed={win === key} className={win === key ? "on" : ""} onClick={() => setWin(key)}>
              <span>{label}</span><b className={`num ${metrics?.price_change[key] == null ? "na" : metrics.price_change[key]! >= 0 ? "good" : "bad"}`}>{pct(metrics?.price_change[key]) ?? "—"}</b>
            </button>)}
          </div>
          {metrics ? <div className="tp-rows">
            <div className="tp-row"><span className="k">Trades</span><span className="v num">{count(tx?.swaps) ?? "—"}</span></div>
            <div className="tp-split"><div className="row between small"><span>Buys <b className="num good">{count(buys) ?? "—"}</b></span><span>Sells <b className="num bad">{count(sells) ?? "—"}</b></span></div>
              <div className="split-bar" aria-hidden="true">{split != null && <><i className="b" style={{ width: `${split * 100}%` }} /><i className="s" style={{ width: `${(1 - split) * 100}%` }} /></>}</div>
            </div>
            <div className="tp-row"><span className="k">Volume</span><span className="v num">{usd(metrics.volume_usd[win]) ?? "—"}</span></div>
            {metrics.last_trade_at && <div className="tp-row"><span className="k">Last trade</span><span className="v">{new Date(metrics.last_trade_at).toLocaleString()}</span></div>}
          </div> : <Empty icon="activity" title="Activity unavailable" text="Counts appear once Codex reports market data for this token." />}
        </div>
      </div>
    </div>
    <p className="market-note">Market data from <a href="https://www.codex.io" target="_blank" rel="noreferrer">Codex</a>; charts from <a href="https://gmgn.ai" target="_blank" rel="noreferrer">GMGN</a> on Solana, Ethereum and BNB Chain, and from Codex on other chains. Indicative only: never used for wallet balances, trade previews, quotes or execution, which come from each chain and the launchpad or router that builds the transaction.</p>
  </section>;
}
