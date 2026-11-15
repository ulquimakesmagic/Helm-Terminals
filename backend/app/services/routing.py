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
    """(symbol, Relay currency address) for "native", "stable", or a token contract/mint address."""
    if asset == "native":
        return native_coin(network), native_currency(network)
    if asset == "stable":
        return stable_for(network)
    return asset[:6], asset  # arbitrary token: the caller resolves a display symbol when it has one


async def asset_decimals(ex: Executor, network: str, asset: str) -> int:
    if asset == "native":
        return 9 if network == "solana" else 18
    token = stable_for(network)[1] if asset == "stable" else asset
    return await token_decimals(ex, network, token)


async def asset_balance(ex: Executor, network: str, asset: str, address: str) -> int:
    if asset == "native":
        return int(await (ex.rpc.get_balance(address) if network == "solana" else ex.evm(network).rpc.get_balance(address)))
    token = stable_for(network)[1] if asset == "stable" else asset
    if network == "solana":
        return int(await ex.rpc.get_token_balance(address, token))
    return int(await ex.evm(network).rpc.erc20_balance(token, address))


# Selectors that move or authorise ERC-20/721/1155 balances. A Relay step may only use them on the route's own
# input token, within the approved amount; anywhere else they could move assets the user never offered.
_ASSET_SELECTORS = {
    bytes.fromhex("a9059cbb"): "transfer", bytes.fromhex("23b872dd"): "transferFrom",
    bytes.fromhex("095ea7b3"): "approve", bytes.fromhex("39509351"): "increaseAllowance",
    bytes.fromhex("d505accf"): "permit", bytes.fromhex("a22cb465"): "setApprovalForAll",
    bytes.fromhex("42842e0e"): "safeTransferFrom", bytes.fromhex("f242432a"): "safeTransferFrom",
}


def route_input(network: str, asset: str) -> str | None:
    """The route's input token address, or None when it spends the chain's native coin."""
    if asset == "native":
        return None
    return stable_for(network)[1] if asset == "stable" else asset


def check_route_call(call: EvmCall, wallet: str, input_token: str | None, amount_raw: int,
                     native_fee_raw: int = 0) -> str | None:
    """Bound a provider-built EVM route step to the approved input (audit HT-02).

    Native input: the step may carry at most `amount_raw` wei and may not touch token balances. Token input: the
    step carries no native value; on the input token it may only approve, increase an allowance or transfer up to
    `amount_raw`; on any other contract it may not call a balance-moving selector. The router then pulls at most
    the approved allowance. This bounds loss to the amount the user chose to route, whatever the provider sends.
    `native_fee_raw` is a provider's approved flat fee in the native coin (deBridge's fixFee), allowed on top."""
    from eth_abi import decode

    if call.to is None:
        return "the route asks to deploy a contract"
    data = call.data or b""
    selector = data[:4]
    if input_token is None:
        if call.value > amount_raw + native_fee_raw:
            return "the route step sends more native coin than approved"
        if selector in _ASSET_SELECTORS:
            return f"a native-coin route step calls {_ASSET_SELECTORS[selector]} on a token"
        return None
    if call.value > native_fee_raw:
        return "a token route step also sends native coin beyond the approved fee"
    if call.to.lower() == input_token.lower():
        if selector not in (bytes.fromhex("095ea7b3"), bytes.fromhex("39509351"), bytes.fromhex("a9059cbb")) \
                or len(data) != 68:
            return "the route calls an unexpected function on the input token"
        try:
            _, value = decode(["address", "uint256"], data[4:])
        except Exception:
            return "the route's token call is malformed"
        if value > amount_raw:
            return "the route moves or approves more of the input token than approved"
        return None
    if selector in _ASSET_SELECTORS:
        return f"the route calls {_ASSET_SELECTORS[selector]} on a contract other than its input token"
    return None


def route_limits(w: Wallet, from_asset: str, amount_raw: int, native_fee_raw: int = 0):
    """Solana spend bound for a provider-built origin transaction (audit HT-01), plus the approved flat fee."""
    from .solana_guard import TRADE_OVERHEAD_LAMPORTS, SpendLimits, budget

    token = route_input("solana", from_asset)
    if token is None:
        return SpendLimits(w.address, budget(amount_raw, 0, TRADE_OVERHEAD_LAMPORTS + native_fee_raw), label="route")
    return SpendLimits(w.address, TRADE_OVERHEAD_LAMPORTS + native_fee_raw, token_out={token: amount_raw}, label="route")


def _rebuild_serialized(data: dict, w: Wallet, blockhash: str) -> bytes | str:
    """A provider's whole Solana transaction (deBridge): it must be a v0 message paid by `w`; it gets a fresh
    blockhash and empty signatures. Returns the unsigned bytes, or a reason to refuse."""
    from solders.hash import Hash
    from solders.message import MessageV0
    from solders.signature import Signature
    from solders.transaction import VersionedTransaction

    try:
        s = data["serializedTx"]
        tx = VersionedTransaction.from_bytes(bytes.fromhex(s[2:] if s.startswith("0x") else s))
    except (KeyError, TypeError, ValueError):
        return "the provider returned a malformed Solana transaction"
    msg = tx.message
    if not isinstance(msg, MessageV0):
        return "the provider returned a legacy Solana transaction"
    if not msg.account_keys or str(msg.account_keys[0]) != w.address:
        return "the provider's Solana transaction is paid by another wallet"
    fresh = MessageV0(msg.header, msg.account_keys, Hash.from_string(blockhash), msg.instructions,
                      msg.address_table_lookups)
    return bytes(VersionedTransaction.populate(fresh, [Signature.default()] * msg.header.num_required_signatures))


async def send_item(ex: Executor, w: Wallet, data: dict, job: Job | None = None,
                    kind: str = "bridge", max_allowance: int | None = None,
                    from_asset: str | None = None, amount_raw: int | None = None,
                    native_fee_raw: int = 0) -> SubmitResult:
    """Send one Relay step item from wallet `w`. `max_allowance` caps provider-built ERC-20 approvals
    at the quoted need (tx_guard), so a compromised Relay response can't grant a route's spender more
    than the route was quoted for. With `from_asset`/`amount_raw` every step is bounded to the approved
    input: Solana steps by simulated wallet effects, EVM steps by `check_route_call`."""
    bounded = from_asset is not None and amount_raw is not None
    if w.network == "solana" and "serializedTx" in data:
        if not bounded:
            return SubmitResult(None, "failed", "a provider-built Solana transaction needs approved bounds")
        raw = _rebuild_serialized(data, w, await ex.rpc.get_latest_blockhash())
        if isinstance(raw, str):
            return SubmitResult(None, "failed", f"refused before signing: {raw}")
        limits = route_limits(w, from_asset, amount_raw, native_fee_raw)
        if job is None:
            return await ex.submit(raw, {w.address: w.signer_ref or ""}, limits=limits)
        return await submit_tracked(ex, job, w, kind, raw, {w.address: w.signer_ref or ""}, limits=limits)
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
            if table is None:
                return SubmitResult(None, "failed", "Relay lookup table could not be resolved; no transaction was signed")
            tables.append(table)
        raw = build_unsigned(Pubkey.from_string(w.address), ixs, await ex.rpc.get_latest_blockhash(), tables)
        limits = route_limits(w, from_asset, amount_raw, native_fee_raw) if bounded else None
        if job is None:  # test/utility call without a user-facing job record
            return await ex.submit(raw, {w.address: w.signer_ref or ""}, limits=limits)
        return await submit_tracked(ex, job, w, kind, raw, {w.address: w.signer_ref or ""}, limits=limits)
    hexdata = data.get("data") or "0x"
    from ..chains.relay import relay_chain_id
    if data.get("chainId") != relay_chain_id(w.network) or (data.get("from") and data["from"].lower() != w.address.lower()):
        return SubmitResult(None, "failed", "Relay transaction targets a different chain or signer")
    try:
        call = EvmCall(to=data["to"], data=bytes.fromhex(hexdata[2:] if hexdata.startswith("0x") else hexdata),
                       value=int(data.get("value") or 0))
    except (KeyError, TypeError, ValueError):
        return SubmitResult(None, "failed", "Relay returned a malformed transaction; nothing was signed")
    if bounded and (reason := check_route_call(call, w.address, route_input(w.network, from_asset), amount_raw,
                                               native_fee_raw)):
        return SubmitResult(None, "failed", f"refused before signing: {reason}")
    sender = ex.evm(w.network)
    if job is None:
        return await sender.send(w.address, w.signer_ref or "", call, max_allowance=max_allowance,
                                 require_contract_recipients=True)
    return await send_evm_tracked(sender, job, w, kind, call, max_allowance=max_allowance,
                                  require_contract_recipients=True)


async def quote(ex: Executor, src: Wallet, dst: Wallet, from_asset: str, to_asset: str, amount_raw: int,
                slippage_pct: float, provider: str | None = None) -> dict:
    """A route quote from the first provider that can serve it, or from `provider` only (confirmation)."""
    return await ex.relay.quote(
        user=src.address, recipient=dst.address, from_network=src.network, to_network=dst.network,
        from_currency=currency(src.network, from_asset)[1], to_currency=currency(dst.network, to_asset)[1],
        amount=amount_raw, slippage_pct=slippage_pct, **({"provider": provider} if provider else {}))


def provider_terms(q: dict | None, network: str) -> tuple[float, str | None]:
    """(flat provider fee in the source chain's coin, Helm fee note when it differs from the default provider's).
    Only deBridge quotes carry either; the fee note is what that order actually charges."""
    if not q or q.get("provider") != "debridge":
        return 0.0, None
    fee = int(q.get("nativeFeeRaw") or 0) / 10 ** (9 if network == "solana" else 18)
    bps = int(q.get("helmFeeBps") or 0)
    return fee, (f"{bps / 100:g}% of the amount sent" if bps else "none")


def provider_label(ex: Executor, q: dict | None) -> str:
    p = (q or {}).get("provider")
    return ex.relay.label_for(p) if hasattr(ex.relay, "label_for") else "Relay (relay.link)"


async def wait_for_fill(ex: Executor, request_id: str | None) -> str:
    """Poll the route's provider until the destination fill lands (or give up and leave it 'pending')."""
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
                        key: str | None = None, approved_bounds: dict | None = None) -> tuple[SubmitResult, str]:
    """Quote, send and wait. Returns (outcome, Relay status). Transactions are recorded on `job` under `kind`;
    the quote, request id and status go in job.params (under `key` when given). The caller finishes the job."""

    def note(**kw) -> None:
        if key:
            job.params = {**job.params, key: {**(job.params or {}).get(key, {}), **kw}}
        else:
            job.params = {**job.params, **kw}

    try:
        if not approved_bounds:
            raise AdapterError("This route has no approved output bound; request a new preview")
        # Fresh quote (they expire fast) from the provider the preview used; older bounds mean the default one.
        q = await quote(ex, src, dst, from_asset, to_asset, amount_raw, slippage_pct,
                        provider=approved_bounds.get("provider") or getattr(ex.relay, "id", None))
        from .quote_approval import check_relay_bounds, check_relay_recipient

        check_relay_bounds(q, approved_bounds, currency(dst.network, to_asset)[1], dst.network)
        check_relay_recipient(q, dst.address, dst.network)
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
    from .execution_journal import require_steps
    await require_steps(session, job, src.id, [
        kind if step.get("id") == "deposit" else f"{kind}_{step.get('id')}"[:24]
        for step in q["steps"] for item in step.get("items") or [] if item.get("status") != "complete"])
    last = None
    for step in q["steps"]:
        for item in step.get("items") or []:
            if item.get("status") == "complete":
                continue
            try:
                check_relay_bounds(q, approved_bounds, currency(dst.network, to_asset)[1], dst.network)
            except AdapterError as e:
                return SubmitResult(last.signature if last else None, "timeout" if last else "failed", str(e)), "not_sent"
            tx_kind = kind if step.get("id") == "deposit" else f"{kind}_{step.get('id')}"[:24]
            res = await send_item(ex, src, item.get("data") or {}, job, tx_kind, max_allowance=amount_raw,
                                  from_asset=from_asset, amount_raw=amount_raw,
                                  native_fee_raw=int(q.get("nativeFeeRaw") or 0) if q.get("provider") == "debridge" else 0)
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
        return SubmitResult(last.signature, "failed", f"{provider_label(ex, q)} reported {status}; a refund goes back "
                            "to the source wallet"), status
    if status not in DONE:
        return SubmitResult(last.signature, "timeout", "Origin transaction confirmed; destination settlement is still unconfirmed."), status
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
