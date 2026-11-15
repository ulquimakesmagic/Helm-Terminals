"""Retired launch-fee compatibility helpers. All new launches are free.

Historical records and native transfer helpers remain readable; quote/charge never bill a launch.
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
MAX_DRIFT = 1.10  # confirmation may charge at most 10% more coin than the preview estimated
MAX_CENTS = 10_000  # $100: the same bound as the admin setting, also applied to the env default
# A price below these is treated as a bad feed, not a market move (it would multiply the coin amount charged).
MIN_PLAUSIBLE_USD = {"SOL": 1.0, "ETH": 10.0, "BNB": 1.0, "TRX": 0.01, "USDC": 0.5}
# Extra networks Helm charges on. NEAR has no verified price source in Helm yet, so Meme.Cooking stays fee-free.
FEE_EXTRA_NETWORKS = ("tron", "arc")
FIXED_USD = {"USDC": 1.0}  # Arc's native coin is USDC


def coin_and_decimals(network: str) -> tuple[str, int]:
    from ..chains.networks import EXTRA_NETWORKS

    if network in EXTRA_NETWORKS:
        n = EXTRA_NETWORKS[network]
        return n.native, n.decimals
    return native_coin(network), 9 if network == "solana" else 18


def fee_applies(network: str) -> bool:
    from ..chains.networks import EXTRA_NETWORKS

    return network not in EXTRA_NETWORKS or network in FEE_EXTRA_NETWORKS


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

    @property
    def max_raw(self) -> int:
        """The ceiling the user approves: the estimate plus 10%, rounded up to two significant figures so a small
        price tick between preview and confirm doesn't change the approved or funded amounts."""
        v = -(-self.raw * round(MAX_DRIFT * 100) // 100)  # integer maths: no float rounding up a whole step
        step = 10 ** max(len(str(v)) - 2, 0)
        return -(-v // step) * step


def approval_scope(launch_id: str, fee: LaunchFee) -> dict:
    return {"kind": "launch_fee", "launch": launch_id, "network": fee.network, "usd": fee.usd,
            "recipient": fee.recipient}


def issue_approval(user_id: str, launch_id: str, fee: LaunchFee | None) -> str | None:
    """Signed, short-lived ceiling for the fee's coin amount, returned with the preview."""
    if fee is None:
        return None
    from .quote_approval import issue

    return issue(user_id, approval_scope(launch_id, fee), {"launch_fee_max_raw": str(fee.max_raw)})


def check_approval(token: str | None, user_id: str, launch_id: str, fee: LaunchFee | None) -> str | None:
    """At confirmation: the preview's signed ceiling must be valid (else 409, review again) and the live coin amount
    must not exceed it. Returns a reason to refuse, or None."""
    if fee is None:
        return None
    from .quote_approval import verify

    approved = verify(token, user_id, approval_scope(launch_id, fee))
    if fee.raw > int(approved.get("launch_fee_max_raw") or 0):
        return (f"{fee.coin}'s price moved more than 10% since the preview, so the ${fee.usd:g} launch fee now needs "
                f"more {fee.coin} than you approved. Review the launch again.")
    return None


async def quote(db: AsyncSession, network: str, prices: dict | None = None) -> tuple[LaunchFee | None, str | None]:
    """Retired pricing path: launches are free regardless of legacy config or price feeds."""
    return None, None


def headroom(fee: LaunchFee | None) -> int:
    """Extra the dev wallet must hold for the fee: the amount plus its own transfer cost on Solana."""
    if fee is None:
        return 0
    return fee.max_raw + (SOL_TRANSFER_FEE if fee.network == "solana" else 0)


async def charge(session: AsyncSession, ex: Executor, job: Job, launch: Launch, dev: Wallet,
                 fee: LaunchFee | None, **kwargs) -> None:
    """Retired collector: never sign or charge a launch payment, including old quotations."""
    return None


def not_charged(job: Job, fee: LaunchFee | None, reason: str) -> None:
    """Note on the job that the fee wasn't taken (e.g. the launch isn't live yet)."""
    if fee is not None:
        job.params = {**(job.params or {}), "launch_fee": {"usd": fee.usd, "status": "not_charged", "reason": reason}}


async def send_native(ex: Executor, network: str, wallet: Wallet, to: str, raw: int) -> SubmitResult:
    """A plain native-coin transfer on TRON or Arc, through the same checked builders as user transfers."""
    from ..chains.networks import EXTRA_NETWORKS
    from ..routes import chains as chain_routes
    from .extra_chains import human

    amount = human(raw, EXTRA_NETWORKS[network].decimals)
    if EXTRA_NETWORKS[network].family == "tron":
        plan = await chain_routes._tron_plan(network, wallet, to, None, amount)
    else:
        plan = await chain_routes._arc_plan(ex, network, wallet, to, None, amount)
    if plan["warnings"]:
        return SubmitResult(None, "failed", plan["warnings"][0])
    return await plan["execute"](ex)


async def _charge(session: AsyncSession, ex: Executor, job: Job, launch: Launch, dev: Wallet,
                  fee: LaunchFee) -> SubmitResult:
    from ..chains.networks import EXTRA_NETWORKS

    if dev.network in EXTRA_NETWORKS:
        res = await send_native(ex, dev.network, dev, fee.recipient, fee.raw)
    elif dev.network == "solana":
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

