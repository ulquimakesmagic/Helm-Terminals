import { Fragment, ReactNode, useEffect, useRef, useState } from "react";
import { api, errMsg, fmt } from "../api";
import { chainOf, explorerUrl } from "../chains";
import { isAddress as isAddr } from "../validate";
import { ReviewHero, summarize } from "./review";
import {
  Address, Callout, Icon, Modal, Spinner, StatusBadge, TableWrap,
} from "./ui";

type Phase = "loading" | "review" | "submitting" | "polling" | "done" | "error";

interface Props {
  title: string;
  path: string;
  body: Record<string, unknown>;
  onClose: () => void;
  onDone?: () => void;
  network?: string;
}

const TERMINAL = ["confirmed", "failed", "partial", "unconfirmed"];
const HIDDEN = new Set(["launch_id", "network_fee_coin", "own_wallets_only", "chain"]);
const LABELS: Record<string, string> = {
  dev_buy_sol: "Dev buy", total_sol: "Total", slippage_pct: "Slippage", stagger_ms: "Delay between wallets",
  platform_fee: "Platform fee", amount_unit: "Unit", from: "From", to: "To", mint: "Token mint", dev_wallet: "Dev wallet",
  irreversible: "Irreversible", launchpad: "Launchpad", destination: "Destination", wallets: "Wallets", wallet: "Wallet",
  network: "Chain", coin: "Coin", total: "Total", max_gas: "Max gas", max_gas_each: "Max gas per transfer",
  percent: "Percent", tokens: "Tokens", side: "Side", amount: "Amount", mode: "Mode", name: "Name", symbol: "Ticker",
  asset: "Asset", token: "Token", chain: "Chain", split: "Split", transactions: "Transactions",
  network_fees_max: "Max network fees", skipped_empty: "Skipped (nothing to move)", transparency: "Transparency",
  send: "Send", send_asset: "Pay with", receive_asset: "Receive", from_chain: "From chain", to_chain: "To chain",
  provider: "Route provider", quote: "Live quote", funding: "Funding from USDC", funding_detail: "Funding covers",
  dev_buy_usd: "Dev buy (USD, est.)", from_address: "From address", route_fee: "Route fee", receive: "Receive", leftover: "Leftover",
  covers: "Covers", helm_creator_share: "Helm's share of your creator rewards", helm_fees: "All Helm fees",
  creator_fees: "Creator fees", platform_share: "Helm's reward share", commission: "Launchpad commission",
  commission_receiver: "Flap commission recipient", claim_scope: "Rewards included in this claim",
  pons_rewards: "Pons reward recipients", reward_terms: "Permanent reward terms",
};
const KIND_LABEL: Record<string, string> = {
  deploy: "deployment", trade: "trade", fund: "funding", withdraw: "withdrawal", claim_fees: "claim", burn: "burn", send_tokens: "send",
  disperse: "disperse", consolidate: "consolidate", bridge: "route", swap: "swap", sweep: "sweep", fund_cash: "cash fund",
};


function Value({ k, v, network, asset }: { k: string; v: unknown; network?: string; asset?: string }): ReactNode {
  const coin = chainOf(network).native;
  const cur = asset ?? coin; // what amounts are counted in: the moved asset when there is one
  if (k === "quote_bounds" && v && typeof v === "object") {
    const bound = v as {minimum_raw: string; asset: string; network: string};
    return <div className="stack tight"><b>Minimum {bound.minimum_raw} base units</b><span>{chainOf(bound.network).name} · <Address address={bound.asset} network={bound.network} /></span><small>Locked to this preview. A lower output or expired quote requires confirmation again.</small></div>;
  }
  if (k === "pons_rewards" && v && typeof v === "object") {
    const r = v as { creator: string; treasury: string; splitter: string };
    return <div className="stack tight">
      <span>Creator 94% <Address address={r.creator} network={network} /></span>
      <span>Helm 6% <Address address={r.treasury} network={network} /></span>
      <span>Launch reward address <Address address={r.splitter} network={network} /></span>
    </div>;
  }
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
  if (k === "helm_creator_share" && v && typeof v === "object") {
    // The creator confirms this split with the rest of the preview; it only applies to this new launch.
    const c = v as { bps: number; note: string; recipient?: string; creator?: string; creator_bps?: number };
    if (!c.bps) return <span>{c.note}</span>;
    return (
      <div className="stack tight" style={{ alignItems: "flex-end" }}>
        <span style={{ fontWeight: 600 }}>{c.note}</span>
        {c.creator && <span className="row nowrap" style={{ gap: 8 }}><span className="muted small">Creator {(c.creator_bps ?? 10_000 - c.bps) / 100}%</span><Address address={c.creator} network={network} /></span>}
        {c.recipient && <span className="row nowrap" style={{ gap: 8 }}><span className="muted small">Helm {c.bps / 100}%</span><Address address={c.recipient} network={network} /></span>}
      </div>
    );
  }
  if ((k === "funding" || k === "funding_detail") && v && typeof v === "object") {
    const o = v as Record<string, unknown>;
    const skip = new Set(["from_wallet_id", "from_network", "amount_usd"]);
    return (
      <div className="stack tight" style={{ alignItems: "flex-end" }}>
        {Object.entries(o).filter(([kk]) => !skip.has(kk)).map(([kk, x]) => (
          <span key={kk} className="row nowrap" style={{ gap: 8 }}>
            <span className="muted small">{LABELS[kk] ?? kk.replace(/_/g, " ")}</span>
            {isAddr(x) ? <Address address={x} network={String(o.from_network ?? network)} /> : <span>{String(x)}</span>}
          </span>
        ))}
      </div>
    );
  }
  if (k === "quote" && v && typeof v === "object") {
    const q = v as { receive_estimate?: number; receive_minimum?: number; receive_symbol?: string; time_estimate_s?: number;
      fees_usd?: { network_gas?: number; relayer?: number; helm_route_fee?: number } };
    const f = q.fees_usd ?? {};
    return (
      <div className="stack tight" style={{ alignItems: "flex-end" }}>
        <span className="num" style={{ fontWeight: 600 }}>≈ {fmt(q.receive_estimate ?? 0, 6)} {q.receive_symbol}</span>
        <span className="muted small">at least {fmt(q.receive_minimum ?? 0, 6)} {q.receive_symbol}{q.time_estimate_s ? ` · about ${Math.max(1, Math.round(q.time_estimate_s))}s` : ""}</span>
        <span className="muted small">fees ≈ ${fmt((f.network_gas ?? 0) + (f.relayer ?? 0), 2)} Relay + gas · ${fmt(f.helm_route_fee ?? 0, 2)} Helm</span>
      </div>
    );
  }
  if (v && typeof v === "object" && isAddr((v as Record<string, unknown>).address)) {
    const o = v as Record<string, unknown>;
    return <span className="row nowrap" style={{ gap: 8 }}>{typeof o.label === "string" && <span className="muted small">{o.label}</span>}<Address address={o.address as string} network={network} /></span>;
  }
  if (isAddr(v)) return <Address address={v} type={k === "mint" || k === "token" ? "token" : "account"} network={network} />;
  if (typeof v === "boolean") return <span className={v && k === "irreversible" ? "bad" : ""} style={{ fontWeight: 600 }}>{v ? "Yes" : "No"}</span>;
  if (typeof v === "number" && k.endsWith("_usd")) return <span className="num" style={{ fontWeight: 600 }}>${fmt(v, 2)}</span>;
  if (typeof v === "number") {
    const unit = ["max_gas", "max_gas_each", "network_fees_max"].includes(k) ? ` ${coin}` : k.endsWith("_sol") || k === "total" ? ` ${cur}` : k === "send" ? "" : k.endsWith("_pct") || k === "percent" ? "%" : k.endsWith("_ms") ? " ms" : "";
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
  const [quoteApproval, setQuoteApproval] = useState<string | undefined>();
  const [totp, setTotp] = useState("");
  const [needs2fa, setNeeds2fa] = useState(false);
  const [error, setError] = useState("");
  const [job, setJob] = useState<any>(null);
  const [jobId, setJobId] = useState("");
  const [lost, setLost] = useState(false);
  const [route, setRoute] = useState<any>(null);
  const key = useRef(crypto.randomUUID());

  useEffect(() => {
    api(path, { body })
      .then((r) => { setPre(r.preflight); setHash(r.preflight_hash); setQuoteApproval(r.quote_approval); setNeeds2fa(Boolean(r.requires_2fa)); setPhase("review"); })
      .catch((e) => { setError(errMsg(e)); setPhase("error"); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A bridge job ends when the origin transaction is settled; Relay's destination fill continues on its own.
  // Keep asking for the route status so the result shows the second leg (or its refund) instead of stopping early.
  useEffect(() => {
    if (phase !== "done" || pre?.kind !== "bridge" || !jobId) return;
    let live = true, tries = 0, timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const r = await api<any>(`/bridge/${jobId}`);
        if (!live) return;
        setRoute(r);
        if (["success", "failure", "refund"].includes(r.route_status) || tries >= 11) return;
      } catch { if (!live) return; }
      tries += 1;
      timer = setTimeout(poll, 5000);
    };
    void poll();
    return () => { live = false; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, pre?.kind, jobId]);

  async function confirm() {
    setPhase("submitting");
    setError("");
    try {
      const r = await api(path, { body: { ...body, confirm: true, preflight_hash: hash, quote_approval: quoteApproval, idempotency_key: key.current, totp: totp || undefined } });
      setJobId(r.job_id);
      setPhase("polling");
      // Every half second at first (most transactions land within a few seconds), then every 1.5 s. A dropped
      // connection doesn't end the wait: the job keeps running on the server, so keep asking.
      const started = Date.now();
      let j: any = { status: r.status };
      let misses = 0;
      while (!TERMINAL.includes(j.status)) {
        await new Promise((res) => setTimeout(res, Date.now() - started < 10_000 ? 500 : 1500));
        try { j = await api(`/jobs/${r.job_id}`); misses = 0; setLost(false); }
        catch { if (++misses >= 3) setLost(true); }
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
  const rows = Object.entries(rest).filter(([k, v]) => !HIDDEN.has(k) && v !== null);
  const busy = phase === "submitting" || phase === "polling";
  const blocked = warnings.length > 0;
  const danger = kind === "burn" || kind === "withdraw" || kind === "send_tokens";
  const summary = summarize(pre, network);
  const foot =
    phase === "done" || phase === "error" ? <button className="btn" onClick={onClose}>Close</button> :
    phase === "loading" ? <button className="btn ghost" onClick={onClose}>Cancel</button> : (
      <>
        <button className="btn ghost" onClick={onClose} disabled={busy}>Cancel</button>
        <button className={`btn ${danger ? "danger" : ""}`} onClick={confirm} disabled={busy || blocked || (needs2fa && totp.length !== 6)}>
          {busy ? <><Spinner />{phase === "polling" ? (kind === "deploy" && pre?.funding ? "Routing USDC, then deploying…" : "Waiting for confirmation…") : "Sending…"}</> : <><Icon name="check" size={15} />Confirm {KIND_LABEL[kind] ?? kind ?? ""}</>}
        </button>
      </>
    );

  return (
    <Modal title={title} sub={<Steps phase={phase} />} onClose={onClose} foot={foot} lock={phase === "submitting"}>
      {phase === "loading" && (
        <div className="row" style={{ padding: "24px 0", justifyContent: "center", gap: 10 }} role="status"><Spinner /><span className="muted">Building the preview and checking balances…</span></div>
      )}
      {phase === "error" && <Callout tone="err"><b>Couldn't build this action.</b><br />{error}</Callout>}

      {pre && phase !== "done" && phase !== "error" && (
        <>
          {summary && <ReviewHero s={summary} />}
          {warnings.map((w: string) => <Callout key={w} tone="warn"><b>Blocked:</b> {w}</Callout>)}
          {summary && !blocked && (
            <div className="review-section">
              <h3>What happens when you confirm</h3>
              <ol className="next-steps">{summary.next.map((n) => <li key={n}>{n}</li>)}</ol>
            </div>
          )}
          <details className="more" open={!summary}>
            <summary>Full preview · every field the server will act on</summary>
            <div className="kv" style={{ marginTop: 4 }}>
              {rows.map(([k, v]) => (
                <Fragment key={k}>
                  <div className="k">{LABELS[k] ?? k.replace(/_/g, " ")}</div>
                  <div className="v"><Value k={k} v={v} network={network} asset={typeof pre?.asset === "string" ? pre.asset : undefined} /></div>
                </Fragment>
              ))}
            </div>
          </details>
          <p className="muted small row" style={{ gap: 6, flexWrap: "nowrap", alignItems: "flex-start" }}><Icon name="eye" size={13} />Nothing is sent until you confirm. The preview is tied to this exact request.</p>
          {error && <Callout tone="err">{error}</Callout>}
          {needs2fa && !blocked && (
            <div className="stack tight" style={{ alignItems: "center", padding: "6px 0" }}>
              <span className="small text-2 row" style={{ gap: 6 }}><Icon name="shield" size={14} />Enter your 2FA code to authorise</span>
              <Otp value={totp} onChange={setTotp} />
            </div>
          )}
        </>
      )}

      {phase === "polling" && (
        <Callout tone={lost ? "warn" : "info"}>
          {lost
            ? <><b>Can't reach Helm right now.</b> Your confirmation was received and keeps running on the server; this window will update when the connection comes back. Don't confirm it again.</>
            : <><b>Submitted.</b> Signed and sent; waiting for the chain to confirm. You can close this window: the result will show in the Transactions tab.</>}
          {jobId && <span className="muted small"> · job {jobId.slice(0, 8)}</span>}
        </Callout>
      )}
      {phase === "done" && job && kind === "bridge" && (
        route?.route_status === "success" ? (
          <Callout icon="checkcircle"><b>Filled on {String(pre?.to_chain ?? "the destination chain")}.</b> Relay credited the destination wallet
            {route.destination_txs?.length ? <> · destination transaction <span className="mono">{String(route.destination_txs[0]).slice(0, 18)}…</span></> : null}.</Callout>
        ) : route?.route_status === "failure" || route?.route_status === "refund" ? (
          <Callout tone="warn"><b>Relay reported {route.route_status}.</b> The route didn't complete; the amount is refunded to the source wallet. Nothing more is retried automatically.</Callout>
        ) : (
          <Callout tone="info"><b>The origin leg is settled; Relay is filling on {String(pre?.to_chain ?? "the destination chain")}.</b> This panel follows the fill{job.status !== "confirmed" ? "" : " — you can close the window and check the Transactions tab later"}.</Callout>
        )
      )}
      {phase === "done" && job && (
        <>
          <div className="stack" style={{ alignItems: "center", textAlign: "center", padding: "8px 0" }}>
            <div className={`feed-ic ${job.status === "confirmed" ? "good" : job.status === "failed" ? "bad" : "warn"}`} style={{ width: 48, height: 48, borderRadius: 14 }}>
              <Icon name={job.status === "confirmed" ? "checkcircle" : job.status === "failed" ? "xcircle" : "alert"} size={24} />
            </div>
            <h2 role="status">{job.status === "confirmed" ? "Confirmed on-chain" : job.status === "failed" ? "Failed"
              : job.status === "unconfirmed" ? "Sent, not confirmed yet" : "Partly completed"}</h2>
            <p className="muted small">{job.transactions.length} transaction{job.transactions.length === 1 ? "" : "s"} recorded{job.status !== "confirmed" ? ". Nothing is retried automatically." : "."}</p>
            {job.status === "unconfirmed" && (
              <p className="small text-2" style={{ maxWidth: 420 }}>At least one transaction was sent but didn't confirm in time. It may still land: check it on the explorer before doing this again, or you could do it twice.</p>
            )}
          </div>
          {job.error && <Callout tone="err">{job.error}</Callout>}
          <div className="card" style={{ background: "var(--bg-2)" }}>
            <TableWrap><table>
              <tbody>
                {job.transactions.map((t: any) => (
                  <tr key={t.id}>
                    <td style={{ textTransform: "capitalize" }}>{String(t.kind).replace(/_/g, " ")}</td>
                    <td><StatusBadge status={t.status} /></td>
                    <td className="r mono">
                      {t.signature ? <a href={explorerUrl(String(t.kind).startsWith("fund_route") ? String(pre?.funding?.from_network ?? network) : network, "tx", t.signature)} target="_blank" rel="noreferrer">View on explorer <Icon name="ext" size={11} /></a> : null}
                      {t.error ? <span className="bad small"> {t.error}</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table></TableWrap>
          </div>
        </>
      )}
    </Modal>
  );
}
