"""Helm's revenue ledger: what Helm itself earns, in the original asset and network.

Only Helm's own share is ever recorded here. Referral shares (paid to referrers, or held and owed to them), creator
rewards, users' trade amounts, LP and launchpad fees are not Helm revenue and are never written to this ledger.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.evm import EVM_CHAINS
from ..models import FeeRecord, RevenueEvent

NATIVE = {"solana": ("SOL", 9), **{n: (c["native"], 18) for n, c in EVM_CHAINS.items()}}


def trade_fee_event(fee: FeeRecord, destination: str, launch_id: str | None) -> RevenueEvent:
    """The platform part of a Solana trade fee. `platform_lamports` already excludes any referral share (paid or
    owed), so a referrer's money is never counted as Helm's."""
    confirmed = fee.status == "confirmed"
    return RevenueEvent(
        source="trade_fee", network=fee.network or "solana", asset="SOL", amount_raw=fee.platform_lamports, decimals=9,
        status="received" if confirmed else "pending", destination=destination, launch_id=launch_id,
        job_id=fee.job_id, payer_user_id=fee.payer_user_id, reference=fee.signature, fee_record_id=fee.id,
        note=f"{fee.side} fee, paid {'in the trade transaction' if fee.mode == 'in_tx' else 'right after the trade'}")


async def backfill_trade_fees(db: AsyncSession) -> int:
    """Ledger rows for confirmed fee records written before the ledger existed (idempotent)."""
    from ..models import Job

    missing = (await db.execute(
        select(FeeRecord, Job.launch_id).outerjoin(Job, Job.id == FeeRecord.job_id)
        .outerjoin(RevenueEvent, RevenueEvent.fee_record_id == FeeRecord.id)
        .where(FeeRecord.status == "confirmed", FeeRecord.platform_lamports > 0, RevenueEvent.id.is_(None)))).all()
    n = 0
    for fee, launch_id in missing:
        ev = trade_fee_event(fee, "", launch_id)
        ev.note += " (recorded before the ledger existed; destination not stored)"
        db.add(ev)
        n += 1
    if n:
        await db.commit()
    return n


def dex_fee_event(network: str, wei: int, estimated: bool, destination: str, job_id: str, launch_id: str | None,
                  payer_user_id: str, reference: str | None, side: str) -> RevenueEvent:
    asset, dec = NATIVE.get(network, ("ETH", 18))
    return RevenueEvent(source="dex_trade_fee", network=network, asset=asset, amount_raw=wei, decimals=dec,
                        estimated=estimated, status="received", destination=destination, launch_id=launch_id,
                        job_id=job_id, payer_user_id=payer_user_id, reference=reference,
                        note=f"KyberSwap router fee on a {side}" + (" (amount estimated from the quote)" if estimated else ""))


def route_fee_event(quote: dict, destination: str, job_id: str, launch_id: str | None, payer_user_id: str,
                    reference: str | None) -> RevenueEvent | None:
    """A Relay app fee: held by Relay for the treasury address until claimed, so it is claimable, never received."""
    app = ((quote or {}).get("fees") or {}).get("app") or {}
    cur = app.get("currency") or {}
    try:
        amount = int(app.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        return None
    chain_id = cur.get("chainId")
    network = next((n for n, c in EVM_CHAINS.items() if c["chain_id"] == chain_id), "solana" if chain_id == 792703809 else "evm")
    return RevenueEvent(source="route_fee", network=network, asset=(cur.get("symbol") or "?")[:16], amount_raw=amount,
                        decimals=int(cur.get("decimals") or 6), estimated=True, status="claimable",
                        destination=destination, launch_id=launch_id, job_id=job_id, payer_user_id=payer_user_id,
                        reference=reference, note="Relay app fee from the executed quote; claim at relay.link")
