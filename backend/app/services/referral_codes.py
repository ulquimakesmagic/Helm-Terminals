"""Username aliases are permanent ownership claims. Historical codes keep working."""
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from ..models import Profile, Referral
from ..config import get_settings


async def ensure_username_code(db, user, handle=None):
    if handle is None:
        profile = (await db.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one_or_none()
        handle = profile.handle if profile else None
    if not handle:
        return None
    code = handle.upper()
    row = (await db.execute(select(Referral).where(func.upper(Referral.code) == code))).scalar_one_or_none()
    if row:
        if row.owner_user_id != user.id:
            raise HTTPException(409, 'That username is reserved by an existing referral code')
        return row
    row = Referral(owner_user_id=user.id, code=code, max_uses=get_settings().referral_code_max_uses)
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        row = (await db.execute(select(Referral).where(func.upper(Referral.code) == code))).scalar_one_or_none()
        if row is None or row.owner_user_id != user.id:
            raise HTTPException(409, 'That username is reserved by an existing referral code') from None
    return row
