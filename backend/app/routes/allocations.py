"""Allocation & vesting plan for a launch (see services/allocations.py)."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import current_user, get_executor, log_activity, valid_address_for, web_user
from ..models import Launch, LaunchAllocation, User, Wallet, utcnow
from ..services.allocations import (CATEGORIES, DRAFT_SUPPLY, INSIDER_WARN_PCT, NO_RECIPIENT, SINGLE_HOLDER_WARN_PCT,
                                    UNLOCKS, dec, unlocked_fraction)
from ..services.executor import Executor

router = APIRouter()


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() + "Z" if d else None


def _naive(d: datetime | None) -> datetime | None:
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d and d.tzinfo else d


async def _launch(db: AsyncSession, user: User, launch_id: str) -> Launch:
    from ..chains.networks import offered

    l = (await db.execute(select(Launch).where(Launch.id == launch_id, Launch.user_id == user.id))).scalar_one_or_none()
    if l is not None and not offered(l.network):
        l = None
    if not l:
        raise HTTPException(404, "launch not found")
    return l


async def supply_of(l: Launch, ex: Executor) -> tuple[Decimal | None, int | None, str]:
    """(whole-token supply, decimals, where it came from). On-chain once deployed; the launchpad's fixed supply
    for drafts where there is one."""
    if l.mint_address:
        try:
            if l.network == "solana":
                raw, d = await asyncio.wait_for(ex.rpc.get_token_supply(l.mint_address), 10)
            else:
                meta = await asyncio.wait_for(ex.evm(l.network).rpc.erc20_meta(l.mint_address), 10)
                raw, d = meta["total_supply"], meta["decimals"]
            return Decimal(int(raw)) / (Decimal(10) ** d), d, "on-chain"
        except Exception:
            return None, None, "unavailable: the chain couldn't be read right now"
    if l.launchpad in DRAFT_SUPPLY:
        return Decimal(DRAFT_SUPPLY[l.launchpad]), None, f"{l.launchpad}'s fixed supply"
    return None, None, "set by the launchpad at deploy; the plan reconciles once the token exists"


async def _own_wallets(db: AsyncSession, user: User, network: str) -> dict[str, Wallet]:
    rows = (await db.execute(select(Wallet).where(Wallet.user_id == user.id, Wallet.network == network,
                                                  Wallet.kind == "managed"))).scalars()
    return {w.address.lower(): w for w in rows}


@router.get("/launches/{launch_id}/allocations")
async def get_allocations(launch_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                          ex: Executor = Depends(get_executor)):
    l = await _launch(db, user, launch_id)
    return await _view(db, user, l, ex)


async def _view(db: AsyncSession, user: User, l: Launch, ex: Executor) -> dict:
    from ..services.chain_balances import token_balance_raw

    rows = (await db.execute(select(LaunchAllocation).where(LaunchAllocation.launch_id == l.id)
                             .order_by(LaunchAllocation.created_at))).scalars().all()
    supply, decimals, source = await supply_of(l, ex)
    own = await _own_wallets(db, user, l.network)
    dev = await db.get(Wallet, l.dev_wallet_id) if l.dev_wallet_id else None
    allocated = sum((dec(a.amount) for a in rows), Decimal(0))
    now = utcnow()
    out_rows, warnings = [], []
    insider = Decimal(0)
    for a in rows:
        amt = dec(a.amount)
        pct = (amt / supply * 100) if supply else None
        tags = []
        w = own.get(a.recipient.lower()) if a.recipient else None
        if w:
            tags.append("your Helm wallet")
        if dev and a.recipient and a.recipient.lower() == dev.address.lower():
            tags.append("dev wallet")
        if a.category not in NO_RECIPIENT and (w or a.category in ("creator", "team")):
            insider += amt
        if pct is not None and a.category not in NO_RECIPIENT and pct > SINGLE_HOLDER_WARN_PCT:
            warnings.append(f"{a.label or CATEGORIES.get(a.category, a.category)} holds {pct:.2f}% of the supply "
                            f"in one wallet.")
        balance = None
        if l.mint_address and a.recipient and decimals is not None and len(rows) <= 40:
            try:
                raw = await asyncio.wait_for(token_balance_raw(ex, l.network, l.mint_address, a.recipient), 8)
                balance = str(Decimal(raw) / (Decimal(10) ** decimals))
            except Exception:
                balance = None
        out_rows.append({
            "id": a.id, "category": a.category, "category_label": CATEGORIES.get(a.category, a.category),
            "label": a.label, "recipient": a.recipient or None, "amount": a.amount,
            "pct": float(round(pct, 4)) if pct is not None else None,
            "unlock": a.unlock, "unlock_start": _iso(a.unlock_start), "unlock_end": _iso(a.unlock_end),
            "unlocked_now_pct": float(round(unlocked_fraction(a, now) * 100, 2)), "conditions": a.conditions,
            "lock": a.lock, "lock_enforced": bool(a.lock and w), "source": a.source, "reference": a.reference,
            "tags": tags, "balance_now": balance, "updated_at": _iso(a.updated_at),
        })
    if supply and insider / supply * 100 > INSIDER_WARN_PCT:
        warnings.append(f"Creator, team and your own Helm wallets hold {insider / supply * 100:.1f}% of the supply "
                        "together. Buyers will see this concentration.")
    unallocated = (supply - allocated) if supply is not None else None
    if unallocated is not None and unallocated < 0:
        warnings.append(f"Allocations exceed the supply by {-unallocated:,} tokens.")
    return {
        "launch_id": l.id, "symbol": l.symbol, "network": l.network, "launchpad": l.launchpad,
        "supply": str(supply) if supply is not None else None, "supply_source": source, "decimals": decimals,
        "allocated": str(allocated), "unallocated": str(unallocated) if unallocated is not None else None,
        "reconciled": unallocated == 0 if unallocated is not None else False,
        "rows": out_rows, "warnings": warnings, "categories": CATEGORIES,
        "note": "Schedules are what you declare to buyers. Helm enforces a lock only on your own Helm wallets "
                "(it refuses sells and transfers of the locked part); other schedules are not enforced on-chain.",
    }


class AllocationIn(BaseModel):
    id: str | None = None
    category: str
    label: str = Field(default="", max_length=80)
    recipient: str = Field(default="", max_length=64)
    amount: str = Field(max_length=80)
    unlock: str = "immediate"
    unlock_start: datetime | None = None
    unlock_end: datetime | None = None
    conditions: str = Field(default="", max_length=280)
    lock: bool = False


class AllocationPlan(BaseModel):
    rows: list[AllocationIn] = Field(max_length=100)


@router.put("/launches/{launch_id}/allocations")
async def put_allocations(launch_id: str, body: AllocationPlan, user: User = Depends(web_user),
                          db: AsyncSession = Depends(get_db), ex: Executor = Depends(get_executor)):
    """Replace the plan. Lines recorded from a dispersal keep their recipient and amount (they happened); locks
    can only be extended."""
    l = await _launch(db, user, launch_id)
    existing = {a.id: a for a in (await db.execute(select(LaunchAllocation).where(
        LaunchAllocation.launch_id == l.id))).scalars()}
    supply, _, _ = await supply_of(l, ex)
    own = await _own_wallets(db, user, l.network)
    ids = [r.id for r in body.rows if r.id]
    if len(ids) != len(set(ids)):
        raise HTTPException(400, "The same line is listed twice")
    kept = [a for a in existing.values() if a.source == "dispersal" and a.id not in set(ids)]
    seen, errors, total, publics = {a.recipient.lower() for a in kept if a.recipient}, [], Decimal(0), 0
    parsed = []
    for i, r in enumerate(body.rows, 1):
        where = f"Line {i}"
        prev = existing.get(r.id) if r.id else None
        if r.id and not prev:
            raise HTTPException(400, f"{where}: unknown allocation")
        if r.category not in CATEGORIES:
            raise HTTPException(400, f"{where}: unknown category")
        recipient, amount_s = r.recipient.strip(), r.amount.strip()
        if prev and prev.source == "dispersal":
            recipient, amount_s = prev.recipient, prev.amount  # a dispersal can't be rewritten
        try:
            amount = dec(amount_s)
        except ValueError as e:
            raise HTTPException(400, f"{where}: {e}")
        if amount <= 0:
            raise HTTPException(400, f"{where}: the amount must be above zero")
        if amount.as_tuple().exponent < -18:
            raise HTTPException(400, f"{where}: too many decimal places")
        if r.category in NO_RECIPIENT:
            if recipient:
                raise HTTPException(400, f"{where}: {CATEGORIES[r.category]} is held by the launchpad, not a wallet; "
                                         "leave the recipient empty")
            publics += r.category == "public"
        else:
            if not recipient:
                raise HTTPException(400, f"{where}: a recipient address is required")
            if not valid_address_for(l.network, recipient):
                raise HTTPException(400, f"{where}: not a valid {l.network} address")
            key = recipient.lower()
            if key in seen:
                raise HTTPException(400, f"{where}: {recipient} is listed twice; combine them into one line")
            seen.add(key)
        if r.unlock not in UNLOCKS:
            raise HTTPException(400, f"{where}: unlock must be immediate, date or linear")
        start, end = _naive(r.unlock_start), _naive(r.unlock_end)
        if r.unlock == "date" and not start:
            raise HTTPException(400, f"{where}: pick the unlock date")
        if r.unlock == "linear" and (not start or not end or end <= start):
            raise HTTPException(400, f"{where}: linear vesting needs a start (cliff) before its end")
        if r.lock and not (prev and prev.lock):  # newly locked: Helm must be able to enforce it
            if not recipient or recipient.lower() not in own:
                raise HTTPException(400, f"{where}: Helm can only enforce a lock on one of your own Helm wallets on "
                                         f"this chain; describe other schedules in the conditions instead")
        if r.lock and r.unlock == "immediate":
            raise HTTPException(400, f"{where}: a lock needs an unlock date or a vesting schedule")
        if prev and prev.lock:
            probe = LaunchAllocation(unlock=r.unlock, unlock_start=start, unlock_end=end)
            later = [t for t in (utcnow(), prev.unlock_start, prev.unlock_end, start, end) if t]
            later += [t - timedelta(seconds=1) for t in later]
            if not r.lock or dec(amount_s) < dec(prev.amount) or recipient.lower() != prev.recipient.lower() \
                    or any(unlocked_fraction(probe, t) > unlocked_fraction(prev, t) for t in later):
                raise HTTPException(400, f"{where}: a lock can be extended but not shortened or removed")
        total += amount
        parsed.append((r, prev, recipient, amount_s, start, end))
    if publics > 1:
        raise HTTPException(400, "Only one public sale / bonding curve line")
    missing_dispersals = [a for a in existing.values() if a.source == "dispersal"
                          and a.id not in {r.id for r in body.rows}]
    missing_locks = [a for a in existing.values() if a.lock and a.id not in {r.id for r in body.rows}]
    if missing_locks:
        raise HTTPException(400, "A locked line can't be removed; locks can only be extended")
    total += sum((dec(a.amount) for a in missing_dispersals), Decimal(0))
    if supply is not None and total > supply:
        raise HTTPException(400, f"The lines add up to {total:,} tokens, more than the {supply:,} supply")
    before = {a.id: (a.category, a.recipient, a.amount, a.unlock, a.lock) for a in existing.values()}
    keep = {a.id for a in missing_dispersals}
    for r, prev, recipient, amount_s, start, end in parsed:
        row = prev or LaunchAllocation(launch_id=l.id, user_id=user.id, source="manual")
        row.category, row.label, row.recipient, row.amount = r.category, r.label.strip(), recipient, amount_s
        row.unlock, row.unlock_start, row.unlock_end = r.unlock, start, end
        row.conditions, row.lock = r.conditions.strip(), r.lock
        if not prev:
            db.add(row)
        await db.flush()
        keep.add(row.id)
    for a in existing.values():
        if a.id not in keep:
            await db.delete(a)
    await db.flush()
    after = {a.id: (a.category, a.recipient, a.amount, a.unlock, a.lock) for a in (await db.execute(
        select(LaunchAllocation).where(LaunchAllocation.launch_id == l.id))).scalars()}
    await log_activity(db, user.id, "allocation.updated", "launch", l.id, {
        "added": len(set(after) - set(before)), "removed": len(set(before) - set(after)),
        "changed": sum(1 for k in set(after) & set(before) if after[k] != before[k]),
        "total": str(total), "supply": str(supply) if supply is not None else None})
    await db.commit()
    return await _view(db, user, l, ex)


async def record_dispersal(db: AsyncSession, launch: Launch, user_id: str, source_address: str,
                           legs: list[tuple[str, Decimal]], job_id: str) -> None:
    """Record confirmed dispersal legs as creator-controlled allocation lines. The tokens came from the source
    wallet: its own line shrinks if it has one, otherwise the public line does (a dev buy comes off the curve)."""
    rows = (await db.execute(select(LaunchAllocation).where(LaunchAllocation.launch_id == launch.id))).scalars().all()
    src = next((a for a in rows if a.recipient and a.recipient.lower() == source_address.lower()), None)
    public = next((a for a in rows if a.category == "public"), None)
    by_addr = {a.recipient.lower(): a for a in rows if a.recipient}
    for address, amount in legs:
        # Take the amount off the source wallet's own line first (unless it is locked: a lock never shrinks), then
        # off the public line. If neither covers it, the plan shows as over-allocated until the owner fixes it.
        left = amount
        for pool in (src, public):
            if pool is None or pool.lock or left <= 0:
                continue
            take = min(dec(pool.amount), left)
            pool.amount = str(dec(pool.amount) - take)
            left -= take
        existing = by_addr.get(address.lower())
        if existing is not None:  # one line per recipient: add to it
            existing.amount = str(dec(existing.amount) + amount)
            existing.reference = job_id
        else:
            row = LaunchAllocation(launch_id=launch.id, user_id=user_id, category="creator",
                                   label="Dispersed from the dev wallet", recipient=address, amount=str(amount),
                                   unlock="immediate", source="dispersal", reference=job_id)
            db.add(row)
            by_addr[address.lower()] = row
    for a in rows:
        if dec(a.amount) <= 0 and not a.lock and a.source == "manual":
            await db.delete(a)
