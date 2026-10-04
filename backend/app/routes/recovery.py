from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import log_activity, web_user
from ..models import AccountSecurity, User, utcnow
from ..services import account_recovery as recovery
from ..services.guard import check_password, check_totp_once, client_address, ensure_not_locked, record_attempt
from ..services.passwords import hash_async

router = APIRouter(prefix="/auth", tags=["account recovery"])


class Credentials(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    totp: str | None = Field(default=None, max_length=12)


class PasswordChange(Credentials):
    new_password: str = Field(min_length=10, max_length=200)


class EmailRequest(BaseModel):
    email: EmailStr


class TokenBody(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class ResetBody(TokenBody):
    new_password: str = Field(min_length=10, max_length=200)
    totp: str | None = Field(default=None, max_length=12)
    recovery_code: str | None = Field(default=None, max_length=40)


class MfaRecovery(Credentials):
    recovery_code: str = Field(min_length=20, max_length=40)
    email: EmailStr


async def throttle(db, request, kind):
    key = f"recovery:{kind}:{client_address(request)}"
    await ensure_not_locked(db, key, 8)
    await record_attempt(db, key)


async def step_up(db, user, body):
    if not await check_password(db, user, body.password):
        raise HTTPException(401, "Invalid credentials")
    if user.totp_enabled and not await check_totp_once(db, user, body.totp):
        raise HTTPException(401, "totp_required")


@router.post("/email/request")
async def request_verification(body: Credentials, request: Request, user: User = Depends(web_user), db: AsyncSession = Depends(get_db)):
    recovery.require_mail()
    await throttle(db, request, "email")
    await step_up(db, user, body)
    await recovery.issue_email_token(db, user, "verify")
    return {"message": "Verification link sent"}


@router.post("/email/verify")
async def verify_email(body: TokenBody, request: Request, db: AsyncSession = Depends(get_db)):
    await throttle(db, request, "verify")
    row, user = await recovery.lock_token(db, body.token, "verify")
    await recovery.consume_token(db, row)
    state = await recovery.security_state(db, user.id)
    state.email_verified_at = utcnow()
    await log_activity(db, user.id, "email.verified")
    await db.commit()
    return {"message": "Email verified; sign in to continue"}


@router.post("/password/request")
async def request_reset(body: EmailRequest, request: Request, background: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    recovery.require_mail()
    await throttle(db, request, "reset")
    # Always return the same result. Only previously verified addresses can reset access.
    background.add_task(recovery.request_reset_delivery, body.email.lower())
    return {"message": "If the address is verified and eligible, a reset link will arrive shortly. Otherwise contact support."}


@router.post("/password/reset")
async def reset_password(body: ResetBody, request: Request, background: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    await throttle(db, request, "consume")
    row, user = await recovery.lock_token(db, body.token, "reset")
    state = await recovery.security_state(db, user.id)
    if not state.email_verified_at:
        raise HTTPException(400, "A verified email is required")
    recovered_mfa = False
    if user.totp_enabled:
        if body.recovery_code:
            if not await recovery.consume_code(db, user.id, body.recovery_code):
                raise HTTPException(401, "Invalid recovery code")
            recovered_mfa = True
        elif not await check_totp_once(db, user, body.totp):
            raise HTTPException(401, "totp_required")
    await recovery.consume_token(db, row)
    user.password_hash = await hash_async(body.new_password)
    if recovered_mfa:
        user.totp_enabled, user.totp_secret_enc = False, None
    # All email resets hold spending; an email takeover cannot immediately move funds.
    state.recovery_hold_until = utcnow() + timedelta(hours=24)
    await recovery.revoke_sessions(db, user.id)
    await log_activity(db, user.id, "password.reset", payload={"mfa_recovered": recovered_mfa, "spend_hold_hours": 24})
    await db.commit()
    background.add_task(recovery.notify_security, user.email, "Your password was reset. All sessions ended and spending is held for 24 hours.")
    return {"message": "Password reset. All sessions ended; spending is held for 24 hours. Set up MFA again if you used a recovery code."}


@router.post("/password/change")
async def change_password(body: PasswordChange, background: BackgroundTasks, user: User = Depends(web_user), db: AsyncSession = Depends(get_db)):
    await step_up(db, user, body)
    user.password_hash = await hash_async(body.new_password)
    await recovery.revoke_sessions(db, user.id)
    await log_activity(db, user.id, "password.changed")
    await db.commit()
    background.add_task(recovery.notify_security, user.email, "Your password changed and all sessions ended.")
    return {"message": "Password changed. Sign in again; all sessions have ended."}


@router.post("/recovery-codes")
async def generate_codes(body: Credentials, background: BackgroundTasks, user: User = Depends(web_user), db: AsyncSession = Depends(get_db)):
    if not user.totp_enabled:
        raise HTTPException(400, "Enable MFA first")
    await step_up(db, user, body)
    codes = await recovery.new_codes(db, user.id)
    await log_activity(db, user.id, "recovery_codes.replaced")
    await db.commit()
    background.add_task(recovery.notify_security, user.email, "Your MFA recovery codes were replaced.")
    return {"codes": codes, "message": "Save offline. These codes are shown once; previous codes are invalid."}


@router.post("/mfa/recover")
async def recover_mfa(body: MfaRecovery, request: Request, background: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    await throttle(db, request, "mfa")
    user = (await db.execute(select(User).where(User.email == body.email.lower()).with_for_update())).scalar_one_or_none()
    from ..services.passwords import verify_async
    if not await verify_async(body.password, user.password_hash if user else None):
        raise HTTPException(401, "Invalid recovery credentials")
    await recovery.ensure_active(db, user.id)
    if not user.totp_enabled or not await recovery.consume_code(db, user.id, body.recovery_code):
        raise HTTPException(401, "Invalid recovery credentials")
    user.totp_enabled, user.totp_secret_enc = False, None
    state = await recovery.security_state(db, user.id)
    state.recovery_hold_until = utcnow() + timedelta(hours=24)
    await recovery.revoke_sessions(db, user.id)
    await log_activity(db, user.id, "mfa.recovered", payload={"spend_hold_hours": 24})
    await db.commit()
    background.add_task(recovery.notify_security, user.email, "Your authenticator was reset with a recovery code. All sessions ended and spending is held for 24 hours.")
    return {"message": "Authenticator reset. Sign in and set up MFA again. Spending is held for 24 hours."}
