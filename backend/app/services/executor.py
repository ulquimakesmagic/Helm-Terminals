"""Sign, simulate, send and confirm transactions, and record results.

Nothing is sent unless simulation succeeds. Failures are recorded as failures, never as successes.
"""

import asyncio
from dataclasses import dataclass

from solders.keypair import Keypair
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.base import AdapterError, BuiltTx, LaunchpadAdapter, TradeRequest
from ..chains.solana import (
    LAMPORTS_PER_SOL, SolanaRPC, TxTooLarge, append_sol_transfers, build_unsigned, sign_versioned, sol_transfer_ix,
)
from ..models import FeeRecord, Job, Transaction, Wallet, utcnow
from ..signing.base import Signer, SigningError
from .fees import RENT_EXEMPT_MIN, FeePlan
from solders.pubkey import Pubkey


@dataclass
class SubmitResult:
    signature: str | None
    status: str  # confirmed | failed | timeout
    error: str | None = None


class Executor:
    def __init__(self, rpc: SolanaRPC, signer: Signer, confirm_timeout_s: float = 60) -> None:
        self.rpc = rpc
        self.signer = signer
        self.confirm_timeout_s = confirm_timeout_s

    async def submit(
        self, raw: bytes, refs: dict[str, str], extra: list[Keypair] | None = None
    ) -> SubmitResult:
        try:
            signed = await sign_versioned(raw, self.signer, refs, extra or [])
        except SigningError as e:
            return SubmitResult(None, "failed", f"signing: {e}")
        sim_err = await self.rpc.simulate(signed)
        if sim_err:
            return SubmitResult(None, "failed", f"simulation failed: {sim_err}")
        sig = await self.rpc.send(signed)
        status, err = await self.rpc.confirm(sig, timeout_s=self.confirm_timeout_s)
        return SubmitResult(sig, status, err)


async def _record(
    db: AsyncSession, job: Job, wallet: Wallet | None, kind: str, res: SubmitResult, delta: int | None
) -> Transaction:
    tx = Transaction(
        job_id=job.id,
        wallet_id=wallet.id if wallet else None,
        kind=kind,
        signature=res.signature,
        status=res.status,
        error=res.error,
        sol_delta_lamports=delta,
    )
    db.add(tx)
    await db.flush()
    return tx


def _finish(job: Job, results: list[SubmitResult]) -> None:
    ok = sum(1 for r in results if r.status == "confirmed")
    job.status = "confirmed" if ok == len(results) and results else ("failed" if ok == 0 else "partial")
    job.updated_at = utcnow()


@dataclass
class FeeOutcome:
    basis: int
    platform: int
    referral: int
    referral_status: str  # none | paid | owed
    mode: str  # in_tx | follow_up
    result: SubmitResult | None = None  # set for follow_up transfers


async def _can_receive(rpc: SolanaRPC, address: str, lamports: int) -> bool:
    """A transfer below the rent minimum to an account that doesn't exist yet would fail the whole transaction."""
    return lamports >= RENT_EXEMPT_MIN or await rpc.get_balance(address) >= RENT_EXEMPT_MIN


async def run_trade(
    db: AsyncSession,
    ex: Executor,
    adapter: LaunchpadAdapter,
    job: Job,
    mint: str,
    wallets: list[Wallet],
    side: str,
    amount: float | str,
    denominated_in_sol: bool,
    mode: str,
    stagger_ms: int,
    slippage_pct: float,
    plan: FeePlan | None = None,
) -> None:
    """Manual, user-initiated trades from wallets the user controls.

    If a fee plan is active, the fee (and the referrer's share) is added to the trade transaction itself, so a
    failed trade never costs a fee. If it can't fit, it is sent as a separate transfer right after a confirmed trade.
    """
    job.status = "submitted"
    await db.commit()
    results: list[SubmitResult] = []
    plan = plan or FeePlan(0, "")

    async def price_fee(w: Wallet, built: BuiltTx) -> tuple[FeeOutcome | None, list[tuple[Pubkey, int]]]:
        if not plan.active:
            return None, []
        if side == "buy":
            basis = int(float(amount) * LAMPORTS_PER_SOL) if denominated_in_sol else 0
        else:
            after = await ex.rpc.simulate_balance_after(built.raw, w.address)
            basis = max(0, after - await ex.rpc.get_balance(w.address)) if after is not None else 0
        platform, referral = plan.split(basis)
        if platform + referral <= 0:
            return None, []
        status = "none"
        transfers: list[tuple[Pubkey, int]] = []
        if referral:
            if plan.referrer_address and await _can_receive(ex.rpc, plan.referrer_address, referral):
                transfers.append((Pubkey.from_string(plan.referrer_address), referral))
                status = "paid"
            else:
                platform += referral  # held by the platform and recorded as owed to the referrer
                status = "owed"
        if not await _can_receive(ex.rpc, plan.platform_wallet, platform):
            return None, []  # never let a fee break a trade
        transfers.insert(0, (Pubkey.from_string(plan.platform_wallet), platform))
        return FeeOutcome(basis, platform - (referral if status == "owed" else 0), referral, status, "in_tx"), transfers

    async def one(w: Wallet) -> tuple[list[tuple[str, SubmitResult, int | None]], FeeOutcome | None]:
        """Chain work only. No DB access, so wallets can run concurrently on one AsyncSession-safe path."""
        req = TradeRequest(mint, w.address, side, amount, denominated_in_sol, slippage_pct)
        try:
            built: BuiltTx = await (adapter.build_buy_tx(req) if side == "buy" else adapter.build_sell_tx(req))
        except AdapterError as e:
            return [(side, SubmitResult(None, "failed", str(e)), None)], None
        refs = {w.address: w.signer_ref or ""}
        fee, transfers = await price_fee(w, built)
        raw = built.raw
        if fee:
            try:
                raw = append_sol_transfers(built.raw, Pubkey.from_string(w.address), transfers)
            except (TxTooLarge, ValueError):
                fee.mode = "follow_up"
        before = await ex.rpc.get_balance(w.address)
        res = await ex.submit(raw, refs, built.extra_signers)
        delta = None
        if res.status == "confirmed":
            delta = await ex.rpc.get_balance(w.address) - before
        records = [(side, res, delta)]
        if fee and fee.mode == "follow_up" and res.status == "confirmed":
            blockhash = await ex.rpc.get_latest_blockhash()
            payer = Pubkey.from_string(w.address)
            fee_raw = build_unsigned(payer, [sol_transfer_ix(payer, d, n) for d, n in transfers], blockhash)
            fee.result = await ex.submit(fee_raw, refs)
            total = sum(n for _, n in transfers)
            records.append(("platform_fee", fee.result, -total if fee.result.status == "confirmed" else None))
        return records, fee

    async def record(w: Wallet, outcome: tuple[list[tuple[str, SubmitResult, int | None]], FeeOutcome | None]) -> None:
        records, fee = outcome
        for kind, res, delta in records:
            await _record(db, job, w, kind, res, delta)
        trade_res = records[0][1]
        results.append(trade_res)  # the trade itself decides the job outcome, not the fee transfer
        if fee and trade_res.status == "confirmed":  # a failed trade never costs a fee
            paid_by = fee.result if fee.mode == "follow_up" else trade_res
            db.add(FeeRecord(
                job_id=job.id, payer_user_id=job.user_id, wallet_id=w.id, side=side, basis_lamports=fee.basis,
                platform_lamports=fee.platform, referrer_user_id=plan.referrer_user_id if fee.referral else None,
                referral_lamports=fee.referral, referral_status=fee.referral_status, mode=fee.mode,
                status="confirmed" if paid_by is not None and paid_by.status == "confirmed" else "failed",
                signature=paid_by.signature if paid_by else None,
            ))
        await db.commit()

    if mode == "parallel":
        outcomes = await asyncio.gather(*(one(w) for w in wallets))
        for w, outcome in zip(wallets, outcomes):
            await record(w, outcome)  # sequential DB writes
    else:
        for i, w in enumerate(wallets):
            if i and stagger_ms:
                await asyncio.sleep(stagger_ms / 1000)
            await record(w, await one(w))  # visible as each trade lands
    _finish(job, results)
    await db.commit()


async def run_single(
    db: AsyncSession,
    ex: Executor,
    job: Job,
    wallet: Wallet | None,
    kind: str,
    raw: bytes,
    refs: dict[str, str],
    extra: list[Keypair] | None = None,
) -> SubmitResult:
    job.status = "submitted"
    await db.commit()
    before = await ex.rpc.get_balance(wallet.address) if wallet else None
    res = await ex.submit(raw, refs, extra)
    delta = None
    if res.status == "confirmed" and wallet is not None and before is not None:
        delta = await ex.rpc.get_balance(wallet.address) - before
    await _record(db, job, wallet, kind, res, delta)
    _finish(job, [res])
    await db.commit()
    return res


async def run_built(
    db: AsyncSession,
    ex: Executor,
    job: Job,
    wallet: Wallet | None,
    kind: str,
    built: BuiltTx,
    refs: dict[str, str],
) -> SubmitResult:
    """Run a launchpad action that may need setup transactions first (e.g. Bags' fee-share config).

    Setup transactions go in order; if one fails, nothing more is sent and the job fails.
    """
    job.status = "submitted"
    await db.commit()
    for raw in built.pre:
        res = await ex.submit(raw, refs)
        await _record(db, job, wallet, f"{kind}_setup"[:24], res, None)
        if res.status != "confirmed":
            _finish(job, [res])
            await db.commit()
            return res
    await db.commit()
    res = await run_single(db, ex, job, wallet, kind, built.raw, refs, built.extra_signers)
    if res.status == "confirmed":
        for raw in built.post:
            cleanup = await ex.submit(raw, refs)
            await _record(db, job, wallet, f"{kind}_cleanup"[:24], cleanup, None)
        await db.commit()
    return res
