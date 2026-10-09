"""Helm's flat launch fee (LAUNCH_FEE_USD, default $10; admin setting `launch_fee_cents`).

The preview states the fee in dollars and estimates it in the launch chain's coin. The exact coin amount is fixed
at confirmation from the live price, the deploy's balance check includes it, and it is sent from the dev wallet to
that chain's treasury only after the token is created, so a failed launch pays nothing. If the price can't be read
the launch is held back rather than charging a guessed amount.
"""

import math
from dataclasses import dataclass

from solders.pubkey import Pubkey
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.solana import build_unsigned, sol_transfer_ix
from ..models import Job, Launch, RevenueEvent, Wallet
from . import platform_config as pc
from .chain_balances import native_coin
from .evm_exec import EvmCall
from .executor import Executor, SubmitResult, _record, send_evm_tracked, submit_tracked

SOL_TRANSFER_FEE = 5_000  # lamports for the fee transfer's own signature


@dataclass
class LaunchFee:
    usd: float
    raw: int  # in the chain's smallest unit
    coin: str
    decimals: int
    recipient: str
    network: str

    @property
    def amount(self) -> float:
        return self.raw / 10**self.decimals

    def summary(self) -> str:
        """Hashed preview line: the dollar amount is what the user agrees to; the coin amount moves with price."""
        return f"${self.usd:g}, paid in {self.coin} at the live price after the token is created"

    def estimate(self) -> str:
        return f"about {self.amount:.6g} {self.coin} right now"


async def quote(db: AsyncSession, network: str, prices: dict | None = None) -> tuple[LaunchFee | None, str | None]:
    """(fee, None), (None, None) when no fee applies, or (None, reason) when it can't be priced."""
    await pc.refresh(db)
    cents = pc.bps("launch_fee_cents")
    recipient = pc.treasury_for(network)
    if cents <= 0 or not recipient:
        return None, None
    coin = native_coin(network)
    if prices is None:
        from .portfolio import coin_prices

        prices = await coin_prices()
    price = prices.get(coin)
    usd = cents / 100
    if not price or not math.isfinite(price) or price <= 0:
        return None, (f"{coin}'s price couldn't be read, so Helm's ${usd:g} launch fee can't be sized. "
                      "Try again in a moment.")
    decimals = 9 if network == "solana" else 18
    raw = math.ceil(usd / price * 10**decimals)
    return LaunchFee(usd, raw, coin, decimals, recipient, network), None


def headroom(fee: LaunchFee | None) -> int:
    """Extra the dev wallet must hold for the fee: the amount plus its own transfer cost on Solana."""
    if fee is None:
        return 0
    return fee.raw + (SOL_TRANSFER_FEE if fee.network == "solana" else 0)


async def charge(session: AsyncSession, ex: Executor, job: Job, launch: Launch, dev: Wallet,
                 fee: LaunchFee | None) -> SubmitResult | None:
    """Send the fee after the token exists, record the transaction and the revenue, and note the outcome on the job.
    A failed fee transfer doesn't undo or block the launch; it is recorded as failed for follow-up."""
    if fee is None:
        return None
    if dev.network == "solana":
        src = Pubkey.from_string(dev.address)
        raw_tx = build_unsigned(src, [sol_transfer_ix(src, Pubkey.from_string(fee.recipient), fee.raw)],
                                await ex.rpc.get_latest_blockhash())
        res = await submit_tracked(ex, job, dev, "launch_fee", raw_tx, {dev.address: dev.signer_ref or ""})
    else:
        res = await send_evm_tracked(ex.evm(dev.network), job, dev, "launch_fee",
                                     EvmCall(to=fee.recipient, value=fee.raw))
    await _record(session, job, dev, "launch_fee", res, None)
    status = {"confirmed": "received", "timeout": "pending"}.get(res.status, "failed")
    session.add(RevenueEvent(
        source="launch_fee", network=fee.network, asset=fee.coin, amount_raw=fee.raw, decimals=fee.decimals,
        estimated=False, status=status, destination=fee.recipient, launch_id=launch.id, job_id=job.id,
        payer_user_id=launch.user_id, reference=res.signature,
        note=f"${fee.usd:g} launch fee for {launch.symbol or launch.name}"[:200]))
    job.params = {**(job.params or {}), "launch_fee": {
        "usd": fee.usd, "amount": fee.amount, "coin": fee.coin, "status": res.status, "signature": res.signature,
        **({"error": res.error} if res.error else {})}}
    await session.commit()
    return res

