"""Deploying and trading launches on SunPump (TRON), Meme.Cooking (NEAR) and Tolly (Arc).

Same shape as Helm's other launches: a preview (the adapter's plan, hashed), a confirmation with the user's 2FA, then
a job that runs the plan's steps with each wallet's own key and records every transaction.
The fixed launch/trading policy is applied outside launchpad-native costs.
"""

from decimal import Decimal

from fastapi import HTTPException

from ..chains.base import AdapterError
from ..chains.extra_pads import PadContext, PadPlan, StepResult
from ..chains.networks import EXTRA_NETWORKS, extra_enabled
from ..config import get_settings
from ..deps import begin_spend, run_job
from ..models import Launch, Wallet, utcnow
from ..services import extra_chains as xc
from ..services.executor import SubmitResult, _finish, _record
from ..services.preflight import build_preflight

UNITS = {"tron": 6, "near": 24, "evm": 18}


def native_raw(network: str, amount: float) -> int:
    dec = UNITS[EXTRA_NETWORKS[network].family]
    return int(Decimal(str(amount)).scaleb(dec).to_integral_value())


def unit(network: str) -> str:
    return EXTRA_NETWORKS[network].native


def ctx_for(ex, network: str, w: Wallet) -> PadContext:
    fam = EXTRA_NETWORKS[network].family
    return PadContext(address=w.address, signer_ref=w.signer_ref or "", signer=ex.signer,
                      tron=xc.tron_client(network) if fam == "tron" else None,
                      near=xc.near_client(network) if fam == "near" else None,
                      evm=ex.evm(network) if fam == "evm" else None)


def check_enabled(network: str) -> None:
    if network not in extra_enabled():
        raise HTTPException(409, f"{EXTRA_NETWORKS[network].name} isn't enabled on this server")


async def run_plan(session, job, wallet: Wallet, kind: str, plan: PadPlan) -> list[StepResult]:
    """Run the steps in order, recording each transaction. An essential step that doesn't confirm stops the rest."""
    results = []
    for step in plan.steps:
        try:
            r = await step.run()
        except Exception as e:  # never leave a job half-known: record what happened
            r = StepResult(None, "failed", f"{step.label}: {type(e).__name__}: {str(e)[:160]}")
        results.append(r)
        if r.signature or r.status != "confirmed":
            await _record(session, job, wallet, f"{kind}_{step.label}", SubmitResult(r.signature, r.status, r.error), None)
            await session.commit()
        if r.status != "confirmed" and step.essential:
            break
    return results


def _job_status(job, results: list[StepResult]) -> None:
    _finish(job, [SubmitResult(r.signature, r.status, r.error) for r in results] or
            [SubmitResult(None, "failed", "nothing ran")])


def _preflight(kind: str, base: dict, plan: PadPlan, extra_warnings: list[str] | None = None) -> dict:
    pf = build_preflight(kind, {**base, **plan.summary, "platform_fee": "none"}, [*plan.warnings, *(extra_warnings or [])])
    pf["preflight"].update(plan.display)
    return pf


async def deploy(l: Launch, body, user, db, ex, dev: Wallet, adapter) -> dict:
    check_enabled(l.network)
    ctx = ctx_for(ex, l.network, dev)
    params = dict(l.launchpad_params or {})
    image_url = None
    if l.image_blob and get_settings().public_api_url:
        image_url = f"{get_settings().public_api_url.rstrip('/')}/public/launch-images/{l.id}"
    try:
        plan = await adapter.plan_create(ctx, l.network, l.name, l.symbol, native_raw(l.network, l.dev_buy_sol),
                                         description=l.description, image=l.image_blob, socials=l.socials or {},
                                         options=params, image_url=image_url)
    except AdapterError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"{adapter.id} couldn't be reached: {type(e).__name__}")
    base = {"launch_id": l.id, "network": l.network, "name": l.name, "symbol": l.symbol, "dev_wallet": dev.address,
            "dev_buy_sol": l.dev_buy_sol, "coin": unit(l.network), "launchpad": l.launchpad}
    pf = _preflight("deploy", base, plan)
    from .charges import attach_launch_fee
    pf = await attach_launch_fee(pf, db, user, ex, l, dev, body)
    if not body.confirm:
        return pf
    if pf["preflight"]["warnings"]:
        raise HTTPException(400, pf["preflight"]["warnings"][0])
    job, created = await begin_spend(db, user, body, pf, "deploy", l.id, pf["preflight"])
    if created:
        l.status = "deploying"
        await db.commit()

        async def work(session, j):
            launch = await session.get(Launch, l.id)
            j.status = "submitted"
            await session.commit()
            from .charges import pay_launch
            if not await pay_launch(session, ex, j, launch):
                return
            results = await run_plan(session, j, dev, "deploy", plan)
            got = await plan.finish(results) if plan.finish else {}
            if got.get("token"):
                launch.status, launch.mint_address, launch.deployed_at = "live", got["token"], utcnow()
                launch.launchpad_params = {**(launch.launchpad_params or {}), **got.get("params", {})}
            elif any(r.status == "timeout" for r in results) or (results and all(r.status == "confirmed" for r in results)):
                # Sent and possibly landed (or landed without a readable token address): never offer a second launch.
                launch.status = "deploying"
                launch.launchpad_params = {**(launch.launchpad_params or {}), "unconfirmed_since": utcnow().isoformat()}
                j.params = {**j.params, "error": "The launch may have gone through; check the dev wallet on the "
                            "explorer before trying again."}
            else:
                launch.status = "failed"
            launch_step = next((r for st, r in zip(plan.steps, results) if st.label == "launch"), None)
            if launch_step and launch_step.signature:
                launch.launchpad_params = {**(launch.launchpad_params or {}), "deploy_tx": launch_step.signature}
            _job_status(j, results)
            if launch.status == "live":
                from .charges import initial_buy_fee
                await initial_buy_fee(session, ex, j, launch, dev)
            await session.commit()

        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


async def _token_balance(ex, network: str, token: str, w: Wallet) -> int:
    return await xc.token_balance(ex, network, token, w.address)


async def trade(l: Launch, body, user, db, ex, wallets: list[Wallet], adapter) -> dict:
    check_enabled(l.network)
    params = dict(l.launchpad_params or {})
    plans: list[tuple[Wallet, PadPlan]] = []
    warnings: list[str] = []
    from .charges import trade_terms
    fee_terms = await trade_terms(db, l.network)
    if l.launchpad == "meme-cooking" and params.get("meme_id") is None:
        raise HTTPException(409, "this launch's Meme.Cooking auction isn't known yet; refresh once it has confirmed")
    for w in wallets:
        if w.network != l.network:
            raise HTTPException(400, f"{w.address} is a {w.network} wallet; this token is on {l.network}")
        ctx = ctx_for(ex, l.network, w)
        try:
            if body.side == "buy":
                amount = native_raw(l.network, body.amount)
            elif l.launchpad == "meme-cooking":
                amount = await adapter.my_deposit(ctx, l.network, params["meme_id"]) * int(round(body.amount * 100)) // 10_000
            else:
                amount = await _token_balance(ex, l.network, l.mint_address, w) * int(round(body.amount * 100)) // 10_000
            if amount <= 0:
                warnings.append(f"{w.address} has nothing to {'withdraw' if l.launchpad == 'meme-cooking' else 'sell'}.")
                continue
            kw = {"meme_id": params.get("meme_id")} if l.launchpad == "meme-cooking" else {}
            plan = await adapter.plan_trade(ctx, l.network, l.mint_address, body.side, amount, body.slippage_pct, **kw)
        except AdapterError as e:
            raise HTTPException(400, str(e))
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"{adapter.id} couldn't be reached: {type(e).__name__}")
        warnings += plan.warnings
        plans.append((w, plan))
    pf = build_preflight("trade", {
        "launch_id": l.id, "network": l.network, "token": l.mint_address, "side": body.side, "amount": body.amount,
        "amount_unit": f"{unit(l.network)} per wallet" if body.side == "buy" else
        ("% of each wallet's deposit" if l.launchpad == "meme-cooking" else "% of each wallet's tokens"),
        "wallets": [w.address for w, _ in plans], "slippage_pct": body.slippage_pct,
        "platform_fee": "1% of the trade notional; 30% of the fee goes to your referrer when applicable",
        "trade_fee_terms": fee_terms,
        "per_wallet": [p.summary for _, p in plans]}, warnings)
    pf["preflight"]["estimates"] = [p.display for _, p in plans]
    if not body.confirm:
        return pf
    if warnings or not plans:
        raise HTTPException(400, warnings[0] if warnings else "nothing to trade")
    job, created = await begin_spend(db, user, body, pf, f"trade_{body.side}", l.id, pf["preflight"])
    if created:
        async def work(session, j):
            j.status = "submitted"
            await session.commit()
            results: list[StepResult] = []
            for w, plan in plans:
                before = await xc.native_balance(ex, l.network, w.address)
                outcome = await run_plan(session, j, w, f"trade_{body.side}", plan)
                results += outcome
                if outcome and all(r.status == "confirmed" for r in outcome):
                    from .charges import charge_trade
                    after = await xc.native_balance(ex, l.network, w.address)
                    # Native proceeds are measured; protocol fees reduce the proceeds before Helm's 1%.
                    basis = native_raw(l.network, body.amount) if body.side == "buy" else max(0, after - before)
                    fee = await charge_trade(session, ex, j, w, basis)
                    if fee.status != "confirmed":
                        results.append(StepResult(fee.signature, fee.status, fee.error))
                        j.params = {**j.params, "fee_outstanding": True, "error": fee.error}
            _job_status(j, results)
            await session.commit()

        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


async def claim(l: Launch, body, user, db, ex, w: Wallet, adapter) -> dict:
    check_enabled(l.network)
    if not hasattr(adapter, "plan_claim"):
        raise HTTPException(400, f"{adapter.id} has nothing to claim")
    if (l.launchpad_params or {}).get("meme_id") is None:
        raise HTTPException(409, "this launch's Meme.Cooking auction isn't known yet")
    try:
        plan = await adapter.plan_claim(ctx_for(ex, l.network, w), l.network, l.mint_address,
                                        (l.launchpad_params or {}).get("meme_id"))
    except AdapterError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"{adapter.id} couldn't be reached: {type(e).__name__}")
    pf = _preflight("claim_fees", {"launch_id": l.id, "wallet": w.address}, plan)
    if not body.confirm:
        return pf
    if pf["preflight"]["warnings"]:
        raise HTTPException(400, pf["preflight"]["warnings"][0])
    job, created = await begin_spend(db, user, body, pf, "claim_fees", l.id, pf["preflight"])
    if created:
        async def work(session, j):
            j.status = "submitted"
            await session.commit()
            _job_status(j, await run_plan(session, j, w, "claim", plan))
            await session.commit()

        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


async def workspace(l: Launch, ex, links, wallets: list[Wallet]) -> dict:
    """Balances on the launch's own network. No holder map or market data: no provider covers these here."""
    out_wallets, meta, err = [], None, None
    net = EXTRA_NETWORKS[l.network]
    try:
        if l.mint_address and l.launchpad != "meme-cooking":
            meta = await xc.token_metadata(ex, l.network, l.mint_address, l.mint_address)
        for w, lk in zip(wallets, links):
            native = await xc.native_balance(ex, l.network, w.address)
            tok = 0
            if meta:
                tok = await xc.token_balance(ex, l.network, l.mint_address, w.address)
            out_wallets.append({"id": w.id, "address": w.address, "label": w.label, "role": lk.role,
                                "sol": native / 10**net.decimals, "token_raw": tok,
                                "tokens": tok / 10 ** meta["decimals"] if meta else 0})
    except Exception as e:
        err = str(e)[:200]
    return {"wallets": out_wallets, "transparency": None, "rpc_error": err,
            "explorer": {"token": net.explorer["token"] + l.mint_address if l.mint_address else None,
                         "account": net.explorer["account"]},
            "market_note": f"No chart or pool data: Helm has no market-data provider verified for {net.name} yet."}


async def recheck(l: Launch, db, ex, adapter) -> None:
    """A launch that was sent but not confirmed (or whose token couldn't be read): look for it again by its
    transaction. It goes live when found, failed only when the chain says the transaction failed."""
    tx = (l.launchpad_params or {}).get("deploy_tx")
    if l.status != "deploying" or not tx or not l.dev_wallet_id or l.network not in extra_enabled():
        return
    dev = await db.get(Wallet, l.dev_wallet_id)
    try:
        got = await adapter.resolve(ctx_for(ex, l.network, dev), l.network, tx, l.symbol)
    except Exception:
        return
    if got.get("token"):
        l.status, l.mint_address, l.deployed_at = "live", got["token"], l.deployed_at or utcnow()
        l.launchpad_params = {**(l.launchpad_params or {}), **got.get("params", {})}
    elif got.get("failed"):
        l.status = "failed"
    await db.commit()
