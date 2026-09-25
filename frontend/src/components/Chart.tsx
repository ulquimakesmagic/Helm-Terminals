import { useEffect, useRef, useState } from "react";
import { fmt } from "../api";

interface Pt { date: string; cumulative_sol: number; daily_sol: number }

/** Cumulative net SOL flow as an area chart with a hover crosshair. */
export default function AreaChart({ points, height = 240 }: { points: Pt[]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [w, setW] = useState(760);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const h = height, padL = 52, padR = 12, padT = 12, padB = 26;

  // One point draws nothing useful: pad with a zero start.
  const pts = points.length === 1 ? [{ date: "", cumulative_sol: 0, daily_sol: 0 }, ...points] : points;
  const ys = pts.map((p) => p.cumulative_sol);
  let min = Math.min(0, ...ys), max = Math.max(0, ...ys);
  if (max === min) { max += 1; min -= 1; }
  // "Nice" tick step (1, 2 or 5 × 10^n) so axis labels read cleanly.
  const raw = (max - min) / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = ([1, 2, 5, 10].find((m) => m * mag >= raw) ?? 10) * mag;
  min = Math.floor(min / step) * step; max = Math.ceil(max / step) * step;
  const span = max - min;
  const x = (i: number) => padL + (i * (w - padL - padR)) / Math.max(1, pts.length - 1);
  const y = (v: number) => padT + (1 - (v - min) / (max - min)) * (h - padT - padB);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.cumulative_sol).toFixed(1)}`).join(" ");
  const area = `${line} L${x(pts.length - 1)},${y(0)} L${x(0)},${y(0)} Z`;
  const last = pts[pts.length - 1]?.cumulative_sol ?? 0;
  const color = last >= 0 ? "var(--good)" : "var(--bad)";
  const ticks = Array.from({ length: Math.round(span / step) + 1 }, (_, i) => min + step * i);

  function onMove(e: React.MouseEvent) {
    const r = ref.current!.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * w;
    const i = Math.round(((px - padL) / (w - padL - padR)) * (pts.length - 1));
    setHover(Math.max(0, Math.min(pts.length - 1, i)));
  }

  const hp = hover !== null ? pts[hover] : null;
  const date = (d: string) => d ? new Date(d + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "start";

  return (
    <div className="chart" ref={ref} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ height }} role="img" aria-label="Cumulative net flow">
        <defs>
          <linearGradient id="area-g" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={last >= 0 ? "#34d399" : "#f87171"} stopOpacity="0.28" />
            <stop offset="1" stopColor={last >= 0 ? "#34d399" : "#f87171"} stopOpacity="0" />
          </linearGradient>
        </defs>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={padL} x2={w - padR} y1={y(t)} y2={y(t)} stroke="var(--line)" strokeDasharray="3 4" vectorEffect="non-scaling-stroke" />
            <text x={padL - 8} y={y(t) + 3} textAnchor="end" className="axis">{fmt(t, step < 0.01 ? 4 : step < 1 ? 2 : 1)}</text>
          </g>
        ))}
        <line x1={padL} x2={w - padR} y1={y(0)} y2={y(0)} stroke="var(--line-2)" vectorEffect="non-scaling-stroke" />
        <path d={area} fill="url(#area-g)" />
        <path d={line} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
        <text x={padL} y={h - 6} className="axis">{date(pts[0].date)}</text>
        <text x={w - padR} y={h - 6} textAnchor="end" className="axis">{date(pts[pts.length - 1].date)}</text>
        {hp && (
          <>
            <line x1={x(hover!)} x2={x(hover!)} y1={padT} y2={h - padB} stroke="var(--line-2)" vectorEffect="non-scaling-stroke" />
          </>
        )}
      </svg>
      {hp && (
        <span style={{ position: "absolute", left: `${(x(hover!) / w) * 100}%`, top: `${(y(hp.cumulative_sol) / h) * 100}%`, width: 10, height: 10, borderRadius: "50%", background: "var(--bg)", border: `2px solid ${color}`, transform: "translate(-50%,-50%)", pointerEvents: "none" }} />
      )}
      {hp && (
        <div className="tip" style={{ left: `${(x(hover!) / w) * 100}%`, top: `${(y(hp.cumulative_sol) / h) * 100}%` }}>
          <div className="d">{date(hp.date)}</div>
          <div className="num"><b className={hp.cumulative_sol >= 0 ? "good" : "bad"}>{fmt(hp.cumulative_sol)} SOL</b> total</div>
          <div className="num muted">{hp.daily_sol >= 0 ? "+" : ""}{fmt(hp.daily_sol)} that day</div>
        </div>
      )}
    </div>
  );
}
