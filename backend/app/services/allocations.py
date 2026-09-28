"""Launch supply allocations and vesting.

An allocation plan says where a token's supply goes: each line has a category, a recipient, an exact amount in
whole tokens, and an unlock schedule. The lines must add up exactly to the supply before the plan counts as
reconciled.

Schedules are public declarations. Helm can only enforce one kind: a lock on the owner's own Helm-managed wallets
(Helm refuses sells and transfers that would dip into the locked part). Locks can be extended, never shortened or
removed. Nothing here is an on-chain vesting contract.
"""

import decimal
from datetime import datetime
from decimal import Decimal, InvalidOperation

# Supplies like 100B tokens with 18 decimals need 30 significant digits; the default context keeps 28.
decimal.DefaultContext.prec = 100
decimal.getcontext().prec = 100

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Launch, LaunchAllocation, Wallet, utcnow

CATEGORIES = {
    "public": "Public sale / bonding curve",
    "liquidity": "Liquidity pool",
    "creator": "Creator (dev)",
    "team": "Team",
    "treasury": "Treasury",
    "marketing": "Marketing",
    "community": "Community / airdrop",
    "advisors": "Advisors",
    "investors": "Investors",
    "other": "Other",
}
NO_RECIPIENT = {"public", "liquidity"}  # held by the launchpad's curve or pool, not a wallet
UNLOCKS = ("immediate", "date", "linear")

# Supplies fixed by the launchpad (whole tokens), so a draft's allocation can be planned before deploy. Other
# launchpads report the supply once the token exists.
DRAFT_SUPPLY = {
    "pumpfun": 1_000_000_000, "letsbonk": 1_000_000_000, "four-meme": 1_000_000_000, "pons": 1_000_000_000,
    "uniswap-v2": 1_000_000_000, "clanker": 100_000_000_000, "clanker-robinhood": 100_000_000_000,
}
SINGLE_HOLDER_WARN_PCT = Decimal(5)
INSIDER_WARN_PCT = Decimal(20)


def dec(s: str | int | float | Decimal) -> Decimal:
    try:
        d = Decimal(str(s))
    except InvalidOperation:
        raise ValueError(f"'{s}' isn't a number")
    if not d.is_finite():
        raise ValueError("amounts must be finite numbers")
    return d


def unlocked_fraction(a: LaunchAllocation, now: datetime | None = None) -> Decimal:
    now = now or utcnow()
    if a.unlock == "immediate":
        return Decimal(1)
    if a.unlock == "date":
        return Decimal(1) if a.unlock_start and now >= a.unlock_start else Decimal(0)
    if not a.unlock_start or not a.unlock_end or now < a.unlock_start:  # linear, before the cliff
        return Decimal(0)
    if now >= a.unlock_end:
        return Decimal(1)
    span = (a.unlock_end - a.unlock_start).total_seconds()
    return Decimal(str((now - a.unlock_start).total_seconds() / span)) if span > 0 else Decimal(1)


async def locked_tokens(db: AsyncSession, address: str, launch_id: str, now: datetime | None = None) -> Decimal:
    """Whole tokens of this launch Helm must keep in `address` right now under the owner's locks."""
    rows = (await db.execute(select(LaunchAllocation).where(
        LaunchAllocation.launch_id == launch_id, LaunchAllocation.lock.is_(True)))).scalars().all()
    total = Decimal(0)
    for a in rows:
        if a.recipient.lower() == address.lower():
            total += dec(a.amount) * (1 - unlocked_fraction(a, now))
    return total


async def locked_raw(db: AsyncSession, wallet: Wallet, launch: Launch, decimals: int | None = None,
                     ex=None) -> int:
    """The locked amount in base units. Reads the token's decimals from the chain when not given."""
    locked = await locked_tokens(db, wallet.address, launch.id)
    if locked <= 0:
        return 0
    if decimals is None:
        from .chain_balances import token_decimals

        if ex is None:
            from ..deps import get_executor, get_rpc
            from ..signing import get_signer

            ex = get_executor(get_rpc(), get_signer())
        decimals = await token_decimals(ex, launch.network, launch.mint_address)
    return int((locked * (Decimal(10) ** decimals)).to_integral_value(rounding="ROUND_CEILING"))


async def lock_warning(db: AsyncSession, launch: Launch, wallet: Wallet, balance_raw: int, out_raw: int,
                       decimals: int) -> str | None:
    """A refusal message when moving `out_raw` out of `wallet` would break one of its locks, else None."""
    locked = await locked_raw(db, wallet, launch, decimals)
    if locked and balance_raw - out_raw < locked:
        free = max(balance_raw - locked, 0) / 10 ** decimals
        return (f"{wallet.label or wallet.address[:8]} has {locked / 10 ** decimals:,.6g} ${launch.symbol} locked by "
                f"your allocation plan; at most {free:,.6g} can leave it now.")
    return None
