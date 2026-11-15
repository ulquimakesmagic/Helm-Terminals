import { useCallback, useEffect, useState } from "react";
import { api, errMsg } from "../api";
import { formatRaw } from "../amounts";
import { chainOf, explorerUrl } from "../chains";
import { Callout, Card, Icon, Loading, StatusBadge, TableWrap } from "../components/ui";
import SpendDialog from "../components/SpendDialog";

interface Payment {
  id: string; kind: string; network: string; asset: string; amount_raw: string; decimals: number;
  status: string; signature: string | null; created_at: string; can_settle: boolean;
}
interface BillingInfo { launch_exempt: boolean; exemption_reason: string | null; payments: Payment[] }

export default function Billing() {
  const [data, setData] = useState<BillingInfo | null>(null);
  const [error, setError] = useState("");
  const [settle, setSettle] = useState<Payment | null>(null);
  const load = useCallback(() => { api<BillingInfo>("/billing").then(value => { setData(value); setError(""); }).catch(e => setError(errMsg(e))); }, []);
  useEffect(load, [load]);
  return <div className="stack">
    <header className="page-head"><div><span className="eyebrow">Clear costs. Complete history.</span><h1>Fees & payments</h1><p className="muted">Your launch credits and platform payments, in their original asset.</p></div><button className="btn secondary" onClick={load}><Icon name="refresh" size={15} />Refresh</button></header>
    <div className="pricing-grid compact-pricing">
      <article><span>Per launch</span><strong>{data?.launch_exempt ? "Free" : "$20"}</strong><p>{data?.exemption_reason ?? "Paid once per draft. A confirmed payment is reused if deployment needs a retry."}</p></article>
      <article><span>Per trade</span><strong>1%</strong><p>Applies to trades on Helm, including admin accounts. Network and launchpad costs are separate.</p></article>
      <article><span>Creator rewards you keep</span><strong>100%</strong><p>No new Helm reward splits, partner commissions, or bridge markups.</p></article>
    </div>
    {error && <Callout tone="err">{error}<button className="btn sm secondary" onClick={load}>Try again</button></Callout>}
    <Card title="Payment history" icon="activity" sub="A pending transaction is not a second payment request." flush>
      {!data ? <Loading rows={4} /> : !data.payments.length ? <div className="billing-empty"><Icon name="wallet" size={30} /><h2>Your payment history starts here</h2><p className="muted">Launch payments and trading fees appear as you use Helm.</p></div> : <TableWrap><table className="rtable"><caption className="sr-only">Platform payments</caption><thead><tr><th>Payment</th><th>Network</th><th className="r">Amount</th><th>Status</th><th>Transaction</th></tr></thead><tbody>{data.payments.map(payment => <tr key={payment.id}>
        <td className="span"><b>{payment.kind === "launch" ? "Launch fee" : "Trading fee"}</b><div className="small muted">{new Date(payment.created_at).toLocaleString()}</div></td>
        <td data-label="Network">{chainOf(payment.network).name}</td><td className="r mono" data-label="Amount">{formatRaw(payment.amount_raw, payment.decimals)} {payment.asset}</td>
        <td data-label="Status"><StatusBadge status={payment.status} /></td><td data-label="Transaction">{payment.can_settle ? <button className="btn sm secondary" onClick={() => setSettle(payment)}>Review settlement</button> : payment.signature ? <a href={explorerUrl(payment.network, "tx", payment.signature)} target="_blank" rel="noreferrer">View transaction ↗</a> : <span className="muted">Not broadcast</span>}</td>
      </tr>)}</tbody></table></TableWrap>}
    </Card>
    <Callout>Creator rewards on historical tokens with immutable on-chain splits retain their original terms. Helm’s new launch policy does not rewrite those contracts.</Callout>
    {settle && <SpendDialog title="Settle trading fee" path={`/billing/${settle.id}/settle`} body={{}} network={settle.network} onClose={() => setSettle(null)} onDone={load} />}
  </div>;
}
