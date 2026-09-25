import { useEffect, useRef, useState } from "react";
import { api, ApiError, solscan, short } from "../api";

type Phase = "loading" | "review" | "submitting" | "polling" | "done" | "error";

interface Props {
  title: string;
  path: string;
  body: Record<string, unknown>;
  onClose: () => void;
  onDone?: () => void;
}

const TERMINAL = ["confirmed", "failed", "partial"];

function Value({ v }: { v: unknown }) {
  if (Array.isArray(v))
    return (
      <div className="stack" style={{ gap: 2 }}>
        {v.map((x, i) => (
          <div key={i} className="mono">
            {typeof x === "object" && x !== null
              ? Object.entries(x as Record<string, unknown>).map(([k, val]) => `${k}: ${String(val)}`).join("  ·  ")
              : String(x)}
          </div>
        ))}
      </div>
    );
  return <span className="mono">{String(v)}</span>;
}

/** Every fund-moving action goes through here: preflight first, then confirm with the same hash. */
export default function SpendDialog({ title, path, body, onClose, onDone }: Props) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [pre, setPre] = useState<any>(null);
  const [hash, setHash] = useState("");
  const [totp, setTotp] = useState("");
  const [error, setError] = useState("");
  const [job, setJob] = useState<any>(null);
  const key = useRef(crypto.randomUUID());

  useEffect(() => {
    api(path, { body })
      .then((r) => {
        setPre(r.preflight);
        setHash(r.preflight_hash);
        setPhase("review");
      })
      .catch((e: ApiError) => {
        setError(e.detail);
        setPhase("error");
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function confirm() {
    setPhase("submitting");
    setError("");
    try {
      const r = await api(path, {
        body: { ...body, confirm: true, preflight_hash: hash, idempotency_key: key.current, totp: totp || undefined },
      });
      setPhase("polling");
      let j = await api(`/jobs/${r.job_id}`);
      while (!TERMINAL.includes(j.status)) {
        await new Promise((res) => setTimeout(res, 1500));
        j = await api(`/jobs/${r.job_id}`);
      }
      setJob(j);
      setPhase("done");
      onDone?.();
    } catch (e) {
      setError((e as ApiError).detail ?? String(e));
      setPhase("review");
    }
  }

  const { warnings = [], kind, ...rest } = pre ?? {};

  return (
    <div className="modal-bg" onClick={phase === "polling" || phase === "submitting" ? undefined : onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>{title}</h2>
        {phase === "loading" && <p className="muted">Building preflight…</p>}
        {phase === "error" && <div className="banner err">{error}</div>}
        {pre && phase !== "done" && (
          <>
            <div className="banner info">Review exactly what will happen. Nothing is sent until you confirm.</div>
            <div className="kv">
              {Object.entries(rest).map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}>
                  <div className="k">{k.replace(/_/g, " ")}</div>
                  <div><Value v={v} /></div>
                </div>
              ))}
            </div>
            {warnings.map((w: string) => (
              <div key={w} className="banner warn" style={{ marginTop: 12 }}>{w}</div>
            ))}
            {error && <div className="banner err" style={{ marginTop: 12 }}>{error}</div>}
            <div className="row" style={{ marginTop: 16 }}>
              <input
                placeholder="2FA code (if enabled)"
                value={totp}
                onChange={(e) => setTotp(e.target.value)}
                inputMode="numeric"
                style={{ width: 170 }}
              />
              <div className="spacer" style={{ flex: 1 }} />
              <button className="ghost" onClick={onClose} disabled={phase !== "review"}>Cancel</button>
              <button onClick={confirm} disabled={phase !== "review" || warnings.length > 0}>
                {phase === "review" ? `Confirm ${kind ?? ""}` : "Working…"}
              </button>
            </div>
          </>
        )}
        {phase === "done" && job && (
          <>
            <p>
              Result: <span className={`badge ${job.status}`}>{job.status}</span>
            </p>
            {job.error && <div className="banner err">{job.error}</div>}
            <table>
              <tbody>
                {job.transactions.map((t: any) => (
                  <tr key={t.id}>
                    <td>{t.kind}</td>
                    <td><span className={`badge ${t.status}`}>{t.status}</span></td>
                    <td className="mono">
                      {t.signature ? <a href={solscan(t.signature)} target="_blank" rel="noreferrer">{short(t.signature)}</a> : ""}
                      {t.error ? <span className="bad"> {t.error}</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="row" style={{ marginTop: 12, justifyContent: "flex-end" }}>
              <button onClick={onClose}>Close</button>
            </div>
          </>
        )}
        {phase === "error" && (
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button onClick={onClose}>Close</button>
          </div>
        )}
      </div>
    </div>
  );
}
