import { useState } from "react";
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
}

export default function ReferralCard({ data, onChange }: { data: ReferralSummary | null; onChange: () => void }) {
  const toast = useToast();
  const [addr, setAddr] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const link = (code: string) => `${window.location.origin}/login?ref=${code}`;

  const savePayout = () => {
    setBusy(true);
    api("/referrals/payout", { method: "PUT", body: { address: addr.trim(), password, totp } })
      .then(() => { toast("good", "Payout address saved"); setEditing(false); setAddr(""); setPassword(""); setTotp(""); onChange(); })
      .catch((e) => toast("bad", errMsg(e)))
      .finally(() => setBusy(false));
  };

  return (
    <Card title="Referrals" icon="gift"
      sub={data && data.fee_pct > 0 ? `Earn ${data.share_pct}% of the trading fees from people you invite` : "Invite people to Helm Terminals"}
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
              ["Earned", fmt(data.earned_sol, 4), "SOL"],
              ["Owed to you", fmt(data.owed_sol, 4), "SOL"],
            ].map(([l, v, u], i) => (
              <div key={l} style={{ padding: "12px 18px", borderLeft: i ? "1px solid var(--line)" : undefined }}>
                <div className="muted small">{l}</div>
                <div className="num" style={{ font: "500 17px var(--mono)" }}>{v} <span className="muted small">{u}</span></div>
              </div>
            ))}
          </div>

          <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--line)" }} className="stack tight">
            <div className="row between">
              <span className="small text-2" style={{ fontWeight: 600 }}>Payout address (Solana)</span>
              {data.payout_address && !editing && <button className="btn sm ghost" onClick={() => setEditing(true)}>Change</button>}
            </div>
            {data.payout_address && !editing ? (
              <span className="mono small" style={{ wordBreak: "break-all" }}>{data.payout_address}</span>
            ) : (
              <>
                {!data.payout_address && data.fee_pct > 0 && (
                  <Callout tone="warn">Add a payout address so your share is paid on-chain with each trade. Until then it's recorded as owed.</Callout>
                )}
                {!data.totp_enabled ? (
                  <Callout tone="warn">Enable two-factor authentication in Settings before setting or changing this payout address.</Callout>
                ) : (
                  <div className="stack tight">
                    <input className="mono" aria-label="Referral payout address (Solana)" placeholder="Your Solana wallet address" value={addr} onChange={(e) => setAddr(e.target.value)} />
                    <div className="row nowrap">
                      <input type="password" autoComplete="current-password" aria-label="Confirm password" placeholder="Confirm your password" value={password} onChange={(e) => setPassword(e.target.value)} />
                      <input inputMode="numeric" autoComplete="one-time-code" aria-label="Authenticator code" placeholder="2FA code" maxLength={8} value={totp} onChange={(e) => setTotp(e.target.value.replace(/\D/g, ""))} />
                      <button className="btn" disabled={addr.trim().length < 32 || !password || totp.length < 6 || busy} onClick={savePayout}>{busy && <Spinner />}Save</button>
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
