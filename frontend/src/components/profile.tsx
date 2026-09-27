import { useMemo, useRef, useState } from "react";
import { chainOf, padName } from "../chains";
import { ChainDot } from "./ui";

export const AVATAR_HUES = [265, 200, 170, 140, 90, 45, 20, 330];

export function initials(name: string) {
  const parts = name.trim().split(/[\s._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}

export function avatarBg(hue: number) {
  return `linear-gradient(135deg, hsl(${hue} 50% 56%), hsl(${(hue + 40) % 360} 45% 42%))`;
}

export function ProfileAvatar({ name, hue, size = 32, radius }: { name: string; hue: number; size?: number; radius?: number }) {
  return (
    <div className="avatar" aria-hidden="true"
      style={{ width: size, height: size, background: avatarBg(hue), fontSize: Math.round(size * 0.38),
               borderRadius: radius ?? Math.round(size * 0.28) }}>
      {initials(name)}
    </div>
  );
}

/* ------------------------------------------------------------------ activity heatmap */

// Sequential, one hue (the accent), dark -> bright on the dark surface. Level 0 is the empty cell.
const LEVELS = ["var(--panel-3)", "#133a52", "#17638c", "#2396cf", "#6fd0fb"];
const WEEKDAYS = ["Mon", "", "Wed", "", "Fri", "", ""];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function level(n: number, max: number) {
  if (!n) return 0;
  if (max <= 4) return Math.min(4, n);
  return Math.min(4, Math.ceil((n / max) * 4));
}

export function Heatmap({ start, days }: { start: string; days: number[] }) {
  const wrap = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);

  const { cells, weeks, months, max } = useMemo(() => {
    const s = new Date(start + "T00:00:00Z");
    const offset = (s.getUTCDay() + 6) % 7; // Monday-first rows
    const total = offset + days.length;
    const weeks = Math.ceil(total / 7);
    const cells: { date: Date | null; n: number }[] = [];
    for (let i = 0; i < weeks * 7; i++) {
      const di = i - offset;
      if (di < 0 || di >= days.length) cells.push({ date: null, n: 0 });
      else cells.push({ date: new Date(s.getTime() + di * 86400000), n: days[di] });
    }
    const months: { col: number; label: string }[] = [];
    let last = -1;
    for (let w = 0; w < weeks; w++) {
      const first = cells.slice(w * 7, w * 7 + 7).find((c) => c.date)?.date;
      if (first && first.getUTCMonth() !== last) {
        if (w < weeks - 2 || last === -1) months.push({ col: w, label: MONTHS[first.getUTCMonth()] });
        last = first.getUTCMonth();
      }
    }
    return { cells, weeks, months, max: Math.max(0, ...days) };
  }, [start, days]);

  const show = (e: React.MouseEvent<HTMLDivElement> | React.FocusEvent<HTMLDivElement>, c: { date: Date | null; n: number }) => {
    if (!c.date || !wrap.current) return;
    const r = e.currentTarget.getBoundingClientRect();
    const w = wrap.current.getBoundingClientRect();
    const day = c.date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
    setTip({ x: r.left - w.left + r.width / 2, y: r.top - w.top, text: `${c.n ? `${c.n} action${c.n === 1 ? "" : "s"}` : "No activity"} · ${day}` });
  };

  return (
    <div className="heatmap" ref={wrap} onMouseLeave={() => setTip(null)}>
      <div className="hm-months" style={{ gridTemplateColumns: `repeat(${weeks}, 1fr)` }}>
        {months.map((m) => <span key={m.col} style={{ gridColumn: `${m.col + 1} / span 3`, gridRow: 1 }}>{m.label}</span>)}
      </div>
      <div className="hm-body">
        <div className="hm-days">{WEEKDAYS.map((d, i) => <span key={i}>{d}</span>)}</div>
        <div className="hm-grid" style={{ gridTemplateColumns: `repeat(${weeks}, 1fr)` }} role="img"
          aria-label={`Activity over the last ${days.length} days: ${days.reduce((a, b) => a + b, 0)} actions`}>
          {cells.map((c, i) => (
            <div key={i} className={`hm-cell ${c.date ? "" : "void"}`} style={{ background: c.date ? LEVELS[level(c.n, max)] : "transparent" }}
              onMouseEnter={(e) => show(e, c)} tabIndex={c.date && c.n ? 0 : -1} onFocus={(e) => show(e, c)} onBlur={() => setTip(null)} />
          ))}
        </div>
      </div>
      <div className="hm-legend">
        <span>Less</span>{LEVELS.map((l) => <i key={l} style={{ background: l }} />)}<span>More</span>
      </div>
      {tip && <div className="hm-tip" style={{ left: tip.x, top: tip.y }}>{tip.text}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ launchpad breakdown */

export interface PadRow { network: string; launchpad: string; count: number; live: number }

export function PadBars({ rows }: { rows: PadRow[] }) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  return (
    <div className="padbars">
      {rows.map((r) => (
        <div key={r.network + r.launchpad} className="padbar" title={`${padName(r.launchpad)} on ${chainOf(r.network).name}: ${r.count} launch${r.count === 1 ? "" : "es"}, ${r.live} live`}>
          <ChainDot network={r.network} size={18} />
          <div className="pb-name truncate">{padName(r.launchpad)}<span className="muted small"> · {chainOf(r.network).name}</span></div>
          <div className="pb-track"><div style={{ width: `${(r.count / max) * 100}%` }} /></div>
          <div className="pb-val num">{r.count}<span className="muted small"> · {r.live} live</span></div>
        </div>
      ))}
    </div>
  );
}
