"""Bounded chain reconciliation independent of browser polling. Never replays a spend."""
import asyncio
import logging

from sqlalchemy import select

from ..config import get_settings
from ..db import get_sessionmaker
from ..models import Job, Launch, PlatformCharge, User, Wallet, utcnow
from datetime import timedelta
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
    # A late-confirming deployment can contain an initial buy. Recovery records its debt without signing.
    async with get_sessionmaker()() as db:
        deployments = list((await db.execute(select(Job.id).join(Launch, Job.launch_id == Launch.id).where(
            Job.kind == "deploy", Launch.status == "live", Job.updated_at < utcnow() - timedelta(seconds=30),
            Job.params["initial_buy_fee"]["amount_raw"].as_string().is_not(None),
            Job.params["initial_buy_fee_handled"].as_boolean().is_not(True))
            .order_by(Job.updated_at).limit(30))).scalars())
    for identifier in deployments:
        async with get_sessionmaker()() as db:
            job = await db.get(Job, identifier)
            terms = (job.params or {}).get("initial_buy_fee")
            if not terms or job.params.get("initial_buy_fee_handled"):
                continue
            from .charges import initial_buy_fee
            try:
                launch, wallet = await db.get(Launch, job.launch_id), await db.get(Wallet, terms["wallet_id"])
                if wallet:
                    await initial_buy_fee(db, ex, job, launch, wallet, recovery=True)
            except Exception:
                await db.rollback()
                log.warning("Initial-buy fee recovery unavailable for %s", identifier, exc_info=True)


async def reconciliation_worker():
    while True:
        try:
            await reconcile_once()
        except Exception:
            log.exception("Reconciliation pass failed")
        await asyncio.sleep(15)
