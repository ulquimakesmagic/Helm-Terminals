"""Sign, simulate, send and confirm transactions, and record results.

Nothing is sent unless simulation succeeds. Failures are recorded as failures, never as successes.
"""

import asyncio

import httpx
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from solders.keypair import Keypair
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.base import AdapterError, BuiltTx, LaunchpadAdapter, TradeRequest
from ..chains.solana import (
    LAMPORTS_PER_SOL, RPCError, RPCUnavailable, SolanaRPC, TxTooLarge, append_sol_transfers, build_unsigned,
    sign_versioned, sol_transfer_ix,
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
    def __init__(self, rpc: SolanaRPC, signer: Signer, confirm_timeout_s: float = 60,
                 evm_rpcs: dict | None = None, dex=None, relay=None) -> None:
        self.rpc = rpc
        self.signer = signer
        self.confirm_timeout_s = confirm_timeout_s
        self.evm_rpcs = evm_rpcs or {}  # tests inject fake EVM nodes here
        self._dex = dex
        self._relay = relay
        self._dex_key = self._relay_key = None  # None with an injected router: keep it

    @property
    def relay(self):
        """Cross-chain router (Relay) for moving USDC or coins between chains. Its fee rate and recipient follow the
        current platform settings (rebuilt when they change); an injected router (tests) is kept as is."""
        from . import platform_config as pc

        if self._relay is not None and getattr(self, "_relay_key", None) is None:
            return self._relay
        key = (pc.bps("route_fee_bps"), pc.treasury_for("evm"))
        if self._relay is None or self._relay_key != key:
            from ..chains.relay import Relay
            from ..config import get_settings

            s = get_settings()
            self._relay = Relay(s.relay_api_url, s.relay_api_key, fee_bps=key[0], fee_recipient=key[1])
            self._relay_key = key
        return self._relay

    @property
    def dex(self):
        """DEX aggregator for EVM tokens that trade on a DEX (graduated launchpad tokens, Uniswap V2 launches)."""
        from . import platform_config as pc

        if self._dex is not None and getattr(self, "_dex_key", None) is None:
            return self._dex
        key = (pc.bps("trade_fee_bps_evm_dex"), pc.treasury_for("evm"))
        if self._dex is None or self._dex_key != key:
            from ..chains.kyber import KyberSwap
            from ..config import get_settings

            s = get_settings()
            self._dex = KyberSwap(s.kyber_api_url, s.kyber_client_id, fee_bps=key[0], fee_receiver=key[1])
            self._dex_key = key
        return self._dex

    def evm(self, network: str):
        """Sender for an EVM chain, using the rotating RPC endpoints configured for it."""
        from ..chains.evm import shared_evm
        from .evm_exec import EvmSender

        rpc = self.evm_rpcs.get(network) or shared_evm(network)
        return EvmSender(rpc, self.signer, poll_s=0 if network in self.evm_rpcs else 1.5)

    async def submit(
        self, raw: bytes, refs: dict[str, str], extra: list[Keypair] | None = None, submit_via=None,
        fast_poll_s: float = 0.25,
    ) -> SubmitResult:
        """Sign, simulate, send, confirm. `submit_via` hands the signed transaction to a launchpad's own submit
        endpoint instead of our RPC (it may add a signature Helm doesn't hold, such as the mint's); the
        transaction is still tracked on-chain by its fee payer's signature."""
        try:
            signed = await sign_versioned(raw, self.signer, refs, extra or [], allow_missing=submit_via is not None)
        except SigningError as e:
            return SubmitResult(None, "failed", f"signing: {e}")
        sim_err = (await self.rpc.simulate(signed) if submit_via is None
                   else await self.rpc.simulate(signed, sig_verify=False))
        if sim_err:
            return SubmitResult(None, "failed", f"simulation failed: {sim_err}")
        from solders.transaction import VersionedTransaction

        sig = str(VersionedTransaction.from_bytes(signed).signatures[0])  # known before sending
        if submit_via is not None:
            try:
                await submit_via(signed)
            except AdapterError as e:
                return SubmitResult(None, "failed", str(e))
        else:
            try:
                sig = await self.rpc.send(signed)
            except RPCError as e:  # the node answered and refused it: nothing was broadcast
                return SubmitResult(None, "failed", f"rejected by the node: {e}")
            except (RPCUnavailable, httpx.HTTPError) as e:
                # No answer: it may or may not have gone out. Treat it as sent-but-unconfirmed, never as failed,
                # so nobody retries into a double buy or send.
                return SubmitResult(sig, "timeout", f"couldn't confirm it was sent ({type(e).__name__}); check the explorer")
        try:
            status, err = await self.rpc.confirm(sig, timeout_s=self.confirm_timeout_s, fast_poll_s=fast_poll_s)
        except Exception as e:  # anything after sending: the transaction may be out there, so never "failed"
            return SubmitResult(sig, "timeout", f"sent, but confirmation couldn't be read ({type(e).__name__}); check the explorer")
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
    """A transaction that was sent but not confirmed in time may still land, so a job with any such transaction is
    "unconfirmed", never "failed": reporting it as failed invites a retry that could buy or send twice."""
    ok = sum(1 for r in results if r.status == "confirmed")
    if results and ok == len(results):
        job.status = "confirmed"
    elif any(r.status == "timeout" for r in results):
        job.status = "unconfirmed"
    else:
        job.status = "failed" if ok == 0 else "partial"
    if (job.params or {}).get("lost_track"):  # it did finish after all
        job.params = {k: v for k, v in job.params.items() if k not in ("lost_track", "error")}
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
    execution_guard: Callable[[], Awaitable[bool]] | None = None,
) -> None:
    """Trades from wallets the user controls, whether manually confirmed or part of a capped schedule.

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
        # The balance before the trade is read while the launchpad builds the transaction, not after it.
        before_task = asyncio.create_task(ex.rpc.get_balance(w.address))
        try:
            built: BuiltTx = await (adapter.build_buy_tx(req) if side == "buy" else adapter.build_sell_tx(req))
        except AdapterError as e:
            before_task.cancel()
            return [(side, SubmitResult(None, "failed", str(e)), None)], None
        refs = {w.address: w.signer_ref or ""}
        fee, transfers = await price_fee(w, built)
        raw = built.raw
        if fee:
            try:
                raw = append_sol_transfers(built.raw, Pubkey.from_string(w.address), transfers)
            except (TxTooLarge, ValueError):
                fee.mode = "follow_up"
        if execution_guard and not await execution_guard():
            before_task.cancel()
            return [(side, SubmitResult(None, "failed", "The scheduled plan stopped before this order was sent."), None)], None
        before = await before_task
        res = await ex.submit(raw, refs, built.extra_signers, fast_poll_s=fast_poll)
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
            fee_rec = FeeRecord(
                job_id=job.id, payer_user_id=job.user_id, wallet_id=w.id, side=side, basis_lamports=fee.basis,
                platform_lamports=fee.platform, referrer_user_id=plan.referrer_user_id if fee.referral else None,
                referral_lamports=fee.referral, referral_status=fee.referral_status, mode=fee.mode,
                status="confirmed" if paid_by is not None and paid_by.status == "confirmed" else "failed",
                signature=paid_by.signature if paid_by else None,
            )
            db.add(fee_rec)
            if fee_rec.status == "confirmed" and fee.platform > 0:
                # Helm's revenue ledger gets only the platform part; the referral share is never Helm's.
                from .revenue import trade_fee_event

                await db.flush()
                db.add(trade_fee_event(fee_rec, plan.platform_wallet, job.launch_id))
        await db.commit()

    # Many wallets confirming at once poll less often each, so a batch stays near one status call per second per
    # wallet (the old fixed rate) instead of multiplying load on the RPC.
    fast_poll = min(1.0, 0.25 * max(1.0, len(wallets) / 2)) if mode == "parallel" else 0.25
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
    submit_via=None,
) -> SubmitResult:
    job.status = "submitted"
    await db.commit()
    before = await ex.rpc.get_balance(wallet.address) if wallet else None
    res = await ex.submit(raw, refs, extra, submit_via=submit_via) if submit_via else await ex.submit(raw, refs, extra)
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
    res = await run_single(db, ex, job, wallet, kind, built.raw, refs, built.extra_signers, built.submit_via)
    if res.status == "confirmed":
        for raw in built.post:
            cleanup = await ex.submit(raw, refs)
            await _record(db, job, wallet, f"{kind}_cleanup"[:24], cleanup, None)
        await db.commit()
    return res


# --- EVM ------------------------------------------------------------------------------------------


async def _run_evm_steps(db: AsyncSession, ex: Executor, job: Job, wallet: Wallet, kind: str, plan) -> SubmitResult:
    """Send a plan's calls in order from one wallet. The last step is the action itself; earlier ones (like an
    approve) are recorded as `{kind}_{label}`. If an essential step fails, nothing after it is sent."""
    from .evm_exec import EvmCall

    sender = ex.evm(wallet.network)
    last = None
    for i, step in enumerate(plan.steps):
        to, data = step.to, step.data
        try:
            if step.build:
                built = await step.build()
                if built is None:  # the step decided there's nothing to do (e.g. no fees in that token)
                    continue
                to, data = built if isinstance(built, tuple) else (to, built)
        except AdapterError as e:
            res = SubmitResult(None, "failed", str(e))
            await _record(db, job, wallet, kind if i == len(plan.steps) - 1 else f"{kind}_{step.label}"[:24], res, None)
            await db.commit()
            return res
        res = await sender.send(wallet.address, wallet.signer_ref or "", EvmCall(to, data, step.value))
        if res.status == "confirmed" and step.after:
            step.after(res)
        label = kind if i == len(plan.steps) - 1 else f"{kind}_{step.label}"[:24]
        await _record(db, job, wallet, label, res, None)
        await db.commit()
        last = res
        if res.status != "confirmed" and step.essential:
            break
    return last or SubmitResult(None, "failed", "nothing to send")


async def run_evm_plan(db: AsyncSession, ex: Executor, job: Job, wallet: Wallet, kind: str, plan) -> SubmitResult:
    job.status = "submitted"
    await db.commit()
    res = await _run_evm_steps(db, ex, job, wallet, kind, plan)
    _finish(job, [res])
    await db.commit()
    return res


async def run_evm_trade(db: AsyncSession, ex: Executor, adapter, job: Job, token: str, wallets: list[Wallet],
                        side: str, amount: float, mode: str, stagger_ms: int, slippage_pct: float,
                        execution_guard: Callable[[], Awaitable[bool]] | None = None) -> None:
    """Buys spend `amount` native coin per wallet; sells sell `amount` percent of each wallet's tokens.
    `execution_guard`, when given, is checked before each wallet's trade; if it returns False nothing more is sent."""
    from ..chains.base import EvmTradeRequest

    job.status = "submitted"
    await db.commit()
    sender_rpc = ex.evm(wallets[0].network).rpc

    async def plan_for(w: Wallet):
        if side == "buy":
            amt = int(round(amount * 10**18))
        else:
            amt = await sender_rpc.erc20_balance(token, w.address) * int(round(amount * 100)) // 10_000
        from ..chains.kyber import Graduated

        try:
            try:
                return await adapter.prepare_trade(EvmTradeRequest(token, w.address, side, amt, slippage_pct)), None
            except Graduated as g:
                # Off the launchpad's curve: trade it on the DEX where it now lives.
                try:
                    return await ex.dex.prepare_trade(w.network, sender_rpc, token, w.address, side, amt,
                                                      slippage_pct), None
                except AdapterError as e:
                    return None, f"{g}; DEX route failed: {e}"
        except AdapterError as e:
            return None, str(e)

    async def one(w: Wallet) -> SubmitResult:
        if execution_guard and not await execution_guard():
            res = SubmitResult(None, "failed", "The scheduled plan stopped before this order was sent.")
            await _record(db, job, w, side, res, None)
            await db.commit()
            return res
        plan, err = await plan_for(w)
        if plan is None:
            res = SubmitResult(None, "failed", err)
            await _record(db, job, w, side, res, None)
            await db.commit()
            return res
        res = await _run_evm_steps(db, ex, job, w, side, plan)
        await _note_dex_fee(w, plan, res)
        return res

    async def _note_dex_fee(w: Wallet, plan, res: SubmitResult) -> None:
        """Ledger entry for Helm's DEX router fee, only when the swap itself confirmed (a failed swap pays none)."""
        info = (getattr(plan, "notes", None) or {}).get("helm_fee")
        if not info or res.status != "confirmed" or info.get("wei", 0) <= 0:
            return
        from .revenue import dex_fee_event

        db.add(dex_fee_event(w.network, int(info["wei"]), bool(info["estimated"]), info["receiver"], job.id,
                             job.launch_id, job.user_id, res.signature, side))
        await db.commit()

    results = []
    if mode == "parallel" and execution_guard and not await execution_guard():
        mode = "stagger"  # each wallet then records the stop individually
    if mode == "parallel":
        # Different wallets never share a nonce, so they can go at once. DB writes stay on one session in order.
        plans = await asyncio.gather(*(plan_for(w) for w in wallets))
        from .evm_exec import EvmCall
        sender = ex.evm(wallets[0].network)

        async def fire(w, plan):
            out = []
            for step in plan.steps:
                to, data = step.to, step.data
                try:
                    if step.build:
                        built = await step.build()
                        to, data = built if isinstance(built, tuple) else (to, built)
                except AdapterError as e:
                    out.append((step, SubmitResult(None, "failed", str(e))))
                    break
                r = await sender.send(w.address, w.signer_ref or "", EvmCall(to, data, step.value))
                out.append((step, r))
                if r.status != "confirmed" and step.essential:
                    break
            return out

        runs = await asyncio.gather(*(fire(w, p) if p else asyncio.sleep(0, result=err) for w, (p, err) in zip(wallets, plans)))
        for w, (plan, err), run in zip(wallets, plans, runs):
            if plan is None:
                res = SubmitResult(None, "failed", err)
                await _record(db, job, w, side, res, None)
                results.append(res)
                continue
            for i, (step, r) in enumerate(run):
                await _record(db, job, w, side if i == len(plan.steps) - 1 else f"{side}_{step.label}", r, None)
            if len(run) == len(plan.steps):  # the swap itself was sent (not stopped at an earlier step)
                await _note_dex_fee(w, plan, run[-1][1])
            results.append(run[-1][1])
        await db.commit()
    else:
        for i, w in enumerate(wallets):
            if i and stagger_ms:
                await asyncio.sleep(stagger_ms / 1000)
            results.append(await one(w))
    _finish(job, results)
    await db.commit()
