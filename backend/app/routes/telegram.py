"""Telegram integration.

Model: the bot is a thin client. It proves who it is with a shared service key (X-Bot-Key), and can then only
obtain short-lived *bot-channel* sessions for users who linked their Telegram account with a one-time code.
Bot-channel sessions use the same API as the web app but cannot touch account security (2FA, sessions, linking).
"""

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..deps import log_activity, web_user
from ..models import LinkCode, Session, TelegramLink, User, utcnow
from ..security import new_token, token_hash

router = APIRouter(prefix="/telegram", tags=["telegram"])
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L


def require_bot_key(x_bot_key: str | None = Header(default=None)) -> None:
    key = get_settings().bot_api_key
    if not key:
        raise HTTPException(503, "telegram integration is not configured")
    if not x_bot_key or not secrets.compare_digest(x_bot_key, key):
        raise HTTPException(403, "bad bot key")


def require_telegram_allowlist(telegram_id: int) -> None:
    """Bot sessions are disabled until the operator configures an explicit Telegram ID allowlist."""
    raw = get_settings().telegram_allowed_user_ids
    if not raw.strip():
        raise HTTPException(503, "Telegram sessions are disabled until TELEGRAM_ALLOWED_USER_IDS is configured")
    try:
        allowed = {int(value.strip()) for value in raw.split(",") if value.strip()}
    except ValueError:
        raise HTTPException(503, "TELEGRAM_ALLOWED_USER_IDS contains an invalid numeric ID")
    if telegram_id not in allowed:
        raise HTTPException(403, "this Telegram account is not allowlisted")


async def _revoke_bot_sessions(db: AsyncSession, user_id: str) -> None:
    await db.execute(update(Session).where(Session.user_id == user_id, Session.channel == "bot",
                                           Session.revoked_at.is_(None)).values(revoked_at=utcnow()))


# --- web app side --------------------------------------------------------------------------------


@router.get("/status")
async def status(user: User = Depends(web_user), db: AsyncSession = Depends(get_db)):
    link = (await db.execute(select(TelegramLink).where(TelegramLink.user_id == user.id))).scalar_one_or_none()
    s = get_settings()
    return {"linked": link is not None, "linked_at": link.created_at.isoformat() if link else None,
            "bot_username": s.telegram_bot_username,
            "enabled": bool(s.bot_api_key and s.telegram_allowed_user_ids.strip())}


@router.post("/link-code")
async def link_code(user: User = Depends(web_user), db: AsyncSession = Depends(get_db)):
    s = get_settings()
    if not s.bot_api_key or not s.telegram_allowed_user_ids.strip():
        raise HTTPException(503, "telegram integration is not configured")
    if (await db.execute(select(TelegramLink).where(TelegramLink.user_id == user.id))).scalar_one_or_none():
        raise HTTPException(409, "a Telegram account is already linked; unlink it first")
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(10))
    db.add(LinkCode(user_id=user.id, code_hash=token_hash(code), expires_at=utcnow() + timedelta(minutes=s.link_code_ttl_minutes)))
    await db.commit()
    return {"code": code, "expires_in_minutes": s.link_code_ttl_minutes, "bot_username": s.telegram_bot_username}


@router.post("/unlink", status_code=204)
async def unlink(user: User = Depends(web_user), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(TelegramLink).where(TelegramLink.user_id == user.id))
    await _revoke_bot_sessions(db, user.id)
    await log_activity(db, user.id, "telegram.unlinked")
    await db.commit()


# --- bot side (service key) ----------------------------------------------------------------------


class LinkBody(BaseModel):
    code: str
    telegram_id: int
    chat_id: int


class TgBody(BaseModel):
    telegram_id: int


@router.post("/bot/link", dependencies=[Depends(require_bot_key)])
async def bot_link(body: LinkBody, db: AsyncSession = Depends(get_db)):
    require_telegram_allowlist(body.telegram_id)
    row = (await db.execute(select(LinkCode).where(LinkCode.code_hash == token_hash(body.code.strip().upper())))).scalar_one_or_none()
    if not row or row.used_at or row.expires_at < utcnow():
        raise HTTPException(400, "invalid or expired code. Generate a new one in the web app.")
    if (await db.execute(select(TelegramLink).where(TelegramLink.telegram_id == body.telegram_id))).scalar_one_or_none():
        raise HTTPException(409, "this Telegram account is already linked to a Helm Terminals account")
    if (await db.execute(select(TelegramLink).where(TelegramLink.user_id == row.user_id))).scalar_one_or_none():
        raise HTTPException(409, "that Helm Terminals account already has a Telegram linked")
    row.used_at = utcnow()
    db.add(TelegramLink(user_id=row.user_id, telegram_id=body.telegram_id, chat_id=body.chat_id))
    await log_activity(db, row.user_id, "telegram.linked")
    await db.commit()
    user = await db.get(User, row.user_id)
    name, _, domain = user.email.partition("@")
    return {"ok": True, "account": f"{name[:2]}***@{domain}"}


@router.post("/bot/session", dependencies=[Depends(require_bot_key)])
async def bot_session(body: TgBody, db: AsyncSession = Depends(get_db)):
    require_telegram_allowlist(body.telegram_id)
    link = (await db.execute(select(TelegramLink).where(TelegramLink.telegram_id == body.telegram_id))).scalar_one_or_none()
    if not link:
        raise HTTPException(404, "not_linked")
    await _revoke_bot_sessions(db, link.user_id)  # one active bot session per user
    token = new_token()
    ttl = get_settings().bot_session_ttl_minutes
    db.add(Session(user_id=link.user_id, token_hash=token_hash(token), channel="bot", user_agent="telegram-bot",
                   expires_at=utcnow() + timedelta(minutes=ttl)))
    await db.commit()
    return {"token": token, "expires_in_minutes": ttl}


@router.post("/bot/unlink", dependencies=[Depends(require_bot_key)])
async def bot_unlink(body: TgBody, db: AsyncSession = Depends(get_db)):
    require_telegram_allowlist(body.telegram_id)
    link = (await db.execute(select(TelegramLink).where(TelegramLink.telegram_id == body.telegram_id))).scalar_one_or_none()
    if not link:
        raise HTTPException(404, "not_linked")
    user_id = link.user_id
    await db.delete(link)
    await _revoke_bot_sessions(db, user_id)
    await log_activity(db, user_id, "telegram.unlinked")
    await db.commit()
    return {"ok": True}
