"""Network registry and the TRON / NEAR / Arc features Helm supports on a documented path.

GET  /chains                                   every network, whether it's enabled, and what Helm supports on it
GET  /chains/{network}/tokens/{token}           token metadata (TRC-20, NEP-141, ERC-20), matched by address
GET  /chains/{network}/wallets/{id}/balances    native balance plus requested/known tokens
GET  /chains/{network}/wallets/{id}/history     account history (TRON: TronGrid; others: Helm's own transfers)
POST /chains/{network}/transfer                 preview, then confirm, a native or token transfer

Launches, swaps, market data and cross-chain routes are not offered on these networks; the matrix says why.
"""

import math
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
    return {"id": n.id, "family": n.family, "name": n.name, "testnet": n.testnet, "native": n.native,
            "decimals": n.decimals, "chain_id": n.chain_id, "explorer": n.explorer, "notes": n.notes,
            "enabled": n.id in extra_enabled(), "capabilities": CAPABILITIES[n.family], "sources": SOURCES[n.family]}


@router.get("/chains")
async def chains():
    core = [{"id": "solana", "name": "Solana", "family": "solana", "testnet": False, "enabled": True,
             "capabilities": "full: wallets, launches, trading, market data"}] + [
        {"id": k, "name": v["name"], "family": "evm", "testnet": False, "enabled": True, "chain_id": v["chain_id"],
         "capabilities": "full: wallets, launches, trading, market data"} for k, v in EVM_CHAINS.items()]
    return {"core": core, "extra": [_net_out(n) for n in EXTRA_NETWORKS.values()],
            "note": "Extra networks are off unless the server lists them in EXTRA_NETWORKS; mainnets also need "
                    "ALLOW_MAINNET_EXTRA_NETWORKS=true."}


@router.get("/chains/{network}/health")
async def network_health(network: str, user: User = Depends(current_user), ex: Executor = Depends(get_executor)):
    """A live read from the network's endpoint: TRON chain parameters, NEAR's final block, Arc's chain id."""
    import time

    net = require_enabled(network)
    t0 = time.monotonic()
    try:
        if net.family == "tron":
            await xc.tron_client(network).chain_parameters()
        elif net.family == "near":
            await xc.near_client(network).final_block_hash()
        else:
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
    chain, note = [], None
    if net.family == "tron":
        from ..chains import tron

        try:
            client = xc.tron_client(network)
            for item in await client.history(w.address, 20):
                row = {"at_ms": item.get("block_timestamp"), "reference": item.get("txID"),
                       "status": ((item.get("ret") or [{}])[0]).get("contractRet"),
                       "explorer": net.explorer["tx"] + (item.get("txID") or "")}
                try:  # a plain TRX transfer: show its amount and counterparty (TronGrid gives hex addresses)
                    ct = item["raw_data"]["contract"][0]
                    if ct.get("type") == "TransferContract":
                        v = ct["parameter"]["value"]
                        row.update({"raw": str(int(v["amount"])), "decimals": 6,
                                    "to": tron.encode_address(bytes.fromhex(v["to_address"]))})
                except (KeyError, IndexError, TypeError, ValueError):
                    pass
                chain.append(row)
            for item in await client.history(w.address, 20, trc20=True):
                ti = item.get("token_info") or {}
                chain.append({"at_ms": item.get("block_timestamp"), "reference": item.get("transaction_id"),
                              "token": ti.get("address"), "symbol": ti.get("symbol"), "from": item.get("from"),
                              "to": item.get("to"), "raw": item.get("value"), "decimals": ti.get("decimals"),
                              "explorer": net.explorer["tx"] + (item.get("transaction_id") or "")})
            chain.sort(key=lambda x: -(x.get("at_ms") or 0))
        except AdapterError as e:
            note = f"TronGrid history is unavailable right now: {e}"
    else:
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
        if net.family == "tron":
            plan = await _tron_plan(network, w, to, token, body.amount)
        elif net.family == "near":
            plan = await _near_plan(network, w, to, token, body.amount)
        else:
            plan = await _arc_plan(ex, network, w, to, token, body.amount)
    except AdapterError as e:
        raise HTTPException(502, str(e))
    kind = "send_tokens" if token else "withdraw"
    summary = {"network": network, "chain": net.name, "testnet": net.testnet, "from": w.address, "to": to,
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


async def _tron_plan(network: str, w: Wallet, to: str, token: str | None, amount: str) -> dict:
    from ..chains import tron

    c = xc.tron_client(network)
    params = await c.chain_parameters()
    energy_price = params.get("getEnergyFee", 100)
    byte_price = params.get("getTransactionFee", 1000)
    res = await c.resources(w.address)
    trx = await c.balance(w.address)
    recipient_active = bool(await c.account(to))
    warnings, display = [], {}
    if token:
        meta = await c.trc20_metadata(token, w.address)
        raw = _raw_amount(amount, meta["decimals"])
        held = await c.trc20_balance(token, w.address)
        if raw > held:
            warnings.append(f"The wallet holds {xc.human(held, meta['decimals'])} {meta['symbol']}, less than {amount}.")
        energy = await c.trc20_transfer_energy(token, w.address, to, raw) if raw <= held else 65_000
        energy_burn = max(energy - res["energy"], 0) * energy_price
        fee_limit = min(max(int(math.ceil(energy * energy_price * 1.3)), 1_000_000), params.get("getMaxFeeLimit", 15_000 * tron.SUN))
        bandwidth = 345  # a TRC-20 transfer is ~345 bytes on-chain
        bw_burn = 0 if res["free_bandwidth"] + res["staked_bandwidth"] >= bandwidth else bandwidth * byte_price
        fee = energy_burn + bw_burn
        display = {"energy_needed": energy, "energy_available": res["energy"], "energy_fee_trx": xc.human(energy_burn, 6),
                   "bandwidth_bytes_about": bandwidth, "bandwidth_fee_trx": xc.human(bw_burn, 6),
                   "fee_limit_trx": xc.human(fee_limit, 6), "network_fee_trx_about": xc.human(fee, 6)}
        if trx < fee:
            warnings.append(f"The wallet needs about {xc.human(fee, 6)} TRX for energy and bandwidth; it holds "
                            f"{xc.human(trx, 6)} TRX. (TRON burns TRX when a wallet has no energy or bandwidth.)")
        if not recipient_active:
            display["recipient_note"] = ("The recipient address isn't activated yet. A token transfer doesn't activate "
                                         "it; it will need TRX before it can move these tokens.")
        symbol, decimals = meta["symbol"], meta["decimals"]

        async def execute(ex):
            tx = await c.build_trc20_transfer(w.address, token, to, raw, fee_limit)
            tron.check_transaction(tx, owner=w.address, token=token, token_to=to, token_amount=raw,
                                   max_fee_limit=fee_limit)
            return await _tron_send(ex, c, w, tx)
    else:
        raw = _raw_amount(amount, 6)
        tx = await c.build_trx_transfer(w.address, to, raw)  # read-only: sizes the transaction for the estimate
        tron.check_transaction(tx, owner=w.address, to=to, amount=raw)
        size = tron.bandwidth_bytes(tx)
        has_bw = res["free_bandwidth"] + res["staked_bandwidth"] >= size
        activation = 0
        if not recipient_active:
            activation = params.get("getCreateNewAccountFeeInSystemContract", 1_000_000) + \
                (0 if has_bw else params.get("getCreateAccountFee", 100_000))
        bw_burn = 0 if (has_bw or not recipient_active) else size * byte_price
        fee = activation + bw_burn
        display = {"bandwidth_bytes": size, "bandwidth_available": res["free_bandwidth"] + res["staked_bandwidth"],
                   "bandwidth_fee_trx": xc.human(bw_burn, 6), "activation_fee_trx": xc.human(activation, 6),
                   "network_fee_trx_about": xc.human(fee, 6)}
        if not recipient_active:
            display["recipient_note"] = "The recipient isn't activated yet; this transfer activates it (fee above)."
        if raw + fee > trx:
            warnings.append(f"The wallet holds {xc.human(trx, 6)} TRX, less than {amount} TRX plus about "
                            f"{xc.human(fee, 6)} TRX in fees.")
        symbol, decimals = "TRX", 6

        async def execute(ex):
            fresh = await c.build_trx_transfer(w.address, to, raw)
            tron.check_transaction(fresh, owner=w.address, to=to, amount=raw)
            return await _tron_send(ex, c, w, fresh)
    display["finality"] = "Final once the solidity node returns the receipt (about a minute)."
    return {"symbol": symbol, "decimals": decimals, "raw": raw, "amount": xc.human(raw, decimals),
            "warnings": warnings, "display": display, "execute": execute}


async def _tron_send(ex, c, w: Wallet, tx: dict) -> SubmitResult:
    from ..chains import tron

    try:
        txid, status, err, _ = await tron.sign_and_send(c, ex.signer, w.signer_ref or "", tx)
    except Exception as e:
        return SubmitResult(None, "failed", f"signing or sending failed: {_why(e)}")
    return SubmitResult(txid, status, err)


async def _near_plan(network: str, w: Wallet, to: str, token: str | None, amount: str) -> dict:
    from ..chains import near

    c = xc.near_client(network)
    acct = await c.account(w.address)
    if acct is None:
        raise HTTPException(400, "this NEAR wallet doesn't exist on-chain yet: send it some NEAR first "
                                 "(a first transfer creates an implicit account)")
    try:
        per_byte = await c.protocol_storage_cost()
    except AdapterError:
        per_byte = 10**19  # 1 NEAR per 100 kB (storage staking docs)
    avail = near.available_balance(acct, per_byte)
    price = await c.gas_price()
    receiver = await c.account(to)
    warnings, display = [], {}
    if near.IMPLICIT.match(to) is None and near.ETH_IMPLICIT.match(to) is None and receiver is None:
        warnings.append("That named account doesn't exist; a transfer to it would fail or strand the tokens. "
                        "Check the name.")
    if token:
        meta = await (xc.token_metadata(None, network, token))
        raw = _raw_amount(amount, meta["decimals"])
        held = int(await c.view(token, "ft_balance_of", {"account_id": w.address}) or 0)
        if raw > held:
            warnings.append(f"The wallet holds {xc.human(held, meta['decimals'])} {meta['symbol']}, less than {amount}.")
        registered = await c.view(token, "storage_balance_of", {"account_id": to})
        actions, deposit, storage = [], 1, 0
        if registered is None:
            bounds = await c.view(token, "storage_balance_bounds", {}) or {}
            storage = int((bounds.get("min") if isinstance(bounds, dict) else None) or 1_250_000_000_000_000_000_000)
            actions.append(near.function_call_action("storage_deposit", {"account_id": to, "registration_only": True},
                                                     near.STORAGE_DEPOSIT_GAS, storage))
            deposit += storage
            display["storage_deposit_near"] = xc.human(storage, 24)
            display["recipient_note"] = "The recipient isn't registered with this token; the deposit registers it."
        actions.append(near.function_call_action("ft_transfer", {"receiver_id": to, "amount": str(raw)},
                                                 near.FT_TRANSFER_GAS, 1))
        gas = near.FT_TRANSFER_GAS + (near.STORAGE_DEPOSIT_GAS if registered is None else 0)
        fee_max = gas * price
        if avail < deposit + fee_max:
            warnings.append(f"The wallet needs about {xc.human(deposit + fee_max, 24)} NEAR for the deposit and gas; "
                            f"it has {xc.human(avail, 24)} NEAR available.")
        symbol, decimals, receiver_id = meta["symbol"], meta["decimals"], token
    else:
        raw = _raw_amount(amount, 24)
        actions = [near.transfer_action(raw)]
        fee_max = near.TRANSFER_GAS * price
        if raw + fee_max > avail:
            warnings.append(f"The wallet has {xc.human(avail, 24)} NEAR available (after storage), less than {amount} "
                            "NEAR plus gas.")
        symbol, decimals, receiver_id = "NEAR", 24, to
        if receiver is None and near.IMPLICIT.match(to):
            display["recipient_note"] = "This implicit account doesn't exist yet; the transfer creates it."
    display.update({"gas_fee_near_max": xc.human(fee_max, 24),
                    "finality": "Helm waits for FINAL execution of every receipt."})
    pubkey = bytes.fromhex(w.address)

    async def execute(ex):
        try:
            h, status, err = await near.sign_and_send(c, ex.signer, w.signer_ref or "", w.address, pubkey,
                                                      receiver_id, actions)
        except Exception as e:
            return SubmitResult(None, "failed", f"signing or sending failed: {_why(e)}")
        return SubmitResult(h, status, err)

    return {"symbol": symbol, "decimals": decimals, "raw": raw, "amount": xc.human(raw, decimals),
            "warnings": warnings, "display": display, "execute": execute,
            "hashed": {"storage_deposit_yocto": str(storage)} if token else {}}


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

    return {"symbol": symbol, "decimals": decimals, "raw": raw, "amount": xc.human(raw, decimals),
            "warnings": warnings, "display": display, "execute": execute}
