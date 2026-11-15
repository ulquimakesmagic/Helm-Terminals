import { useEffect, useState } from "react";
import { Callout, Icon, Spinner } from "./ui";

/** GMGN's public chart embed. The page loads in the visitor's browser straight from GMGN; Helm sends GMGN nothing
 *  but the token address in the URL. Prices, pools, holders and activity elsewhere on the page come from Codex. */
export default function GmgnChart({ url, symbol }: { url: string; symbol: string }) {
  const [status, setStatus] = useState<"loading" | "loaded" | "slow">("loading");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    setStatus("loading");
    const timer = setTimeout(() => setStatus((s) => s === "loading" ? "slow" : s), 15000);
    return () => clearTimeout(timer);
  }, [url, revision]);
  return <>
    <div className="gmgn-frame-wrap">
      {status === "loading" && <div className="gmgn-loading" role="status"><Spinner />Opening GMGN chart…</div>}
      <iframe key={`${url}:${revision}`} className="gmgn-frame" src={url} title={`GMGN price chart for ${symbol}`}
        referrerPolicy="no-referrer" sandbox="allow-scripts allow-same-origin" allowFullScreen loading="lazy"
        onLoad={() => setStatus("loaded")} />
    </div>
    {status === "slow" && <Callout tone="warn">GMGN's chart is taking longer than expected. Reload it or open it on GMGN.</Callout>}
    <div className="chart-support">
      <p className="muted small">Blank chart? New tokens can take a few minutes to appear on GMGN after their first trades.</p>
      <div className="row">
        <button type="button" className="btn sm secondary" onClick={() => setRevision((r) => r + 1)}><Icon name="refresh" size={13} />Reload chart</button>
        <a className="btn sm ghost" href={url} target="_blank" rel="noreferrer">Open on GMGN <Icon name="ext" size={12} /></a>
      </div>
    </div>
  </>;
}
