"""Per-account trading policy. Never accepts exemption claims from a browser."""
from ..models import TradeFeeExemption
from .admin import role_of
from .pricing import TRADE_BPS


async def policy_for(db, user):
    role = await role_of(db, user)
    if role in ('superadmin', 'viewer'):
        return {'bps': 0, 'exempt': True, 'reason': 'Admin account', 'account_exception': False}
    row = await db.get(TradeFeeExemption, user.id)
    if row and row.enabled:
        return {'bps': 0, 'exempt': True, 'reason': 'Account trading-fee exemption', 'account_exception': True}
    return {'bps': TRADE_BPS, 'exempt': False, 'reason': None, 'account_exception': False}
