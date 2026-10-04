import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.solana import LAMPORTS_PER_SOL
from ..db import get_db
from ..config import get_settings
from ..deps import current_user, get_executor, log_activity, valid_address_for, web_user
from ..models import (
    Activity, AdminAudit, FeeRecord, Job, Launch, PayoutAddress, Referral, ReferralLink, Transaction, User, Wallet,
    Watch, utcnow,
)
from ..services.guard import check_password, check_totp_once
from ..services.executor import Executor
from ..services.portfolio import build_portfolio, daily_prices

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
    # Activity per chain: launches, and confirmed transactions from your wallets.
    cq = (select(Launch.network, func.count(Launch.id)).where(Launch.user_id == user.id, Launch.status != "archived",
                                                             Launch.imported.is_(False)).group_by(Launch.network))
    xq = (select(Wallet.network, func.count(Transaction.id)).join(Job, Job.id == Transaction.job_id)
          .join(Wallet, Wallet.id == Transaction.wallet_id)
          .where(Job.user_id == user.id, Transaction.status == "confirmed").group_by(Wallet.network))
    if since:
        cq = cq.where(Launch.created_at >= since)
        xq = xq.where(Transaction.created_at >= since)
    by_chain: dict[str, dict] = {}
    for net, n in (await db.execute(cq)).all():
        by_chain.setdefault(net, {"launches": 0, "transactions": 0})["launches"] = n
    for net, n in (await db.execute(xq)).all():
        by_chain.setdefault(net, {"launches": 0, "transactions": 0})["transactions"] = n
    daily: dict[str, int] = {}
    for delta, created in rows:
        daily[created.date().isoformat()] = daily.get(created.date().isoformat(), 0) + int(delta)
    # Flows are measured in SOL (the coin that moved) and valued in USD at each day's price, so the chart reads in
    # dollars like the rest of your balances.
    prices = await daily_prices("SOL", sorted(daily))
    cum = cum_usd = 0.0
    priced = True
    series = []
    for day in sorted(daily):
        sol = daily[day] / LAMPORTS_PER_SOL
        price = prices.get(day)
        priced = priced and price is not None
        day_usd = sol * price if price is not None else 0.0
        cum += sol
        cum_usd += day_usd
        series.append({"date": day, "daily_sol": sol, "cumulative_sol": cum, "sol_price_usd": price,
                       "daily_usd": day_usd if price is not None else None,
                       "cumulative_usd": cum_usd if priced else None})
    return {
        "period": period,
        "total_launches": launches,
        "by_chain": by_chain,
        "net_sol_flow": cum,
        "net_usd_flow": cum_usd if priced else None,
        "flow_scope": {"network": "solana", "asset": "SOL", "complete_portfolio": False},
        "usd_priced": priced,
        "series": series,
        "referral": await referral_summary(db, user),
        "note": "SOL-only movement recorded by Helm from confirmed transactions, including fees. USD values use daily SOL prices; missing prices remain unavailable. Excludes other chains and tokens, external activity and unrealized P&L; internal transfers are not adjusted.",
    }


@router.get("/portfolio")
async def portfolio(fresh: bool = False, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                    ex: Executor = Depends(get_executor)):
    """Coins and dollar stablecoins across every chain, valued in USD."""
    from ..chains.networks import offered

    wallets = [w for w in (await db.execute(select(Wallet).where(Wallet.user_id == user.id))).scalars().all()
               if offered(w.network)]
    return await build_portfolio(ex, user.id, wallets, fresh=fresh)


@router.post("/referrals", status_code=201)
async def create_referral(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    existing = (await db.execute(select(func.count(Referral.id)).where(Referral.owner_user_id == user.id))).scalar_one()
    if existing >= get_settings().referral_max_codes_per_user:
        raise HTTPException(429, "you have reached the referral-code limit")
    r = Referral(owner_user_id=user.id, code=secrets.token_hex(4).upper(), max_uses=get_settings().referral_code_max_uses)
    db.add(r)
    await db.commit()
    return {"code": r.code}


async def referral_summary(db: AsyncSession, user: User) -> dict:
    from ..services import platform_config as pc

    s = get_settings()
    await pc.refresh(db)
    fee_bps = pc.bps("trade_fee_bps_solana")
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
        "share_pct": s.referral_share_bps / 100 if fee_bps else 0,
        "fee_pct": fee_bps / 100,
        "totp_enabled": user.totp_enabled,
    }


@router.get("/referrals")
async def referrals(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await referral_summary(db, user)


class PayoutBody(BaseModel):
    address: str
    network: str = "solana"
    password: SecretStr
    totp: str


@router.put("/referrals/payout")
async def set_payout(body: PayoutBody, user: User = Depends(web_user), db: AsyncSession = Depends(get_db)):
    """Changing referral earnings destination requires password re-entry and a fresh 2FA code."""
    if body.network != "solana" or not valid_address_for("solana", body.address):
        raise HTTPException(400, "invalid solana address")
    if not user.totp_enabled:
        raise HTTPException(403, "enable 2FA before setting a referral payout address")
    if not await check_password(db, user, body.password.get_secret_value()):
        raise HTTPException(400, "wrong password")
    if not await check_totp_once(db, user, body.totp):
        raise HTTPException(401, "totp_required: provide a fresh 2FA code")
    referred_ids = select(ReferralLink.referred_user_id).where(ReferralLink.referrer_user_id == user.id)
    linked_wallet = (await db.execute(select(Wallet.id).where(
        Wallet.user_id.in_(referred_ids), Wallet.address == body.address).limit(1))).scalar_one_or_none()
    if linked_wallet:
        db.add(AdminAudit(actor_user_id=None, action="referral.self_referral_suspected", target=user.id,
                          changes={"flag": [None, "payout address belongs to a referred account"]}, context={}))
        await db.commit()
        raise HTTPException(400, "this payout address belongs to an account you referred")
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
