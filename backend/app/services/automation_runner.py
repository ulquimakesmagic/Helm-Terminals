"""Persistent worker for finite one-way scheduled trade plans.

Each tranche is a normal Helm Job: it uses the existing adapter, signer, simulation-before-send path, fee plan,
transaction records and audit trail. A claimed tranche is never retried automatically after an uncertain outcome.
"""

import asyncio
import logging
from datetime import timedelta
from contextlib import suppress

from sqlalchemy import select, update

from ..chains.registry import get_adapter
from ..chains.solana import shared_rpc
from ..config import get_settings
from ..db import get_sessionmaker
from ..deps import log_activity
from ..models import Automation, Job, Launch, LaunchWallet, User, Wallet, utcnow
from ..services.chain_balances import fee_reserve, native_balance, native_coin, token_balance_raw
from ..services.executor import Executor, run_evm_trade, run_trade
from ..services.fees import fee_plan_for
from ..signing import get_signer

log = logging.getLogger(__name__)


async def _fail(automation_id: str, message: str, job_id: str | None = None) -> None:
    async with get_sessionmaker()() as db:
        automation = await db.get(Automation, automation_id)
        if automation and automation.status == "executing":
            automation.status = "failed"
            automation.error = message[:300]
            if job_id:
                automation.last_job_id = job_id
            await db.commit()


async def _stop_in_session(db, automation: Automation, message: str) -> None:
    automation.status = "failed"
    automation.error = message[:300]
    await db.commit()


async def _mark_interrupted() -> None:
    """Stop an in-flight plan after a worker restart instead of risking a duplicate trade."""
    cutoff = utcnow() - timedelta(minutes=3)
    async with get_sessionmaker()() as db:
        rows = (await db.execute(select(Automation).where(
            Automation.status == "executing", Automation.updated_at < cutoff
        ).with_for_update(skip_locked=True))).scalars().all()
        for row in rows:
            row.status = "failed"
            row.error = "Worker stopped during execution. Check the linked job and chain before creating another plan."
        if rows:
            await db.commit()


async def _heartbeat(automation_id: str) -> None:
    """Keep a live, long confirmation from being mistaken for a crashed worker."""
    while True:
        await asyncio.sleep(30)
        async with get_sessionmaker()() as db:
            await db.execute(update(Automation).where(
                Automation.id == automation_id, Automation.status == "executing"
            ).values(updated_at=utcnow()))
            await db.commit()


async def run_due_automation_once(ex: Executor | None = None) -> bool:
    """Claim and execute at most one due tranche. Returns whether work was found."""
    await _mark_interrupted()
    async with get_sessionmaker()() as db:
        row = (await db.execute(select(Automation).where(
            Automation.status == "scheduled", Automation.next_run_at <= utcnow()
        ).order_by(Automation.next_run_at, Automation.created_at).with_for_update(skip_locked=True).limit(1))).scalars().first()
        if not row:
            return False
        row.status = "executing"
        await db.commit()
        automation_id = row.id

    if ex is None:
        settings = get_settings()
        ex = Executor(shared_rpc(settings.solana_rpc_url), get_signer())

    job_id: str | None = None
    heartbeat = asyncio.create_task(_heartbeat(automation_id))
    try:
        async with get_sessionmaker()() as db:
            automation = await db.get(Automation, automation_id)
            if not automation or automation.status != "executing":
                return True
            launch = await db.get(Launch, automation.launch_id)
            user = await db.get(User, automation.user_id)
            if not launch or launch.status != "live" or not launch.mint_address:
                await _stop_in_session(db, automation, "Launch is no longer live; scheduled trades stopped.")
                return True
            if not user:
                await _stop_in_session(db, automation, "Account is unavailable; scheduled trades stopped.")
                return True

            wallets: list[Wallet] = []
            for wallet_id in automation.wallet_ids:
                wallet = await db.get(Wallet, wallet_id)
                linked = await db.get(LaunchWallet, (launch.id, wallet_id))
                if not wallet or wallet.user_id != user.id or wallet.kind != "managed" or not linked:
                    await _stop_in_session(db, automation, "A selected managed wallet is no longer attached; scheduled trades stopped.")
                    return True
                wallets.append(wallet)
            if not wallets:
                await _stop_in_session(db, automation, "The plan has no attached managed wallets.")
                return True

            net = launch.network
            coin = native_coin(net)
            fee_plan = await fee_plan_for(db, user, net)
            remaining = automation.total_orders - automation.runs_completed
            if automation.side == "buy":
                fee_mult = 1 + (fee_plan.bps / 10_000 if fee_plan.active and net == "solana" else 0)
                per_wallet_required = automation.order_sol * remaining * fee_mult + await fee_reserve(ex, net, remaining)
                for wallet in wallets:
                    if await native_balance(ex, net, wallet.address) < per_wallet_required:
                        await _stop_in_session(db, automation, f"{wallet.label or wallet.address[:8]} no longer has enough {coin} for the remaining plan.")
                        return True
                amount = automation.order_sol
            else:
                sell_percent = automation.sell_percent
                sellable: list[Wallet] = []
                for wallet in wallets:
                    token_balance = await token_balance_raw(ex, net, launch.mint_address, wallet.address)
                    if int(token_balance * sell_percent / 100) > 0:
                        sellable.append(wallet)
                if not sellable:
                    automation.status = "completed"
                    automation.error = None
                    await log_activity(db, user.id, "automation.sell_plan_exhausted", "automation", automation.id, {
                        "order_number": automation.runs_completed + 1, "side": "sell",
                    })
                    await db.commit()
                    return True
                wallets = sellable
                per_wallet_fee_reserve = await fee_reserve(ex, net, remaining * (1 if net == "solana" else 2))
                for wallet in wallets:
                    if await native_balance(ex, net, wallet.address) < per_wallet_fee_reserve:
                        await _stop_in_session(db, automation, f"{wallet.label or wallet.address[:8]} no longer has enough {coin} for network fees.")
                        return True
                amount = f"{sell_percent:g}%" if net == "solana" else sell_percent

            order_number = automation.runs_completed + 1
            job = Job(
                user_id=user.id,
                launch_id=launch.id,
                kind=f"scheduled_{automation.side}",
                params={
                    "automation_id": automation.id,
                    "order_number": order_number,
                    "total_orders": automation.total_orders,
                    "side": automation.side,
                    "wallets": [w.address for w in wallets],
                    "amount": amount,
                    "network": launch.network,
                    "slippage_pct": automation.slippage_pct,
                },
                idempotency_key=f"automation:{automation.id}:{order_number}",
                channel="web",
            )
            db.add(job)
            await db.flush()
            job_id = job.id
            automation.last_job_id = job.id
            await db.commit()

            async def still_executing() -> bool:
                async with get_sessionmaker()() as guard_db:
                    state = await guard_db.get(Automation, automation_id)
                    return bool(state and state.status == "executing")

            if net == "solana":
                await run_trade(
                    db, ex, get_adapter(launch.launchpad), job, launch.mint_address, wallets,
                    automation.side, amount, automation.side == "buy", "stagger", 500,
                    automation.slippage_pct, fee_plan,
                    execution_guard=still_executing,
                )
            else:
                await run_evm_trade(
                    db, ex, get_adapter(launch.launchpad), job, launch.mint_address, wallets,
                    automation.side, amount, "stagger", 500, automation.slippage_pct,
                    execution_guard=still_executing,
                )
            await db.refresh(job)
            await db.refresh(automation)
            if automation.status != "executing":
                if job.status == "confirmed":
                    automation.runs_completed += 1
                    await db.commit()
                return True
            if job.status == "confirmed":
                automation.runs_completed += 1
                automation.error = None
                if automation.runs_completed >= automation.total_orders:
                    automation.status = "completed"
                    automation.next_run_at = utcnow()
                else:
                    automation.status = "scheduled"
                    automation.next_run_at = utcnow() + timedelta(seconds=automation.interval_seconds)
                await log_activity(db, user.id, "automation.order_confirmed", "automation", automation.id, {
                    "job_id": job.id, "order_number": order_number, "side": automation.side,
                })
            else:
                automation.status = "failed"
                automation.error = f"Order {order_number} ended as {job.status}; the remaining schedule was stopped."
                await log_activity(db, user.id, "automation.order_failed", "automation", automation.id, {
                    "job_id": job.id, "order_number": order_number, "status": job.status, "side": automation.side,
                })
            await db.commit()
    except Exception as exc:
        log.exception("Scheduled automation %s stopped", automation_id)
        await _fail(automation_id, f"Execution stopped: {str(exc)[:240]}", job_id)
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
    return True


async def automation_worker() -> None:
    settings = get_settings()
    while True:
        try:
            await run_due_automation_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Scheduled-buy worker cycle failed")
        await asyncio.sleep(max(1.0, settings.automation_poll_seconds))
