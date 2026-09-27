import { useCallback, useEffect, useMemo, useState } from "react";
import { api, compact, errMsg, fmt } from "../api";
import { explorerUrl } from "../chains";
import { Callout, Icon, Spinner } from "./ui";

type Period = "1h" | "24h" | "7d" | "30d";
type Candle = { time: number; open: number; high: number; low: number; close: number; volume_usd: number };
type Pool = {
  pool_address: string; pool_name: string; dex: string; quote_asset: string; quote_symbol: string;
  price_usd: number | null; price_native: number | null; price_change: Record<string, number | null>;
  volume_usd_24h: number | null; transactions_24h: number | null; buys_24h: number | null; sells_24h: number | null;
  liquidity_usd: number | null; fdv_usd: number | null; market_cap_usd: number | null; pool_url: string;
};
type Market = {
  status: string; reason?: string; source?: string; source_url?: string; updated_at?: string | null; stale?: boolean;
  pairs?: Pool[]; selected_pool?: Pool | null;
};
type History = { status: string; reason?: string; source?: string; updated_at?: string | null; stale?: boolean;
  candles?: Candle[]; timeframe?: Period; pool_address?: string };

const PERIODS: [Period, string][] = [["1h", "1H"], ["24h", "24H"], ["7d", "7D"], ["30d", "30D"]];
const REFRESH_MS = 120_000;

function ago(value?: string | null) {
  if (!value) return "Update time unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Update time unavailable";
  return `Fetched ${date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
}

function usdValue(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumSignificantDigits: 5 }).format(value);
}

function priceValue(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(undefined, { maximumSignificantDigits: 6 }).format(value);
}

function formatPct(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `${value > 0 ? "+" : ""}${fmt(value, 2)}%`;
}

function Chart({ candles, symbol }: { candles: Candle[]; symbol: string }) {
  const w = 900, h = 300;
  const left = 72, right = 16, top = 14, priceBottom = 224, volumeBottom = 276;
  const min = Math.min(...candles.map((c) => c.low));
  const max = Math.max(...candles.map((c) => c.high));
  const range = max - min || Math.abs(max) * 0.02 || 1;
  const xStep = (w - left - right) / Math.max(candles.length, 1);
  const candleWidth = Math.max(1, Math.min(8, xStep * 0.66));
  const y = (value: number) => top + ((max - value) / range) * (priceBottom - top);
  const maxVol = Math.max(1, ...candles.map((c) => c.volume_usd));
  const labelTime = (seconds: number) => new Date(seconds * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const priceLabels = [max, max - range / 2, min];
  return (
    <svg className="market-chart" viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`${symbol} price chart in USD with ${candles.length} OHLCV candles`}>
      {priceLabels.map((v, i) => {
        const yy = top + i * ((priceBottom - top) / 2);
        return <g key={i}><line x1={left} x2={w - right} y1={yy} y2={yy} className="market-gridline" />
          <text x={left - 9} y={yy + 4} textAnchor="end" className="market-axis">{usdValue(v)}</text></g>;
      })}
      {candles.map((c, i) => {
        const x = left + i * xStep + xStep / 2;
        const up = c.close >= c.open;
        const bodyY = y(Math.max(c.open, c.close));
        const bodyH = Math.max(1, Math.abs(y(c.open) - y(c.close)));
        const volumeH = (c.volume_usd / maxVol) * (volumeBottom - priceBottom - 8);
        const volumeY = volumeBottom - volumeH;
        return <g key={c.time} className={up ? "market-candle up" : "market-candle down"}>
          <title>{`${labelTime(c.time)} · O ${usdValue(c.open)} · H ${usdValue(c.high)} · L ${usdValue(c.low)} · C ${usdValue(c.close)} · Volume ${usdValue(c.volume_usd)}`}</title>
          <line x1={x} x2={x} y1={y(c.high)} y2={y(c.low)} />
          <rect x={x - candleWidth / 2} y={bodyY} width={candleWidth} height={bodyH} rx="0.6" />
          <rect x={x - candleWidth / 2} y={volumeY} width={candleWidth} height={Math.max(1, volumeH)} className="market-volume" />
        </g>;
      })}
      <line x1={left} x2={w - right} y1={priceBottom + 4} y2={priceBottom + 4} className="market-gridline" />
      {candles.length > 1 && <>
        <text x={left} y={h - 5} textAnchor="start" className="market-axis">{labelTime(candles[0].time)}</text>
        <text x={w - right} y={h - 5} textAnchor="end" className="market-axis">{labelTime(candles[candles.length - 1].time)}</text>
      </>}
    </svg>
  );
}

function Metric({ label, value, title }: { label: string; value: string; title?: string }) {
  return <div className="market-metric" title={title}><div className="muted small">{label}</div><div className="market-metric-value num">{value}</div></div>;
}

export default function MarketPanel({ launchId, network, mint, live, symbol, native }: {
  launchId: string; network: string; mint?: string | null; live: boolean; symbol: string; native: string;
}) {
  const [market, setMarket] = useState<Market | null>(null);
  const [history, setHistory] = useState<History | null>(null);
  const [selected, setSelected] = useState("");
  const [period, setPeriod] = useState<Period>("24h");
  const [loading, setLoading] = useState(true);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  const loadMarket = useCallback(async (alive?: () => boolean) => {
    if (!live || !mint) {
      setMarket({ status: "pending", reason: "Market data appears after the token is live and an eligible pool is indexed.", pairs: [] });
      setLoading(false);
      return;
    }
    setLoading((current) => market ? current : true);
    try {
      const query = selected ? `?pool_address=${encodeURIComponent(selected)}` : "";
      const next = await api<Market>(`/launches/${launchId}/market${query}`);
      if (alive && !alive()) return;
      setMarket(next);
      setError("");
      if (next.pairs?.length && (!selected || !next.pairs.some((p) => p.pool_address === selected))) {
        setSelected(next.selected_pool?.pool_address || next.pairs[0].pool_address);
      }
    } catch (e) {
      if (alive && !alive()) return;
      setError(errMsg(e));
    } finally {
      if (!alive || alive()) setLoading(false);
    }
  }, [launchId, live, mint, selected, refreshKey]);

  useEffect(() => {
    let active = true;
    void loadMarket(() => active);
    const timer = window.setInterval(() => { void loadMarket(() => active); }, REFRESH_MS);
    return () => { active = false; window.clearInterval(timer); };
  }, [loadMarket]);

  const selectedPool = market?.selected_pool ?? market?.pairs?.find((p) => p.pool_address === selected) ?? null;
  const loadHistory = useCallback(async (alive?: () => boolean) => {
    if (!selectedPool || !live || !mint) { setHistory(null); return; }
    setHistoryLoading(true);
    try {
      const result = await api<History>(`/launches/${launchId}/market/${encodeURIComponent(selectedPool.pool_address)}/ohlcv?timeframe=${period}`);
      if (alive && !alive()) return;
      setHistory({ ...result, timeframe: period, pool_address: selectedPool.pool_address });
    } catch (e) {
      if (alive && !alive()) return;
      setHistory({ status: "unavailable", reason: errMsg(e), candles: [], timeframe: period,
        pool_address: selectedPool.pool_address });
    } finally {
      if (!alive || alive()) setHistoryLoading(false);
    }
  }, [launchId, live, mint, period, refreshKey, selectedPool?.pool_address]);

  useEffect(() => {
    let active = true;
    void loadHistory(() => active);
    const timer = window.setInterval(() => { void loadHistory(() => active); }, REFRESH_MS);
    return () => { active = false; window.clearInterval(timer); };
  }, [loadHistory]);

  const changePct = selectedPool?.price_change?.h24;
  const visibleHistory = history?.timeframe === period && history?.pool_address === selectedPool?.pool_address ? history : null;
  const chartUpdated = visibleHistory?.updated_at || market?.updated_at;
  const chartStale = Boolean(visibleHistory?.stale || market?.stale);
  const chart = useMemo(() => visibleHistory?.candles ?? [], [visibleHistory]);
  const tokenExplorer = mint ? explorerUrl(network, "token", mint) : undefined;

  return (
    <section className="market-section" aria-labelledby="market-title">
      <div className="market-heading">
        <div>
          <div className="market-kicker"><Icon name="trend" size={14} />MARKET VIEW</div>
          <h2 id="market-title">Token market</h2>
          <p className="muted small">Indicative third-party market data · CoinGecko API, powered by GeckoTerminal.</p>
        </div>
        <button type="button" className="btn sm secondary" onClick={() => setRefreshKey((n) => n + 1)} disabled={loading}>
          {loading ? <Spinner /> : <Icon name="refresh" size={13} />}Refresh market
        </button>
      </div>

      {!live || !mint ? (
        <div className="market-state"><span className="market-state-icon"><Icon name="activity" size={18} /></span>
          <div><b>Market data is not available yet</b><p className="muted small">{market?.reason ?? "Market data appears after the token is live and an eligible pool is indexed."}</p></div>
        </div>
      ) : loading && !market ? (
        <div className="market-state" aria-busy="true"><Spinner /><span className="muted">Loading indexed pools…</span></div>
      ) : error && !market ? (
        <div className="market-state"><div><b>Market data could not be loaded</b><p className="muted small">{error}</p></div>
          <button className="btn sm secondary" onClick={() => setRefreshKey((n) => n + 1)}>Try again</button></div>
      ) : market?.status === "no_pool" ? (
        <div className="market-state"><span className="market-state-icon"><Icon name="activity" size={18} /></span>
          <div className="grow"><b>No eligible pool indexed yet</b><p className="muted small">Market data will appear once an eligible pool for this exact token address is indexed.</p>
            <p className="market-asof" style={{ marginTop: 5 }}>{ago(market?.updated_at)}</p></div>
          {tokenExplorer && <a className="btn sm ghost" href={tokenExplorer} target="_blank" rel="noreferrer">Token explorer <Icon name="ext" size={12} /></a>}
        </div>
      ) : market?.status === "unsupported" || market?.status === "unavailable" || market?.status === "pending" ? (
        <div className="market-state"><span className="market-state-icon"><Icon name="alert" size={18} /></span>
          <div className="grow"><b>{market.status === "unsupported" ? "Market coverage unavailable" : "Market data unavailable"}</b><p className="muted small">{market.reason}</p></div>
          {market.status === "unavailable" && <button className="btn sm secondary" onClick={() => setRefreshKey((n) => n + 1)}>Retry</button>}
          {tokenExplorer && <a className="btn sm ghost" href={tokenExplorer} target="_blank" rel="noreferrer">Token explorer <Icon name="ext" size={12} /></a>}
        </div>
      ) : selectedPool ? (
        <>
          {error && <Callout tone="warn">Market refresh failed: {error}. Showing the last available response.</Callout>}
          <div className="market-toolbar">
            <div className="market-pair-meta">
              {market?.pairs && market.pairs.length > 1 ? (
                <label className="market-select"><span className="sr-only">Select a liquidity pool</span>
                  <select value={selectedPool.pool_address} onChange={(e) => setSelected(e.target.value)} aria-label="Select a liquidity pool">
                    {market.pairs.map((p) => <option key={p.pool_address} value={p.pool_address}>{p.dex} · {p.pool_name} · {usdValue(p.liquidity_usd)}</option>)}
                  </select>
                </label>
              ) : <span className="market-pool-title">{selectedPool.pool_name}</span>}
              <div className="market-meta-line"><span>{selectedPool.dex}</span><span className="market-dot">·</span><span>Quote {selectedPool.quote_asset}</span>
                <span className="market-dot">·</span><span className="market-address" title={selectedPool.pool_address}>{selectedPool.pool_address}</span>
              </div>
            </div>
            <a href={selectedPool.pool_url} target="_blank" rel="noreferrer" className="btn sm ghost">View pool <Icon name="ext" size={12} /></a>
          </div>

          <div className="market-metrics">
            <Metric label="Token price · USD" value={usdValue(selectedPool.price_usd)} />
            <Metric label={`Token price · ${native}`} value={selectedPool.price_native == null ? "—" : `${priceValue(selectedPool.price_native)} ${native}`} />
            <Metric label="Price change · 24h" value={formatPct(changePct)} />
            <Metric label="Pool volume · 24h" value={usdValue(selectedPool.volume_usd_24h)} />
            <Metric label="Pool transactions · 24h" value={selectedPool.transactions_24h == null ? "—" : compact(selectedPool.transactions_24h, 0)} title={`${selectedPool.buys_24h ?? "—"} buys · ${selectedPool.sells_24h ?? "—"} sells`} />
            <Metric label="Pool liquidity" value={usdValue(selectedPool.liquidity_usd)} />
            <Metric label="FDV" value={usdValue(selectedPool.fdv_usd)} title="Provider-reported fully diluted valuation" />
            <Metric label="Market cap" value={usdValue(selectedPool.market_cap_usd)} title="Provider-reported market capitalization; may be unavailable" />
          </div>

          <div className="market-chart-head">
            <div><div className="market-chart-title">{symbol} / USD <span className="muted small">OHLCV · volume bars in USD</span></div><div className={`market-asof ${chartStale ? "stale" : ""}`}>
              {chartStale && <span>Stale · </span>}{ago(chartUpdated)} · provider data may be delayed</div></div>
            <div className="market-periods" role="group" aria-label="Chart timeframe">
              {PERIODS.map(([id, label]) => <button type="button" key={id} className={period === id ? "active" : ""} aria-pressed={period === id} onClick={() => setPeriod(id)}>{label}</button>)}
            </div>
          </div>
          <div className="market-chart-wrap" aria-busy={historyLoading}>
            {historyLoading && !visibleHistory ? <div className="market-chart-message"><Spinner />Loading historical OHLCV…</div>
              : chart.length ? <Chart candles={chart} symbol={symbol} />
              : <div className="market-chart-message"><div><b>{visibleHistory?.status === "unavailable" ? "Chart temporarily unavailable" : "No chart history yet"}</b>
                <p className="muted small">{visibleHistory?.reason ?? "Historical candles will appear when the provider has indexed trades for this pool."}</p>
                {visibleHistory?.status === "unavailable" && <button className="btn sm secondary" style={{ marginTop: 10 }} onClick={() => void loadHistory()}>Retry chart</button>}</div></div>}
          </div>
          <div className="market-foot">
            <span><i className="market-data-dot" />Indicative market data · not an execution quote</span>
            <span>FDV and market cap are separate provider fields</span>
            <span className="row nowrap" style={{ gap: 9 }}>
              <a href={market?.source_url || "https://www.coingecko.com/en/api"} target="_blank" rel="noreferrer">Data provided by CoinGecko <Icon name="ext" size={11} /></a>
              <a href="https://www.geckoterminal.com/" target="_blank" rel="noreferrer">On-chain via GeckoTerminal <Icon name="ext" size={11} /></a>
            </span>
          </div>
          <p className="market-disclaimer">Market data is separate from Helm wallet balances, supply transparency, transaction history, and trade previews. It is never used to authorize or execute trades.</p>
        </>
      ) : (
        <div className="market-state"><div><b>Market data unavailable</b><p className="muted small">{market?.reason ?? "No selected pool is available."}</p></div>
          {tokenExplorer && <a className="btn sm ghost" href={tokenExplorer} target="_blank" rel="noreferrer">Token explorer <Icon name="ext" size={12} /></a>}
        </div>
      )}
    </section>
  );
}
