import { useState } from "react";
import { formatRaw } from "../amounts";
import { chainOf } from "../chains";
import { api, errMsg, fmt } from "../api";
import { Callout, Card, CopyButton, Icon, Loading, Spinner, useToast } from "./ui";

export interface ReferralSummary {
  codes: { code: string; uses: number; max_uses: number }[];
  referred_users: number;
  earned_sol: number;
  owed_sol: number;
  payout_address: string | null;
  share_pct: number;
  fee_pct: number;
  totp_enabled: boolean;
  earnings?: { network: string; asset: string; decimals: number; paid_raw: string; owed_raw: string }[];
  settlement_note?: string;
  payout_addresses?: { network: string; address: string }[];
}

export default function ReferralCard({ data, onChange }: { data: ReferralSummary | null; onChange: () => void }) {
  const toast = useToast();
  const [addr, setAddr] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [payoutNetwork, setPayoutNetwork] = useState("solana");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const link = (code: string) => `${window.location.origin}/login?ref=${code}`;

  const savePayout = () => {
    setBusy(true);
    api("/referrals/payout", { method: "PUT", body: { address: addr.trim(), network: payoutNetwork, password, totp } })
      .then(() => { toast("good", "Payout address saved"); setEditing(false); setAddr(""); setPassword(""); setTotp(""); onChange(); })
      .catch((e) => toast("bad", errMsg(e)))
      .finally(() => setBusy(false));
  };

  return (
    <Card title="Referrals" icon="gift"
      sub={data && data.fee_pct > 0 ? `Earn ${data.share_pct}% of collected Helm fees from people you invite` : "Invite people to Helm Terminals"}
      actions={
        <button className="btn sm secondary" onClick={() => api("/referrals", { method: "POST", body: {} })
          .then(() => { toast("good", "Referral code created"); onChange(); }).catch((e) => toast("bad", errMsg(e)))}>
          <Icon name="plus" size={13} />New code
        </button>} flush>
      {!data ? <Loading rows={3} /> : (
        <>
          <div className="grid" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 0, borderBottom: "1px solid var(--line)" }}>
            {[
              ["Referred", String(data.referred_users), ""],
              ["Paid to wallet", fmt(data.earned_sol, 4), "SOL"],
              ["Owed to you", fmt(data.owed_sol, 4), "SOL"],
            ].map(([l, v, u], i) => (
              <div key={l} style={{ padding: "12px 18px", borderLeft: i ? "1px solid var(--line)" : undefined }}>
                <div className="muted small">{l}</div>
                <div className="num" style={{ font: "500 17px var(--mono)" }}>{v} <span className="muted small">{u}</span></div>
              </div>
            ))}
          </div>

          <div className="stack tight" style={{ padding: "18px", borderBottom: "1px solid var(--line)" }}>
            <b>30% of the fees your referrals pay</b><p className="small text-2">A $100 trade pays a $2.50 Helm fee and earns its referrer $0.75. Free launches generate no fee or commission.</p>
            {data.earnings?.map(row => <div className="row between small" key={`${row.network}:${row.asset}`}><span>{chainOf(row.network).name} · {row.asset}</span><span className="mono">{formatRaw(row.paid_raw, row.decimals)} paid · {formatRaw(row.owed_raw, row.decimals)} owed</span></div>)}
            <p className="small muted">{data.settlement_note ?? "Confirmed payments only. Owed balances have not yet been paid to your wallet."}</p>
          </div>
          <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--line)" }} className="stack tight">
            <div className="row between">
              <span className="small text-2" style={{ fontWeight: 600 }}>Payout address</span>
              {!editing && <button className="btn sm ghost" onClick={() => setEditing(true)}>Add or change</button>}
            </div>
            {!editing && (data.payout_addresses?.length || data.payout_address) ? (
              <div className="stack tight">{(data.payout_addresses?.length ? data.payout_addresses : [{ network: "solana", address: data.payout_address! }]).map(row =>
                <div key={row.network}><span className="small muted">{chainOf(row.network).name}</span><div className="mono small" style={{ overflowWrap: "anywhere" }}>{row.address}</div></div>)}</div>
            ) : !editing ? <p className="small muted">Add a payout address when you are ready to receive earnings.</p> : (
              <>
                {!data.payout_address && data.fee_pct > 0 && (
                  <Callout tone="warn">Add your payout address. Asset balances marked owed remain visible until settlement; entering an address does not move treasury funds.</Callout>
                )}
                {!data.totp_enabled ? (
                  <Callout tone="warn">Enable two-factor authentication in Settings before setting or changing this payout address.</Callout>
                ) : (
                  <div className="stack tight">
                    <label className="small">Network<select value={payoutNetwork} onChange={event => setPayoutNetwork(event.target.value)}>{["solana", "base", "ethereum", "bsc", "robinhood", "tron", "near", "arc"].map(network => <option key={network} value={network}>{chainOf(network).name}</option>)}</select></label>
                    <input className="mono" name="referral_payout_address" autoComplete="off" aria-label="Referral payout address" placeholder="Your address on the selected network" value={addr} onChange={(e) => setAddr(e.target.value)} />
                    <div className="row nowrap">
                      <input type="password" autoComplete="current-password" aria-label="Confirm password" placeholder="Confirm your password" value={password} onChange={(e) => setPassword(e.target.value)} />
                      <input inputMode="numeric" autoComplete="one-time-code" aria-label="Authenticator code" placeholder="2FA code" maxLength={8} value={totp} onChange={(e) => setTotp(e.target.value.replace(/\D/g, ""))} />
                      <button className="btn" disabled={addr.trim().length < 2 || !password || totp.length < 6 || busy} onClick={savePayout}>{busy && <Spinner />}Save</button>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>

          {data.codes.length === 0 ? (
            <p className="muted small" style={{ padding: "12px 18px" }}>No referral codes yet. Use <b className="text-2">New code</b> to create an invite link.</p>
          ) : (
            <div className="feed">
              {data.codes.map((c) => (
                <div key={c.code} className="feed-item" style={{ alignItems: "center" }}>
                  <div className="grow">
                    <div className="row nowrap" style={{ gap: 4 }}>
                      <span className="mono" style={{ fontWeight: 600, letterSpacing: "0.06em" }}>{c.code}</span>
                      <CopyButton text={c.code} />
                    </div>
                    <div className="row nowrap small muted" style={{ gap: 4 }}>
                      <span className="truncate">{link(c.code)}</span><CopyButton text={link(c.code)} />
                    </div>
                  </div>
                  <span className="muted small num">{c.uses} signups</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </Card>
  );
}
