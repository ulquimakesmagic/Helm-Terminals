"""Network registry and the Arc features Helm supports on a documented path.

GET  /chains                                   every network, whether it's enabled, and what Helm supports on it
GET  /chains/{network}/tokens/{token}           token metadata (ERC-20), matched by address
GET  /chains/{network}/wallets/{id}/balances    native balance plus requested/known tokens
GET  /chains/{network}/wallets/{id}/history     Helm's own transfers (Arc has no official history API)
POST /chains/{network}/transfer                 preview, then confirm, a native or token transfer

Launches, swaps, market data and cross-chain routes are not offered on these networks; the matrix says why.
"""

from decimal import Decimal, InvalidOperation, localcontext

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.base import AdapterError
from ..chains.evm import EVM_CHAINS
from ..chains.networks import (CAPABILITIES, EXTRA_NETWORKS, KNOWN_TOKENS, SOURCES, extra_enabled,
                               require_enabled)
from ..db import get_db
from ..deps import (SpendConfirm, begin_spend, current_user, find_replay, get_executor, owned_wallet, run_job,
                    valid_address_for)
from ..models import Job, Transaction, User, Wallet
from ..services import extra_chains as xc
from ..services.executor import Executor, SubmitResult, _finish, _record
from ..services.preflight import build_preflight

router = APIRouter()


def _net_out(n) -> dict:
    return {"id": n.id, "family": n.family, "name": n.name, "native": n.native,
            "decimals": n.decimals, "chain_id": n.chain_id, "explorer": n.explorer,
            "notes": n.notes,
            "enabled": n.id in extra_enabled(), "capabilities": _caps(n), "sources": SOURCES[n.family]}


def _caps(n) -> dict:
    from ..services.market import CODEX_NETWORK

    market = "implemented" if n.id in CODEX_NETWORK else "unavailable"
    return {**CAPABILITIES[n.family], "market_metrics": market, "market_charts": market}


@router.get("/chains")
async def chains():
    from ..services.market import CODEX_NETWORK
    def core_caps(network):
        market = "implemented" if network in CODEX_NETWORK else "unavailable"  # Codex is the only market source
        return {"wallets": "implemented", "launches": "launchpad_specific",
                "trading": "adapter_and_pool_specific", "cash_routes": "implemented",
                "market_metrics": market, "market_charts": market,
                "eligibility": "checked_per_action"}
    core = [{"id": "solana", "name": "Solana", "family": "solana", "enabled": True,
             "capabilities": core_caps("solana")}] + [
        {"id": k, "name": v["name"], "family": "evm", "enabled": True, "chain_id": v["chain_id"],
         "capabilities": core_caps(k)} for k, v in EVM_CHAINS.items()]
    return {"core": core, "extra": [_net_out(n) for n in EXTRA_NETWORKS.values()],
            "note": "Enabled networks are the ones the server lists in EXTRA_NETWORKS (mainnets by default; "
                    "ALLOW_MAINNET_EXTRA_NETWORKS=false turns every mainnet off)."}


@router.get("/chains/{network}/health")
async def network_health(network: str, user: User = Depends(current_user), ex: Executor = Depends(get_executor)):
    """A live read from the network's endpoint: Arc's chain id."""
    import time

    net = require_enabled(network)
    t0 = time.monotonic()
    try:
        got = int(await ex.evm(network).rpc.call("eth_chainId", []), 16)
        if got != net.chain_id:
            return {"network": network, "ok": False, "error": f"endpoint reports chain {got}, expected {net.chain_id}"}
    except Exception as e:
        return {"network": network, "ok": False, "error": _why(e)}
    return {"network": network, "ok": True, "latency_ms": round((time.monotonic() - t0) * 1000)}


def _token_ok(network: str, token: str) -> str:
    token = token.strip()
    if not valid_address_for(network, token):
        raise HTTPException(400, f"that isn't a valid {EXTRA_NETWORKS[network].name} token address")
    return token


@router.get("/chains/{network}/tokens/{token}")
async def token_info(network: str, token: str, user: User = Depends(current_user), ex: Executor = Depends(get_executor)):
    net = require_enabled(network)
    token = _token_ok(network, token)
    try:
        meta = await xc.token_metadata(ex, network, token)
    except AdapterError as e:
        raise HTTPException(502, str(e))
    return {"network": network, "address": token, **meta, "explorer": net.explorer["token"] + token}


async def _wallet(db: AsyncSession, user: User, wallet_id: str, network: str, managed: bool = False) -> Wallet:
    w = await owned_wallet(db, user, wallet_id, managed=managed)
    if w.network != network:
        raise HTTPException(400, f"that wallet is on {w.network}, not {network}")
    return w


@router.get("/chains/{network}/wallets/{wallet_id}/balances")
async def balances(network: str, wallet_id: str, tokens: str = Query(default="", max_length=2000),
                   user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                   ex: Executor = Depends(get_executor)):
    net = require_enabled(network)
    w = await _wallet(db, user, wallet_id, network)
    wanted = [t.strip() for t in tokens.split(",") if t.strip()][:20]
    known = {t["address"]: t for t in KNOWN_TOKENS.get(network, [])}
    addrs = list(dict.fromkeys([*known, *[_token_ok(network, t) for t in wanted]]))
    out = {"network": network, "wallet": w.address, "explorer": net.explorer["account"] + w.address,
           "native": None, "tokens": [], "errors": []}
    try:
        raw = await xc.native_balance(ex, network, w.address)
        out["native"] = {"asset": net.native, "raw": str(raw), "decimals": net.decimals,
                         "amount": xc.human(raw, net.decimals)}
    except Exception as e:
        out["errors"].append(f"{net.native} balance unavailable: {_why(e)}")
    for t in addrs:
        try:
            meta = await xc.token_metadata(ex, network, t, w.address)
            raw = await xc.token_balance(ex, network, t, w.address)
            out["tokens"].append({"address": t, "symbol": meta.get("symbol"), "name": meta.get("name"),
                                  "decimals": meta["decimals"], "raw": str(raw),
                                  "amount": xc.human(raw, meta["decimals"]), "explorer": net.explorer["token"] + t})
        except Exception as e:
            out["errors"].append(f"{t}: {_why(e)}")
    return out


def _why(e: Exception) -> str:
    return str(e)[:160] if isinstance(e, (AdapterError, HTTPException)) else type(e).__name__


@router.get("/chains/{network}/wallets/{wallet_id}/history")
async def history(network: str, wallet_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    net = require_enabled(network)
    w = await _wallet(db, user, wallet_id, network)
    helm = (await db.execute(select(Transaction, Job).join(Job, Job.id == Transaction.job_id).where(
        Transaction.wallet_id == w.id).order_by(Transaction.created_at.desc()).limit(50))).all()
    own = [{"at": t.created_at.isoformat() + "Z", "kind": t.kind, "status": t.status, "reference": t.signature,
            "error": t.error, "explorer": net.explorer["tx"] + t.signature if t.signature else None,
            "details": {k: j.params.get(k) for k in ("to", "amount", "asset", "token") if k in (j.params or {})}}
           for t, j in helm]
    chain = []
    note = (f"{net.name} has no official account-history API, so only transfers made through Helm are listed. "
            "The explorer link shows everything.")
    return {"network": network, "wallet": w.address, "explorer": net.explorer["account"] + w.address,
            "helm": own, "chain": chain, "note": note}


# --- transfers -----------------------------------------------------------------------------------------


class TransferBody(SpendConfirm):
    wallet_id: str
    to: str = Field(max_length=100)
    amount: str = Field(max_length=60)  # decimal string in whole units of the asset
    token: str | None = Field(default=None, max_length=100)  # None = the native asset


def _raw_amount(amount: str, decimals: int) -> int:
    try:
        d = Decimal(amount.strip())
    except (InvalidOperation, AttributeError):
        raise HTTPException(400, "the amount isn't a number")
    if not d.is_finite() or d <= 0:
        raise HTTPException(400, "the amount must be above zero")
    if d.normalize().as_tuple().exponent < -decimals:
        raise HTTPException(400, f"at most {decimals} decimal places for this asset")
    with localcontext() as ctx:
        ctx.prec = 100  # exact: no rounding of long amounts at the default 28 digits
        return int(d.scaleb(decimals))


async def _lock_warnings(db: AsyncSession, user: User, network: str, w: Wallet, token: str, plan: dict, ex) -> list[str]:
    from sqlalchemy import func

    from ..models import Launch
    from ..services.allocations import lock_warning

    launch = (await db.execute(select(Launch).where(
        Launch.user_id == user.id, Launch.network == network,
        func.lower(Launch.mint_address) == token.lower()))).scalars().first()
    if not launch:
        return []
    try:
        bal = await xc.token_balance(ex, network, token, w.address)
    except Exception:
        return ["The token balance couldn't be read to check your allocation locks; try again in a moment."]
    msg = await lock_warning(db, launch, w, bal, int(plan["raw"]), int(plan["decimals"]))
    return [msg] if msg else []


@router.post("/chains/{network}/transfer")
async def transfer(network: str, body: TransferBody, user: User = Depends(current_user),
                   db: AsyncSession = Depends(get_db), ex: Executor = Depends(get_executor)):
    if (prior := await find_replay(db, user, body)):
        return prior
    net = require_enabled(network)
    w = await _wallet(db, user, body.wallet_id, network, managed=True)
    to = body.to.strip()
    if not valid_address_for(network, to):
        raise HTTPException(400, f"that isn't a valid {net.name} address")
    if to.lower() == w.address.lower():
        raise HTTPException(400, "the recipient is the sending wallet")
    token = _token_ok(network, body.token) if body.token else None
    try:
        plan = await _arc_plan(ex, network, w, to, token, body.amount)
    except AdapterError as e:
        raise HTTPException(502, str(e))
    if token:  # a token of one of the user's own launches: its allocation locks apply here too
        plan["warnings"] = list(plan["warnings"]) + await _lock_warnings(db, user, network, w, token, plan, ex)
    kind = "send_tokens" if token else "withdraw"
    summary = {"network": network, "chain": net.name, "from": w.address, "to": to,
               "asset": plan["symbol"], "token": token, "amount": plan["amount"], "amount_raw": str(plan["raw"]),
               "decimals": plan["decimals"], "platform_fee": "none", **plan.get("hashed", {})}
    pf = build_preflight(kind, summary, plan["warnings"])
    pf["preflight"].update(plan["display"])  # fee estimates move with the chain: shown, not part of the hash
    if not body.confirm:
        return pf
    if plan["warnings"]:
        raise HTTPException(400, plan["warnings"][0])
    job, created = await begin_spend(db, user, body, pf, kind, None, {**pf["preflight"], "kind": kind})
    if created:
        async def work(session, j):
            j.status = "submitted"
            await session.commit()
            res = await plan["execute"](ex)
            await _record(session, j, w, kind, res, None)
            _finish(j, [res])
            await session.commit()

        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


async def _arc_plan(ex, network: str, w: Wallet, to: str, token: str | None, amount: str) -> dict:
    from eth_abi import encode as abi_encode
    from eth_utils import to_checksum_address

    from ..chains.flap import selector
    from ..services.evm_exec import EvmCall

    if int(to, 16) == 0:
        raise HTTPException(400, "Arc refuses transfers to the zero address")
    sender = ex.evm(network)
    usdc = await sender.rpc.get_balance(w.address)
    warnings, display = [], {}
    if token:
        meta = await xc.token_metadata(ex, network, token)
        raw = _raw_amount(amount, meta["decimals"])
        held = await sender.rpc.erc20_balance(token, w.address)
        if raw > held:
            warnings.append(f"The wallet holds {xc.human(held, meta['decimals'])} {meta['symbol']}, less than {amount}.")
        call = EvmCall(to_checksum_address(token), selector("transfer(address,uint256)")
                       + abi_encode(["address", "uint256"], [to_checksum_address(to), raw]))
        gas = 90_000
        symbol, decimals, spend = meta["symbol"], meta["decimals"], 0
    else:
        raw = _raw_amount(amount, 18)
        call = EvmCall(to_checksum_address(to), b"", raw)
        gas = 21_000
        symbol, decimals, spend = "USDC", 18, raw
    fee = await sender.max_gas_cost(gas)
    if spend + fee > usdc:
        warnings.append(f"The wallet holds {xc.human(usdc, 18)} USDC, less than the amount plus about "
                        f"{xc.human(fee, 18)} USDC in gas.")
    display = {"gas_fee_usdc_max": xc.human(fee, 18),
               "finality": "Final at one confirmation (Arc has deterministic finality)."}

    async def execute(ex):
        r = await ex.evm(network).send(w.address, w.signer_ref or "", call)
        return SubmitResult(r.signature, r.status, r.error)

    wallet_tx = {"from": to_checksum_address(w.address), "to": call.to, "data": "0x" + call.data.hex(),
                 "value": hex(call.value), "chain_id": EXTRA_NETWORKS[network].chain_id}
    return {"symbol": symbol, "decimals": decimals, "raw": raw, "amount": xc.human(raw, decimals),
            "warnings": warnings, "display": display, "execute": execute, "wallet_tx": wallet_tx}
