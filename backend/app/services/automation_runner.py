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


async def _fee_raised(db, automation, net: str) -> bool:
    """Whether the Helm trading fee on this chain is now higher than when the plan was confirmed."""
    from ..models import AutomationTerms
    from . import platform_config as pc

    terms = (await db.execute(select(AutomationTerms).where(
        AutomationTerms.automation_id == automation.id))).scalar_one_or_none()
    key = "trade_fee_bps_solana" if net == "solana" else "trade_fee_bps_evm_dex"
    agreed = getattr(terms, key) if terms else pc._env_defaults()[key]  # older plans: the env rate of the time
    await pc.refresh(db)
    return (pc.bps(key) > agreed or not terms or not terms.fee_terms
            or terms.fee_terms.get("bps") != pc.bps(key)
            or terms.fee_terms.get("destination") != pc.treasury_for(net))


async def run_due_automation_once(ex: Executor | None = None) -> bool:
    from . import platform_config as pc
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
            from ..models import AccountStatus

            suspended = user and (await db.execute(select(AccountStatus.status).where(
                AccountStatus.user_id == user.id))).scalar_one_or_none() == "suspended"
            if not user or suspended:
                await _stop_in_session(db, automation, "Account is unavailable; scheduled trades stopped.")
                return True
            from ..models import AccountSecurity
            security = await db.get(AccountSecurity, user.id)
            if security and security.recovery_hold_until and security.recovery_hold_until > utcnow():
                await _stop_in_session(db, automation, "Account recovery hold is active; create a new plan after the hold ends.")
                return True

            from ..models import AutomationLimits

            lim = (await db.execute(select(AutomationLimits).where(
                AutomationLimits.automation_id == automation.id))).scalar_one_or_none()
            if lim and lim.expires_at <= utcnow():
                automation.status = "expired"
                automation.error = (f"Expired after {automation.runs_completed} of {automation.total_orders} orders; "
                                    "nothing more will run.")
                await log_activity(db, user.id, "automation.expired", "automation", automation.id,
                                   {"orders_done": automation.runs_completed, "launch_id": launch.id})
                await db.commit()
                return True
            if len(automation.wallet_ids or []) > 1:
                # Plans from before one-wallet plans: never trade several wallets in step. Pause for the owner.
                automation.status = "paused"
                automation.error = ("This older plan trades from several wallets at once, which Helm no longer does. "
                                    "Cancel it and create a plan for one wallet in the launch's Launchdesk.")
                await log_activity(db, user.id, "automation.paused", "automation", automation.id,
                                   {"reason": "multi-wallet plan", "launch_id": launch.id})
                await db.commit()
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
            from ..models import PlatformCharge
            outstanding = (await db.execute(select(PlatformCharge.id).where(
                PlatformCharge.payer_user_id == user.id, PlatformCharge.kind == "trade",
                PlatformCharge.status == "failed").limit(1))).first()
            if outstanding:
                await _stop_in_session(db, automation, "A trading fee needs settlement in Fees & payments before this plan can continue.")
                return True
            fee_plan = await fee_plan_for(db, user, net)
            if await _fee_raised(db, automation, net):
                await _stop_in_session(db, automation, "Helm's trading fee went up after this plan was scheduled, so it "
                                                       "stopped instead of charging more. Schedule a new plan to accept "
                                                       "the current rate.")
                return True
            remaining = automation.total_orders - automation.runs_completed
            if automation.side == "buy":
                fee_mult = 1.025
                per_wallet_required = automation.order_sol * remaining * fee_mult + await fee_reserve(ex, net, remaining)
                for wallet in wallets:
                    if await native_balance(ex, net, wallet.address) < per_wallet_required:
                        await _stop_in_session(db, automation, f"{wallet.label or wallet.address[:8]} no longer has enough {coin} for the remaining plan.")
                        return True
                amount = automation.order_sol
                sell_raw = None
            elif lim and lim.sell_order_raw is not None:
                # Staged exit with an explicit total: a fixed token amount per order (the last one takes the
                # remainder), never more than the wallet holds outside any allocation lock.
                from .allocations import locked_raw

                wallet = wallets[0]
                balance = await token_balance_raw(ex, net, launch.mint_address, wallet.address)
                free = max(balance - await locked_raw(db, wallet, launch, lim.token_decimals, ex), 0)
                order_raw = int(lim.sell_order_raw)
                if remaining == 1:  # the last order also takes the rounding remainder, never more
                    order_raw += max(0, int(lim.sell_total_raw) - int(lim.sell_order_raw) * automation.total_orders)
                order_raw = min(order_raw, free)
                if order_raw <= 0:
                    automation.status = "completed"
                    automation.error = "Stopped early: the wallet has no unlocked tokens left to sell."
                    await log_activity(db, user.id, "automation.sell_plan_exhausted", "automation", automation.id, {
                        "order_number": automation.runs_completed + 1, "side": "sell"})
                    await db.commit()
                    return True
                if await native_balance(ex, net, wallet.address) < await fee_reserve(
                        ex, net, remaining * (1 if net == "solana" else 2)):
                    await _stop_in_session(db, automation, f"{wallet.label or wallet.address[:8]} no longer has enough {coin} for network fees.")
                    return True
                amount = order_raw / 10 ** int(lim.token_decimals or 0)  # token units
                sell_raw = order_raw
            else:
                sell_percent = automation.sell_percent
                sell_raw = None
                from .allocations import locked_tokens

                if any([await locked_tokens(db, w.address, launch.id) > 0 for w in wallets]):
                    await _stop_in_session(db, automation, "Part of this wallet's tokens are locked by the allocation "
                                                           "plan; this older sell plan stopped. Create a new staged exit.")
                    return True
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
                    "trade_fee_terms": {"bps": 250, "destination": pc.treasury_for(launch.network), "network": launch.network},
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
                    max_price_impact_pct=lim.max_price_impact_pct if lim else None, sell_raw=sell_raw,
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
                    "job_id": job.id, "order_number": order_number, "side": automation.side, "launch_id": launch.id,
                })
            else:
                automation.status = "failed"
                automation.error = f"Order {order_number} ended as {job.status}; the remaining schedule was stopped."
                await log_activity(db, user.id, "automation.order_failed", "automation", automation.id, {
                    "job_id": job.id, "order_number": order_number, "status": job.status, "side": automation.side, "launch_id": launch.id,
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
            from ..db import get_sessionmaker
            from . import platform_config as pc

            async with get_sessionmaker()() as db:
                await pc.refresh(db)  # fee rates and treasury as set by the admin
            await run_due_automation_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Scheduled-buy worker cycle failed")
        await asyncio.sleep(max(1.0, settings.automation_poll_seconds))
