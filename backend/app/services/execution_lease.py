"""Fail closed after losing the singleton's original PostgreSQL session.

This is a one-replica safety boundary, not permission for horizontal scaling.
An already-issued remote signing request cannot be recalled by a database check.
"""
import asyncio

from sqlalchemy import text

from ..config import get_settings
from ..signing.base import SigningError

_connection = None
_pid = None
_key = None
_lost = False
_lock = None


async def bind(connection, key):
    global _connection, _pid, _key, _lost, _lock
    _connection, _key, _lost, _lock = connection, key, False, asyncio.Lock()
    _pid = await connection.scalar(text("SELECT pg_backend_pid()")) if connection else None
    if connection:
        await connection.commit()


def invalidate():
    global _lost
    _lost = True


async def assert_owned():
    if get_settings().env != "production" and _connection is None:
        return
    if _lost or _connection is None or _lock is None:
        raise SigningError("Execution ownership is unavailable; signing is paused")
    async with _lock:
        try:
            if _lost or _connection.closed or _connection.invalidated:
                raise RuntimeError("session lost")
            ok = await _connection.scalar(text("""SELECT pg_backend_pid() = :pid AND EXISTS (
                SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND pid = :pid
                AND classid = 0 AND objid = :key AND objsubid = 1 AND granted)
            """), {"pid": _pid, "key": _key})
            await _connection.commit()
            if not ok:
                raise RuntimeError("ownership lost")
        except Exception:
            invalidate()
            raise SigningError("Execution ownership was lost; signing is paused until a controlled restart") from None


async def authorize_signer_ref(signer_ref):
    await assert_owned()
    if get_settings().env != "production":
        return
    from ..db import get_sessionmaker
    from ..models import Wallet, AccountSecurity, AccountStatus, utcnow
    from sqlalchemy import select

    async with get_sessionmaker()() as db:
        owners = set((await db.execute(select(Wallet.user_id).where(Wallet.signer_ref == signer_ref))).scalars())
        if len(owners) != 1:
            raise SigningError("Signing reference has no unambiguous account owner")
        owner = owners.pop()
        state = await db.get(AccountSecurity, owner)
        suspended = (await db.execute(select(AccountStatus.status).where(AccountStatus.user_id == owner))).scalar_one_or_none()
        if suspended == "suspended" or (state and state.recovery_hold_until and state.recovery_hold_until > utcnow()):
            raise SigningError("Account spending is currently restricted")
