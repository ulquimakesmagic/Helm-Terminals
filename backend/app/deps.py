import logging
import asyncio
from functools import lru_cache

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel
from solders.pubkey import Pubkey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .chains.solana import SolanaRPC, shared_rpc
from .config import get_settings
from .db import get_db, get_sessionmaker
from .models import Activity, Job, Session, User, Wallet, utcnow
from .security import token_hash
from .services.executor import Executor
from .signing import get_signer


@lru_cache
def get_rpc() -> SolanaRPC:
    return shared_rpc(get_settings().solana_rpc_url)


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
    from .models import AccountStatus

    st = (await db.execute(select(AccountStatus.status).where(AccountStatus.user_id == user.id))).scalar_one_or_none()
    if st == "suspended":
        raise HTTPException(403, "this account is suspended; contact support")
    user.channel = sess.channel  # type: ignore[attr-defined]  # transient: which client this request came from
    return user


async def web_user(user: User = Depends(current_user)) -> User:
    """Account-security actions (2FA, sessions, Telegram linking) are only allowed from the web app."""
    if getattr(user, "channel", "web") != "web":
        raise HTTPException(403, "this action is only available in the web app")
    return user


# Sending to an address Helm doesn't hold the key for always needs 2FA, whatever TFA_REQUIRED_KINDS says: these are
# the paths a stolen session (or a leaked bot key) would use to take funds out.
ALWAYS_2FA = frozenset({"withdraw", "send_tokens", "fund_external"})


def needs_2fa(kind: str) -> bool:
    configured = {k.strip() for k in get_settings().tfa_required_kinds.split(",") if k.strip()}
    return kind in ALWAYS_2FA or kind in configured


def two_fa_warnings(user: User, kind: str) -> list[str]:
    if needs_2fa(kind) and not user.totp_enabled:
        return ["Enable 2FA in the web app (Settings) before sending assets out of your wallets."]
    return []


EVM_NETWORKS = {"base", "bsc", "ethereum", "robinhood"}
NETWORKS = {"solana", *EVM_NETWORKS}


def valid_address_for(network: str, addr: str) -> bool:
    """Address format check per chain. EVM chains share the 0x + 40 hex format."""
    if network == "solana":
        return valid_address(addr)
    if network in EVM_NETWORKS:
        return len(addr) == 42 and addr[:2].lower() == "0x" and all(c in "0123456789abcdefABCDEF" for c in addr[2:])
    from .chains.networks import EXTRA_NETWORKS

    net = EXTRA_NETWORKS.get(network)
    if net is None:
        return False
    if net.family == "tron":
        from .chains import tron

        return tron.valid_address(addr)
    if net.family == "near":
        from .chains import near

        # Helm is on NEAR mainnet only: a .testnet account doesn't exist there, so a transfer to it would fail
        return near.valid_account(addr) and not addr.strip().lower().endswith(".testnet")
    if net.family == "ton":
        from .chains import ton

        return ton.valid_address(addr)
    from eth_utils import is_checksum_address  # Arc: EVM addresses, checksum enforced when mixed-case

    body = addr[2:] if addr[:2] == "0x" else ""
    if len(body) != 40 or any(c not in "0123456789abcdefABCDEF" for c in body):
        return False
    return body in (body.lower(), body.upper()) or is_checksum_address(addr)


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
    quote_approval: str | None = None
    launch_fee_approval: str | None = None
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
    # Telegram sessions currently share the web spend API. Keep them read-only until every action has an
    # independently reviewed per-action cap and provider-built transactions are validated before signing.
    # Enforce this at the API boundary so a modified or stale bot client cannot bypass the UI restriction.
    if getattr(user, "channel", "web") != "web":
        raise HTTPException(403, "Telegram is temporarily read-only; use the Helm web app to confirm transactions")
    if kind in {"trade_buy", "trade_sell", "swap"}:
        from .models import PlatformCharge
        unpaid = (await db.execute(select(PlatformCharge.id).where(
            PlatformCharge.payer_user_id == user.id, PlatformCharge.kind == "trade",
            PlatformCharge.status == "failed").limit(1))).first()
        if unpaid:
            raise HTTPException(402, "A trading fee needs settlement. Open Fees & payments before placing another trade.")
    if body.preflight_hash != pf["preflight_hash"]:
        raise HTTPException(409, "preflight_changed: review the new preflight and confirm again")
    from .models import AccountSecurity

    security = await db.get(AccountSecurity, user.id)
    if security and security.recovery_hold_until and security.recovery_hold_until > utcnow():
        raise HTTPException(403, "Spending is temporarily held after account recovery; try again after "
                            + security.recovery_hold_until.isoformat() + " UTC")
    if not body.idempotency_key:
        raise HTTPException(400, "idempotency_key is required when confirm=true")
    if needs_2fa(kind):
        if not user.totp_enabled:
            raise HTTPException(403, "2fa_setup_required: enable 2FA in the web app before sending assets out")
        from .services.guard import check_totp_once

        if not body.totp or not await check_totp_once(db, user, body.totp):
            raise HTTPException(401, "totp_required: provide a valid 2FA code to confirm")
    existing = (
        await db.execute(select(Job).where(Job.user_id == user.id, Job.idempotency_key == body.idempotency_key))
    ).scalar_one_or_none()
    if existing:
        return existing, False
    if kind == "deploy" and launch_id:
        from sqlalchemy import update
        from .models import Launch
        claimed = await db.execute(update(Launch).where(Launch.id == launch_id, Launch.user_id == user.id,
            Launch.status.in_(("draft", "failed"))).values(status="deploying").execution_options(synchronize_session=False))
        if claimed.rowcount != 1:
            await db.rollback()
            existing = (await db.execute(select(Job).where(Job.user_id == user.id,
                Job.idempotency_key == body.idempotency_key))).scalar_one_or_none()
            if existing:
                return existing, False
            raise HTTPException(409, "This launch is already being deployed; refresh its workspace")
    from .services.execution_journal import initialize

    job = Job(user_id=user.id, launch_id=launch_id, kind=kind, params=initialize(params), idempotency_key=body.idempotency_key,
              channel=getattr(user, "channel", "web"))
    db.add(job)
    await log_activity(db, user.id, f"{kind}.confirmed", "job", job.id, {"preflight_hash": body.preflight_hash})
    await db.commit()
    return job, True


_tasks: set[asyncio.Task] = set()


async def run_job(job_id: str, fn, *, already_claimed: bool = False) -> None:
    """Run `fn(db, job)` in its own session, in the background (or inline in tests)."""

    async def _wrapped() -> None:
        async with get_sessionmaker()() as db:
            from sqlalchemy import update
            # Claim once durably before any preparation or signature. A restarted process never replays
            # a submitted job merely because its in-memory task disappeared.
            if not already_claimed:
                claim = await db.execute(update(Job).where(Job.id == job_id, Job.status == "pending")
                                         .values(status="submitted", updated_at=utcnow()))
                await db.commit()
                if claim.rowcount != 1:
                    return
            job = await db.get(Job, job_id)
            try:
                await fn(db, job)
            except Exception as e:  # never leave a job stuck in "submitted"
                logging.getLogger("helm.jobs").exception("job %s (%s) raised", job_id, getattr(job, "kind", "?"))
                await db.rollback()
                job = await db.get(Job, job_id)
                from .models import Transaction

                sent = (await db.execute(select(Transaction.id).where(
                    Transaction.job_id == job_id, Transaction.signature.is_not(None)).limit(1))).first()
                # Once something was sent, "failed" would invite a retry of money that may already have moved.
                job.status = "unconfirmed" if sent else "failed"
                job.params = {**job.params, "error": (str(e)[:240] + (
                    " Something was already sent: check the explorer before trying again." if sent else ""))}
                await db.commit()

    if get_settings().run_jobs_inline:
        await _wrapped()
    else:
        t = asyncio.create_task(_wrapped())
        _tasks.add(t)
        t.add_done_callback(_tasks.discard)
