import { useEffect, useRef, useState } from "react";
import { chainOf } from "../chains";
import { CHART_INTERVALS, ChartInterval, gmgnChartUrl } from "../gmgn";
import { Callout, Empty, Icon, Seg, Spinner } from "./ui";

function ChartFrame({ url, symbol }: { url: string; symbol: string }) {
  const [status, setStatus] = useState<"loading" | "loaded" | "slow" | "error">("loading");
  useEffect(() => {
    const timer = setTimeout(() => setStatus((s) => s === "loading" ? "slow" : s), 15000);
    return () => clearTimeout(timer);
  }, []);
  return (
    <div className="gmgn-frame-wrap">
      {status === "loading" && <div className="gmgn-loading" role="status"><Spinner />Opening GMGN chart…</div>}
      {(status === "slow" || status === "error") && <Callout tone="warn">GMGN's chart is taking longer than expected. Reload it or open it directly using the controls below.</Callout>}
      <iframe className="gmgn-frame" src={url} title={`GMGN price chart for ${symbol}`} referrerPolicy="no-referrer"
        sandbox="allow-scripts allow-same-origin" allowFullScreen
        onLoad={() => setStatus("loaded")} onError={() => setStatus("error")} />
    </div>
  );
}

export default function GmgnChart({ network, token, symbol, explorer }: { network: string; token: string | null; symbol: string; explorer?: string | null }) {
  const [interval, setInterval] = useState<ChartInterval>("15");
  const [revision, setRevision] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const expandButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!expanded) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setExpanded(false); expandButton.current?.focus(); }
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [expanded]);
  const url = gmgnChartUrl(network, token, interval);
  return (
    <section className={`term-chart ${expanded ? "chart-expanded" : ""}`} aria-label="GMGN token chart">
      <div className="chart-head">
        <div className="row small"><span className="term-kicker"><Icon name="trend" size={13} />GMGN chart</span><span className="badge">Token view</span></div>
        <button ref={expandButton} type="button" className="btn sm ghost" aria-pressed={expanded} onClick={() => setExpanded(!expanded)}>
          <Icon name="monitor" size={14} />{expanded ? "Compact chart" : "Expand chart"}
        </button>
      </div>
      {url && <div className="chart-head"><Seg value={interval} onChange={setInterval} options={CHART_INTERVALS} /><span className="muted small">{symbol} · {chainOf(network).name}</span></div>}
      {url ? <ChartFrame key={`${url}:${revision}`} url={url} symbol={symbol} /> : (
        <div className="chart-unavailable"><Empty icon="globe" title={token ? "GMGN chart unavailable" : "No token yet"}
          text={token ? `GMGN's documented chart embed does not cover this chain or token address. ${chainOf(network).name} market metrics, when supported, appear separately.` : "The chart will appear once this launch has a token address and GMGN has indexed trades."} />
          {explorer && <a href={explorer} target="_blank" rel="noreferrer">View token on explorer <Icon name="ext" size={12} /></a>}
        </div>
      )}
      {url && <div className="chart-support">
        <p className="muted small">Chart blank or unavailable? New tokens may need time for GMGN to index trades.</p>
        <div className="row"><button type="button" className="btn sm secondary" onClick={() => setRevision((r) => r + 1)}><Icon name="refresh" size={13} />Reload chart</button>
          <a className="btn sm ghost" href={url} target="_blank" rel="noreferrer">Open GMGN chart <Icon name="ext" size={12} /></a></div>
      </div>}
      <p className="market-note">GMGN controls chart updates and pool routing. Its token chart may differ from the main pool shown below. Helm cannot verify the freshness of data inside the embedded chart.</p>
    </section>
  );
}
