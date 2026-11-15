"""Bounded chain reconciliation independent of browser polling. Never replays a spend."""
import asyncio
import logging

from sqlalchemy import select

from ..config import get_settings
from ..db import get_sessionmaker
from ..models import Job, PlatformCharge, User
from .executor import Executor

log = logging.getLogger("helm.reconciliation")


async def reconcile_once(ex=None):
    if ex is None:
        from ..chains.solana import shared_rpc
        from ..signing import get_signer
        ex = Executor(shared_rpc(get_settings().solana_rpc_url), get_signer())
    from .charges import reconcile
    async with get_sessionmaker()() as db:
        ids = list((await db.execute(select(PlatformCharge.id).where(
            PlatformCharge.status.in_(("pending", "processing")), PlatformCharge.signature.is_not(None))
            .order_by(PlatformCharge.updated_at).limit(30))).scalars())
    for identifier in ids:
        async with get_sessionmaker()() as db:
            try:
                await asyncio.wait_for(reconcile(db, ex, await db.get(PlatformCharge, identifier)), 8)
            except Exception:
                await db.rollback()
                log.warning("Payment reconciliation unavailable for %s", identifier, exc_info=True)
    # Reuse the same evidence rules as the job API; never mark a successful prefix as completed work.
    from ..routes.launches import get_job
    async with get_sessionmaker()() as db:
        jobs = list((await db.execute(select(Job.id).where(Job.status.in_(("submitted", "unconfirmed")))
                    .order_by(Job.updated_at).limit(30))).scalars())
    for identifier in jobs:
        async with get_sessionmaker()() as db:
            job = await db.get(Job, identifier)
            user = await db.get(User, job.user_id)
            if user:
                try:
                    await asyncio.wait_for(get_job(identifier, user, db, ex.rpc, ex), 8)
                except Exception:
                    await db.rollback()
                    log.warning("Job reconciliation unavailable for %s", identifier, exc_info=True)


async def reconciliation_worker():
    while True:
        try:
            await reconcile_once()
        except Exception:
            log.exception("Reconciliation pass failed")
        await asyncio.sleep(15)
