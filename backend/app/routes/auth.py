import logging
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..db import get_db
from ..deps import current_user, log_activity, web_user
from ..models import Profile, Referral, ReferralLink, Session, User, utcnow
from ..services.guard import (
    LOGIN_LIMIT, LOGIN_LIMIT_ANY, SIGNUP_LIMIT, check_password, check_totp_once, client_address, ensure_not_locked,
    record_attempt, record_failure,
)
from ..services.main_wallet import ensure_main_wallets
from ..signing import get_signer
from ..security import (
    encrypt_secret, hash_password, new_token, new_totp_secret, token_hash, totp_uri, verify_password,
)

from ..services.admin import role_of
from ..services.passwords import hash_async, verify_async

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger(__name__)


class Register(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    referral_code: str | None = Field(default=None, max_length=16)


class Login(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)
    totp: str | None = Field(default=None, max_length=12)


class Code(BaseModel):
    code: str


class PasswordConfirm(BaseModel):
    password: str = Field(min_length=1, max_length=200)


@router.post("/register", status_code=201)
async def register(body: Register, request: Request, background: BackgroundTasks, db: AsyncSession = Depends(get_db), signer=Depends(get_signer)):
    signup_key = f"signup:{client_address(request)}"
    await ensure_not_locked(db, signup_key, SIGNUP_LIMIT)
    await record_attempt(db, signup_key)
    email = body.email.lower()
    password_hash = await hash_async(body.password)  # before the existence check: a duplicate must take the
    # same Argon2 time as a fresh account, or registration latency reveals which emails are registered
    response = {"message": "Registration processed. Sign in with your credentials; existing accounts keep their password."}
    ref = None
    if body.referral_code:
        ref = (await db.execute(select(Referral).where(Referral.code == body.referral_code.strip().upper()))).scalar_one_or_none()
        if not ref or ref.uses >= ref.max_uses:
            raise HTTPException(400, "referral code is invalid or used up")
    if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        return response
    user = User(email=email, password_hash=password_hash)
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        if (await db.execute(select(User.id).where(User.email == email))).scalar_one_or_none():
            return response  # a simultaneous registration won; never overwrite its credentials
        raise
    if ref:
        ref.uses += 1
        db.add(ReferralLink(referred_user_id=user.id, referrer_user_id=ref.owner_user_id, code=ref.code))
    await log_activity(db, user.id, "account.created", payload={"referred": bool(ref)})
    await db.commit()
    # Every account gets its main wallet. A signer outage mustn't block sign-up: the Wallets page offers to finish it.
    async def provision(uid):
        from ..db import get_sessionmaker
        async with get_sessionmaker()() as session:
            try:
                await ensure_main_wallets(session, signer, uid)
                await session.commit()
            except Exception:
                await session.rollback()
                log.warning("main wallet provisioning unavailable")
    background.add_task(provision, user.id)
    return response


@router.post("/login")
async def login(body: Login, request: Request, db: AsyncSession = Depends(get_db)):
    email = body.email.lower()
    here, anywhere = f"login:{email}:{client_address(request)}", f"login:{email}"
    origin = f"login-origin:{client_address(request)}"
    await ensure_not_locked(db, origin, 60)
    await record_attempt(db, origin)
    await ensure_not_locked(db, here, LOGIN_LIMIT)
    await ensure_not_locked(db, anywhere, LOGIN_LIMIT_ANY)
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not await verify_async(body.password, user.password_hash if user else None):
        await record_failure(db, here, anywhere)
        raise HTTPException(401, "invalid credentials")
    from ..services.account_recovery import ensure_active
    await ensure_active(db, user.id)
    if user.totp_enabled:
        if not body.totp:
            raise HTTPException(401, "totp_required")
        if not await check_totp_once(db, user, body.totp):
            raise HTTPException(401, "totp_required")
    token = new_token()
    ttl = timedelta(hours=get_settings().session_ttl_hours)
    sess = Session(
        user_id=user.id,
        token_hash=token_hash(token),
        ip=client_address(request),
        user_agent=(request.headers.get("user-agent") or "")[:255],
        expires_at=utcnow() + ttl,
    )
    db.add(sess)
    await log_activity(db, user.id, "session.created", "session", sess.id)
    await db.commit()
    # The role binds only when the configured user ID and designated email both match.
    await role_of(db, user)
    return {"token": token, "expires_at": sess.expires_at.isoformat()}


@router.post("/totp/enable")
async def totp_enable(body: PasswordConfirm, user: User = Depends(web_user), db: AsyncSession = Depends(get_db)):
    if user.totp_enabled:
        raise HTTPException(409, "2FA already enabled")
    # 2FA guards withdrawals. Setting it up asks for the password, so someone holding only a stolen session can't
    # attach their own authenticator and then withdraw.
    if not await check_password(db, user, body.password):
        raise HTTPException(400, "wrong password")
    secret = new_totp_secret()
    user.totp_secret_enc = encrypt_secret(secret)
    await db.commit()
    return {"provisioning_uri": totp_uri(secret, user.email), "secret": secret}


@router.post("/totp/verify")
async def totp_verify(body: Code, user: User = Depends(web_user), db: AsyncSession = Depends(get_db),
                      authorization: str | None = Header(default=None)):
    if not user.totp_secret_enc:
        raise HTTPException(400, "call /auth/totp/enable first")
    if not await check_totp_once(db, user, body.code):
        raise HTTPException(400, "invalid code")
    was_on = user.totp_enabled
    user.totp_enabled = True
    ended = 0
    if not was_on:
        # Turning 2FA on signs out every other web session, so a session someone else already holds doesn't outlive
        # the upgrade. This session and the Telegram bot's stay.
        from sqlalchemy import update

        current = token_hash((authorization or "")[7:].strip())
        res = await db.execute(update(Session).where(
            Session.user_id == user.id, Session.channel == "web", Session.revoked_at.is_(None),
            Session.token_hash != current).values(revoked_at=utcnow()))
        ended = res.rowcount or 0
    await log_activity(db, user.id, "2fa.enabled", payload={"other_sessions_ended": ended})
    await db.commit()
    return {"totp_enabled": True, "other_sessions_ended": ended}


@router.get("/me")
async def me(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from ..models import AccountSecurity
    security = await db.get(AccountSecurity, user.id)
    p = (await db.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one_or_none()
    return {"id": user.id, "email": user.email, "totp_enabled": user.totp_enabled,
            "email_verified": bool(security and security.email_verified_at),
            "recovery_hold_until": security.recovery_hold_until.isoformat() if security and security.recovery_hold_until else None,
            "created_at": user.created_at.isoformat(),
            "display_name": p.display_name if p else user.email.split("@")[0][:40],
            "handle": p.handle if p else None, "avatar_hue": p.avatar_hue if p else 265,
            # Only decides whether the web app shows the admin link; every admin API checks the role itself.
            "admin_role": await role_of(db, user) if user.channel == "web" else None}


@router.get("/sessions")
async def sessions(user: User = Depends(web_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(Session).where(Session.user_id == user.id, Session.revoked_at.is_(None)))
    ).scalars().all()
    return [
        {"id": s.id, "ip": s.ip, "user_agent": s.user_agent, "created_at": s.created_at.isoformat(),
         "expires_at": s.expires_at.isoformat()}
        for s in rows
    ]


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke(session_id: str, user: User = Depends(web_user), db: AsyncSession = Depends(get_db)):
    s = await db.get(Session, session_id)
    if not s or s.user_id != user.id:
        raise HTTPException(404, "session not found")
    s.revoked_at = utcnow()
    await log_activity(db, user.id, "session.revoked", "session", s.id)
    await db.commit()


@router.post("/logout", status_code=204)
async def logout(authorization: str | None = Header(default=None), user: User = Depends(current_user),
                 db: AsyncSession = Depends(get_db)):
    """End this session on the server too, so a copied token stops working when the user signs out."""
    th = token_hash((authorization or "")[7:].strip())
    s = (await db.execute(select(Session).where(Session.token_hash == th))).scalar_one_or_none()
    if s and s.user_id == user.id and not s.revoked_at:
        s.revoked_at = utcnow()
        await log_activity(db, user.id, "session.revoked", "session", s.id)
        await db.commit()
