"""Sign, simulate, send and confirm transactions, and record results.

Nothing is sent unless simulation succeeds. Failures are recorded as failures, never as successes.
"""

import asyncio
from dataclasses import dataclass

from solders.keypair import Keypair
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.base import AdapterError, BuiltTx, LaunchpadAdapter, TradeRequest
from ..chains.solana import SolanaRPC, sign_versioned
from ..models import Job, Transaction, Wallet, utcnow
from ..signing.base import Signer, SigningError
from .preflight import platform_fee_lamports
from ..chains.solana import build_unsigned, sol_transfer_ix
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
    fee_bps: int,
    fee_wallet: str,
) -> None:
    """Manual, user-initiated trades from wallets the user controls."""
    job.status = "submitted"
    await db.commit()
    results: list[SubmitResult] = []

    async def one(w: Wallet) -> list[tuple[str, SubmitResult, int | None]]:
        """Chain work only. No DB access, so wallets can run concurrently on one AsyncSession-safe path."""
        req = TradeRequest(mint, w.address, side, amount, denominated_in_sol, slippage_pct)
        try:
            built: BuiltTx = await (adapter.build_buy_tx(req) if side == "buy" else adapter.build_sell_tx(req))
        except AdapterError as e:
            return [(side, SubmitResult(None, "failed", str(e)), None)]
        before = await ex.rpc.get_balance(w.address)
        res = await ex.submit(built.raw, {w.address: w.signer_ref or ""}, built.extra_signers)
        delta = None
        if res.status == "confirmed":
            delta = await ex.rpc.get_balance(w.address) - before
        records = [(side, res, delta)]
        if side == "sell" and res.status == "confirmed" and fee_bps > 0 and fee_wallet and delta:
            fee = platform_fee_lamports(delta, fee_bps)
            if fee > 0:
                blockhash = await ex.rpc.get_latest_blockhash()
                raw = build_unsigned(
                    Pubkey.from_string(w.address),
                    [sol_transfer_ix(Pubkey.from_string(w.address), Pubkey.from_string(fee_wallet), fee)],
                    blockhash,
                )
                fee_res = await ex.submit(raw, {w.address: w.signer_ref or ""})
                records.append(("platform_fee", fee_res, -fee if fee_res.status == "confirmed" else None))
        return records

    async def record(w: Wallet, records: list[tuple[str, SubmitResult, int | None]]) -> None:
        for kind, res, delta in records:
            await _record(db, job, w, kind, res, delta)
        results.append(records[0][1])  # the trade itself decides the job outcome, not the fee transfer
        await db.commit()

    if mode == "parallel":
        outcomes = await asyncio.gather(*(one(w) for w in wallets))
        for w, records in zip(wallets, outcomes):
            await record(w, records)  # sequential DB writes
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
