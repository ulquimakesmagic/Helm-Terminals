"""Trading fees and referral shares.

The fee is a fixed percentage of the SOL side of each trade (what is spent on a buy, what is received on a sell).
It is shown in every preview, added to the trade transaction itself so it succeeds or fails with the trade, and a
share of it goes straight to the referrer's payout address on-chain.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import PayoutAddress, ReferralLink, User

# Lamports an empty Solana account needs to exist. A transfer smaller than this to an account that does not exist
# yet fails, and would take the whole trade down with it, so such payouts are recorded as owed instead.
RENT_EXEMPT_MIN = 890_880


@dataclass
class FeePlan:
    bps: int
    platform_wallet: str
    referral_share_bps: int = 0
    referrer_user_id: str | None = None
    referrer_address: str | None = None
    exemption_reason: str | None = None

    @property
    def active(self) -> bool:
        return self.bps > 0 and bool(self.platform_wallet)

    def split(self, basis_lamports: int) -> tuple[int, int]:
        """(platform_lamports, referral_lamports) for a trade whose SOL side is `basis_lamports`."""
        if not self.active or basis_lamports <= 0:
            return 0, 0
        total = basis_lamports * self.bps // 10_000
        ref = total * self.referral_share_bps // 10_000 if self.referrer_user_id else 0
        return total - ref, ref


async def fee_plan_for(db: AsyncSession, user: User, network: str = "solana") -> FeePlan:
    from . import platform_config as pc

    s = get_settings()
    await pc.refresh(db)
    from .trade_policy import policy_for
    policy = await policy_for(db, user)
    plan = FeePlan(bps=policy['bps'], platform_wallet=pc.treasury_for(network), exemption_reason=policy['reason'])
    if not plan.active:
        return plan
    link = (await db.execute(select(ReferralLink).where(ReferralLink.referred_user_id == user.id))).scalar_one_or_none()
    if link:
        plan.referral_share_bps = 3000
        plan.referrer_user_id = link.referrer_user_id
        payout = (await db.execute(select(PayoutAddress).where(
            PayoutAddress.user_id == link.referrer_user_id, PayoutAddress.network == network))).scalar_one_or_none()
        plan.referrer_address = payout.address if payout else None
    return plan


def describe(plan: FeePlan, side: str, amount_sol: float | None = None) -> str:
    """Plain-language fee line for the preview."""
    if not plan.active:
        return f"None · {plan.exemption_reason}" if plan.exemption_reason else "none"
    pct = f"{plan.bps / 100:.2f}%"
    if side == "buy" and amount_sol is not None:
        per = f"{amount_sol * plan.bps / 10_000:.6f}".rstrip("0").rstrip(".")
        base = f"{pct} of each buy ({per} SOL per wallet)"
    else:
        base = f"{pct} of the SOL each sell receives (priced by simulation just before sending)"
    if plan.referrer_user_id:
        base += f"; {plan.referral_share_bps / 100:.0f}% of this fee goes to the person who referred you"
    return base
