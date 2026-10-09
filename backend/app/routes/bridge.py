"""Routes: turn a dollar balance (USDC, or USDG on Robinhood Chain) into gas on any chain, or move value between
chains, through Relay. Keep the main balance in USDC and top up whichever chain you're launching on.

Both ends are wallets on your own account. The preview shows the fresh quote (what should arrive, the minimum
after slippage, Relay's fees and Helm's route fee); confirming re-quotes and sends from the source wallet.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.base import AdapterError
from ..chains.relay import DONE, FAILED
from ..db import get_db
from ..deps import SpendConfirm, begin_spend, current_user, find_replay, get_executor, owned_wallet, run_job
from ..models import Job, User
from ..services.chain_balances import chain_name, fee_reserve, native_coin
from ..services.executor import Executor, _finish
from ..services.preflight import build_preflight
from ..services.routing import (ROUTE_GAS, asset_balance, asset_decimals, currency, execute_route, provider_label,
                                send_item)
from ..services.routing import quote as route_quote

router = APIRouter()


class RouteBody(SpendConfirm):
    from_wallet_id: str
    to_wallet_id: str
    from_asset: Literal["stable", "native"] = "stable"
    to_asset: Literal["native", "stable"] = "native"
    amount: float = Field(gt=0)  # in the from asset
    slippage_pct: float = Field(default=1.0, ge=0.1, le=5)


# Kept as module names for callers and tests that used them here.
_currency = currency
_send_item = send_item


@router.post("/bridge")
async def route(body: RouteBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                ex: Executor = Depends(get_executor)):
    if (prior := await find_replay(db, user, body)):
        return prior
    src = await owned_wallet(db, user, body.from_wallet_id)
    dst = await owned_wallet(db, user, body.to_wallet_id)
    from .wallets import _core_only

    _core_only(src.network)
    _core_only(dst.network)
    if src.network == dst.network and body.from_asset == body.to_asset:
        raise HTTPException(400, "that's a plain transfer; use disperse or consolidate for same-chain moves")
    from_sym = _currency(src.network, body.from_asset)[0]
    to_sym = _currency(dst.network, body.to_asset)[0]
    warnings: list[str] = []
    try:
        dec = await asset_decimals(ex, src.network, body.from_asset)
        amount_raw = int(round(body.amount * 10**dec))
        bal = await asset_balance(ex, src.network, body.from_asset, src.address)
        gas = await fee_reserve(ex, src.network, 2, ROUTE_GAS)
        native_bal = await asset_balance(ex, src.network, "native", src.address) / 10 ** (9 if src.network == "solana" else 18)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Couldn't read balances on {chain_name(src.network)}: {type(e).__name__}")
    need_native = gas + (body.amount if body.from_asset == "native" else 0)
    if bal < amount_raw:
        warnings.append(f"{src.label or src.address[:8]} has {bal / 10**dec:,.6g} {from_sym}, less than the "
                        f"{body.amount:g} {from_sym} to route.")
    if native_bal < need_native:
        warnings.append(f"{src.label or src.address[:8]} needs about {need_native:.6f} {native_coin(src.network)} "
                        f"on {chain_name(src.network)} for network fees"
                        + (" plus the amount" if body.from_asset == "native" else "") + ".")
    # Largest amount that passes these balance checks right now: the whole stablecoin balance (gas is paid in
    # the native coin) or the native balance minus the fee reserve. The client's MAX button fills this.
    max_send = max(native_bal - gas, 0.0) if body.from_asset == "native" else bal / 10**dec
    summary = {
        "from": {"label": src.label or "Wallet", "address": src.address}, "from_chain": chain_name(src.network),
        "to": {"label": dst.label or "Wallet", "address": dst.address}, "to_chain": chain_name(dst.network),
        "send": body.amount, "send_asset": from_sym, "receive_asset": to_sym, "slippage_pct": body.slippage_pct,
        "max_send": round(max_send, 12),
        "provider": getattr(ex.relay, "label", "Relay (relay.link)"), "own_wallets_only": True,
        "platform_fee": ex.relay.fee_note,
    }
    from ..services.quote_approval import issue, verify, relay_bounds, check_relay_bounds

    scope = {"from_wallet": src.id, "to_wallet": dst.id, "from_network": src.network,
             "to_network": dst.network, "from_address": src.address, "to_address": dst.address,
             "from_asset": body.from_asset, "to_asset": body.to_asset, "amount_raw": str(amount_raw),
             "slippage_pct": body.slippage_pct, "fee_bps": ex.relay.fee_bps,
             "fee_recipient": ex.relay.fee_recipient}
    approved = verify(body.quote_approval, user.id, scope) if body.confirm else None
    quote_bounds = None
    quote = None
    try:
        quote = await route_quote(ex, src, dst, body.from_asset, body.to_asset, amount_raw, body.slippage_pct,
                                  provider=approved.get("provider") if approved else None)
        summary["provider"] = provider_label(ex, quote)
        quote_bounds = relay_bounds(quote, destination_asset=currency(dst.network, body.to_asset)[1],
                                    destination_network=dst.network)
        if approved:
            check_relay_bounds(quote, approved, currency(dst.network, body.to_asset)[1], dst.network)
    except AdapterError as e:
        warnings.append(str(e))
    if approved:
        quote_bounds = {k: v for k, v in approved.items() if k != "expires"}
    summary["quote_bounds"] = quote_bounds
    pf = build_preflight("bridge", summary, warnings)
    if quote:
        # Estimate may move; the authenticated original floor is part of the confirmation hash.
        pf["preflight"]["quote"] = ex.relay.summarize(quote)
    if not body.confirm:
        if quote_bounds and not warnings:
            pf["quote_approval"] = issue(user.id, scope, quote_bounds)
        return pf
    if pf["preflight"]["warnings"]:
        raise HTTPException(400, pf["preflight"]["warnings"][0])
    job, created = await begin_spend(db, user, body, pf, "bridge", None, {**pf["preflight"], "network": src.network})
    if created:
        async def work(session, j):
            j.status = "submitted"
            await session.commit()
            res, _ = await execute_route(session, ex, j, src, dst, body.from_asset, body.to_asset, amount_raw,
                                         body.slippage_pct, approved_bounds=approved)
            _finish(j, [res])
            await session.commit()

        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


@router.get("/bridge/{job_id}")
async def route_status(job_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                       ex: Executor = Depends(get_executor)):
    """Live status of a route: the provider's view of the destination fill."""
    job = (await db.execute(select(Job).where(Job.id == job_id, Job.user_id == user.id, Job.kind == "bridge"))
           ).scalar_one_or_none()
    if not job:
        raise HTTPException(404, "route not found")
    request_id = (job.params or {}).get("request_id")
    out = {"job_id": job.id, "job_status": job.status, "request_id": request_id,
           "route_status": (job.params or {}).get("route_status"), "quote": (job.params or {}).get("quote")}
    if request_id and out["route_status"] not in DONE | FAILED:
        try:
            live = await ex.relay.status(request_id)
            out["route_status"] = live.get("status") or out["route_status"]
            out["destination_txs"] = live.get("txHashes") or []
            if out["route_status"] != (job.params or {}).get("route_status"):
                job.params = {**job.params, "route_status": out["route_status"]}
                await db.commit()
        except AdapterError as e:
            out["error"] = str(e)
    return out
