"""An account's own platform payments and explicit settlement of an unpaid trade fee."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from ..db import get_db
from ..deps import SpendConfirm, begin_spend, current_user, find_replay, get_executor, run_job
from ..models import FeeRecord, PlatformCharge, User, Wallet
from ..services import charges
from ..services.executor import _finish
from ..services.preflight import build_preflight
from ..services.pricing import public_pricing

router = APIRouter(prefix="/billing", tags=["billing"])


def charge_out(row):
    return {"id": row.id, "kind": row.kind, "network": row.network, "asset": row.asset,
            "amount_raw": str(int(row.amount_raw)), "decimals": row.decimals, "status": row.status,
            "platform_raw": str(int(row.platform_raw)), "referral_raw": str(int(row.referral_raw)),
            "referral_status": row.referral_status, "job_id": row.job_id, "launch_id": row.launch_id,
            "signature": row.signature, "created_at": row.created_at.isoformat() + "Z",
            "can_settle": row.kind == "trade" and row.status == "failed"}


@router.get("")
async def billing(user: User = Depends(current_user), db=Depends(get_db)):
    rows = (await db.execute(select(PlatformCharge).where(PlatformCharge.payer_user_id == user.id)
                            .order_by(PlatformCharge.created_at.desc()).limit(100))).scalars().all()
    reason = await charges.exemption(db, user)
    atomic = (await db.execute(select(FeeRecord).where(FeeRecord.payer_user_id == user.id)
                              .order_by(FeeRecord.created_at.desc()).limit(100))).scalars().all()
    payments = [charge_out(row) for row in rows]
    payments.extend({"id": f"atomic:{row.id}", "kind": "trade", "network": row.network, "asset": "SOL",
        "amount_raw": str(row.platform_lamports + row.referral_lamports), "decimals": 9, "status": row.status,
        "platform_raw": str(row.platform_lamports), "referral_raw": str(row.referral_lamports),
        "referral_status": row.referral_status, "job_id": row.job_id, "launch_id": None,
        "signature": row.signature, "created_at": row.created_at.isoformat() + "Z", "can_settle": False}
        for row in atomic)
    payments.sort(key=lambda row: row["created_at"], reverse=True)
    return {"pricing": public_pricing(), "launch_exempt": bool(reason), "exemption_reason": reason,
            "payments": payments[:100]}


@router.post("/{charge_id}/settle")
async def settle(charge_id: str, body: SpendConfirm, user: User = Depends(current_user),
                 db=Depends(get_db), ex=Depends(get_executor)):
    if replay := await find_replay(db, user, body):
        return replay
    row = await db.get(PlatformCharge, charge_id)
    if not row or row.payer_user_id != user.id:
        raise HTTPException(404, "no such payment")
    if row.kind != "trade" or row.status != "failed":
        raise HTTPException(409, "Only a definitively failed trade-fee payment can be settled here; uncertain payments need reconciliation")
    wallet = await db.get(Wallet, row.wallet_id)
    pf = build_preflight("fee_payment", {"charge_id": row.id, "network": row.network,
        "wallet": wallet.address, "asset": row.asset, "amount_raw": str(int(row.amount_raw)),
        "decimals": row.decimals, "destination": row.destination,
        "platform_fee": "Settlement of the original reviewed trading fee; no additional Helm charge"})
    if not body.confirm:
        return pf
    job, created = await begin_spend(db, user, body, pf, "fee_payment", row.launch_id, pf["preflight"])
    if created:
        terms, reference = dict(row.terms), row.reference_key
        async def work(session, current):
            result = await charges.collect(session, ex, current, wallet, "trade", terms, reference)
            _finish(current, [result])
            await session.commit()
        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}
