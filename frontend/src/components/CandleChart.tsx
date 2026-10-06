import { useEffect, useMemo, useRef, useState } from "react";

export interface Bar { t: number; o: number; h: number; l: number; c: number; v: number | null }

/** Prices from fractions of a cent to thousands, without scientific notation where it can be avoided. */
export function fmtPrice(v: number): string {
  if (!Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a === 0) return "0";
  if (a >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (a >= 1) return v.toLocaleString(undefined, { maximumFractionDigits: 4 });
  const digits = Math.min(12, Math.max(2, -Math.floor(Math.log10(a)) + 3));
  return v.toFixed(digits).replace(/0+$/, "").replace(/\.$/, "");
}

const fmtVol = (v: number | null) => v == null ? "—" : `$${v.toLocaleString(undefined, { notation: v >= 10000 ? "compact" : "standard", maximumFractionDigits: 2 })}`;

function niceTicks(min: number, max: number, count = 5): number[] {
  const span = max - min || Math.abs(max) || 1;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = ([1, 2, 2.5, 5, 10].find((m) => m * mag >= raw) ?? 10) * mag;
  const out: number[] = [];
  for (let t = Math.ceil(min / step) * step; t <= max + step * 1e-9; t += step) out.push(t);
  return out;
}

function timeLabel(t: number, barSeconds: number) {
  const d = new Date(t * 1000);
  return barSeconds >= 86_400 ? d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
    : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/**
 * OHLCV candles exactly as the provider returned them: no smoothing, gap filling or extrapolation.
 * Mouse, touch and keyboard (←/→, Home/End) inspect one bar at a time; the values are announced politely.
 */
export default function CandleChart({ bars, barSeconds, label, height = 380 }: { bars: Bar[]; barSeconds: number; label: string; height?: number }) {
  const wrap = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(720);
  const [focus, setFocus] = useState<number | null>(null);
  useEffect(() => {
    const el = wrap.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, Math.floor(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const padL = 8, padR = w < 420 ? 58 : 72, padT = 10, padB = 24;
  const volH = Math.round(height * 0.18);
  const priceH = height - padT - padB - volH - 6;
  // Show as many of the latest bars as fit at a legible width.
  const maxBars = Math.max(20, Math.floor((w - padL - padR) / 5));
  const shown = useMemo(() => bars.slice(-maxBars), [bars, maxBars]);
  const n = shown.length;
  const lo = Math.min(...shown.map((b) => b.l)), hi = Math.max(...shown.map((b) => b.h));
  const pad = (hi - lo) * 0.06 || hi * 0.02 || 1;
  const yMin = Math.max(0, lo - pad), yMax = hi + pad;
  const vMax = Math.max(1, ...shown.map((b) => b.v ?? 0));
  const slot = (w - padL - padR) / Math.max(1, n);
  const body = Math.max(1, Math.min(14, slot * 0.68));
  const x = (i: number) => padL + slot * i + slot / 2;
  const y = (p: number) => padT + (1 - (p - yMin) / (yMax - yMin)) * priceH;
  const vy = (v: number) => padT + priceH + 6 + volH - (v / vMax) * volH;
  const ticks = niceTicks(yMin, yMax, height < 300 ? 4 : 6);
  const labelEvery = Math.max(1, Math.ceil(n / Math.max(2, Math.floor((w - padL - padR) / 90))));
  const sel = focus != null && focus < n ? shown[focus] : null;
  const last = shown[n - 1];

  function pick(clientX: number) {
    const r = wrap.current!.getBoundingClientRect();
    const px = ((clientX - r.left) / r.width) * w;
    setFocus(Math.max(0, Math.min(n - 1, Math.floor((px - padL) / slot))));
  }
  function onKey(e: React.KeyboardEvent) {
    const cur = focus ?? n - 1;
    const next = e.key === "ArrowLeft" ? cur - 1 : e.key === "ArrowRight" ? cur + 1 : e.key === "Home" ? 0 : e.key === "End" ? n - 1 : null;
    if (next == null) return;
    e.preventDefault();
    setFocus(Math.max(0, Math.min(n - 1, next)));
  }
  const info = sel ?? last;
  const up = info ? info.c >= info.o : true;

  return (
    <div className="candles">
      <div className="candle-legend mono small" aria-hidden="true">
        {info && <>
          <span className="muted">{timeLabel(info.t, barSeconds)}</span>
          <span>O <b>{fmtPrice(info.o)}</b></span><span>H <b>{fmtPrice(info.h)}</b></span>
          <span>L <b>{fmtPrice(info.l)}</b></span><span>C <b className={up ? "good" : "bad"}>{fmtPrice(info.c)}</b></span>
          <span className="muted">Vol {fmtVol(info.v)}</span>
        </>}
      </div>
      <div ref={wrap} className="candle-plot" tabIndex={0} role="img" aria-roledescription="candlestick chart"
        aria-label={`${label}. ${n} candles. Use the left and right arrow keys to inspect each candle.`}
        onKeyDown={onKey} onMouseMove={(e) => pick(e.clientX)} onMouseLeave={() => setFocus(null)}
        onTouchStart={(e) => pick(e.touches[0].clientX)} onTouchMove={(e) => pick(e.touches[0].clientX)}
        onBlur={() => setFocus(null)}>
        <svg viewBox={`0 0 ${w} ${height}`} width="100%" height={height} preserveAspectRatio="none" aria-hidden="true">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={padL} x2={w - padR} y1={y(t)} y2={y(t)} className="grid" />
              {!(last && Math.abs(y(t) - y(last.c)) < 12) && <text x={w - padR + 6} y={y(t) + 3} className="axis">{fmtPrice(t)}</text>}
            </g>
          ))}
          {shown.map((b, i) => {
            const rising = b.c >= b.o;
            const top = y(Math.max(b.o, b.c)), bot = y(Math.min(b.o, b.c));
            return (
              <g key={b.t} className={rising ? "up" : "down"} opacity={sel && focus !== i ? 0.55 : 1}>
                {b.v != null && <rect x={x(i) - body / 2} y={vy(b.v)} width={body} height={padT + priceH + 6 + volH - vy(b.v)} className="vol" />}
                <line x1={x(i)} x2={x(i)} y1={y(b.h)} y2={y(b.l)} className="wick" />
                <rect x={x(i) - body / 2} y={top} width={body} height={Math.max(1, bot - top)} className="body" />
              </g>
            );
          })}
          {last && <g>
            <line x1={padL} x2={w - padR} y1={y(last.c)} y2={y(last.c)} className={`last ${last.c >= last.o ? "up" : "down"}`} />
            <rect x={w - padR + 2} y={y(last.c) - 9} width={padR - 4} height={18} rx={3} className={`last-tag ${last.c >= last.o ? "up" : "down"}`} />
            <text x={w - padR + 6} y={y(last.c) + 4} className="last-text">{fmtPrice(last.c)}</text>
          </g>}
          {sel && <line x1={x(focus!)} x2={x(focus!)} y1={padT} y2={height - padB} className="cross" />}
          {shown.map((b, i) => i % labelEvery === 0 && x(i) > padL + 30 && x(i) < w - padR - 30 ? (
            <text key={`t${b.t}`} x={x(i)} y={height - 6} textAnchor="middle" className="axis">{timeLabel(b.t, barSeconds)}</text>
          ) : null)}
        </svg>
      </div>
      <p className="sr-only" aria-live="polite">{sel ? `${timeLabel(sel.t, barSeconds)}: open ${fmtPrice(sel.o)}, high ${fmtPrice(sel.h)}, low ${fmtPrice(sel.l)}, close ${fmtPrice(sel.c)} US dollars, volume ${fmtVol(sel.v)}` : ""}</p>
      <details className="more candle-table">
        <summary>Candle data table ({n} rows)</summary>
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Candle data">
          <table>
            <thead><tr><th scope="col">Time</th><th scope="col" className="r">Open</th><th scope="col" className="r">High</th><th scope="col" className="r">Low</th><th scope="col" className="r">Close</th><th scope="col" className="r">Volume (USD)</th></tr></thead>
            <tbody>{[...shown].reverse().map((b) => (
              <tr key={b.t}><td>{new Date(b.t * 1000).toLocaleString()}</td><td className="r num">{fmtPrice(b.o)}</td><td className="r num">{fmtPrice(b.h)}</td>
                <td className="r num">{fmtPrice(b.l)}</td><td className="r num">{fmtPrice(b.c)}</td><td className="r num">{fmtVol(b.v)}</td></tr>
            ))}</tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
