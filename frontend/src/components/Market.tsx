import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { api, errMsg, short } from "../api";
import { chainOf } from "../chains";
import CandleChart, { Candle, fmtPrice, fmtUsdCompact } from "./CandleChart";
import { Address, Callout, Empty, Icon, Seg, Skeleton, Spinner } from "./ui";

/*
 * The market side of a live launch, laid out like a trading terminal: a header strip of pool metrics, a large price
 * chart, and a column of pool activity with Helm's own order ticket underneath.
 *
 * Everything in the strip, chart and activity panel is indicative third-party data (pools and metrics from
 * DEX Screener, candles from GeckoTerminal), fetched through Helm's API, which caches and throttles it. It is kept
 * visually and logically apart from Helm's own figures: nothing here feeds wallet balances, supply transparency,
 * previews, quotes or execution. The order ticket passed in as `ticket` always shows the server's own preview.
 */

type TF = "5m" | "15m" | "1h" | "4h" | "1d";
type Win = "m5" | "h1" | "h6" | "h24";
const TFS: [TF, string][] = [["5m", "5m"], ["15m", "15m"], ["1h", "1H"], ["4h", "4H"], ["1d", "1D"]];
const WINS: [Win, string][] = [["m5", "5m"], ["h1", "1h"], ["h6", "6h"], ["h24", "24h"]];
const REFRESH_MS = 60_000;
const STALE_S = 5 * 60;

interface Pair {
  pair_address: string; dex_id: string | null; url: string | null; labels: string[]; token_side: "base" | "quote";
  base: { address: string; symbol: string }; quote: { address: string; symbol: string };
  price_usd: number | null; price_native: number | null; price_change: Record<string, number | null>;
  volume_usd: Record<string, number | null>; txns: Record<string, { buys: number | null; sells: number | null }>;
  liquidity_usd: number | null; fdv: number | null; market_cap: number | null; pair_created_at: number | null;
}
interface PairsRes {
  state: "ok" | "no_pool" | "no_token" | "unsupported" | "error"; pairs: Pair[]; note: string; source?: string;
  fetched_at?: number | null; stale?: boolean; age_s?: number; error?: string; error_kind?: string; explorer_url?: string | null;
  network?: string; token?: string | null; whose?: "launch" | "source";
}
interface CandlesRes {
  state: "ok" | "no_candles" | "unsupported" | "error"; candles: Candle[]; source?: string; fetched_at?: number | null;
  stale?: boolean; age_s?: number; error?: string; error_kind?: string; reason?: string;
}

export const dexName = (id: string | null) => !id ? "Unknown DEX" : id.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
const at = (s?: number | null) => s ? new Date(s * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—";
const pct = (v: number | null | undefined) => v == null ? null : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
const count = (v: number | null | undefined) => v == null ? "—" : v.toLocaleString();

function Freshness({ source, fetched, stale, age }: { source?: string; fetched?: number | null; stale?: boolean; age?: number }) {
  const old = stale || (age ?? 0) > STALE_S;
  return (
    <span className={`fresh ${old ? "stale" : ""}`}>
      {old && <Icon name="alert" size={12} />}{source ?? "Provider"} · {old ? "stale, " : ""}as of {at(fetched)}
    </span>
  );
}

/** One figure in the header strip. Missing reads "—" with a "Not reported" title, never 0. */
function HeadStat({ label, value, sub, tone, title }: { label: string; value: string | null; sub?: ReactNode; tone?: string; title?: string }) {
  return (
    <div className="th-stat" title={value == null ? "Not reported by the provider" : title}>
      <span className="k">{label}</span>
      <span className={`v num ${value == null ? "na" : tone ?? ""}`}>{value ?? "—"}{value == null && <span className="sr-only"> not reported</span>}</span>
      {sub && <span className="s num">{sub}</span>}
    </div>
  );
}

export default function MarketTerminal({ launchId, network: launchNet, symbol: launchSymbol, ticket }: { launchId: string; network: string; symbol: string; ticket?: ReactNode }) {
  const [res, setRes] = useState<PairsRes | null>(null);
  const [loadErr, setLoadErr] = useState("");
  const [pool, setPool] = useState<string>("");
  const [tf, setTfState] = useState<TF>("15m");
  const tfChosen = useRef(false);
  const setTf = (v: TF) => { tfChosen.current = true; setTfState(v); };
  const [win, setWin] = useState<Win>("h24");
  const [cs, setCs] = useState<CandlesRes | null>(null);
  const [csBusy, setCsBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const loadPairs = useCallback(() => {
    setBusy(true);
    api<PairsRes>(`/launches/${launchId}/market`)
      .then((r) => {
        if (!alive.current) return;
        setRes(r); setLoadErr("");
        setPool((p) => r.pairs.some((x) => x.pair_address === p) ? p : r.pairs[0]?.pair_address ?? "");
        // A pool under a day old has only a handful of 15-minute candles; start it on 5-minute ones instead.
        const born = r.pairs[0]?.pair_created_at;
        if (!tfChosen.current && born && Date.now() - born < 86_400_000) setTfState("5m");
      })
      .catch((e) => alive.current && setLoadErr(errMsg(e)))
      .finally(() => alive.current && setBusy(false));
  }, [launchId]);

  const loadCandles = useCallback(() => {
    if (!pool) return;
    setCsBusy(true);
    api<CandlesRes>(`/launches/${launchId}/market/candles?pool=${encodeURIComponent(pool)}&tf=${tf}`)
      .then((r) => alive.current && setCs(r))
      .catch((e) => alive.current && setCs({ state: "error", candles: [], error: errMsg(e) }))
      .finally(() => alive.current && setCsBusy(false));
  }, [launchId, pool, tf]);

  useEffect(() => { loadPairs(); }, [loadPairs]);
  useEffect(() => { setCs(null); loadCandles(); }, [loadCandles]);
  // Refresh once a minute while the tab is visible; the server caches, so this never hammers the providers.
  useEffect(() => {
    const t = setInterval(() => { if (document.visibilityState === "visible") { loadPairs(); loadCandles(); } }, REFRESH_MS);
    return () => clearInterval(t);
  }, [loadPairs, loadCandles]);

  const p = res?.pairs.find((x) => x.pair_address === pool) ?? null;
  // For a copied draft the server charts the token it was copied from, which may be on another chain.
  const source = res?.whose === "source";
  const network = res?.network ?? launchNet;
  const chain = chainOf(network);
  const symbol = source ? (p?.token_side === "quote" ? p.quote.symbol : p?.base.symbol) ?? launchSymbol : launchSymbol;
  const chartH = typeof window !== "undefined" && window.innerWidth < 640 ? 280 : 380;
  const ours = p?.token_side === "base";
  const refresh = (
    <button className="btn icon sm ghost" onClick={() => { loadPairs(); loadCandles(); }} disabled={busy} aria-label="Refresh market data" title="Refresh market data">
      {busy ? <Spinner /> : <Icon name="refresh" size={13} />}
    </button>
  );

  // Every state keeps the order ticket in reach; only the chart area changes.
  let chartArea: ReactNode;
  if (!res && !loadErr) chartArea = <Skeleton h={380} />;
  else if (!res) chartArea = <Callout tone="err"><b>Market data couldn't be loaded.</b> {loadErr} <button className="linkish" onClick={loadPairs}>Try again</button></Callout>;
  else if (res.state === "unsupported") chartArea = <Empty icon="globe" title={`Market data isn't available for ${chain.name}`} text="DEX Screener doesn't cover this chain, so no pool or price is shown." />;
  else if (res.state === "no_token") chartArea = <Empty icon="rocket" title="No token yet" text="Market data appears after the token is deployed and a pool is indexed." />;
  else if (res.state === "error") chartArea = (
    <Callout tone={res.error_kind === "rate_limited" ? "warn" : "err"}>
      <b>{res.error_kind === "rate_limited" ? "Market data is paused for a moment." : "DEX Screener is unavailable right now."}</b> {res.error}.{" "}
      <button className="linkish" onClick={loadPairs}>Try again</button>
    </Callout>
  );
  else if (res.state === "no_pool" || !p) chartArea = (
    <Empty icon="radar" title="No indexed pool yet"
      text={<>Market data will appear once DEX Screener indexes an eligible pool for this token on {chain.name}. New tokens can take a few minutes.{" "}
        {res.explorer_url && <a href={res.explorer_url} target="_blank" rel="noreferrer">View the token on the explorer<Icon name="ext" size={11} /></a>}</>} />
  );
  else chartArea = !cs ? <Skeleton h={380} />
    : cs.state === "ok" ? (
      <>
        {cs.stale && <p className="small warn-t" style={{ marginBottom: 6 }}><Icon name="alert" size={12} /> Showing the last candles received. {cs.error}</p>}
        <CandleChart candles={cs.candles} height={chartH} daily={tf === "1d"} label={`${symbol} price in USD on ${dexName(p.dex_id)}, ${TFS.find((x) => x[0] === tf)?.[1]} candles`} />
      </>
    ) : cs.state === "no_candles" ? (
      <Empty icon="trend" title="No chart history yet" text={`${cs.reason ?? "GeckoTerminal has no candles for this pool yet"}. Charts appear once it indexes trades; new pools can take a while.`} />
    ) : cs.state === "unsupported" ? (
      <Empty icon="globe" title="Charts aren't available for this chain" text="GeckoTerminal doesn't cover it." />
    ) : (
      <Callout tone={cs.error_kind === "rate_limited" ? "warn" : "err"}>
        <b>{cs.error_kind === "rate_limited" ? "Chart paused briefly." : "The chart couldn't be loaded."}</b> {cs.error}.{" "}
        <button className="linkish" onClick={loadCandles}>Try again</button>
      </Callout>
    );

  const tx = p?.txns?.[win];
  const buys = tx?.buys ?? null, sells = tx?.sells ?? null;
  const total = (buys ?? 0) + (sells ?? 0);

  return (
    <section className="term" aria-label="Market">
      <div className="term-head">
        <div className="th-pool">
          <span className="term-kicker"><Icon name="trend" size={12} />Market · indicative</span>
          {p && res && res.pairs.length > 1 ? (
            <label className="pool-pick">
              <span className="sr-only">Pool</span>
              <select value={pool} onChange={(e) => setPool(e.target.value)}>
                {res.pairs.map((x) => (
                  <option key={x.pair_address} value={x.pair_address}>
                    {dexName(x.dex_id)}{x.labels.length ? ` ${x.labels.join(" ")}` : ""} · {x.base.symbol}/{x.quote.symbol} · liq {fmtUsdCompact(x.liquidity_usd)} · {short(x.pair_address)}
                  </option>
                ))}
              </select>
            </label>
          ) : p ? (
            <b className="th-poolname">{dexName(p.dex_id)}{p.labels.length ? ` ${p.labels.join(" ")}` : ""} · {p.base.symbol}/{p.quote.symbol}</b>
          ) : <b className="th-poolname muted">No pool yet</b>}
        </div>
        {p ? (
          <div className="th-stats">
            <HeadStat label="Market cap" value={p.market_cap == null ? null : fmtUsdCompact(p.market_cap)} title="As reported by DEX Screener; not derived from FDV" />
            <HeadStat label="FDV" value={p.fdv == null ? null : fmtUsdCompact(p.fdv)} title="Fully diluted valuation, as reported by DEX Screener" />
            <HeadStat label="Price" value={p.price_usd == null ? null : `$${fmtPrice(p.price_usd)}`}
              sub={p.price_native != null ? `${fmtPrice(p.price_native)} ${p.quote.symbol}` : null} />
            <HeadStat label="24h change" value={pct(p.price_change.h24)} tone={p.price_change.h24 == null ? "" : p.price_change.h24 >= 0 ? "good" : "bad"} />
            <HeadStat label="Liquidity" value={p.liquidity_usd == null ? null : fmtUsdCompact(p.liquidity_usd)} />
            <HeadStat label="24h pool vol" value={p.volume_usd.h24 == null ? null : fmtUsdCompact(p.volume_usd.h24)} />
          </div>
        ) : <div className="th-stats" />}
        <div className="th-src">
          {res?.fetched_at ? <Freshness source={res.source} fetched={res.fetched_at} stale={res.stale} age={res.age_s} /> : null}
          {refresh}
        </div>
      </div>

      {source && res?.token && (
        <Callout icon="copy">
          <b>This is the token you copied</b> ({chain.name} · <Address address={res.token} network={network} type="token" chars={6} />). Your relaunch isn't deployed yet;
          once it is, this terminal switches to your own token's market.
        </Callout>
      )}
      {res?.stale && <Callout tone="warn"><b>Showing the last market data received.</b> {res.error}</Callout>}
      {p && !ours && (
        <Callout icon="info">This pool lists ${symbol} as its quote asset. DEX Screener's price, change, FDV and market cap describe {p.base.symbol} there, so they're not shown. Pool liquidity, volume and trade counts still apply.</Callout>
      )}

      <div className="term-main">
        <div className="term-chart">
          <div className="chart-head">
            <Seg value={tf} onChange={setTf} options={TFS} />
            <span className="row nowrap small" style={{ gap: 8 }}>
              {csBusy && <Spinner />}
              {cs?.fetched_at ? <Freshness source="Candles: GeckoTerminal" fetched={cs.fetched_at} stale={cs.stale} age={cs.age_s} /> : null}
            </span>
          </div>
          <div className="chart-box" aria-busy={csBusy}>{chartArea}</div>
          {p && (
            <dl className="pool-facts">
              <div><dt>DEX</dt><dd>{dexName(p.dex_id)}{p.labels.length ? <span className="badge" style={{ marginLeft: 6 }}>{p.labels.join(" ")}</span> : null}</dd></div>
              <div><dt>Pair address</dt><dd><Address address={p.pair_address} network={network} chars={5} /></dd></div>
              <div>{ours
                ? <><dt>Quote asset</dt><dd>{p.quote.symbol}</dd></>
                : <><dt>Paired with</dt><dd>{p.base.symbol} <span className="muted small">(pool's base)</span></dd></>}</div>
              <div><dt>Pool page</dt><dd>{p.url ? <a href={p.url} target="_blank" rel="noreferrer">DEX Screener<Icon name="ext" size={11} /></a> : <span className="na">Not reported</span>}</dd></div>
            </dl>
          )}
        </div>

        <div className="term-side">
          {p && (
            <div className="term-panel" role="group" aria-label="Pool activity">
              <div className="tp-head"><b>Pool activity</b><span className="muted small">DEX Screener · indicative</span></div>
              <div className="win-tabs" role="group" aria-label="Time window">
                {WINS.map(([k, l]) => {
                  const c = ours ? p.price_change[k] : null;
                  return (
                    <button key={k} type="button" aria-pressed={win === k} className={win === k ? "on" : ""} onClick={() => setWin(k)}>
                      <span>{l}</span>
                      <b className={`num ${c == null ? "na" : c >= 0 ? "good" : "bad"}`}>{c == null ? "—" : pct(c)}</b>
                    </button>
                  );
                })}
              </div>
              <div className="tp-rows">
                <div className="tp-row"><span className="k">Trades</span><span className="v num">{tx ? count(total) : <span className="na">Not reported</span>}</span></div>
                <div className="tp-split">
                  <div className="row between small"><span>Buys <b className="num good">{count(buys)}</b></span><span>Sells <b className="num bad">{count(sells)}</b></span></div>
                  <div className="split-bar" aria-hidden="true">
                    {total > 0 && <><i className="b" style={{ width: `${((buys ?? 0) / total) * 100}%` }} /><i className="s" style={{ width: `${((sells ?? 0) / total) * 100}%` }} /></>}
                  </div>
                </div>
                <div className="tp-row"><span className="k">Pool volume</span><span className="v num">{p.volume_usd[win] == null ? <span className="na">Not reported</span> : fmtUsdCompact(p.volume_usd[win])}</span></div>
              </div>
            </div>
          )}
          {ticket}
        </div>
      </div>

      <p className="market-note">
        Market data: pools and metrics from <a href="https://dexscreener.com" target="_blank" rel="noreferrer">DEX Screener</a>, candles from{" "}
        <a href="https://www.geckoterminal.com" target="_blank" rel="noreferrer">GeckoTerminal</a>. Indicative only; it can lag or differ from what a trade
        gets and never feeds Helm's previews, quotes or execution.
      </p>
    </section>
  );
}
