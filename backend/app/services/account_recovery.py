"""Verified-email recovery with independently protected MFA and atomic consumption."""
import asyncio
import secrets
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import select, update

from ..config import get_settings
from ..models import AccountSecurity, AccountStatus, RecoveryCode, RecoveryToken, Session, User, utcnow
from ..security import new_token, token_hash


def require_mail():
    s = get_settings()
    if not s.smtp_host or not s.smtp_from or urlsplit(s.public_app_url).scheme != "https":
        raise HTTPException(503, "Email recovery is not configured; contact support")


async def deliver(email: str, subject: str, message: str):
    require_mail()
    s = get_settings()
    def send():
        mail = EmailMessage()
        mail["From"], mail["To"], mail["Subject"] = s.smtp_from, email, subject
        mail.set_content(message)
        context = ssl.create_default_context()
        if s.smtp_implicit_tls:
            connection = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=15, context=context)
        else:
            connection = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15)
        with connection as smtp:
            if not s.smtp_implicit_tls:
                smtp.starttls(context=context)
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password)
            smtp.send_message(mail)
    await asyncio.to_thread(send)


async def security_state(db, user_id):
    state = await db.get(AccountSecurity, user_id)
    if state is None:
        state = AccountSecurity(user_id=user_id)
        db.add(state)
        await db.flush()
    return state


async def ensure_active(db, user_id):
    status = (await db.execute(select(AccountStatus.status).where(AccountStatus.user_id == user_id))).scalar_one_or_none()
    if status == "suspended":
        raise HTTPException(403, "Account access is restricted; contact support")


async def issue_email_token(db, user, purpose):
    token = new_token()
    await db.execute(update(RecoveryToken).where(RecoveryToken.user_id == user.id,
        RecoveryToken.purpose == purpose, RecoveryToken.used_at.is_(None)).values(used_at=utcnow()))
    db.add(RecoveryToken(user_id=user.id, purpose=purpose, token_hash=token_hash(token),
                         expires_at=utcnow() + timedelta(minutes=20)))
    await db.commit()
    # Fragment avoids placing the token in HTTP access logs or referrer headers.
    link = f"{get_settings().public_app_url.rstrip('/')}/recover#{purpose}:{token}"
    await deliver(user.email, "Helm account verification" if purpose == "verify" else "Helm password reset",
                  f"Open this link within 20 minutes:\n{link}\n\nIf you did not request this, do not use the link. Never share recovery codes.")


async def lock_token(db, token, purpose):
    row = (await db.execute(select(RecoveryToken).where(RecoveryToken.token_hash == token_hash(token),
        RecoveryToken.purpose == purpose, RecoveryToken.used_at.is_(None),
        RecoveryToken.expires_at > utcnow()).with_for_update())).scalar_one_or_none()
    if row is None:
        raise HTTPException(400, "The link is invalid or expired; request another")
    user = (await db.execute(select(User).where(User.id == row.user_id).with_for_update())).scalar_one()
    await ensure_active(db, user.id)
    return row, user


async def consume_token(db, row):
    result = await db.execute(update(RecoveryToken).where(RecoveryToken.id == row.id,
        RecoveryToken.used_at.is_(None), RecoveryToken.expires_at > utcnow()).values(used_at=utcnow()))
    if result.rowcount != 1:
        raise HTTPException(409, "This link was already used; request another")


async def revoke_sessions(db, user_id):
    await db.execute(update(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
                     .values(revoked_at=utcnow()))
    await db.execute(update(RecoveryToken).where(RecoveryToken.user_id == user_id,
        RecoveryToken.used_at.is_(None)).values(used_at=utcnow()))


async def consume_code(db, user_id, code):
    result = await db.execute(update(RecoveryCode).where(RecoveryCode.user_id == user_id,
        RecoveryCode.code_hash == token_hash(code.strip().upper()), RecoveryCode.used_at.is_(None))
        .values(used_at=utcnow()))
    return result.rowcount == 1


async def new_codes(db, user_id):
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
    await db.execute(update(RecoveryCode).where(RecoveryCode.user_id == user_id,
        RecoveryCode.used_at.is_(None)).values(used_at=utcnow()))
    codes = [secrets.token_hex(10).upper() for _ in range(10)]
    db.add_all([RecoveryCode(user_id=user_id, code_hash=token_hash(c)) for c in codes])
    return codes


async def notify_security(email, action):
    try:
        await deliver(email, "Helm account security changed",
                      f"{action}\nIf this was not you, contact Helm support immediately. Never send passwords, private keys or recovery codes to support.")
    except Exception:
        import logging
        logging.getLogger("helm.recovery").warning("Security notification delivery unavailable")


async def request_reset_delivery(email):
    # Work is deferred until after the uniform public response; use a new session.
    from ..db import get_sessionmaker
    async with get_sessionmaker()() as db:
        user = (await db.execute(select(User).join(AccountSecurity, AccountSecurity.user_id == User.id)
            .where(User.email == email, AccountSecurity.email_verified_at.is_not(None)))).scalar_one_or_none()
        if user:
            try:
                await ensure_active(db, user.id)
                await issue_email_token(db, user, "reset")
            except Exception:
                import logging
                logging.getLogger("helm.recovery").warning("Password reset delivery unavailable")
