"""Preflight and tracked execution for locally constructed Solana cash operations."""
from decimal import Decimal

from fastapi import HTTPException

from ..config import get_settings
from ..deps import begin_spend, run_job, two_fa_warnings
from .executor import SubmitResult, _finish, _record, submit_tracked
from .execution_journal import require_steps
from .preflight import build_preflight
from .solana_cash import CashError, prepare


async def cash_operation(body, user, db, ex, wallets, destination, *, sweep=False):
    stable = not sweep and body.asset == "stable"
    kind = "sweep" if sweep else "withdraw"
    amount = None
    if not sweep and not body.all:
        value = body.amount if stable else body.amount_sol
        if value is None:
            raise HTTPException(400, "Specify an amount or select Max")
        scaled = Decimal(str(value)) * (10 ** (6 if stable else 9))
        if not scaled.is_finite() or scaled != scaled.to_integral_value() or scaled <= 0:
            raise HTTPException(400, "Amount exceeds the asset's supported decimal precision")
        amount = int(scaled)
    plans, warnings, skipped = [], [], []
    for wallet in wallets:
        try:
            plan = await prepare(ex.rpc, get_settings(), wallet, destination,
                                 stable=stable, sweep=sweep, amount=amount)
            plans.append((wallet, plan))
        except CashError as error:
            if sweep and str(error) == "Nothing available to sweep":
                skipped.append(wallet.address)
            else:
                warnings.append(f"{wallet.label or wallet.address[:8]}: {error}")
        except Exception as error:
            raise HTTPException(502, f"Could not prepare Solana withdrawal ({type(error).__name__})") from error
    if not plans and not warnings:
        warnings.append("None of the selected wallets has anything to sweep")
    if not sweep:
        warnings += two_fa_warnings(user, "withdraw")
    details = [{"address": w.address, "label": w.label, **p.summary()} for w, p in plans]
    rows = []
    for wallet, plan in plans:
        if sweep:
            if plan.stable:
                rows.append({"address": wallet.address, "asset": "USDC", "amount": plan.stable / 10**6})
            if plan.native:
                rows.append({"address": wallet.address, "asset": "SOL", "amount": plan.native / 10**9})
        else:
            rows.append({"address": wallet.address, "amount" if stable else "sol":
                         plan.stable / 10**6 if stable else plan.native / 10**9})
    summary = {"network": "solana", "chain": "Solana", "asset": "USDC" if stable else "SOL",
               "destination": destination, "to": {"address": destination}, "from": rows,
               "cash_transfers": details, "wallets_swept": len(plans), "skipped_empty": skipped,
               "total_stable": sum(p.stable for _, p in plans) / 10**6,
               "total_native": sum(p.native for _, p in plans) / 10**9,
               "network_fees_max": sum(p.fee + p.rent for _, p in plans) / 10**9,
               "network_fee_coin": "SOL", "own_wallets_only": sweep,
               "fee_reimbursement_usdc": sum(p.reimbursement for _, p in plans) / 10**6,
               "platform_fee": "none", "automatic_fee_funding": any(p.reimbursement for _, p in plans)}
    if sweep:
        summary["amount_policy"] = "Quoted minimum; any saved destination-account rent also reaches the recipient"
    pf = build_preflight(kind, summary, warnings)
    if not body.confirm:
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, kind, None, summary)
    if created:
        async def work(session, current):
            for wallet, _ in plans:
                await require_steps(session, current, wallet.id, [kind])
            results = []
            for wallet, plan in plans:
                if sweep:
                    try:
                        fresh = await prepare(ex.rpc, get_settings(), wallet, destination, sweep=True,
                                              amount=plan.stable + plan.reimbursement)
                        saved = plan.rent - fresh.rent
                        native_gain = saved if plan.payer == wallet.address else 0
                        if (fresh.payer != plan.payer or fresh.reimbursement != plan.reimbursement
                                or fresh.stable != plan.stable or fresh.recovered != plan.recovered
                                or fresh.fee != plan.fee or saved < 0
                                or fresh.native != plan.native + native_gain):
                            raise CashError("Wallet state or fee terms changed; request a new sweep preview")
                        plan = fresh
                    except CashError as error:
                        result = SubmitResult(None, "failed", str(error))
                        await _record(session, current, wallet, kind, result, None)
                        results.append(result)
                        continue
                result = await submit_tracked(ex, current, wallet, kind, plan.raw, plan.refs)
                await _record(session, current, wallet, kind, result, None)
                await session.commit()
                results.append(result)
                # No automatic retry, including ambiguous submissions.
            _finish(current, results)
            await session.commit()
        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}
