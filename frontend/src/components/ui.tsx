import { createContext, ReactNode, useCallback, useContext, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { BASE, launchImage, short } from "../api";
import { chainOf, explorerUrl } from "../chains";
import { ChainMark } from "./marks";

/* ---------------------------------------------------------------- icons */
const P: Record<string, ReactNode> = {
  dashboard: <><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>,
  rocket: <><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09Z" /><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2Z" /><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0" /><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5" /></>,
  wallet: <><path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1" /><path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4" /></>,
  radar: <><path d="M19.07 4.93A10 10 0 0 0 6.99 3.34" /><path d="M4 6h.01" /><path d="M2.29 9.62A10 10 0 1 0 21.31 8.35" /><path d="M16.24 7.76A6 6 0 1 0 8.23 16.67" /><path d="M12 18h.01" /><path d="M17.99 11.66A6 6 0 0 1 15.77 16.67" /><circle cx="12" cy="12" r="2" /><path d="m13.41 10.59 5.66-5.66" /></>,
  activity: <path d="M22 12h-4l-3 9L9 3l-3 9H2" />,
  settings: <><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" /><circle cx="12" cy="12" r="3" /></>,
  logout: <><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="m16 17 5-5-5-5" /><path d="M21 12H9" /></>,
  menu: <><path d="M4 6h16" /><path d="M4 12h16" /><path d="M4 18h16" /></>,
  x: <><path d="M18 6 6 18" /><path d="m6 6 12 12" /></>,
  plus: <><path d="M12 5v14" /><path d="M5 12h14" /></>,
  copy: <><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></>,
  check: <path d="M20 6 9 17l-5-5" />,
  ext: <><path d="M15 3h6v6" /><path d="M10 14 21 3" /><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /></>,
  refresh: <><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" /><path d="M21 3v5h-5" /><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" /><path d="M8 16H3v5" /></>,
  shield: <><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" /><path d="m9 12 2 2 4-4" /></>,
  lock: <><rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /></>,
  send: <><path d="m22 2-7 20-4-9-9-4Z" /><path d="M22 2 11 13" /></>,
  telegram: <path d="M21.2 4.4 2.9 11.5c-1 .4-1 1.5 0 1.8l4.6 1.4 1.8 5.5c.2.6 1 .8 1.5.3l2.6-2.4 4.7 3.5c.6.4 1.4.1 1.6-.6l3.3-15.3c.2-.9-.7-1.6-1.8-1.3ZM9.5 14.6l8.2-7.2" />,
  alert: <><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" /><path d="M12 9v4" /><path d="M12 17h.01" /></>,
  info: <><circle cx="12" cy="12" r="10" /><path d="M12 16v-4" /><path d="M12 8h.01" /></>,
  xcircle: <><circle cx="12" cy="12" r="10" /><path d="m15 9-6 6" /><path d="m9 9 6 6" /></>,
  checkcircle: <><circle cx="12" cy="12" r="10" /><path d="m9 12 2 2 4-4" /></>,
  flame: <path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.07-2.14-.22-4.05 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.15.43-2.29 1-3a2.5 2.5 0 0 0 2.5 2.5z" />,
  coins: <><circle cx="8" cy="8" r="6" /><path d="M18.09 10.37A6 6 0 1 1 10.34 18" /><path d="M7 6h1v4" /><path d="m16.71 13.88.7.71-2.82 2.82" /></>,
  trend: <><path d="m22 7-8.5 8.5-5-5L2 17" /><path d="M16 7h6v6" /></>,
  pie: <><path d="M21.21 15.89A10 10 0 1 1 8 2.83" /><path d="M22 12A10 10 0 0 0 12 2v10z" /></>,
  star: <path d="M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z" />,
  trash: <><path d="M3 6h18" /><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" /><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" /></>,
  search: <><circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" /></>,
  folder: <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" />,
  eye: <><path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0" /><circle cx="12" cy="12" r="3" /></>,
  eyeoff: <><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68" /><path d="M6.61 6.61A13.53 13.53 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61" /><path d="m2 2 20 20" /><path d="M14.12 14.12a3 3 0 1 1-4.24-4.24" /></>,
  download: <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="m7 10 5 5 5-5" /><path d="M12 15V3" /></>,
  upload: <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="m17 8-5-5-5 5" /><path d="M12 3v12" /></>,
  arrowleft: <><path d="m12 19-7-7 7-7" /><path d="M19 12H5" /></>,
  arrowright: <><path d="M5 12h14" /><path d="m12 5 7 7-7 7" /></>,
  arrowdown: <><path d="M12 5v14" /><path d="m19 12-7 7-7-7" /></>,
  arrowup: <><path d="M12 19V5" /><path d="m5 12 7-7 7 7" /></>,
  chevron: <path d="m9 18 6-6-6-6" />,
  key: <><path d="m15.5 7.5 2.3 2.3a1 1 0 0 0 1.4 0l2.1-2.1a1 1 0 0 0 0-1.4L19 4" /><path d="m21 2-9.6 9.6" /><circle cx="7.5" cy="15.5" r="5.5" /></>,
  monitor: <><rect width="20" height="14" x="2" y="3" rx="2" /><path d="M8 21h8" /><path d="M12 17v4" /></>,
  link: <><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" /><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" /></>,
  gift: <><rect x="3" y="8" width="18" height="4" rx="1" /><path d="M12 8v13" /><path d="M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7" /><path d="M7.5 8a2.5 2.5 0 0 1 0-5A4.8 8 0 0 1 12 8a4.8 8 0 0 1 4.5-5 2.5 2.5 0 0 1 0 5" /></>,
  layers: <><path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z" /><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65" /><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65" /></>,
  user: <><circle cx="12" cy="8" r="5" /><path d="M20 21a8 8 0 0 0-16 0" /></>,
  zap: <path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z" />,
  clone: <><rect width="14" height="14" x="8" y="8" rx="2" /><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" /></>,
  archive: <><rect width="20" height="5" x="2" y="3" rx="1" /><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8" /><path d="M10 12h4" /></>,
  image: <><rect width="18" height="18" x="3" y="3" rx="2" /><circle cx="9" cy="9" r="2" /><path d="m21 15-3.09-3.09a2 2 0 0 0-2.82 0L6 21" /></>,
  globe: <><circle cx="12" cy="12" r="10" /><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20" /><path d="M2 12h20" /></>,
  edit: <><path d="M12 20h9" /><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" /></>,
  clock: <><circle cx="12" cy="12" r="10" /><path d="M12 6v6l4 2" /></>,
  calendar: <><rect x="3" y="4" width="18" height="18" rx="2" /><path d="M16 2v4M8 2v4M3 10h18" /></>,
  mail: <><rect x="2" y="4" width="20" height="16" rx="2" /><path d="m22 7-10 6L2 7" /></>,
};

export type IconName = keyof typeof P;

export function Icon({ name, size = 16, stroke = 2, className }: { name: IconName; size?: number; stroke?: number; className?: string }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {P[name]}
    </svg>
  );
}

export function Logo({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="7" fill="#0c1114" stroke="#233139" />
      <g fill="none" stroke="#35d0a0" strokeWidth="2.2" strokeLinecap="round">
        <circle cx="16" cy="16" r="6.5" />
        <circle cx="16" cy="16" r="1.8" fill="#35d0a0" />
        <path d="M16 5v4.5M16 22.5V27M5 16h4.5M22.5 16H27M8.2 8.2l3.2 3.2M20.6 20.6l3.2 3.2M23.8 8.2l-3.2 3.2M11.4 20.6l-3.2 3.2" />
      </g>
    </svg>
  );
}

/* ---------------------------------------------------------------- layout */
export function PageHeader({ title, sub, actions }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {sub && <p className="sub">{sub}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

export function Card({ title, sub, icon, actions, children, flush, foot, className = "", style, id }: {
  title?: ReactNode; sub?: ReactNode; icon?: IconName; actions?: ReactNode; children?: ReactNode;
  flush?: boolean; foot?: ReactNode; className?: string; style?: React.CSSProperties; id?: string;
}) {
  return (
    <section className={`card ${className}`} style={style} id={id} tabIndex={id ? -1 : undefined}>
      {(title || actions) && (
        <div className="card-head">
          <div className="card-title">
            {icon && <span className="ic"><Icon name={icon} size={15} /></span>}
            <div>
              <h2>{title}</h2>
              {sub && <div className="sub">{sub}</div>}
            </div>
          </div>
          {actions && <div className="row">{actions}</div>}
        </div>
      )}
      <div className={`card-body ${flush ? "flush" : ""}`}>{children}</div>
      {foot && <div className="card-foot">{foot}</div>}
    </section>
  );
}

export function Stat({ label, value, unit, hint, icon, tone, accent }: {
  label: string; value: ReactNode; unit?: string; hint?: ReactNode; icon?: IconName; tone?: "good" | "bad"; accent?: boolean;
}) {
  return (
    <div className={`card stat ${accent ? "accent" : ""}`}>
      <div className="l">{icon && <Icon name={icon} size={14} />}{label}</div>
      <div className={`v ${tone ?? ""}`}>{value}{unit && <small>{unit}</small>}</div>
      {hint && <div className="h">{hint}</div>}
    </div>
  );
}

/** A dense strip of key numbers separated by hairlines. Every value must come from real data. */
export function Metrics({ items, label }: { items: { label: string; value: ReactNode; unit?: string; hint?: ReactNode; tone?: "good" | "bad" | "warn" }[]; label?: string }) {
  return (
    <section className="metrics" aria-label={label}>
      {items.map((m) => (
        <div className="metric" key={m.label}>
          <div className="l">{m.label}</div>
          <div className={`v ${m.tone === "warn" ? "warn-t" : m.tone ?? ""}`}>{m.value}{m.unit && <small>{m.unit}</small>}</div>
          {m.hint && <div className="h">{m.hint}</div>}
        </div>
      ))}
    </section>
  );
}

/** A failed load, shown instead of an empty state so a failure never looks like "nothing here". */
export function LoadError({ what, error, onRetry, busy }: { what: string; error: string; onRetry?: () => void; busy?: boolean }) {
  return (
    <div className="load-error" role="alert">
      <Icon name="xcircle" size={16} className="bad" />
      <span className="grow"><b>{what} couldn't be loaded.</b> {error}</span>
      {onRetry && <button type="button" className="btn sm secondary" onClick={onRetry} disabled={busy}>{busy ? <Spinner /> : <Icon name="refresh" size={13} />}Try again</button>}
    </div>
  );
}

export function Empty({ icon = "layers", title, text, action }: { icon?: IconName; title: string; text?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <div className="ic"><Icon name={icon} size={22} /></div>
      <h2>{title}</h2>
      {text && <p>{text}</p>}
      {action && <div style={{ marginTop: 6 }}>{action}</div>}
    </div>
  );
}

export function Callout({ tone = "info", children, icon }: { tone?: "info" | "warn" | "err" | "good"; children: ReactNode; icon?: IconName }) {
  const ic: IconName = icon ?? (tone === "err" ? "xcircle" : tone === "warn" ? "alert" : tone === "good" ? "checkcircle" : "info");
  return <div className={`callout ${tone}`} role={tone === "err" ? "alert" : undefined}><Icon name={ic} size={16} /><div className="grow">{children}</div></div>;
}

export const Spinner = () => <span className="spinner" role="status" aria-label="Loading" />;

export function Skeleton({ h = 16, w = "100%", style }: { h?: number; w?: number | string; style?: React.CSSProperties }) {
  return <div className="skel" style={{ height: h, width: w, ...style }} />;
}

export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="stack" role="status" style={{ padding: 16 }} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => <Skeleton key={i} h={14} w={`${90 - i * 12}%`} />)}
    </div>
  );
}

/* ---------------------------------------------------------------- controls */
export function Seg<T extends string>({ options, value, onChange, full, className = "" }: {
  options: [T, ReactNode][]; value: T; onChange: (v: T) => void; full?: boolean; className?: string;
}) {
  return (
    <div className={`seg ${full ? "full" : ""} ${className}`} role="group">
      {options.map(([k, l]) => (
        <button key={k} type="button" aria-pressed={value === k} className={`${value === k ? "on" : ""} ${k}`} onClick={() => onChange(k)}>{l}</button>
      ))}
    </div>
  );
}

export function Field({ label, hint, extra, children, style }: { label: ReactNode; hint?: ReactNode; extra?: ReactNode; children: ReactNode; style?: React.CSSProperties }) {
  return (
    <label className="field" style={style}>
      <span>{label}{extra && <em>{extra}</em>}</span>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}

export function UnitInput({ unit, ...props }: React.InputHTMLAttributes<HTMLInputElement> & { unit: string }) {
  return (
    <div className="input-wrap">
      <input inputMode="decimal" {...props} />
      <span className="unit">{unit}</span>
    </div>
  );
}

export function SearchInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <div className="input-wrap has-icon" style={{ minWidth: 200 }}>
      <span className="icon-left"><Icon name="search" size={14} /></span>
      <input {...props} />
    </div>
  );
}

/* ---------------------------------------------------------------- status */
const STATUS_TONE: Record<string, string> = {
  live: "good", confirmed: "good", draft: "", planned: "", archived: "", failed: "bad",
  completed: "good", cancelled: "", scheduled: "info", paused: "warn", executing: "warn",
  deploying: "warn", submitted: "warn", pending: "warn", partial: "warn", running: "warn", available: "good",
  managed: "info", watch: "", imported: "violet",
};

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const tone = STATUS_TONE[status] ?? "";
  const pulse = ["deploying", "submitted", "pending", "running", "executing"].includes(status);
  return <span className={`badge cap ${tone} ${pulse ? "pulse" : ""}`}><span className="dot" />{label ?? status.replace(/_/g, " ")}</span>;
}

/* ---------------------------------------------------------------- address */
export function CopyButton({ text, size = 13 }: { text: string; size?: number }) {
  const [ok, setOk] = useState(false);
  return (
    <button type="button" className="icon-btn" title={ok ? "Copied" : "Copy"} aria-label={ok ? "Copied" : "Copy to clipboard"} onClick={(e) => {
      e.stopPropagation(); e.preventDefault();
      navigator.clipboard?.writeText(text).then(() => { setOk(true); setTimeout(() => setOk(false), 1200); }).catch(() => undefined);
    }}>
      <Icon name={ok ? "check" : "copy"} size={size} />
    </button>
  );
}

export function Address({ address, type = "account", chars, network }: { address: string; type?: "account" | "token" | "tx"; chars?: number; network?: string | null }) {
  const label = chars ? `${address.slice(0, chars)}…${address.slice(-chars)}` : short(address);
  return (
    <span className="addr" onClick={(e) => e.stopPropagation()}>
      <a href={explorerUrl(network, type, address)} target="_blank" rel="noreferrer" title={address}>{label}</a>
      <CopyButton text={address} />
    </span>
  );
}

/* ---------------------------------------------------------------- chains */
/** A chain's mark with its name available to assistive tech. Prefer ChainLabel when there's room for the name. */
export function ChainDot({ network, size = 16 }: { network?: string | null; size?: number }) {
  return <ChainMark network={network} size={size} label />;
}

export function ChainBadge({ network, label }: { network?: string | null; label?: string }) {
  const c = chainOf(network);
  return (
    <span className="badge" style={{ color: c.color, background: `${c.color}14`, borderColor: `${c.color}40` }}>
      <span className="dot" />{label ?? c.name}
    </span>
  );
}

/* ---------------------------------------------------------------- token avatar */
function hue(s: string) {
  let h = 0;
  for (const c of s) h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
}
export function TokenAvatar({ symbol, size = 40 }: { symbol: string; size?: number }) {
  const h = hue(symbol || "?");
  return (
    <div className="tok" style={{
      width: size, height: size, fontSize: size * 0.34, borderRadius: size * 0.3,
      background: `linear-gradient(135deg, hsl(${h} 38% 34%), hsl(${(h + 40) % 360} 36% 22%))`,
    }} aria-hidden="true">{(symbol || "?").slice(0, 3).toUpperCase()}</div>
  );
}

/** The launch's own image when the API can serve it (deploying or live), otherwise the generated avatar. */
export function TokenImage({ launch, size = 40 }: { launch: { id: string; symbol: string; has_image?: boolean; status?: string; draft_revision?: number }; size?: number }) {
  const [broken, setBroken] = useState(false);
  const [privateImage, setPrivateImage] = useState<{key:string;url:string} | null>(null);
  const publicImage = launch.status === 'live' || launch.status === 'deploying';
  const key = `${launch.id}:${launch.draft_revision ?? 0}:${launch.has_image}:${publicImage}`;
  useEffect(()=>{
    let active=true, url:string | undefined;setBroken(false);
    if(launch.has_image && !publicImage)launchImage(launch.id).then(blob=>{if(active){url=URL.createObjectURL(blob);setPrivateImage({key,url});}}).catch(()=>{if(active)setBroken(true);});
    return ()=>{active=false;if(url)URL.revokeObjectURL(url);};
  },[key,launch.id,launch.has_image,publicImage]);
  const src = publicImage ? `${BASE}/public/launch-images/${launch.id}` : privateImage?.key===key ? privateImage.url : '';
  if (!launch.has_image || broken || !src) return <TokenAvatar symbol={launch.symbol} size={size} />;
  return <img src={src} alt={`${launch.symbol} token artwork`} width={size} height={size} onError={() => setBroken(true)}
    style={{ width: size, height: size, borderRadius: size * 0.3, objectFit: "cover", flex: "none", border: "1px solid var(--line-2)" }} />;
}

/* ---------------------------------------------------------------- modal */
/** A table's horizontal scroll area. When the table is wider than the screen it becomes a labelled, focusable
 * region so keyboard users can scroll it too (WCAG 2.1.1); otherwise it adds no tab stop. */
export function TableWrap({ children, label = "Table" }: { children: ReactNode; label?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [scrolls, setScrolls] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const check = () => setScrolls(el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1);
    check();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(check) : null;
    ro?.observe(el);
    if (el.firstElementChild) ro?.observe(el.firstElementChild);
    return () => ro?.disconnect();
  }, []);
  return (
    <div ref={ref} className="table-wrap" {...(scrolls ? { tabIndex: 0, role: "region", "aria-label": `${label} (scrollable)` } : {})}>
      {children}
    </div>
  );
}

export function Modal({ title, sub, onClose, children, foot, wide, lock }: {
  title: ReactNode; sub?: ReactNode; onClose: () => void; children: ReactNode; foot?: ReactNode; wide?: boolean; lock?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const [closing, setClosing] = useState(false);
  // Close through a short exit animation, then really unmount; `lock` (a spend in flight) still refuses.
  const close = useCallback(() => {
    if (lock || closing) return;
    setClosing(true);
    window.setTimeout(() => onClose(), 150);
  }, [lock, closing, onClose]);
  // Escape closes; Tab stays inside the dialog; focus returns to whatever opened it.
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !lock) close();
      if (e.key !== "Tab" || !ref.current) return;
      const f = [...ref.current.querySelectorAll<HTMLElement>("a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex='-1'])")]
        .filter((el) => el.offsetParent !== null);
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [close, lock]);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const el = ref.current;
    if (el && !el.contains(document.activeElement)) {
      const auto = el.querySelector<HTMLElement>("[autofocus]");
      (auto ?? el).focus();
    }
    // The page behind is inert (not focusable, hidden from assistive tech) and doesn't scroll while the dialog is
    // open. Nested dialogs keep a count so closing the inner one doesn't release the outer one's lock.
    const root = document.getElementById("root");
    const depth = Number(document.body.dataset.modals ?? 0) + 1;
    document.body.dataset.modals = String(depth);
    root?.setAttribute("inert", "");
    document.body.style.overflow = "hidden";
    return () => {
      const left = Number(document.body.dataset.modals ?? 1) - 1;
      document.body.dataset.modals = String(left);
      if (left <= 0) { root?.removeAttribute("inert"); document.body.style.overflow = ""; }
      opener?.focus?.();
    };
  }, []);
  return createPortal(
    <div className={`modal-bg ${closing ? "out" : ""}`} onMouseDown={lock ? undefined : close}>
      <div ref={ref} tabIndex={-1} className={`modal ${wide ? "wide" : ""} ${closing ? "out" : ""}`} onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <div className="modal-head">
          <div style={{ minWidth: 0 }}>
            <h2 id={titleId}>{title}</h2>
            {sub && <div className="muted small" style={{ marginTop: 2 }}>{sub}</div>}
          </div>
          <button className="icon-btn" onClick={close} disabled={lock} aria-label="Close"><Icon name="x" size={16} /></button>
        </div>
        <div className="modal-body">{children}</div>
        {foot && <div className="modal-foot">{foot}</div>}
      </div>
    </div>,
    document.body,
  );
}

/* ---------------------------------------------------------------- toasts */
type Toast = { id: number; tone: "good" | "bad" | "info"; text: string };
const ToastCtx = createContext<(tone: Toast["tone"], text: string) => void>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const n = useRef(0);
  const push = useCallback((tone: Toast["tone"], text: string) => {
    const id = ++n.current;
    setItems((x) => [...x, { id, tone, text }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), tone === "bad" ? 6000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      {createPortal(<div className="toasts" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`}>
            <Icon name={t.tone === "good" ? "checkcircle" : t.tone === "bad" ? "xcircle" : "info"} size={16} />
            <div className="grow">{t.text}</div>
          </div>
        ))}
      </div>, document.body)}
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

/* ---------------------------------------------------------------- time */
export function parseDate(s: string) {
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(s) ? s : s + "Z");
}
export function relTime(s: string) {
  const d = parseDate(s).getTime();
  const sec = Math.round((Date.now() - d) / 1000);
  if (sec < 45) return "just now";
  const m = Math.round(sec / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const dd = Math.round(h / 24);
  if (dd < 7) return `${dd}d ago`;
  return parseDate(s).toLocaleDateString(undefined, { month: "short", day: "numeric", year: dd > 300 ? "numeric" : undefined });
}
