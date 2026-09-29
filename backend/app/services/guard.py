"""Brute-force and replay protection for passwords and 2FA codes.

- Failed attempts are counted per target (an account's email for login, the user for 2FA) in a sliding window;
  past the limit, further attempts are refused with 429 until the window passes, even with the right answer.
- An accepted 2FA code's time step is recorded per user, so the same code can't be used twice.
Login counts two ways: per (email, client address), which stops a guesser quickly, and per email overall with a much
higher limit, which caps distributed guessing. The overall limit is high so that someone who only knows a victim's
email can't cheaply lock them out.
"""

import time
import ipaddress
from datetime import timedelta

import pyotp
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from cryptography.fernet import InvalidToken

from ..models import AuthFailure, TotpUse, User, utcnow
from ..security import decrypt_secret, verify_password

WINDOW = timedelta(minutes=15)
LOGIN_LIMIT = 10  # wrong passwords per email from one client address, per window
LOGIN_LIMIT_ANY = 100  # wrong passwords per email from anywhere, per window
TOTP_LIMIT = 8  # wrong 2FA codes per user per window


async def _failures(db: AsyncSession, key: str) -> int:
    since = utcnow() - WINDOW
    return (await db.execute(select(func.count()).select_from(AuthFailure).where(
        AuthFailure.key == key, AuthFailure.created_at >= since))).scalar_one()


async def ensure_not_locked(db: AsyncSession, key: str, limit: int) -> None:
    if await _failures(db, key) >= limit:
        raise HTTPException(429, "too many attempts; wait 15 minutes and try again")


async def record_attempt(db: AsyncSession, *keys: str) -> None:
    """Record and commit throttled attempts at once so the count survives the response."""
    for key in keys:
        db.add(AuthFailure(key=key))
    await db.execute(delete(AuthFailure).where(AuthFailure.created_at < utcnow() - WINDOW * 4))
    await db.commit()


record_failure = record_attempt


SIGNUP_LIMIT = 5  # registration attempts per client address per 15-minute window


def client_address(request) -> str:
    """Return a client IP only from the socket or a chain received through configured trusted proxies.

    The setting must contain provider-verified CIDRs. With no trusted ranges, forwarding headers are ignored.
    Walking from the socket toward the client selects the nearest untrusted hop, so a client-supplied prefix
    cannot replace the address appended by a trusted proxy.
    """
    peer_text = request.client.host if request.client else "unknown"
    try:
        peer = ipaddress.ip_address(peer_text)
    except ValueError:
        return "unknown"

    networks = []
    from ..config import get_settings

    for value in get_settings().trusted_proxy_cidrs.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            # A malformed proxy configuration must never broaden who is trusted.
            return str(peer)

    def trusted(address):
        return any(address.version == network.version and address in network for network in networks)

    if not networks or not trusted(peer):
        return str(peer)
    parts = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
    # Reject oversized or malformed chains and use the verified socket peer instead.
    if not parts or len(parts) > 20:
        return str(peer)
    try:
        chain = [ipaddress.ip_address(part) for part in parts]
    except ValueError:
        return str(peer)
    for address in reversed([*chain, peer]):
        if not trusted(address):
            return str(address)
    return str(peer)


def _matching_step(secret: str, code: str) -> int | None:
    code = (code or "").strip()
    if not code.isdigit():
        return None
    totp, now = pyotp.TOTP(secret), time.time()
    for drift in (0, -1, 1):  # the current 30 s step, then one either side for clock skew
        t = now + drift * totp.interval
        if pyotp.utils.strings_equal(totp.at(t), code):
            return int(t // totp.interval)
    return None


async def check_totp_once(db: AsyncSession, user: User, code: str | None) -> bool:
    """True only for a valid, unused code, under the attempt limit. Failures are counted."""
    key = f"totp:{user.id}"
    await ensure_not_locked(db, key, TOTP_LIMIT)
    try:
        secret = decrypt_secret(user.totp_secret_enc) if user.totp_secret_enc else None
    except InvalidToken:
        # The 2FA secret was encrypted under a different SECRET_KEY. Fail clearly, not with a 500 on every login.
        raise HTTPException(503, "2FA can't be checked right now (server key mismatch); contact support")
    step = _matching_step(secret, code or "") if secret else None
    if step is None:
        await record_failure(db, key)
        return False
    try:
        async with db.begin_nested():  # a savepoint: a clash undoes only this insert
            db.add(TotpUse(user_id=user.id, step=step))
    except IntegrityError:  # this code was already used
        await record_failure(db, key)
        return False
    await db.execute(delete(TotpUse).where(TotpUse.user_id == user.id, TotpUse.step < step - 10))
    return True


async def check_password(db: AsyncSession, user: User, password: str, key: str | None = None) -> bool:
    """Password re-entry inside the app (not login). Counted per user, apart from login, so a stolen session can't
    use it to lock the owner out of signing in."""
    key = key or f"reauth:{user.id}"
    await ensure_not_locked(db, key, LOGIN_LIMIT)
    if verify_password(password, user.password_hash):
        return True
    await record_failure(db, key)
    return False
