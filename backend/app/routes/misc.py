import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.solana import LAMPORTS_PER_SOL
from ..db import get_db
from ..config import get_settings
from ..deps import current_user, log_activity, valid_address_for, web_user
from ..models import (
    Activity, FeeRecord, Job, Launch, PayoutAddress, Referral, ReferralLink, Transaction, User, Watch, utcnow,
)

router = APIRouter(tags=["misc"])
PERIODS = {"all": None, "1y": timedelta(days=365), "1m": timedelta(days=30), "7d": timedelta(days=7), "1d": timedelta(days=1)}


@router.get("/dashboard")
async def dashboard(period: str = "all", user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if period not in PERIODS:
        raise HTTPException(400, f"period must be one of {list(PERIODS)}")
    since = utcnow() - PERIODS[period] if PERIODS[period] else None
    lq = select(func.count(Launch.id)).where(Launch.user_id == user.id, Launch.status != "archived", Launch.imported.is_(False))
    tq = (select(Transaction.sol_delta_lamports, Transaction.created_at)
          .join(Job, Job.id == Transaction.job_id)
          .where(Job.user_id == user.id, Transaction.status == "confirmed", Transaction.sol_delta_lamports.is_not(None)))
    if since:
        lq = lq.where(Launch.created_at >= since)
        tq = tq.where(Transaction.created_at >= since)
    launches = (await db.execute(lq)).scalar_one()
    rows = (await db.execute(tq)).all()
    daily: dict[str, int] = {}
    for delta, created in rows:
        daily[created.date().isoformat()] = daily.get(created.date().isoformat(), 0) + int(delta)
    cum, series = 0, []
    for day in sorted(daily):
        cum += daily[day]
        series.append({"date": day, "daily_sol": daily[day] / LAMPORTS_PER_SOL, "cumulative_sol": cum / LAMPORTS_PER_SOL})
    return {
        "period": period,
        "total_launches": launches,
        "net_sol_flow": cum / LAMPORTS_PER_SOL,
        "series": series,
        "referral": await referral_summary(db, user),
        "note": "Net SOL flow from confirmed on-chain transactions, including fees. Unsold token holdings are not valued.",
    }


@router.post("/referrals", status_code=201)
async def create_referral(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r = Referral(owner_user_id=user.id, code=secrets.token_hex(4).upper(), max_uses=get_settings().referral_code_max_uses)
    db.add(r)
    await db.commit()
    return {"code": r.code}


async def referral_summary(db: AsyncSession, user: User) -> dict:
    s = get_settings()
    codes = (await db.execute(select(Referral).where(Referral.owner_user_id == user.id))).scalars().all()
    referred = (await db.execute(select(func.count(ReferralLink.id)).where(
        ReferralLink.referrer_user_id == user.id))).scalar_one()
    rows = (await db.execute(select(FeeRecord.referral_status, func.coalesce(func.sum(FeeRecord.referral_lamports), 0))
                             .where(FeeRecord.referrer_user_id == user.id, FeeRecord.status == "confirmed")
                             .group_by(FeeRecord.referral_status))).all()
    by_status = {st: int(v) for st, v in rows}
    payout = (await db.execute(select(PayoutAddress).where(
        PayoutAddress.user_id == user.id, PayoutAddress.network == "solana"))).scalar_one_or_none()
    return {
        "codes": [{"code": c.code, "uses": c.uses, "max_uses": c.max_uses} for c in codes],
        "referred_users": referred,
        "earned_sol": by_status.get("paid", 0) / LAMPORTS_PER_SOL,
        "owed_sol": by_status.get("owed", 0) / LAMPORTS_PER_SOL,
        "payout_address": payout.address if payout else None,
        "share_pct": s.referral_share_bps / 100 if s.platform_fee_bps else 0,
        "fee_pct": s.platform_fee_bps / 100,
    }


@router.get("/referrals")
async def referrals(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await referral_summary(db, user)


class PayoutBody(BaseModel):
    address: str
    network: str = "solana"


@router.put("/referrals/payout")
async def set_payout(body: PayoutBody, user: User = Depends(web_user), db: AsyncSession = Depends(get_db)):
    """Web only: a stolen chat session must not be able to redirect someone's earnings."""
    if body.network != "solana" or not valid_address_for("solana", body.address):
        raise HTTPException(400, "invalid solana address")
    row = (await db.execute(select(PayoutAddress).where(
        PayoutAddress.user_id == user.id, PayoutAddress.network == body.network))).scalar_one_or_none()
    if row:
        row.address = body.address
    else:
        db.add(PayoutAddress(user_id=user.id, network=body.network, address=body.address))
    await log_activity(db, user.id, "referral.payout_set", payload={"address": body.address})
    await db.commit()
    return await referral_summary(db, user)


@router.get("/activity")
async def activity(limit: int = 100, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Activity).where(Activity.user_id == user.id)
                             .order_by(Activity.created_at.desc()).limit(min(limit, 500)))).scalars().all()
    return [{"id": a.id, "kind": a.kind, "ref_type": a.ref_type, "ref_id": a.ref_id, "payload": a.payload,
             "created_at": a.created_at.isoformat()} for a in rows]


class WatchBody(BaseModel):
    address: str
    label: str = ""
    network: str = "solana"


@router.get("/tracker")
async def tracker(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Watch).where(Watch.user_id == user.id))).scalars().all()
    return [{"id": w.id, "address": w.address, "label": w.label, "bookmarked": w.bookmarked, "network": w.network}
            for w in rows]


@router.post("/tracker", status_code=201)
async def tracker_add(body: WatchBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if not valid_address_for(body.network, body.address):
        raise HTTPException(400, f"invalid {body.network} address")
    w = Watch(user_id=user.id, network=body.network, address=body.address, label=body.label)
    db.add(w)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(409, "address already tracked")
    return {"id": w.id, "address": w.address, "label": w.label, "bookmarked": w.bookmarked, "network": w.network}


@router.patch("/tracker/{watch_id}")
async def tracker_update(watch_id: str, label: str | None = None, bookmarked: bool | None = None,
                         user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    w = await db.get(Watch, watch_id)
    if not w or w.user_id != user.id:
        raise HTTPException(404, "not found")
    if label is not None:
        w.label = label
    if bookmarked is not None:
        w.bookmarked = bookmarked
    await db.commit()
    return {"id": w.id, "label": w.label, "bookmarked": w.bookmarked}


@router.delete("/tracker/{watch_id}", status_code=204)
async def tracker_delete(watch_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    w = await db.get(Watch, watch_id)
    if not w or w.user_id != user.id:
        raise HTTPException(404, "not found")
    await db.delete(w)
    await db.commit()
