import asyncio
from functools import lru_cache

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel
from solders.pubkey import Pubkey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .chains.solana import SolanaRPC
from .config import get_settings
from .db import get_db, get_sessionmaker
from .models import Activity, Job, Session, User, Wallet, utcnow
from .security import check_totp, decrypt_secret, token_hash
from .services.executor import Executor
from .signing import get_signer


@lru_cache
def get_rpc() -> SolanaRPC:
    return SolanaRPC(get_settings().solana_rpc_url)


def get_executor(rpc: SolanaRPC = Depends(get_rpc), signer=Depends(get_signer)) -> Executor:
    return Executor(rpc, signer)


async def current_user(authorization: str | None = Header(default=None), db: AsyncSession = Depends(get_db)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    th = token_hash(authorization[7:].strip())
    sess = (await db.execute(select(Session).where(Session.token_hash == th))).scalar_one_or_none()
    if not sess or sess.revoked_at or sess.expires_at < utcnow():
        raise HTTPException(401, "invalid or expired session")
    user = await db.get(User, sess.user_id)
    if not user:
        raise HTTPException(401, "invalid session")
    return user


def valid_address(addr: str) -> bool:
    try:
        Pubkey.from_string(addr)
        return True
    except Exception:
        return False


async def log_activity(db: AsyncSession, user_id: str, kind: str, ref_type: str | None = None,
                       ref_id: str | None = None, payload: dict | None = None) -> None:
    db.add(Activity(user_id=user_id, kind=kind, ref_type=ref_type, ref_id=ref_id, payload=payload or {}))


async def owned_wallet(db: AsyncSession, user: User, wallet_id: str, managed: bool = True) -> Wallet:
    w = await db.get(Wallet, wallet_id)
    if not w or w.user_id != user.id:
        raise HTTPException(404, f"wallet {wallet_id} not found")
    if managed and w.kind != "managed":
        raise HTTPException(400, f"wallet {wallet_id} is watch-only and cannot sign")
    return w


class SpendConfirm(BaseModel):
    """Every fund-moving request first returns a preflight, then must be repeated with these fields."""

    confirm: bool = False
    preflight_hash: str | None = None
    idempotency_key: str | None = None
    totp: str | None = None


async def find_replay(db: AsyncSession, user: User, body: SpendConfirm) -> dict | None:
    """A retried confirmation returns the original job, even if chain state has moved on since."""
    if not (body.confirm and body.idempotency_key):
        return None
    job = (
        await db.execute(select(Job).where(Job.user_id == user.id, Job.idempotency_key == body.idempotency_key))
    ).scalar_one_or_none()
    return {"job_id": job.id, "status": job.status} if job else None


async def begin_spend(
    db: AsyncSession, user: User, body: SpendConfirm, pf: dict, kind: str, launch_id: str | None, params: dict
) -> tuple[Job, bool]:
    """Validate a confirmation and create (or find) the Job. Returns (job, created)."""
    if body.preflight_hash != pf["preflight_hash"]:
        raise HTTPException(409, "preflight_changed: review the new preflight and confirm again")
    if not body.idempotency_key:
        raise HTTPException(400, "idempotency_key is required when confirm=true")
    if user.totp_enabled:
        if not body.totp or not user.totp_secret_enc or not check_totp(decrypt_secret(user.totp_secret_enc), body.totp):
            raise HTTPException(401, "totp_required: provide a valid 2FA code to confirm")
    existing = (
        await db.execute(select(Job).where(Job.user_id == user.id, Job.idempotency_key == body.idempotency_key))
    ).scalar_one_or_none()
    if existing:
        return existing, False
    job = Job(user_id=user.id, launch_id=launch_id, kind=kind, params=params, idempotency_key=body.idempotency_key)
    db.add(job)
    await log_activity(db, user.id, f"{kind}.confirmed", "job", job.id, {"preflight_hash": body.preflight_hash})
    await db.commit()
    return job, True


_tasks: set[asyncio.Task] = set()


async def run_job(job_id: str, fn) -> None:
    """Run `fn(db, job)` in its own session, in the background (or inline in tests)."""

    async def _wrapped() -> None:
        async with get_sessionmaker()() as db:
            job = await db.get(Job, job_id)
            try:
                await fn(db, job)
            except Exception as e:  # never leave a job stuck in "submitted"
                await db.rollback()
                job = await db.get(Job, job_id)
                job.status = "failed"
                job.params = {**job.params, "error": str(e)[:300]}
                await db.commit()

    if get_settings().run_jobs_inline:
        await _wrapped()
    else:
        t = asyncio.create_task(_wrapped())
        _tasks.add(t)
        t.add_done_callback(_tasks.discard)
