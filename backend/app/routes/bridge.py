"""Routes: turn a dollar balance (USDC, or USDG on Robinhood Chain) into gas on any chain, or move value between
chains, through Relay. Keep the main balance in USDC and top up whichever chain you're launching on.

Both ends are wallets on your own account. The preview shows the fresh quote (what should arrive, the minimum
after slippage, Relay's fees and Helm's route fee); confirming re-quotes and sends from the source wallet.
"""

import asyncio
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.base import AdapterError
from ..chains.relay import DONE, FAILED, native_currency
from ..chains.solana import build_unsigned
from ..config import get_settings
from ..db import get_db
from ..deps import SpendConfirm, begin_spend, current_user, find_replay, get_executor, owned_wallet, run_job
from ..models import Job, User, Wallet
from ..services.chain_balances import chain_name, fee_reserve, native_coin, stable_for
from ..services.evm_exec import EvmCall
from ..services.executor import Executor, SubmitResult, _finish, _record
from ..services.preflight import build_preflight

router = APIRouter()
ROUTE_GAS = 350_000  # approve + deposit on the origin chain


class RouteBody(SpendConfirm):
    from_wallet_id: str
    to_wallet_id: str
    from_asset: Literal["stable", "native"] = "stable"
    to_asset: Literal["native", "stable"] = "native"
    amount: float = Field(gt=0)  # in the from asset
    slippage_pct: float = Field(default=1.0, ge=0.1, le=5)


def _currency(network: str, asset: str) -> tuple[str, str]:
    """(symbol, Relay currency address)."""
    if asset == "native":
        return native_coin(network), native_currency(network)
    return stable_for(network)


async def _decimals(ex: Executor, network: str, asset: str) -> int:
    if asset == "native":
        return 9 if network == "solana" else 18
    token = stable_for(network)[1]
    if network == "solana":
        return (await ex.rpc.get_token_supply(token))[1]
    return int((await ex.evm(network).rpc.erc20_meta(token))["decimals"])


async def _balance(ex: Executor, network: str, asset: str, address: str) -> int:
    if asset == "native":
        return int(await (ex.rpc.get_balance(address) if network == "solana" else ex.evm(network).rpc.get_balance(address)))
    token = stable_for(network)[1]
    if network == "solana":
        return int(await ex.rpc.get_token_balance(address, token))
    return int(await ex.evm(network).rpc.erc20_balance(token, address))


async def _send_item(ex: Executor, w: Wallet, data: dict) -> SubmitResult:
    """Send one Relay step item from wallet `w`."""
    if w.network == "solana":
        ixs = [Instruction(Pubkey.from_string(i["programId"]), bytes.fromhex(i.get("data") or ""),
                           [AccountMeta(Pubkey.from_string(k["pubkey"]), bool(k.get("isSigner")),
                                        bool(k.get("isWritable"))) for k in i.get("keys") or []])
               for i in data.get("instructions") or []]
        if not ixs:
            return SubmitResult(None, "failed", "Relay returned an empty Solana transaction")
        tables = []
        for addr in data.get("addressLookupTableAddresses") or []:
            table = await ex.rpc.get_lookup_table(addr)
            if table:
                tables.append(table)
        raw = build_unsigned(Pubkey.from_string(w.address), ixs, await ex.rpc.get_latest_blockhash(), tables)
        return await ex.submit(raw, {w.address: w.signer_ref or ""})
    hexdata = data.get("data") or "0x"
    call = EvmCall(to=data["to"], data=bytes.fromhex(hexdata[2:] if hexdata.startswith("0x") else hexdata),
                   value=int(data.get("value") or 0))
    return await ex.evm(w.network).send(w.address, w.signer_ref or "", call)


async def _quote(ex: Executor, body: RouteBody, src: Wallet, dst: Wallet, amount_raw: int) -> dict:
    return await ex.relay.quote(
        user=src.address, recipient=dst.address, from_network=src.network, to_network=dst.network,
        from_currency=_currency(src.network, body.from_asset)[1], to_currency=_currency(dst.network, body.to_asset)[1],
        amount=amount_raw, slippage_pct=body.slippage_pct)


@router.post("/bridge")
async def route(body: RouteBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                ex: Executor = Depends(get_executor)):
    if (prior := await find_replay(db, user, body)):
        return prior
    src = await owned_wallet(db, user, body.from_wallet_id)
    dst = await owned_wallet(db, user, body.to_wallet_id)
    if src.network == dst.network and body.from_asset == body.to_asset:
        raise HTTPException(400, "that's a plain transfer; use disperse or consolidate for same-chain moves")
    from_sym = _currency(src.network, body.from_asset)[0]
    to_sym = _currency(dst.network, body.to_asset)[0]
    warnings: list[str] = []
    try:
        dec = await _decimals(ex, src.network, body.from_asset)
        amount_raw = int(round(body.amount * 10**dec))
        bal = await _balance(ex, src.network, body.from_asset, src.address)
        gas = await fee_reserve(ex, src.network, 2, ROUTE_GAS)
        native_bal = await _balance(ex, src.network, "native", src.address) / 10 ** (9 if src.network == "solana" else 18)
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
    summary = {
        "from": {"label": src.label or "Wallet", "address": src.address}, "from_chain": chain_name(src.network),
        "to": {"label": dst.label or "Wallet", "address": dst.address}, "to_chain": chain_name(dst.network),
        "send": body.amount, "send_asset": from_sym, "receive_asset": to_sym, "slippage_pct": body.slippage_pct,
        "provider": "Relay (relay.link)", "own_wallets_only": True,
        "platform_fee": ex.relay.fee_note,
    }
    pf = build_preflight("bridge", summary, warnings)
    quote = None
    try:
        quote = await _quote(ex, body, src, dst, amount_raw)
    except AdapterError as e:
        pf["preflight"]["warnings"].append(str(e))
    if quote:
        # The quote moves with prices, so it's shown but not part of the confirmation hash; slippage protects you.
        pf["preflight"]["quote"] = ex.relay.summarize(quote)
    if not body.confirm:
        return pf
    if pf["preflight"]["warnings"]:
        raise HTTPException(400, pf["preflight"]["warnings"][0])
    job, created = await begin_spend(db, user, body, pf, "bridge", None, {**pf["preflight"], "network": src.network})
    if created:
        async def work(session, j):
            j.status = "submitted"
            await session.commit()
            try:
                q = await _quote(ex, body, src, dst, amount_raw)  # fresh: quotes expire within seconds
            except AdapterError as e:
                res = SubmitResult(None, "failed", str(e))
                await _record(session, j, src, "bridge", res, None)
                _finish(j, [res])
                await session.commit()
                return
            info = ex.relay.summarize(q)
            j.params = {**j.params, "request_id": info["request_id"], "quote": info, "route_status": "sending"}
            await session.commit()
            last = None
            for step in q["steps"]:
                for item in step.get("items") or []:
                    if item.get("status") == "complete":
                        continue
                    res = await _send_item(ex, src, item.get("data") or {})
                    await _record(session, j, src, "bridge" if step.get("id") == "deposit" else f"bridge_{step.get('id')}"[:24],
                                  res, None)
                    await session.commit()
                    last = res
                    if res.status != "confirmed":
                        j.params = {**j.params, "route_status": "not_sent"}
                        _finish(j, [res])
                        await session.commit()
                        return
            status = await _wait(ex, info["request_id"])
            j.params = {**j.params, "route_status": status}
            if status in FAILED:
                last = SubmitResult(last.signature if last else None, "failed",
                                    f"Relay reported {status}; a refund goes back to the source wallet")
            _finish(j, [last or SubmitResult(None, "failed", "nothing to send")])
            await session.commit()

        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


async def _wait(ex: Executor, request_id: str | None) -> str:
    """Poll Relay until the destination fill lands (or give up and leave it 'pending' for the status endpoint)."""
    if not request_id:
        return "unknown"
    s = get_settings()
    deadline = time.monotonic() + s.route_wait_seconds
    status = "pending"
    while True:
        try:
            status = (await ex.relay.status(request_id)).get("status") or status
        except AdapterError:
            pass
        if status in DONE or status in FAILED or time.monotonic() >= deadline:
            return status
        await asyncio.sleep(s.route_poll_seconds)


@router.get("/bridge/{job_id}")
async def route_status(job_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                       ex: Executor = Depends(get_executor)):
    """Live status of a route: Relay's view of the destination fill."""
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
