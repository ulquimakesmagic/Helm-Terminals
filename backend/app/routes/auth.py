from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..deps import current_user, log_activity
from ..models import Session, User, utcnow
from ..security import (
    check_totp, decrypt_secret, encrypt_secret, hash_password, new_token, new_totp_secret,
    token_hash, totp_uri, verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class Register(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)


class Login(BaseModel):
    email: EmailStr
    password: str
    totp: str | None = None


class Code(BaseModel):
    code: str


@router.post("/register", status_code=201)
async def register(body: Register, db: AsyncSession = Depends(get_db)):
    email = body.email.lower()
    if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise HTTPException(409, "email already registered")
    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    await log_activity(db, user.id, "account.created")
    await db.commit()
    return {"id": user.id, "email": user.email}


@router.post("/login")
async def login(body: Login, request: Request, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "invalid credentials")
    if user.totp_enabled:
        if not body.totp or not check_totp(decrypt_secret(user.totp_secret_enc or ""), body.totp):
            raise HTTPException(401, "totp_required")
    token = new_token()
    ttl = timedelta(hours=get_settings().session_ttl_hours)
    sess = Session(
        user_id=user.id,
        token_hash=token_hash(token),
        ip=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:255],
        expires_at=utcnow() + ttl,
    )
    db.add(sess)
    await log_activity(db, user.id, "session.created", "session", sess.id)
    await db.commit()
    return {"token": token, "expires_at": sess.expires_at.isoformat()}


@router.post("/totp/enable")
async def totp_enable(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if user.totp_enabled:
        raise HTTPException(409, "2FA already enabled")
    secret = new_totp_secret()
    user.totp_secret_enc = encrypt_secret(secret)
    await db.commit()
    return {"provisioning_uri": totp_uri(secret, user.email), "secret": secret}


@router.post("/totp/verify")
async def totp_verify(body: Code, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if not user.totp_secret_enc:
        raise HTTPException(400, "call /auth/totp/enable first")
    if not check_totp(decrypt_secret(user.totp_secret_enc), body.code):
        raise HTTPException(400, "invalid code")
    user.totp_enabled = True
    await log_activity(db, user.id, "2fa.enabled")
    await db.commit()
    return {"totp_enabled": True}


@router.get("/sessions")
async def sessions(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(Session).where(Session.user_id == user.id, Session.revoked_at.is_(None)))
    ).scalars().all()
    return [
        {"id": s.id, "ip": s.ip, "user_agent": s.user_agent, "created_at": s.created_at.isoformat(),
         "expires_at": s.expires_at.isoformat()}
        for s in rows
    ]


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke(session_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    s = await db.get(Session, session_id)
    if not s or s.user_id != user.id:
        raise HTTPException(404, "session not found")
    s.revoked_at = utcnow()
    await log_activity(db, user.id, "session.revoked", "session", s.id)
    await db.commit()
