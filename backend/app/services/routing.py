"""Relay routes between your own wallets: USDC (USDG on Robinhood Chain) into any chain's coin, or back.

Shared by the Route funds screen (/bridge) and by launches that fund their dev wallet from your dollar balance.
A route is: fresh quote -> send each origin transaction from the source wallet -> wait for Relay's fill.
"""

import asyncio
import time

from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey

from ..chains.base import AdapterError
from ..chains.relay import DONE, FAILED, native_currency
from ..chains.solana import build_unsigned
from ..config import get_settings
from ..models import Job, Wallet
from .chain_balances import native_coin, stable_for, token_decimals
from .evm_exec import EvmCall
from .executor import Executor, SubmitResult, _record, send_evm_tracked, submit_tracked

ROUTE_GAS = 350_000  # approve + deposit on the origin chain


def currency(network: str, asset: str) -> tuple[str, str]:
    """(symbol, Relay currency address) for "native" or "stable" on a chain."""
    if asset == "native":
        return native_coin(network), native_currency(network)
    return stable_for(network)


async def asset_decimals(ex: Executor, network: str, asset: str) -> int:
    if asset == "native":
        return 9 if network == "solana" else 18
    return await token_decimals(ex, network, stable_for(network)[1])


async def asset_balance(ex: Executor, network: str, asset: str, address: str) -> int:
    if asset == "native":
        return int(await (ex.rpc.get_balance(address) if network == "solana" else ex.evm(network).rpc.get_balance(address)))
    token = stable_for(network)[1]
    if network == "solana":
        return int(await ex.rpc.get_token_balance(address, token))
    return int(await ex.evm(network).rpc.erc20_balance(token, address))


async def send_item(ex: Executor, w: Wallet, data: dict, job: Job | None = None,
                    kind: str = "bridge") -> SubmitResult:
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
        if job is None:  # test/utility call without a user-facing job record
            return await ex.submit(raw, {w.address: w.signer_ref or ""})
        return await submit_tracked(ex, job, w, kind, raw, {w.address: w.signer_ref or ""})
    hexdata = data.get("data") or "0x"
    call = EvmCall(to=data["to"], data=bytes.fromhex(hexdata[2:] if hexdata.startswith("0x") else hexdata),
                   value=int(data.get("value") or 0))
    sender = ex.evm(w.network)
    if job is None:
        return await sender.send(w.address, w.signer_ref or "", call)
    return await send_evm_tracked(sender, job, w, kind, call)


async def quote(ex: Executor, src: Wallet, dst: Wallet, from_asset: str, to_asset: str, amount_raw: int,
                slippage_pct: float) -> dict:
    return await ex.relay.quote(
        user=src.address, recipient=dst.address, from_network=src.network, to_network=dst.network,
        from_currency=currency(src.network, from_asset)[1], to_currency=currency(dst.network, to_asset)[1],
        amount=amount_raw, slippage_pct=slippage_pct)


async def wait_for_fill(ex: Executor, request_id: str | None) -> str:
    """Poll Relay until the destination fill lands (or give up and leave it 'pending')."""
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


async def execute_route(session, ex: Executor, job: Job, src: Wallet, dst: Wallet, from_asset: str, to_asset: str,
                        amount_raw: int, slippage_pct: float, kind: str = "bridge",
                        key: str | None = None) -> tuple[SubmitResult, str]:
    """Quote, send and wait. Returns (outcome, Relay status). Transactions are recorded on `job` under `kind`;
    the quote, request id and status go in job.params (under `key` when given). The caller finishes the job."""

    def note(**kw) -> None:
        if key:
            job.params = {**job.params, key: {**(job.params or {}).get(key, {}), **kw}}
        else:
            job.params = {**job.params, **kw}

    try:
        q = await quote(ex, src, dst, from_asset, to_asset, amount_raw, slippage_pct)  # fresh: quotes expire fast
    except AdapterError as e:
        res = SubmitResult(None, "failed", str(e))
        await _record(session, job, src, kind, res, None)
        note(route_status="not_sent")
        await session.commit()
        return res, "not_sent"
    info = ex.relay.summarize(q)
    # Helm's fee terms as quoted (a settings change while the route fills doesn't alter what this route paid).
    fee_terms = (ex.relay.fee_bps, ex.relay.fee_recipient)
    note(request_id=info["request_id"], quote=info, route_status="sending")
    await session.commit()
    last = None
    for step in q["steps"]:
        for item in step.get("items") or []:
            if item.get("status") == "complete":
                continue
            tx_kind = kind if step.get("id") == "deposit" else f"{kind}_{step.get('id')}"[:24]
            res = await send_item(ex, src, item.get("data") or {}, job, tx_kind)
            await _record(session, job, src, tx_kind, res, None)
            await session.commit()
            last = res
            if res.status != "confirmed":
                note(route_status="not_sent")
                await session.commit()
                return res, "not_sent"
    status = await wait_for_fill(ex, info["request_id"])
    note(route_status=status)
    await session.commit()
    if last is None:
        return SubmitResult(None, "failed", "nothing to send"), status
    if status in FAILED:
        return SubmitResult(last.signature, "failed", f"Relay reported {status}; a refund goes back to the source wallet"), status
    if status in DONE and last.status == "confirmed":
        # Helm's Relay app fee, if the quote carried one: Relay holds it for the treasury until claimed, so it goes in
        # the ledger as claimable. A route that failed, was refunded or hasn't filled records nothing.
        from .revenue import route_fee_event

        ev = route_fee_event(q, fee_terms[1], job.id, job.launch_id, job.user_id,
                             info["request_id"] or last.signature)
        if ev is not None and fee_terms[0]:
            session.add(ev)
            await session.commit()
    return last, status
