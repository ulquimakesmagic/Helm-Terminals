"""Position plans, managed from each launch's Launchdesk: finite scheduled accumulation or staged exits.

A plan trades the owner's real position from one wallet they pick, with explicit limits (total budget or sell
amount, schedule, slippage, price impact, expiry, maximum executions). One active plan per launch, so buying and
selling never alternate and wallets never trade in step. It stops at the first failed order and never retries or
grows an order. Works on every chain Helm trades on. (Request bodies changed with this design: `budget` /
`sell_amount` replace `amount_sol` / `sell_percent`.)
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
from ..models import Automation, AutomationLimits, Job, Launch, LaunchWallet, Transaction, User, Wallet, utcnow
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


def min_order(network: str) -> float:
    """Smallest native-coin order a plan may place on a chain (AUTOMATION_MIN_ORDERS)."""
    for part in (get_settings().automation_min_orders or "").split(","):
        name, _, value = part.partition("=")
        if name.strip() == network:
            try:
                return float(value)
            except ValueError:
                break
    return 0.01


class AutomationBody(BaseModel):
    """A finite position plan on one launch, from one wallet you pick. Every limit is explicit."""
    side: Literal["buy", "sell"] = "buy"
    wallet_id: str | None = None
    wallet_ids: list[str] | None = Field(default=None, max_length=20)  # older clients; must hold exactly one
    budget: float | None = Field(default=None, gt=0, le=100)  # buys: total native coin the plan may spend
    sell_amount: float | None = Field(default=None, gt=0)  # sells: total tokens the plan may sell
    total_orders: int = Field(ge=2, le=12)  # maximum executions
    interval_minutes: int = Field(ge=5, le=1440)
    start_in_minutes: int = Field(default=1, ge=1, le=1440)
    slippage_pct: float = Field(ge=0.1, le=20)
    max_price_impact_pct: float = Field(ge=0.1, le=50)
    expires_in_hours: int = Field(ge=1, le=24 * 365)
    confirm: bool = False
    preflight_hash: str | None = Field(default=None, min_length=64, max_length=64)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=64)

    @model_validator(mode="after")
    def validate_plan(self):
        ids = [self.wallet_id] if self.wallet_id else list(self.wallet_ids or [])
        if len(ids) != 1:
            raise ValueError("a plan runs from exactly one wallet you select; create a separate plan for another "
                             "wallet (plans never coordinate trades across wallets)")
        self.wallet_id, self.wallet_ids = ids[0], None
        if self.side == "buy" and (self.budget is None or self.sell_amount is not None):
            raise ValueError("a buy plan needs a total budget and no sell amount")
        if self.side == "sell" and (self.sell_amount is None or self.budget is not None):
            raise ValueError("a sell plan needs a total sell amount and no budget")
        return self


def _out(a: Automation, launch: Launch | None = None, lim: AutomationLimits | None = None) -> dict:
    per_order = a.order_sol if a.side == "buy" else None
    dec = lim.token_decimals if lim and lim.token_decimals is not None else None
    return {
        "id": a.id,
        "launch_id": a.launch_id,
        "launch_name": launch.name if launch else None,
        "launch_symbol": launch.symbol if launch else None,
        "network": launch.network if launch else "solana",
        "coin": native_coin(launch.network) if launch else "SOL",
        "status": a.status,
        "side": a.side,
        "wallet_id": (lim.wallet_id if lim else (a.wallet_ids or [None])[0]),
        "wallet_count": len(a.wallet_ids or []),
        "legacy_multi_wallet": len(a.wallet_ids or []) > 1,
        "order_sol": per_order,
        "sell_percent": a.sell_percent if a.side == "sell" and not lim else None,
        "budget": (lim.budget if lim and lim.budget is not None else
                   a.order_sol * len(a.wallet_ids or []) * a.total_orders if a.side == "buy" else None),
        "sell_amount": (int(lim.sell_total_raw) / 10 ** dec if lim and lim.sell_total_raw is not None and dec is not None
                        else None),
        "sell_per_order": (int(lim.sell_order_raw) / 10 ** dec if lim and lim.sell_order_raw is not None and dec is not None
                           else None),
        "total_orders": a.total_orders,
        "interval_seconds": a.interval_seconds,
        "slippage_pct": a.slippage_pct,
        "max_price_impact_pct": lim.max_price_impact_pct if lim else None,
        "expires_at": lim.expires_at.isoformat() + "Z" if lim else None,
        "planned_trade_sol": a.order_sol * len(a.wallet_ids or []) * a.total_orders if a.side == "buy" else None,
        "remaining_trade_sol": remaining_buy(a) if a.side == "buy" else None,
        "planned_position_exit_pct": ((1 - (1 - a.sell_percent / 100) ** a.total_orders) * 100
                                      if a.side == "sell" and not lim else None),
        "runs_completed": a.runs_completed,
        "next_run_at": a.next_run_at.isoformat() + "Z" if a.next_run_at else None,
        "last_job_id": a.last_job_id,
        "error": a.error,
        "created_at": a.created_at.isoformat() + "Z" if a.created_at else None,
    }


async def _limits(db: AsyncSession, ids: list[str]) -> dict[str, AutomationLimits]:
    if not ids:
        return {}
    rows = (await db.execute(select(AutomationLimits).where(AutomationLimits.automation_id.in_(ids)))).scalars()
    return {r.automation_id: r for r in rows}


async def _owned_launch(db: AsyncSession, user: User, launch_id: str) -> Launch:
    from ..chains.networks import offered

    launch = (await db.execute(select(Launch).where(Launch.id == launch_id, Launch.user_id == user.id))).scalar_one_or_none()
    if launch is not None and not offered(launch.network):
        launch = None
    if not launch:
        raise HTTPException(404, "launch not found")
    return launch


async def _plan_wallet(db: AsyncSession, user: User, launch: Launch, wallet_id: str) -> Wallet:
    """The one wallet a plan trades from: a Helm-managed wallet on your account, on the launch's chain, attached
    to this launch."""
    wallet = await owned_wallet(db, user, wallet_id)
    if wallet.network != launch.network:
        raise HTTPException(400, "The plan's wallet must be on the launch's chain")
    if await db.get(LaunchWallet, (launch.id, wallet_id)) is None:
        raise HTTPException(400, f"Wallet {wallet.address} is not attached to this launch")
    return wallet


@router.get("/automations")
async def list_automations(launch_id: str | None = None, user: User = Depends(current_user),
                           db: AsyncSession = Depends(get_db)):
    await _expire_stale(db, user)
    q = select(Automation).where(Automation.user_id == user.id)
    if launch_id:
        q = q.where(Automation.launch_id == launch_id)
    rows = (await db.execute(q.order_by(Automation.created_at.desc()))).scalars().all()
    launches = {l.id: l for l in (await db.execute(select(Launch).where(Launch.id.in_([r.launch_id for r in rows])))).scalars().all()} if rows else {}
    lims = await _limits(db, [r.id for r in rows])
    return [_out(row, launches.get(row.launch_id), lims.get(row.id)) for row in rows]


@router.get("/launches/{launch_id}/automations")
async def launch_automations(launch_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _owned_launch(db, user, launch_id)
    return await list_automations(launch_id, user, db)


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


async def _expire_stale(db: AsyncSession, user: User) -> None:
    """Paused plans past their expiry are expired, so they stop holding the launch or the budget."""
    rows = (await db.execute(select(Automation, AutomationLimits).join(
        AutomationLimits, AutomationLimits.automation_id == Automation.id).where(
        Automation.user_id == user.id, Automation.status == "paused", AutomationLimits.expires_at <= utcnow()))).all()
    for a, _ in rows:
        a.status = "expired"
        a.error = f"Expired while paused after {a.runs_completed} of {a.total_orders} orders."
        await log_activity(db, user.id, "automation.expired", "automation", a.id, {"launch_id": a.launch_id})
    if rows:
        await db.commit()


async def _take_slot(db: AsyncSession, launch_id: str, automation_id: str) -> None:
    """Claim the launch's single plan slot in this transaction, or refuse if another active plan holds it."""
    from ..models import AutomationLaunchSlot

    slot = (await db.execute(select(AutomationLaunchSlot).where(AutomationLaunchSlot.launch_id == launch_id)
                             .with_for_update())).scalar_one_or_none()
    if slot:
        holder = await db.get(Automation, slot.automation_id)
        if holder and holder.status in ACTIVE:
            raise HTTPException(409, "This launch already has an active plan. Finish or cancel it first.")
        slot.automation_id = automation_id
    else:
        db.add(AutomationLaunchSlot(launch_id=launch_id, automation_id=automation_id))


async def _schedule(launch_id: str, body: AutomationBody, user: User, db: AsyncSession, ex: Executor):
    from ..services.allocations import locked_raw
    from ..services import platform_config as pc

    launch = await _owned_launch(db, user, launch_id)
    net = launch.network
    coin = native_coin(net)
    if launch.status != "live" or not launch.mint_address:
        raise HTTPException(409, "Launch must be live before it can have a position plan")
    s = get_settings()
    if body.expires_in_hours > s.automation_max_days * 24:
        raise HTTPException(400, f"A plan can run for at most {s.automation_max_days} days")
    wallet = await _plan_wallet(db, user, launch, body.wallet_id or "")
    plan = await fee_plan_for(db, user, launch.network)
    orders = body.total_orders
    first_at = utcnow() + timedelta(minutes=body.start_in_minutes)
    expires_at = utcnow() + timedelta(hours=body.expires_in_hours)
    last_at = first_at + timedelta(minutes=body.interval_minutes * (orders - 1))
    per_order = round(body.budget / orders, 9) if body.side == "buy" else None
    warnings: list[str] = []
    # One active plan per launch: no buy plan alongside a sell plan (a loop), and no second wallet running the
    # same token at the same time (coordinated trading).
    await _expire_stale(db, user)
    others = (await db.execute(select(Automation).where(
        Automation.user_id == user.id, Automation.launch_id == launch.id, Automation.status.in_(ACTIVE)))).scalars().all()
    if others:
        o = others[0]
        warnings.append(f"This launch already has an active {o.side} plan. Helm runs one plan per launch at a "
                        "time, so buying and selling never alternate and wallets never trade in step. Finish or "
                        "cancel it first.")
    if last_at > expires_at:
        warnings.append("The plan would expire before its last order. Lengthen the expiry, or use fewer orders or "
                        "a shorter interval.")
    helm_fee: dict = {}
    if net == "solana":
        fee_text = describe(plan, body.side, per_order)
        if body.side == "buy" and plan.active:
            helm_fee = {"estimated_helm_fee": round(body.budget * plan.bps / 10_000, 9)}
    else:
        dex = pc.bps("trade_fee_bps_evm_dex") if pc.treasury_for("evm") else 0
        fee_text = (f"none on bonding-curve trades; {dex / 100:g}% if an order is routed through a DEX" if dex
                    else "none")
        if body.side == "buy" and dex:
            helm_fee = {"estimated_helm_fee_max": round(body.budget * dex / 10_000, 9)}
    impact_check = ("checked against the DEX quote before each order that is routed through a DEX; bonding-curve "
                    "orders are bounded by the slippage limit" if net != "solana" else
                    f"{launch.launchpad} doesn't give Helm a price-impact estimate before sending, so each order is "
                    "bounded by the slippage limit instead")
    summary: dict = {
        "launch_id": launch.id,
        "launch": f"{launch.name} (${launch.symbol})",
        "network": launch.network,
        "coin": coin,
        "strategy": "scheduled accumulation" if body.side == "buy" else "staged exit",
        "wallet": {"label": wallet.label or "Wallet", "address": wallet.address},
        "maximum_executions": orders,
        "interval_minutes": body.interval_minutes,
        "first_order_in_minutes": body.start_in_minutes,
        "expires_in_hours": body.expires_in_hours,
        "slippage_pct": body.slippage_pct,
        "max_price_impact_pct": body.max_price_impact_pct,
        "price_impact_check": impact_check,
        "platform_fee": fee_text,
        **helm_fee,
        "network_fees": f"Paid in {coin}; variable and not part of the budget",
        "what_this_is": "Trades your own position from one wallet on a fixed, finite schedule. It doesn't create "
                        "volume or holders, and it stops at the first failed order.",
    }
    sell_total_raw = sell_order_raw = decimals = None
    display: dict = {}  # shown in the preview but not part of its hash: these move with gas prices and balances
    try:
        reserve = await fee_reserve(ex, net, orders * (1 if net == "solana" else 2))
        display["estimated_network_fees_max"] = round(reserve, 9)
        if body.side == "buy":
            summary["budget"] = body.budget
            summary["amount_per_order"] = per_order
            if per_order < min_order(net):
                warnings.append(f"Each order would be {per_order:g} {coin}; the smallest order a plan may place on "
                                f"{chain_name(net)} is {min_order(net):g} {coin}. Use fewer orders or a larger budget.")
            cap = budget_cap(net)
            already = await committed_buys(db, user, net)
            if body.budget + already > cap + 1e-9:
                warnings.append(f"This plan ({body.budget:g} {coin}) plus your other active plans on {chain_name(net)} "
                                f"({already:g} {coin} still to spend) would go over the {cap:g} {coin} limit.")
            fee_mult = 1 + (plan.bps / 10_000 if plan.active and net == "solana" else 0)
            need = body.budget * fee_mult + reserve
            balance = await native_balance(ex, net, wallet.address)
            if balance < need:
                warnings.append(f"{wallet.label or wallet.address[:8]} needs about {need:.4f} {coin} for the full plan "
                                f"and fees; it holds {balance:.4f} {coin}.")
        else:
            decimals = await token_decimals(ex, net, launch.mint_address)
            balance_raw = await token_balance_raw(ex, net, launch.mint_address, wallet.address)
            sell_total_raw = int(round(body.sell_amount * 10 ** decimals))
            sell_order_raw = sell_total_raw // orders
            summary["sell_amount"] = body.sell_amount
            summary["sell_per_order"] = sell_order_raw / 10 ** decimals
            display["wallet_token_balance"] = balance_raw / 10 ** decimals
            if 0 < sell_order_raw < balance_raw // 100:
                warnings.append("Each order would sell less than 1% of what this wallet holds. Use fewer orders or a "
                                "larger total; Helm doesn't run plans made of many tiny trades.")
            locked = await locked_raw(db, wallet, launch, decimals)
            if balance_raw <= 0:
                warnings.append(f"{wallet.label or wallet.address[:8]} holds no ${launch.symbol} to sell.")
            elif sell_total_raw > balance_raw - locked:
                free = max(balance_raw - locked, 0) / 10 ** decimals
                warnings.append(f"The plan would sell more than this wallet can: it holds "
                                f"{balance_raw / 10 ** decimals:,.6g} ${launch.symbol}"
                                + (f", of which {locked / 10 ** decimals:,.6g} are locked by your allocation plan"
                                   if locked else "") + f" ({free:,.6g} available).")
            if sell_order_raw <= 0:
                warnings.append("The sell amount is too small to split into that many orders.")
            if await native_balance(ex, net, wallet.address) < reserve:
                warnings.append(f"{wallet.label or wallet.address[:8]} needs about {reserve:.4f} {coin} for network fees.")
    except HTTPException:
        raise
    except Exception as e:  # an RPC outage shouldn't look like a server bug
        raise HTTPException(502, f"Couldn't read balances on {chain_name(net)}: {type(e).__name__}")
    kind = "scheduled_buy" if body.side == "buy" else "scheduled_sell"
    preview = build_preflight(kind, summary, warnings)
    preview["preflight"].update(display)
    if body.confirm and body.idempotency_key:
        existing = (await db.execute(select(Automation).where(
            Automation.user_id == user.id, Automation.idempotency_key == body.idempotency_key
        ))).scalar_one_or_none()
        if existing:
            if existing.preflight_hash != body.preflight_hash:
                raise HTTPException(409, "idempotency_key_reused: review the plan and use a new key")
            return _out(existing, launch, (await _limits(db, [existing.id])).get(existing.id))
    if not body.confirm:
        return preview
    if warnings:
        raise HTTPException(400, warnings[0])
    if body.preflight_hash != preview["preflight_hash"]:
        raise HTTPException(409, "preflight_changed: review the updated plan and confirm again")
    if not body.idempotency_key:
        raise HTTPException(400, "idempotency_key is required when confirm=true")

    automation = Automation(
        user_id=user.id, launch_id=launch.id, wallet_ids=[wallet.id], side=body.side,
        order_sol=per_order or 0, sell_percent=0, total_orders=orders,
        interval_seconds=body.interval_minutes * 60, slippage_pct=body.slippage_pct, next_run_at=first_at,
        idempotency_key=body.idempotency_key, preflight_hash=preview["preflight_hash"],
    )
    db.add(automation)
    from ..models import AutomationTerms

    await db.flush()
    await _take_slot(db, launch.id, automation.id)
    db.add(AutomationLimits(automation_id=automation.id, wallet_id=wallet.id,
                            budget=body.budget if body.side == "buy" else None,
                            sell_total_raw=sell_total_raw, sell_order_raw=sell_order_raw, token_decimals=decimals,
                            max_price_impact_pct=body.max_price_impact_pct, expires_at=expires_at))
    # The trading-fee rates this plan is confirmed at (see AutomationTerms): a later increase stops the plan.
    db.add(AutomationTerms(automation_id=automation.id, trade_fee_bps_solana=pc.bps("trade_fee_bps_solana"),
                           trade_fee_bps_evm_dex=pc.bps("trade_fee_bps_evm_dex")))
    await log_activity(db, user.id, "automation.scheduled", "automation", automation.id, {
        "launch_id": launch.id, "side": body.side, "orders": orders, "wallet": wallet.address,
        "budget": body.budget, "sell_amount": body.sell_amount, "slippage_pct": body.slippage_pct,
        "max_price_impact_pct": body.max_price_impact_pct, "expires_at": expires_at.isoformat() + "Z",
        "network": net, "coin": coin,
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
            return _out(existing, launch, (await _limits(db, [existing.id])).get(existing.id))
        raise HTTPException(409, "Another plan was just created for this launch. Refresh and review it.")
    await db.refresh(automation)
    return _out(automation, launch, (await _limits(db, [automation.id])).get(automation.id))


async def _owned_automation(db: AsyncSession, user: User, automation_id: str) -> Automation:
    row = (await db.execute(select(Automation).where(
        Automation.id == automation_id, Automation.user_id == user.id
    ).with_for_update())).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "automation not found")
    return row


async def _respond(db: AsyncSession, row: Automation) -> dict:
    return _out(row, await db.get(Launch, row.launch_id), (await _limits(db, [row.id])).get(row.id))


@router.post("/automations/{automation_id}/pause")
async def pause_automation(automation_id: str, user: User = Depends(web_user),
                           db: AsyncSession = Depends(get_db)):
    row = await _owned_automation(db, user, automation_id)
    if row.status != "scheduled":
        raise HTTPException(409, f"Cannot pause a plan that is {row.status}")
    row.status = "paused"
    await log_activity(db, user.id, "automation.paused", "automation", row.id, {"launch_id": row.launch_id})
    await db.commit()
    return await _respond(db, row)


@router.post("/automations/{automation_id}/resume")
async def resume_automation(automation_id: str, user: User = Depends(web_user),
                            db: AsyncSession = Depends(get_db)):
    row = await _owned_automation(db, user, automation_id)
    if row.status != "paused":
        raise HTTPException(409, f"Cannot resume a plan that is {row.status}")
    if len(row.wallet_ids or []) > 1:
        raise HTTPException(409, "This older plan trades from several wallets at once, which Helm no longer does. "
                                 "Cancel it and create a plan for one wallet.")
    lim = (await _limits(db, [row.id])).get(row.id)
    if lim and lim.expires_at <= utcnow():
        raise HTTPException(409, "This plan has expired. Create a new plan.")
    row.status = "scheduled"
    row.next_run_at = max(row.next_run_at or utcnow(), utcnow())  # resuming never skips the interval
    row.error = None
    await log_activity(db, user.id, "automation.resumed", "automation", row.id, {"launch_id": row.launch_id})
    await db.commit()
    return await _respond(db, row)


@router.post("/automations/{automation_id}/cancel")
async def cancel_automation(automation_id: str, user: User = Depends(web_user),
                            db: AsyncSession = Depends(get_db)):
    row = await _owned_automation(db, user, automation_id)
    if row.status not in {"scheduled", "paused"}:
        raise HTTPException(409, f"Cannot cancel a plan that is {row.status}")
    row.status = "cancelled"
    await log_activity(db, user.id, "automation.cancelled", "automation", row.id, {
        "launch_id": row.launch_id, "orders_done": row.runs_completed, "of": row.total_orders})
    await db.commit()
    return await _respond(db, row)


class PlanEdit(BaseModel):
    """Edits can only make a plan more conservative: fewer orders, tighter slippage or price impact, a longer
    interval or an earlier expiry. Anything else is a new plan with its own preview."""
    total_orders: int | None = Field(default=None, ge=1, le=12)
    slippage_pct: float | None = Field(default=None, ge=0.1, le=20)
    max_price_impact_pct: float | None = Field(default=None, ge=0.1, le=50)
    interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    expires_in_hours: int | None = Field(default=None, ge=1, le=24 * 365)


@router.patch("/automations/{automation_id}")
async def edit_automation(automation_id: str, body: PlanEdit, user: User = Depends(web_user),
                          db: AsyncSession = Depends(get_db)):
    row = await _owned_automation(db, user, automation_id)
    if row.status not in {"scheduled", "paused"}:
        raise HTTPException(409, f"Cannot edit a plan that is {row.status}")
    lim = (await _limits(db, [row.id])).get(row.id)
    changes: dict = {}
    if body.total_orders is not None:
        if body.total_orders > row.total_orders:
            raise HTTPException(400, "A plan can only get fewer orders; create a new plan for more")
        if body.total_orders <= row.runs_completed:
            raise HTTPException(400, f"{row.runs_completed} orders already ran; to stop now, cancel the plan")
        changes["total_orders"] = [row.total_orders, body.total_orders]
        row.total_orders = body.total_orders
        if lim and lim.budget is not None:
            new_budget = round(row.order_sol * body.total_orders, 9)  # order size never changes
            changes["budget"] = [lim.budget, new_budget]
            lim.budget = new_budget
        if lim and lim.sell_order_raw is not None:
            new_total = int(lim.sell_order_raw) * body.total_orders  # per-order size stays; the total shrinks
            changes["sell_amount"] = [str(lim.sell_total_raw), str(new_total)]
            lim.sell_total_raw = new_total
    if body.slippage_pct is not None:
        if body.slippage_pct > row.slippage_pct:
            raise HTTPException(400, "Slippage can only be lowered")
        changes["slippage_pct"] = [row.slippage_pct, body.slippage_pct]
        row.slippage_pct = body.slippage_pct
    if body.interval_minutes is not None:
        if body.interval_minutes * 60 < row.interval_seconds:
            raise HTTPException(400, "The interval can only get longer")
        changes["interval_minutes"] = [row.interval_seconds // 60, body.interval_minutes]
        row.interval_seconds = body.interval_minutes * 60
    if body.max_price_impact_pct is not None:
        if not lim or body.max_price_impact_pct > lim.max_price_impact_pct:
            raise HTTPException(400, "The price-impact limit can only be lowered")
        changes["max_price_impact_pct"] = [lim.max_price_impact_pct, body.max_price_impact_pct]
        lim.max_price_impact_pct = body.max_price_impact_pct
    if body.expires_in_hours is not None:
        new_exp = utcnow() + timedelta(hours=body.expires_in_hours)
        if not lim or new_exp > lim.expires_at:
            raise HTTPException(400, "The expiry can only move earlier")
        changes["expires_at"] = [lim.expires_at.isoformat() + "Z", new_exp.isoformat() + "Z"]
        lim.expires_at = new_exp
    if not changes:
        raise HTTPException(400, "nothing to change")
    await log_activity(db, user.id, "automation.edited", "automation", row.id, {"launch_id": row.launch_id,
                                                                                 "changes": changes})
    await db.commit()
    return await _respond(db, row)


@router.get("/automations/{automation_id}")
async def automation_detail(automation_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """The plan, each order it ran (with its transactions and errors), and its change history."""
    from ..models import Activity

    row = (await db.execute(select(Automation).where(Automation.id == automation_id, Automation.user_id == user.id))
           ).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "automation not found")
    jobs = [j for j in (await db.execute(select(Job).where(
        Job.user_id == user.id, Job.launch_id == row.launch_id, Job.kind.in_(("scheduled_buy", "scheduled_sell")))
        .order_by(Job.created_at))).scalars() if (j.params or {}).get("automation_id") == row.id]
    txs = (await db.execute(select(Transaction).where(Transaction.job_id.in_([j.id for j in jobs]))
                            .order_by(Transaction.created_at))).scalars().all() if jobs else []
    by_job: dict[str, list] = defaultdict(list)
    for t in txs:
        by_job[t.job_id].append({"kind": t.kind, "status": t.status, "signature": t.signature, "error": t.error,
                                 "at": t.created_at.isoformat() + "Z"})
    history = (await db.execute(select(Activity).where(
        Activity.user_id == user.id, Activity.ref_type == "automation", Activity.ref_id == row.id)
        .order_by(Activity.created_at))).scalars().all()
    return {**await _respond(db, row),
            "executions": [{"job_id": j.id, "order_number": (j.params or {}).get("order_number"), "status": j.status,
                            "error": (j.params or {}).get("error"), "at": j.created_at.isoformat() + "Z",
                            "transactions": by_job.get(j.id, [])} for j in jobs],
            "history": [{"event": h.kind.removeprefix("automation."), "at": h.created_at.isoformat() + "Z",
                         "details": h.payload} for h in history]}
