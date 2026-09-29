import { KeyboardEvent, useEffect, useRef, useState } from "react";
import { TableWrap } from "./ui";

export interface Candle { t: number; o: number; h: number; l: number; c: number; v: number }

/** A price with enough significant digits for tiny token prices (0.00001834), never in exponent notation. */
export function fmtPrice(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (a >= 1) return v.toLocaleString(undefined, { maximumFractionDigits: 4 });
  // Tiny prices the way DEX terminals write them: 0.000005232 -> 0.0₅5232 (the subscript counts the zeros).
  let zeros = -Math.floor(Math.log10(a)) - 1; // zeros between the point and the first significant digit
  let m = Math.round(a * 10 ** (zeros + 4)); // four significant digits
  if (m >= 10000) { zeros -= 1; m = Math.round(m / 10); } // 0.000099999 rounds up to 0.0001
  if (zeros >= 4) {
    const digits = String(m).replace(/0+$/, "") || "0";
    const sub = String(zeros).split("").map((d) => "₀₁₂₃₄₅₆₇₈₉"[+d]).join("");
    return `${v < 0 ? "-" : ""}0.0${sub}${digits}`;
  }
  return v.toLocaleString(undefined, { maximumSignificantDigits: 4, maximumFractionDigits: 20 });
}

export const fmtUsdCompact = (v: number | null | undefined) =>
  v == null || !Number.isFinite(v) ? "—" : `$${v.toLocaleString(undefined, { notation: Math.abs(v) >= 100_000 ? "compact" : "standard", maximumFractionDigits: Math.abs(v) >= 100_000 ? 2 : Math.abs(v) >= 1 ? 0 : 4 })}`;

const when = (t: number, daily: boolean) => {
  const d = new Date(t * 1000);
  return daily ? d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
    : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
};

/**
 * OHLC candles from the provider as-is: rising candles hollow in green, falling ones filled in red, so direction
 * reads by shape as well as colour. Pool volume sits in a strip below on its own scale (never a second price axis).
 * Hover, touch or arrow keys move a crosshair with the candle's open, high, low, close and volume.
 */
export default function CandleChart({ candles: all, height = 300, daily, label }: { candles: Candle[]; height?: number; daily?: boolean; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(720);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const padL = 8, padT = 10, padB = 24, volH = Math.round(height * 0.16), gap = 8;
  const priceH = height - padT - padB - volH - gap;
  // On narrow screens show only the most recent candles that fit legibly (about 6px each, at least 30).
  // The price axis is as wide as its longest label, so small prices are never cut off at the edge.
  const labelW = (t: number) => fmtPrice(t).length * 6.6 + 14;
  const lo0 = Math.min(...all.map((c) => c.l)), hi0 = Math.max(...all.map((c) => c.h));
  const padR = Math.max(56, Math.ceil(Math.max(labelW(lo0), labelW(hi0), labelW(all[all.length - 1].c))));
  const fit = Math.max(30, Math.floor((w - padL - padR) / 6));
  const candles = all.length > fit ? all.slice(-fit) : all;
  const n = candles.length;
  const lo = Math.min(...candles.map((c) => c.l)), hi = Math.max(...candles.map((c) => c.h));
  const span = hi - lo || Math.abs(hi) * 0.02 || 1;
  const min = lo - span * 0.06, max = hi + span * 0.06;
  const vmax = Math.max(...candles.map((c) => c.v), 0) || 1;
  const plotW = w - padL - padR;
  // Room for at least 60 candles (or all that fit), filled from the right like an exchange chart, so a new pool's
  // first few candles are normal-width bars at the right edge instead of a few huge blocks spread across the chart.
  const slots = Math.max(n, Math.min(fit, 36));
  const step = plotW / slots;
  const lead = slots - n;
  const bw = Math.max(1, Math.min(14, step * 0.64));
  const x = (i: number) => padL + step * (lead + i) + step / 2;
  const y = (v: number) => padT + (1 - (v - min) / (max - min)) * priceH;
  const volTop = padT + priceH + gap;
  const ticks = Array.from({ length: 5 }, (_, i) => min + ((max - min) * (i + 0.5)) / 5);
  const hc = hover != null ? candles[hover] : null;
  const last = candles[n - 1];

  function pick(clientX: number) {
    const r = ref.current!.getBoundingClientRect();
    const px = ((clientX - r.left) / r.width) * w;
    setHover(Math.max(0, Math.min(n - 1, Math.floor((px - padL) / step) - lead)));
  }
  function onKey(e: KeyboardEvent) {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      setHover((h) => Math.max(0, Math.min(n - 1, (h ?? n - 1) + (e.key === "ArrowLeft" ? -1 : 1))));
    } else if (e.key === "Escape") setHover(null);
  }

  const summary = `${label}: ${n} candles from ${when(candles[0].t, !!daily)} to ${when(last.t, !!daily)}. `
    + `Latest close ${fmtPrice(last.c)} USD. Range ${fmtPrice(lo)} to ${fmtPrice(hi)} USD. Use left and right arrow keys to inspect candles.`;

  return (
    <div className="chart candle-chart" tabIndex={0} role="group" aria-label={`Interactive price chart. ${summary}`} onKeyDown={onKey} onBlur={() => setHover(null)}>
      <div className="chart-canvas" ref={ref}
        // Pointer events so a touch's trailing compatibility "mouseleave" doesn't hide the tooltip it just opened;
        // on touch the tooltip stays until the chart loses focus.
        onPointerMove={(e) => e.pointerType !== "touch" && pick(e.clientX)}
        onPointerLeave={(e) => e.pointerType !== "touch" && setHover(null)}
        onTouchStart={(e) => pick(e.touches[0].clientX)} onTouchMove={(e) => pick(e.touches[0].clientX)}>
        <svg viewBox={`0 0 ${w} ${height}`} height={height} preserveAspectRatio="none" aria-hidden="true">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={padL} x2={w - padR} y1={y(t)} y2={y(t)} stroke="var(--line)" strokeDasharray="3 4" />
            {Math.abs(y(t) - y(last.c)) > 14 && <text x={w - padR + 6} y={y(t) + 3} className="axis">{fmtPrice(t)}</text>}
          </g>
        ))}
        <line x1={padL} x2={w - padR} y1={volTop - gap / 2} y2={volTop - gap / 2} stroke="var(--line-2)" />
        {candles.map((c, i) => {
          const up = c.c >= c.o;
          const top = y(Math.max(c.o, c.c)), bot = y(Math.min(c.o, c.c));
          const color = up ? "var(--good)" : "var(--bad)";
          const vh = (c.v / vmax) * volH;
          return (
            <g key={c.t} opacity={hover == null || hover === i ? 1 : 0.55}>
              <rect x={x(i) - bw / 2} y={volTop + volH - vh} width={bw} height={Math.max(0, vh)} fill="var(--line-3)" rx={Math.min(2, bw / 3)} />
              <line x1={x(i)} x2={x(i)} y1={y(c.h)} y2={y(c.l)} stroke={color} strokeWidth={1} />
              <rect x={x(i) - bw / 2 + (up ? 0.5 : 0)} y={top} width={Math.max(1, bw - (up ? 1 : 0))} height={Math.max(1, bot - top)}
                fill={up ? "var(--panel)" : color} stroke={color} strokeWidth={up ? 1 : 0} rx={Math.min(1.5, bw / 4)} />
            </g>
          );
        })}
        <line x1={padL} x2={w - padR} y1={y(last.c)} y2={y(last.c)} stroke="var(--text-2)" strokeDasharray="2 3" opacity={0.5} />
        <rect x={w - padR + 2} y={y(last.c) - 9} width={padR - 4} height={18} rx={4} fill="var(--panel-3)" stroke="var(--line-3)" />
        <text x={w - padR + 6} y={y(last.c) + 4} className="axis" style={{ fill: "var(--text)" }}>{fmtPrice(last.c)}</text>
        {n > 1 && x(0) < w - padR - 200 && <text x={lead ? x(0) - step / 2 : padL} y={height - 6} className="axis">{when(candles[0].t, !!daily)}</text>}
        <text x={w - padR} y={height - 6} textAnchor="end" className="axis">{when(last.t, !!daily)}</text>
        {hc && <line x1={x(hover!)} x2={x(hover!)} y1={padT} y2={volTop + volH} stroke="var(--line-3)" />}
        </svg>
        {hc && (
          <div className="tip candle-tip" style={{ left: `${Math.min(84, Math.max(16, (x(hover!) / w) * 100))}%`, top: 8 }} role="status">
          <div className="d">{when(hc.t, !!daily)}</div>
          <div className="ohlc num">
            <span>O</span><b>{fmtPrice(hc.o)}</b><span>H</span><b>{fmtPrice(hc.h)}</b>
            <span>L</span><b>{fmtPrice(hc.l)}</b><span>C</span><b>{fmtPrice(hc.c)}</b>
          </div>
          <div className="num small">
            <span className={hc.c >= hc.o ? "good" : "bad"}>{hc.c >= hc.o ? "▲ +" : "▼ "}{hc.o ? (((hc.c - hc.o) / hc.o) * 100).toFixed(2) : "0.00"}%</span>
            <span className="muted"> · vol {fmtUsdCompact(hc.v)}</span>
          </div>
          </div>
        )}
      </div>
      <details className="chart-data-disclosure">
        <summary>Text summary and candle data</summary>
        <p className="small muted">{summary}</p>
        <div className="chart-data-scroll" role="region" aria-label="Displayed candle values" tabIndex={0}>
          <TableWrap label="Displayed chart candle data">
            <table className="rtable">
              <caption>{label}. Displayed candles; OHLC prices and pool volume in USD.</caption>
              <thead><tr><th>Time</th><th className="r">Open</th><th className="r">High</th><th className="r">Low</th><th className="r">Close</th><th className="r">Volume USD</th></tr></thead>
              <tbody>{candles.map((c) => (
                <tr key={c.t}>
                  <td data-label="Time">{when(c.t, !!daily)}</td>
                  <td className="r mono" data-label="Open">${fmtPrice(c.o)}</td>
                  <td className="r mono" data-label="High">${fmtPrice(c.h)}</td>
                  <td className="r mono" data-label="Low">${fmtPrice(c.l)}</td>
                  <td className="r mono" data-label="Close">${fmtPrice(c.c)}</td>
                  <td className="r mono" data-label="Volume USD">{fmtUsdCompact(c.v)}</td>
                </tr>))}</tbody>
            </table>
          </TableWrap>
        </div>
      </details>
    </div>
  );
}
