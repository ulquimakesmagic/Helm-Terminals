"""Receipt-based Pons payout accounting, also used after timeouts. No balance estimates booked as revenue."""
from sqlalchemy import select

from ..chains.pons_rewards import treasury_released
from ..models import Job, Launch, RevenueEvent, Transaction


async def record_payout(db, launch, job, signature, receipt):
    config = (launch.launchpad_params or {}).get("pons_rewards")
    if not config or not signature or int(receipt.get("status", "0x0"), 16) != 1:
        return
    amount = treasury_released(config, receipt.get("logs") or [])
    if not amount:
        return
    await db.execute(select(Launch.id).where(Launch.id == launch.id).with_for_update())
    exists = (await db.execute(select(RevenueEvent.id).where(
        RevenueEvent.source == "creator_share", RevenueEvent.launch_id == launch.id,
        RevenueEvent.reference == signature,
    ))).first()
    if not exists:
        db.add(RevenueEvent(source="creator_share", network="robinhood", asset="ETH", decimals=18,
                            amount_raw=amount, estimated=False, status="received",
                            destination=config["treasury"], launch_id=launch.id, job_id=job.id,
                            payer_user_id=launch.user_id, reference=signature,
                            note="Confirmed native payout from this Pons reward splitter; may include donations. "
                                 "Only the treasury's successful transfer is counted."))


async def reconcile(db, launch, rpc, job_id=None):
    if not (launch.launchpad_params or {}).get("pons_rewards"):
        return
    # Include confirmed jobs too: recover a crash between chain confirmation and ledger persistence.
    query = select(Transaction, Job).join(Job, Job.id == Transaction.job_id).where(
        Job.launch_id == launch.id, Job.kind == "claim_fees", Transaction.signature.is_not(None),
        Transaction.kind == "claim_fees", Transaction.status.in_(["pending", "submitted", "timeout", "confirmed"]),
    )
    if job_id:
        query = query.where(Job.id == job_id)
    rows = (await db.execute(query)).all()
    for tx, job in rows:
        if (job.params or {}).get("pons_payout_checked"):
            continue
        known = (await db.execute(select(RevenueEvent.id).where(
            RevenueEvent.source == "creator_share", RevenueEvent.launch_id == launch.id,
            RevenueEvent.reference == tx.signature,
        ))).first()
        if known:
            continue
        try:
            receipt = await rpc.receipt(tx.signature)
        except Exception:
            continue  # provider outage is not proof of success or failure
        if receipt:
            ok = int(receipt.get("status", "0x0"), 16) == 1
            tx.status = job.status = "confirmed" if ok else "failed"
            await record_payout(db, launch, job, tx.signature, receipt)
            job.params = {**(job.params or {}), "pons_payout_checked": True}
    await db.commit()
