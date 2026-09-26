import { Fragment, ReactNode, useEffect, useRef, useState } from "react";
import { api, errMsg, fmt } from "../api";
import { chainOf, explorerUrl } from "../chains";
import { Address, Callout, Icon, Modal, Spinner, StatusBadge } from "./ui";

type Phase = "loading" | "review" | "submitting" | "polling" | "done" | "error";

interface Props {
  title: string;
  path: string;
  body: Record<string, unknown>;
  onClose: () => void;
  onDone?: () => void;
  network?: string;
}

const TERMINAL = ["confirmed", "failed", "partial"];
const HIDDEN = new Set(["launch_id"]);
const LABELS: Record<string, string> = {
  dev_buy_sol: "Dev buy", total_sol: "Total", slippage_pct: "Slippage", stagger_ms: "Delay between wallets",
  platform_fee: "Platform fee", amount_unit: "Unit", from: "From", to: "To", mint: "Token mint", dev_wallet: "Dev wallet",
  irreversible: "Irreversible", launchpad: "Launchpad", destination: "Destination", wallets: "Wallets", wallet: "Wallet",
  network: "Chain", coin: "Coin", total: "Total", max_gas: "Max gas", max_gas_each: "Max gas per transfer",
  percent: "Percent", tokens: "Tokens", side: "Side", amount: "Amount", mode: "Mode", name: "Name", symbol: "Ticker",
};
const KIND_LABEL: Record<string, string> = {
  deploy: "deployment", trade: "trade", fund: "funding", withdraw: "withdrawal", claim_fees: "claim", burn: "burn", send_tokens: "send",
};

const isAddr = (v: unknown): v is string => typeof v === "string" && (/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(v) || /^0x[0-9a-fA-F]{40}$/.test(v));

function Value({ k, v, network }: { k: string; v: unknown; network?: string }): ReactNode {
  const cur = chainOf(network).native;
  if (Array.isArray(v)) {
    return (
      <div className="stack tight" style={{ alignItems: "flex-end" }}>
        {v.map((x, i) => {
          if (isAddr(x)) return <Address key={i} address={x} network={network} />;
          if (x && typeof x === "object") {
            const o = x as Record<string, unknown>;
            return (
              <div key={i} className="row nowrap" style={{ gap: 10 }}>
                {isAddr(o.address) && <Address address={o.address} network={network} />}
                {typeof o.sol === "number" && <span className="num" style={{ fontWeight: 600 }}>{fmt(o.sol)} {cur}</span>}
                {typeof o.amount === "number" && <span className="num" style={{ fontWeight: 600 }}>{fmt(o.amount, 6)} {cur}</span>}
              </div>
            );
          }
          return <span key={i} className="mono">{String(x)}</span>;
        })}
        <span className="muted small">{v.length} item{v.length === 1 ? "" : "s"}</span>
      </div>
    );
  }
  if (isAddr(v)) return <Address address={v} type={k === "mint" ? "token" : "account"} network={network} />;
  if (typeof v === "boolean") return <span className={v && k === "irreversible" ? "bad" : ""} style={{ fontWeight: 600 }}>{v ? "Yes" : "No"}</span>;
  if (typeof v === "number") {
    const unit = k.endsWith("_sol") || ["total", "max_gas", "max_gas_each"].includes(k) ? ` ${cur}` : k.endsWith("_pct") || k === "percent" ? "%" : k.endsWith("_ms") ? " ms" : "";
    return <span className="num" style={{ fontWeight: 600 }}>{fmt(v, 6)}{unit}</span>;
  }
  if (k === "network") return <span style={{ fontWeight: 600 }}>{chainOf(String(v)).name}</span>;
  if (k === "side") return <span className={`badge cap ${v === "buy" ? "good" : "bad"}`}>{String(v)}</span>;
  return <span>{String(v ?? "—")}</span>;
}

function Steps({ phase }: { phase: Phase }) {
  const cur = phase === "loading" || phase === "error" ? 0 : phase === "done" ? 2 : 1;
  return (
    <div className="steps">
      {["Preview", "Confirm", "Result"].map((l, i) => {
        const done = i < cur || (phase === "done" && i === 2);
        return (
          <span key={l} className="row nowrap" style={{ gap: 6 }}>
            {i > 0 && <span className="sep" />}
            <span className={`s ${done ? "done" : i === cur ? "on" : ""}`}>
              <span className="n">{done ? <Icon name="check" size={11} stroke={3} /> : i + 1}</span>{l}
            </span>
          </span>
        );
      })}
    </div>
  );
}

function Otp({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const refs = useRef<(HTMLInputElement | null)[]>([]);
  const set = (i: number, ch: string) => {
    const digits = ch.replace(/\D/g, "");
    if (digits.length > 1) { onChange(digits.slice(0, 6)); refs.current[Math.min(5, digits.length)]?.focus(); return; }
    const arr = value.padEnd(6, " ").split("");
    arr[i] = digits || " ";
    onChange(arr.join("").trimEnd().replace(/ /g, ""));
    if (digits && i < 5) refs.current[i + 1]?.focus();
  };
  return (
    <div className="otp">
      {Array.from({ length: 6 }).map((_, i) => (
        <input key={i} ref={(el) => (refs.current[i] = el)} value={value[i] ?? ""} inputMode="numeric" autoComplete={i === 0 ? "one-time-code" : "off"}
          maxLength={6} aria-label={`Digit ${i + 1}`} autoFocus={i === 0}
          onChange={(e) => set(i, e.target.value)}
          onKeyDown={(e) => { if (e.key === "Backspace" && !value[i] && i > 0) refs.current[i - 1]?.focus(); }} />
      ))}
    </div>
  );
}

/** Every fund-moving action goes through here: preflight first, then confirm with the same hash. */
export default function SpendDialog({ title, path, body, onClose, onDone, network = "solana" }: Props) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [pre, setPre] = useState<any>(null);
  const [hash, setHash] = useState("");
  const [totp, setTotp] = useState("");
  const [needs2fa, setNeeds2fa] = useState(false);
  const [error, setError] = useState("");
  const [job, setJob] = useState<any>(null);
  const key = useRef(crypto.randomUUID());

  useEffect(() => {
    api(path, { body })
      .then((r) => { setPre(r.preflight); setHash(r.preflight_hash); setNeeds2fa(Boolean(r.requires_2fa)); setPhase("review"); })
      .catch((e) => { setError(errMsg(e)); setPhase("error"); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function confirm() {
    setPhase("submitting");
    setError("");
    try {
      const r = await api(path, { body: { ...body, confirm: true, preflight_hash: hash, idempotency_key: key.current, totp: totp || undefined } });
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
      setError(errMsg(e));
      setPhase("review");
    }
  }

  const { warnings = [], kind, ...rest } = pre ?? {};
  const rows = Object.entries(rest).filter(([k]) => !HIDDEN.has(k));
  const busy = phase === "submitting" || phase === "polling";
  const blocked = warnings.length > 0;
  const danger = kind === "burn" || kind === "withdraw" || kind === "send_tokens";

  const foot =
    phase === "done" || phase === "error" ? <button className="btn" onClick={onClose}>Close</button> :
    phase === "loading" ? <button className="btn ghost" onClick={onClose}>Cancel</button> : (
      <>
        <button className="btn ghost" onClick={onClose} disabled={busy}>Cancel</button>
        <button className={`btn ${danger ? "danger" : ""}`} onClick={confirm} disabled={busy || blocked || (needs2fa && totp.length !== 6)}>
          {busy ? <><Spinner />{phase === "polling" ? "Waiting for confirmation…" : "Sending…"}</> : <><Icon name="check" size={15} />Confirm {KIND_LABEL[kind] ?? kind ?? ""}</>}
        </button>
      </>
    );

  return (
    <Modal title={title} sub={<Steps phase={phase} />} onClose={onClose} foot={foot} lock={busy}>
      {phase === "loading" && (
        <div className="row" style={{ padding: "24px 0", justifyContent: "center", gap: 10 }}><Spinner /><span className="muted">Building preview and simulating…</span></div>
      )}
      {phase === "error" && <Callout tone="err"><b>Couldn't build this action.</b><br />{error}</Callout>}

      {pre && phase !== "done" && phase !== "error" && (
        <>
          <Callout icon="eye">Review exactly what will happen. <b>Nothing is sent until you confirm.</b></Callout>
          <div className="kv">
            {rows.map(([k, v]) => (
              <Fragment key={k}>
                <div className="k">{LABELS[k] ?? k.replace(/_/g, " ")}</div>
                <div className="v"><Value k={k} v={v} network={network} /></div>
              </Fragment>
            ))}
          </div>
          {warnings.map((w: string) => <Callout key={w} tone="warn"><b>Blocked:</b> {w}</Callout>)}
          {error && <Callout tone="err">{error}</Callout>}
          {needs2fa && !blocked && (
            <div className="stack tight" style={{ alignItems: "center", padding: "6px 0" }}>
              <span className="small text-2 row" style={{ gap: 6 }}><Icon name="shield" size={14} />Enter your 2FA code to authorise</span>
              <Otp value={totp} onChange={setTotp} />
            </div>
          )}
        </>
      )}

      {phase === "done" && job && (
        <>
          <div className="stack" style={{ alignItems: "center", textAlign: "center", padding: "8px 0" }}>
            <div className={`feed-ic ${job.status === "confirmed" ? "good" : job.status === "failed" ? "bad" : "warn"}`} style={{ width: 48, height: 48, borderRadius: 14 }}>
              <Icon name={job.status === "confirmed" ? "checkcircle" : job.status === "failed" ? "xcircle" : "alert"} size={24} />
            </div>
            <h2>{job.status === "confirmed" ? "Confirmed on-chain" : job.status === "failed" ? "Failed" : "Partly completed"}</h2>
          </div>
          {job.error && <Callout tone="err">{job.error}</Callout>}
          <div className="card" style={{ background: "var(--bg-2)" }}>
            <div className="table-wrap"><table>
              <tbody>
                {job.transactions.map((t: any) => (
                  <tr key={t.id}>
                    <td>{t.kind}</td>
                    <td><StatusBadge status={t.status} /></td>
                    <td className="r mono">
                      {t.signature ? <a href={explorerUrl(network, "tx", t.signature)} target="_blank" rel="noreferrer">View on explorer <Icon name="ext" size={11} /></a> : null}
                      {t.error ? <span className="bad small"> {t.error}</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          </div>
        </>
      )}
    </Modal>
  );
}
