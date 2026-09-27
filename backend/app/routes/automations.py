"""Bounded launch automations: finite scheduled buys and staged sells with explicit preflights.

Works on every chain Helm trades on. `amount_sol` is the per-order amount in the launch chain's native coin
(SOL, ETH or BNB); the field keeps its original name so existing clients keep working.
"""

import asyncio
from collections import defaultdict
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..deps import current_user, get_executor, log_activity, owned_wallet, web_user
from ..models import Automation, Launch, LaunchWallet, User, Wallet, utcnow
from ..services.chain_balances import (chain_name, fee_reserve, native_balance, native_coin, token_balance_raw,
                                       token_decimals)
from ..services.executor import Executor
from ..services.fees import describe, fee_plan_for
from ..services.preflight import build_preflight

router = APIRouter()
ACTIVE = ("scheduled", "executing", "paused")
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def budget_cap(network: str) -> float:
    """Most native coin one account may have committed to scheduled buys on a chain at once."""
    s = get_settings()
    if network == "solana":
        return s.automation_max_budget_sol
    for part in (s.automation_max_budgets or "").split(","):
        name, _, value = part.partition("=")
        if name.strip() == network:
            try:
                return float(value)
            except ValueError:
                break
    return 1.0


def remaining_buy(a: Automation) -> float:
    return a.order_sol * len(a.wallet_ids or []) * max(a.total_orders - a.runs_completed, 0) if a.side == "buy" else 0.0


async def committed_buys(db: AsyncSession, user: User, network: str) -> float:
    """Native coin still to be spent by this account's active scheduled buys on `network`."""
    rows = (await db.execute(select(Automation).join(Launch, Launch.id == Automation.launch_id).where(
        Automation.user_id == user.id, Automation.side == "buy", Automation.status.in_(ACTIVE),
        Launch.network == network))).scalars().all()
    return sum(remaining_buy(a) for a in rows)


class AutomationBody(BaseModel):
    side: Literal["buy", "sell"] = "buy"
    wallet_ids: list[str] = Field(min_length=1, max_length=20)
    amount_sol: float | None = Field(default=None, gt=0, le=10)
    sell_percent: float | None = Field(default=None, gt=0, le=100)
    total_orders: int = Field(ge=2, le=12)
    interval_minutes: int = Field(ge=5, le=1440)
    start_in_minutes: int = Field(default=1, ge=1, le=1440)
    slippage_pct: float = Field(default=5.0, ge=0, le=20)
    confirm: bool = False
    preflight_hash: str | None = Field(default=None, min_length=64, max_length=64)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=64)

    @model_validator(mode="after")
    def validate_order_size(self):
        if self.side == "buy" and (self.amount_sol is None or self.sell_percent is not None):
            raise ValueError("buy schedules need amount_sol and cannot include sell_percent")
        if self.side == "sell" and (self.sell_percent is None or self.amount_sol is not None):
            raise ValueError("sell schedules need sell_percent and cannot include amount_sol")
        return self


def _out(a: Automation, launch: Launch | None = None) -> dict:
    return {
        "id": a.id,
        "launch_id": a.launch_id,
        "launch_name": launch.name if launch else None,
        "launch_symbol": launch.symbol if launch else None,
        "network": launch.network if launch else "solana",
        "coin": native_coin(launch.network) if launch else "SOL",
        "status": a.status,
        "side": a.side,
        "wallet_count": len(a.wallet_ids or []),
        "order_sol": a.order_sol,
        "sell_percent": a.sell_percent,
        "total_orders": a.total_orders,
        "interval_seconds": a.interval_seconds,
        "slippage_pct": a.slippage_pct,
        "planned_trade_sol": a.order_sol * len(a.wallet_ids or []) * a.total_orders if a.side == "buy" else None,
        "remaining_trade_sol": remaining_buy(a) if a.side == "buy" else None,
        "planned_position_exit_pct": (1 - (1 - a.sell_percent / 100) ** a.total_orders) * 100 if a.side == "sell" else None,
        "runs_completed": a.runs_completed,
        "next_run_at": a.next_run_at.isoformat() + "Z" if a.next_run_at else None,
        "last_job_id": a.last_job_id,
        "error": a.error,
        "created_at": a.created_at.isoformat() + "Z" if a.created_at else None,
    }


async def _owned_launch(db: AsyncSession, user: User, launch_id: str) -> Launch:
    launch = (await db.execute(select(Launch).where(Launch.id == launch_id, Launch.user_id == user.id))).scalar_one_or_none()
    if not launch:
        raise HTTPException(404, "launch not found")
    return launch


async def _wallets_for_schedule(db: AsyncSession, user: User, launch: Launch, ids: list[str]) -> list[Wallet]:
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "Select each wallet only once")
    wallets = []
    for wallet_id in ids:
        wallet = await owned_wallet(db, user, wallet_id)
        if wallet.network != launch.network:
            raise HTTPException(400, "All automation wallets must use the launch network")
        if await db.get(LaunchWallet, (launch.id, wallet_id)) is None:
            raise HTTPException(400, f"Wallet {wallet.address} is not attached to this launch")
        wallets.append(wallet)
    return wallets


@router.get("/automations")
async def list_automations(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Automation).where(Automation.user_id == user.id).order_by(Automation.created_at.desc()))).scalars().all()
    launches = {l.id: l for l in (await db.execute(select(Launch).where(Launch.id.in_([r.launch_id for r in rows])))).scalars().all()} if rows else {}
    return [_out(row, launches.get(row.launch_id)) for row in rows]


@router.post("/launches/{launch_id}/automations")
@router.post("/launches/{launch_id}/automations/dca", include_in_schema=False)
async def schedule_automation(launch_id: str, body: AutomationBody, user: User = Depends(web_user),
                              db: AsyncSession = Depends(get_db), ex: Executor = Depends(get_executor)):
    if not get_settings().automation_worker_enabled:
        raise HTTPException(503, "Scheduled trades are temporarily disabled on this server")
    if not body.confirm:
        return await _schedule(launch_id, body, user, db, ex)
    async with _locks[user.id]:  # so two confirms can't both slip under the budget cap
        return await _schedule(launch_id, body, user, db, ex)


async def _schedule(launch_id: str, body: AutomationBody, user: User, db: AsyncSession, ex: Executor):
    launch = await _owned_launch(db, user, launch_id)
    net = launch.network
    coin = native_coin(net)
    if launch.status != "live" or not launch.mint_address:
        raise HTTPException(409, "Launch must be live before it can have a scheduled trade plan")
    wallets = await _wallets_for_schedule(db, user, launch, body.wallet_ids)
    plan = await fee_plan_for(db, user, launch.network)
    planned_trade_sol = (body.amount_sol or 0) * len(wallets) * body.total_orders if body.side == "buy" else None
    max_exit_pct = (1 - (1 - (body.sell_percent or 0) / 100) ** body.total_orders) * 100 if body.side == "sell" else None
    summary: dict = {
        "launch_id": launch.id,
        "launch": f"{launch.name} (${launch.symbol})",
        "network": launch.network,
        "coin": coin,
        "strategy": "finite scheduled buys" if body.side == "buy" else "finite staged sells",
        "wallets": [{"label": w.label or "Wallet", "address": w.address} for w in wallets],
        "number_of_orders": body.total_orders,
        "interval_minutes": body.interval_minutes,
        "first_order_in_minutes": body.start_in_minutes,
        "slippage_pct": body.slippage_pct,
        "platform_fee": describe(plan, body.side, body.amount_sol if body.side == "buy" else None) if net == "solana"
        else (f"none on bonding-curve trades; {ex.dex.fee_note} if an order is routed through a DEX"
              if ex.dex.fee_bps else "none"),
        "network_fees": f"Paid in {coin}; variable and not included in the planned trade amount",
    }
    if body.side == "buy":
        summary["amount_sol_per_wallet_per_order"] = body.amount_sol
        summary["amount_unit"] = f"{coin} per wallet per order"
        summary["planned_trade_sol"] = planned_trade_sol
    else:
        summary["side"] = body.side
        summary["sell_percent_of_remaining_per_order"] = body.sell_percent
        summary["maximum_position_exit_percent"] = max_exit_pct
    kind = "scheduled_buy" if body.side == "buy" else "scheduled_sell"
    preview = build_preflight(kind, summary)

    # A retried confirmation must resolve to its original plan even if chain balances have changed since
    # scheduling. Also prevent accidental reuse of one key for different settings.
    if body.confirm and body.idempotency_key:
        existing = (await db.execute(select(Automation).where(
            Automation.user_id == user.id, Automation.idempotency_key == body.idempotency_key
        ))).scalar_one_or_none()
        if existing:
            if existing.preflight_hash != preview["preflight_hash"] or body.preflight_hash != existing.preflight_hash:
                raise HTTPException(409, "idempotency_key_reused: review the plan and use a new key")
            return _out(existing, launch)

    warnings: list[str] = []
    token_balances: dict[str, float] = {}
    try:
        if body.side == "buy":
            cap = budget_cap(net)
            already = await committed_buys(db, user, net)
            if planned_trade_sol + already > cap + 1e-9:
                if already:
                    warnings.append(
                        f"This plan ({planned_trade_sol:g} {coin}) plus your other active scheduled buys on "
                        f"{chain_name(net)} ({already:g} {coin} still to spend) would go over the {cap:g} {coin} "
                        "budget limit. Cancel or finish a plan, or make this one smaller.")
                else:
                    warnings.append(f"This plan ({planned_trade_sol:g} {coin}) exceeds the {cap:g} {coin} "
                                    f"scheduled-buy budget limit on {chain_name(net)}.")
            fee_mult = 1 + (plan.bps / 10_000 if plan.active and net == "solana" else 0)
            per_wallet_required = (body.amount_sol or 0) * body.total_orders * fee_mult \
                + await fee_reserve(ex, net, body.total_orders)
            for wallet in wallets:
                balance = await native_balance(ex, net, wallet.address)
                if balance < per_wallet_required:
                    warnings.append(f"{wallet.label or wallet.address[:8]} needs about {per_wallet_required:.4f} {coin} "
                                    f"for the full plan; current balance is {balance:.4f} {coin}.")
        else:
            decimals = await token_decimals(ex, net, launch.mint_address)
            # EVM sells can need an approve before the sell, so reserve gas for two transactions per order.
            reserve = await fee_reserve(ex, net, body.total_orders * (1 if net == "solana" else 2))
            for wallet in wallets:
                balance = await token_balance_raw(ex, net, launch.mint_address, wallet.address)
                token_balances[wallet.id] = balance / (10 ** decimals)
                if balance <= 0:
                    warnings.append(f"{wallet.label or wallet.address[:8]} has no tokens to sell.")
                elif int(balance * (body.sell_percent or 0) / 100) <= 0:
                    warnings.append(f"{wallet.label or wallet.address[:8]} has too few tokens for the selected sell size.")
                if await native_balance(ex, net, wallet.address) < reserve:
                    warnings.append(f"{wallet.label or wallet.address[:8]} needs about {reserve:.4f} {coin} "
                                    "reserved for network fees.")
    except HTTPException:
        raise
    except Exception as e:  # an RPC outage shouldn't look like a server bug
        raise HTTPException(502, f"Couldn't read balances on {chain_name(net)}: {type(e).__name__}")
    preview = build_preflight(kind, summary, warnings)
    if body.side == "sell":
        preview["preflight"]["wallets"] = [
            {"label": w.label or "Wallet", "address": w.address, "token_balance": token_balances[w.id]}
            for w in wallets
        ]
    if not body.confirm:
        return preview
    if warnings:
        raise HTTPException(400, warnings[0])
    if body.preflight_hash != preview["preflight_hash"]:
        raise HTTPException(409, "preflight_changed: review the updated plan and confirm again")
    if not body.idempotency_key:
        raise HTTPException(400, "idempotency_key is required when confirm=true")
    existing = (await db.execute(select(Automation).where(
        Automation.user_id == user.id, Automation.idempotency_key == body.idempotency_key
    ))).scalar_one_or_none()
    if existing:
        return _out(existing, launch)

    automation = Automation(
        user_id=user.id,
        launch_id=launch.id,
        wallet_ids=body.wallet_ids,
        side=body.side,
        order_sol=body.amount_sol or 0,
        sell_percent=body.sell_percent or 0,
        total_orders=body.total_orders,
        interval_seconds=body.interval_minutes * 60,
        slippage_pct=body.slippage_pct,
        next_run_at=utcnow() + timedelta(minutes=body.start_in_minutes),
        idempotency_key=body.idempotency_key,
        preflight_hash=preview["preflight_hash"],
    )
    db.add(automation)
    await log_activity(db, user.id, "automation.scheduled", "automation", automation.id, {
        "launch_id": launch.id, "side": body.side, "orders": body.total_orders, "wallet_count": len(wallets),
        "planned_trade_sol": planned_trade_sol, "sell_percent": body.sell_percent, "network": net, "coin": coin,
    })
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (await db.execute(select(Automation).where(
            Automation.user_id == user.id, Automation.idempotency_key == body.idempotency_key
        ))).scalar_one_or_none()
        if existing:
            if existing.preflight_hash != preview["preflight_hash"]:
                raise HTTPException(409, "idempotency_key_reused: review the plan and use a new key")
            return _out(existing, launch)
        raise
    await db.refresh(automation)
    return _out(automation, launch)


async def _owned_automation(db: AsyncSession, user: User, automation_id: str) -> Automation:
    row = (await db.execute(select(Automation).where(
        Automation.id == automation_id, Automation.user_id == user.id
    ).with_for_update())).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "automation not found")
    return row


@router.post("/automations/{automation_id}/pause")
async def pause_automation(automation_id: str, user: User = Depends(web_user),
                           db: AsyncSession = Depends(get_db)):
    row = await _owned_automation(db, user, automation_id)
    if row.status != "scheduled":
        raise HTTPException(409, f"Cannot pause an automation in {row.status} state")
    row.status = "paused"
    await db.commit()
    return _out(row, await db.get(Launch, row.launch_id))


@router.post("/automations/{automation_id}/resume")
async def resume_automation(automation_id: str, user: User = Depends(web_user),
                            db: AsyncSession = Depends(get_db)):
    row = await _owned_automation(db, user, automation_id)
    if row.status != "paused":
        raise HTTPException(409, f"Cannot resume an automation in {row.status} state")
    row.status = "scheduled"
    row.next_run_at = utcnow()
    row.error = None
    await db.commit()
    return _out(row, await db.get(Launch, row.launch_id))


@router.post("/automations/{automation_id}/cancel")
async def cancel_automation(automation_id: str, user: User = Depends(web_user),
                            db: AsyncSession = Depends(get_db)):
    row = await _owned_automation(db, user, automation_id)
    if row.status not in {"scheduled", "paused"}:
        raise HTTPException(409, f"Cannot cancel an automation in {row.status} state")
    row.status = "cancelled"
    await db.commit()
    return _out(row, await db.get(Launch, row.launch_id))
