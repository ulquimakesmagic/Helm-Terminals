"""Admin roles, the superadmin identity, step-up checks and the admin audit log.

The superadmin
- Superadmin access fails closed unless SUPERADMIN_USER_ID is configured to the owner's account ID. The initial
  binding additionally requires the designated email; an email address alone never grants the role.
- Once bound, the row stays with that user ID. Another account cannot take it: there is no endpoint that creates,
  moves or restores a superadmin row. A missing or mismatched pin disables that row.
- Admin access needs a web session (not the Telegram bot) and 2FA turned on. Sensitive changes (treasury
  addresses, fee settings, suspensions, roles) also need the password and a fresh 2FA code.

Other admins
- The only other role is "viewer": read-only access to the dashboard. Only the superadmin can grant or revoke it,
  and a viewer can't change anything, including roles.
- Admin access gives no access to users' wallets, balances, keys or signing: the admin API only reads aggregates,
  account metadata and Helm's own revenue.
"""

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..deps import web_user
from ..models import AdminAudit, AdminRole, User, utcnow
from .guard import check_password, check_totp_once

SUPERADMIN_EMAIL = "maliksucess@gmail.com"
ROLES = ("superadmin", "viewer")


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


async def role_of(db: AsyncSession, user: User) -> str | None:
    """The user's admin role. The superadmin fails closed unless its exact user ID is configured."""
    row = (await db.execute(select(AdminRole).where(AdminRole.user_id == user.id))).scalar_one_or_none()
    pinned = get_settings().superadmin_user_id.strip()
    if row:
        if row.role == "superadmin" and (not pinned or row.user_id != pinned):
            return None  # missing or mismatched pin overrides any historical binding
        return row.role
    if not pinned:
        return None
    if normalize_email(user.email) != SUPERADMIN_EMAIL:
        return None
    if pinned != user.id:
        return None
    bound = (await db.execute(select(AdminRole).where(AdminRole.role == "superadmin"))).scalar_one_or_none()
    if bound:  # already bound to another user ID: this account can't take it
        return None
    db.add(AdminRole(user_id=user.id, role="superadmin", granted_by="system:email-binding"))
    db.add(AdminAudit(actor_user_id=user.id, action="superadmin.bound", target=user.id,
                      changes={"role": [None, "superadmin"]}, context={"by": "configured user ID"}))
    await db.commit()
    return "superadmin"


def request_context(request: Request | None) -> dict:
    if request is None:
        return {}
    from .guard import client_address

    return {"ip": client_address(request), "ua": (request.headers.get("user-agent") or "")[:120]}


async def audit(db: AsyncSession, actor: User | None, action: str, target: str | None = None,
                changes: dict | None = None, request: Request | None = None) -> None:
    db.add(AdminAudit(actor_user_id=actor.id if actor else None, action=action, target=target,
                      changes=changes or {}, context=request_context(request)))


def require_role(*allowed: str):
    async def dep(request: Request, user: User = Depends(web_user), db: AsyncSession = Depends(get_db)) -> User:
        role = await role_of(db, user)
        if role not in allowed:
            # Same answer whether the account is unknown to the admin system or just lacks the role.
            raise HTTPException(403, "not allowed")
        if not user.totp_enabled:
            raise HTTPException(403, "admin access needs 2FA turned on for this account")
        user.admin_role = role  # type: ignore[attr-defined]
        await _note_session(db, user, request)
        return user
    return dep


async def _note_session(db: AsyncSession, user: User, request: Request) -> None:
    """Audit the first admin request of each session (an 'admin login')."""
    from ..security import token_hash

    auth = request.headers.get("authorization", "")
    ref = token_hash(auth[7:].strip())[:16] if auth.lower().startswith("bearer ") else "unknown"
    seen = (await db.execute(select(AdminAudit.id).where(
        AdminAudit.actor_user_id == user.id, AdminAudit.action == "admin.session", AdminAudit.target == ref
    ).limit(1))).scalar_one_or_none()
    if not seen:
        await audit(db, user, "admin.session", ref, request=request)
        await db.commit()


any_admin = require_role("superadmin", "viewer")
superadmin = require_role("superadmin")


class StepUp(BaseModel):
    password: SecretStr
    totp: str


async def step_up(db: AsyncSession, user: User, proof: StepUp | None) -> None:
    """Password plus a fresh 2FA code, for changes to money routing, fees and account access."""
    if proof is None:
        raise HTTPException(401, "totp_required: confirm with your password and a 2FA code")
    if not await check_password(db, user, proof.password.get_secret_value()):
        await _denied(db, user, "password")
        raise HTTPException(400, "wrong password")
    if not await check_totp_once(db, user, proof.totp):
        await _denied(db, user, "2fa code")
        raise HTTPException(401, "totp_required: that 2FA code isn't valid (or was already used)")
    await db.commit()  # the code is used up now, even if the change itself is then refused


async def _denied(db: AsyncSession, user: User, what: str) -> None:
    await audit(db, user, "stepup.denied", user.id, {"failed": [None, what]})
    await db.commit()
