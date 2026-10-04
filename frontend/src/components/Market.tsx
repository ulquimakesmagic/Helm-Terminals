import { ReactNode, useEffect, useRef, useState } from "react";
import { api, errMsg } from "../api";
import { chainOf } from "../chains";
import GmgnChart from "./GmgnChart";
import { Address, Callout, Empty, Icon, Skeleton, Spinner } from "./ui";

type Win = "m5" | "h1" | "h6" | "h24";
const WINS: [Win, string][] = [["m5", "5m"], ["h1", "1h"], ["h6", "6h"], ["h24", "24h"]];
interface MarketRes {
  state: "ok" | "no_pool" | "no_token" | "unsupported" | "not_configured" | "error";
  network: string; token: string | null; whose: "launch" | "source"; symbol?: string; source?: string | null;
  fetched_at?: number; stale?: boolean; error?: string; error_kind?: string; retry_after_s?: number; explorer_url?: string; gmgn_url?: string;
  metric_scope?: "pair" | "token";
  pairs?: { pool: MarketRes["pool"]; metrics: MarketRes["metrics"] }[];
  pool: { address: string; dex: string | null; quote_address: string | null; quote_symbol: string | null; liquidity_usd: number | null; market_url?: string; orientation?: string } | null;
  metrics: { price_usd: number | null; price_native?: number | null; holder_count: number | null; market_cap_usd: number | null; fdv_usd: number | null;
    liquidity_usd: number | null; price_change: Record<Win, number | null>;
    volume_usd: Record<Win, number | null>; txns: Record<Win, { buys: number | null; sells: number | null; swaps: number | null }> } | null;
}
const usd = (v: number | null | undefined) => v == null ? null : `$${v.toLocaleString(undefined, { notation: v >= 100000 ? "compact" : "standard", maximumSignificantDigits: 5 })}`;
const count = (v: number | null | undefined) => v == null ? null : v.toLocaleString();
const pct = (v: number | null | undefined) => v == null ? null : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
const dexName = (id: string | null) => id?.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) ?? "DEX not reported";

function HeadStat({ label, value, tone, title }: { label: string; value: string | null; tone?: string; title?: string }) {
  return <div className="th-stat" title={value == null ? "Not reported by this data source" : title}>
    <span className="k">{label}</span><span className={`v num ${value == null ? "na" : tone ?? ""}`}>{value ?? "—"}{value == null && <span className="sr-only"> not reported</span>}</span>
  </div>;
}

/** Indicative provider data is separate from balances, quotes and transaction history. */
export default function MarketTerminal({ launchId, network, token, symbol, ticket, mobileView = "chart" }: {
  launchId: string; network: string; token: string | null; symbol: string; ticket?: ReactNode; mobileView?: string;
}) {
  const [res, setRes] = useState<MarketRes | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [retryAt, setRetryAt] = useState(0);
  const nextRequestAt = useRef(0);
  const [win, setWin] = useState<Win>("h24");
  const [selectedPair, setSelectedPair] = useState("");
  useEffect(() => {
    nextRequestAt.current = 0; setRetryAt(0); setRes(null); setError(""); setSelectedPair("");
  }, [launchId, network, token]);
  useEffect(() => {
    let active = true, inFlight = false, pollable = true;
    const load = async () => {
      if (inFlight || Date.now() < nextRequestAt.current) return;
      inFlight = true; setBusy(true);
      try {
        const data = await api<MarketRes>(`/launches/${launchId}/market`);
        if (!active) return;
        setRes(data); setError(""); setNow(Date.now());
        const delay = Number.isFinite(data.retry_after_s) ? Math.max(0, data.retry_after_s ?? 0) : 0;
        nextRequestAt.current = delay ? Date.now() + delay * 1000 : 0;
        setRetryAt(nextRequestAt.current);
        pollable = !["unsupported", "no_token", "not_configured"].includes(data.state);
      } catch (e) { if (active) setError(errMsg(e)); }
      finally { inFlight = false; if (active) setBusy(false); }
    };
    void load();
    const poll = setInterval(() => { if (pollable && document.visibilityState === "visible") void load(); }, 60000);
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => { active = false; clearInterval(poll); clearInterval(clock); };
  }, [launchId, network, token, revision]);
  const retrySeconds = Math.max(0, Math.ceil((retryAt - now) / 1000));
  const age = res?.fetched_at ? now / 1000 - res.fetched_at : 0;
  const expired = age > 300;
  const stale = !!res?.stale || !!error || age > 90;
  const selected = res?.pairs?.find(p => p.pool?.address === selectedPair);
  const metrics = expired ? null : selected?.metrics ?? res?.metrics;
  const pool = expired ? null : selected?.pool ?? res?.pool;
  const source = res?.source ?? "Market provider";
  const pairScope = res?.metric_scope === "pair";
  const change = metrics?.price_change.h24;
  const tx = metrics?.txns[win];
  const buys = tx?.buys, sells = tx?.sells;
  const total = buys != null && sells != null ? buys + sells : null;
  const explorer = res?.explorer_url;
  const chartNetwork = res?.network ?? network;
  const chartToken = res ? res.token : token;
  let notice: ReactNode = null;
  if (error) notice = <Callout tone="warn"><b>Market refresh failed.</b> {error}{res?.fetched_at && " The last response is marked stale."}</Callout>;
  else if (res?.state === "not_configured") notice = <Callout icon="info"><b>GMGN market metrics need setup.</b> The platform operator needs to connect GMGN API access. The chart is loaded independently.</Callout>;
  else if (res?.state === "unsupported") notice = <Callout icon="globe">Market data is not supported for {chainOf(chartNetwork).name} by this integration. Wallets and Helm trading remain available where supported.</Callout>;
  else if (res?.state === "error" || res?.stale) notice = <Callout tone="warn"><b>{res.stale ? `Showing previously fetched ${source} data.` : "Market metrics are unavailable."}</b> {res.error}{retrySeconds > 0 && <> Retry available at {new Date(retryAt).toLocaleTimeString()}.</>}</Callout>;
  else if (res?.state === "no_pool") notice = <Callout icon="radar"><b>No indexed pool yet.</b> Details will appear once {source} indexes an eligible pool. The independently hosted chart may become available earlier.{explorer && <> <a href={explorer} target="_blank" rel="noreferrer">View token on explorer</a></>}</Callout>;

  return <section className={`term terminal-view-${mobileView}`} aria-label="Indicative market data">
    <div className="term-head">
      <div className="th-pool"><span className="term-kicker"><Icon name="trend" size={12} />{res?.source ?? "GMGN"} · indicative market data</span>
        <b className="th-poolname">{res?.symbol ?? symbol} <span className="muted">/ {chainOf(chartNetwork).name}</span></b>
      </div>
      <div className="th-stats">
        <HeadStat label="Price USD" value={usd(metrics?.price_usd)} />
        <HeadStat label="24h change" value={pct(change)} tone={change != null && change >= 0 ? "good" : "bad"} title={`${source} · ${pairScope ? "selected pair" : "token"} price change`} />
        <HeadStat label="Mkt cap" value={usd(metrics?.market_cap_usd)} title={res?.source === "DexScreener" ? "Reported by DexScreener" : `${res?.source ?? "GMGN"}: price × circulating supply, as the provider defines market cap`} />
        <HeadStat label="FDV" value={usd(metrics?.fdv_usd)} title="Fully diluted valuation; distinct from circulating market capitalization" />
        <HeadStat label="Pool liquidity" value={usd(pool?.liquidity_usd)} title="Liquidity in the displayed pool" />
        <HeadStat label={pairScope ? "24h pair vol." : "24h token vol."} value={usd(metrics?.volume_usd.h24)} />
        <HeadStat label="24h swaps" value={count(metrics?.txns.h24.swaps)} />
        <HeadStat label="Holders" value={count(metrics?.holder_count)} />
      </div>
      <div className="th-src">
        <span className={`fresh ${stale || expired ? "stale" : ""}`}>
          {res?.fetched_at ? `${expired ? "Expired · " : stale ? "Stale · " : ""}${res.source ?? "GMGN"} metrics fetched ${new Date(res.fetched_at * 1000).toLocaleTimeString()}` : "Market metrics · awaiting data"}
        </span>
        <button className="btn sm ghost" onClick={() => setRevision((r) => r + 1)} disabled={busy || retrySeconds > 0} aria-label={retrySeconds > 0 ? `Refresh available in ${retrySeconds} seconds` : "Refresh market data"}>
          {busy ? <Spinner /> : <Icon name="refresh" size={13} />}{retrySeconds > 0 ? `Retry in ${retrySeconds}s` : "Refresh"}
        </button>
      </div>
    </div>
    <div className="market-notices">
      {res?.whose === "source" && <Callout icon="copy">You are viewing the copied token's market. After deployment, this workspace switches to your launch's token.</Callout>}
      {notice}
      {expired && <Callout tone="warn">The last metrics are over five minutes old and have been hidden. Refresh to check for new data.</Callout>}
    </div>
    <div className="term-main">
      <div className="term-analysis">
        <GmgnChart key={`${chartNetwork}:${chartToken}`} network={chartNetwork} token={chartToken} symbol={res?.symbol ?? symbol} explorer={explorer} />
        <div className="term-panel gmgn-pool" aria-label="Selected market pool">
          <div className="tp-head"><b>Selected pool</b><span className="muted small">{source}</span></div>
          {(res?.pairs?.length ?? 0) > 1 && <label className="field">Pool<select value={pool?.address ?? ""} onChange={e => setSelectedPair(e.target.value)}>
            {res!.pairs!.map(p => p.pool && <option key={p.pool.address} value={p.pool.address}>{dexName(p.pool.dex)} · {p.pool.quote_symbol ?? "Quote"} · {p.pool.address.slice(0, 8)}</option>)}
          </select></label>}
          {!res && !error ? <Skeleton h={90} /> : pool ? <dl className="pool-facts">
            <div><dt>DEX</dt><dd>{dexName(pool.dex)}</dd></div>
            <div><dt>Pool address</dt><dd><Address address={pool.address} network={chartNetwork} chars={5} /></dd></div>
            <div><dt>Quote asset</dt><dd>{pool.quote_symbol ?? "Not reported"}{pool.quote_address && <Address address={pool.quote_address} network={chartNetwork} type="token" chars={4} />}</dd></div>
            <div><dt>Price in quote asset</dt><dd>{metrics?.price_native == null ? "Not reported" : `${metrics.price_native.toLocaleString(undefined, {maximumSignificantDigits: 8})} ${pool.quote_symbol ?? ""}`}</dd></div>
            <div><dt>Market page</dt><dd>{(pool.market_url || res?.gmgn_url) ? <a href={pool.market_url || res?.gmgn_url} target="_blank" rel="noreferrer">{source} <Icon name="ext" size={11} /></a> : "Not reported"}</dd></div>
          </dl> : <p className="pool-placeholder muted small">{res?.state === "no_pool" ? "Waiting for an indexed pool." : "Pool details are unavailable."}</p>}
        </div>
      </div>
      <div className="term-side">
        {ticket}
        <div className="term-panel gmgn-activity" role="group" aria-label="Market activity">
          <div className="tp-head"><b>{pairScope ? "Pair activity" : "Token activity"}</b><span className="muted small">{source} · {pairScope ? "selected pair" : "token-wide"}</span></div>
          {pool?.orientation === "quote" && <p className="muted small">Buy/sell counts refer to this pair's base token. Price and valuation for the selected quote token are not supplied.</p>}
          <div className="win-tabs" role="group" aria-label="Activity time window">
            {WINS.map(([key, label]) => <button key={key} type="button" aria-pressed={win === key} className={win === key ? "on" : ""} onClick={() => setWin(key)}>
              <span>{label}</span><b className={`num ${metrics?.price_change[key] == null ? "na" : metrics.price_change[key]! >= 0 ? "good" : "bad"}`}>{pct(metrics?.price_change[key]) ?? "—"}</b>
            </button>)}
          </div>
          <div className="tp-rows">
            <div className="tp-row"><span className="k">Swaps</span><span className="v num">{count(tx?.swaps) ?? "—"}</span></div>
            <div className="tp-split"><div className="row between small"><span>Buys <b className="num good">{count(buys) ?? "—"}</b></span><span>Sells <b className="num bad">{count(sells) ?? "—"}</b></span></div>
              <div className="split-bar" aria-hidden="true">{total != null && total > 0 && <><i className="b" style={{ width: `${buys! / total * 100}%` }} /><i className="s" style={{ width: `${sells! / total * 100}%` }} /></>}</div>
            </div>
            <div className="tp-row"><span className="k">{pairScope ? "Pair volume" : "Token volume"}</span><span className="v num">{usd(metrics?.volume_usd[win]) ?? "—"}</span></div>
          </div>
          {!metrics && <Empty icon="activity" title="Activity unavailable" text="Counts appear once market data is available." />}
        </div>
      </div>
    </div>
    <p className="market-note">Chart from <a href="https://gmgn.ai" target="_blank" rel="noreferrer">GMGN</a>; metrics from {source}. {pairScope ? "Activity covers only the selected pair. Pool selection does not change GMGN's token chart." : "Token-wide activity is separate from the displayed pool's liquidity."} Indicative only; never used for wallet balances, trade previews, quotes or execution.</p>
  </section>;
}
