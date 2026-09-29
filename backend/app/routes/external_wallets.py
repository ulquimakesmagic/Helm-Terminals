"""Wallet settings for TRON, NEAR, Arc and TON: link your own wallet, rename, disconnect, see balances and what fees
or network resources a transfer needs, and send from a linked wallet with your wallet's own approval.

Linking proves ownership with a signature over a Helm challenge (TRON: TronLink signMessageV2; Arc: EIP-191
personal_sign; NEAR: NEP-413 signMessage with a full-access key; TON: TON Connect ton_proof, in routes/ton.py). The
challenge is bound to the Helm user, the network, the address and the site, and expires in 15 minutes. Linking a
wallet authorises nothing: every transfer is previewed in Helm, recorded, then approved and signed in the wallet.

POST   /chains/{network}/wallet-proof/challenge     the message to sign
POST   /chains/{network}/wallet-proof/verify        check the signature and link the wallet ("connected")
GET    /chains/wallets/overview                      linked, managed and watched wallets on these networks
PATCH  /wallets/{id}                                 rename any of your wallets
POST   /wallets/{id}/disconnect                      unlink a connected wallet (it stays as watch-only)
DELETE /wallets/{id}                                 remove a watch-only wallet on these networks
POST   /chains/{network}/external/transfer           preview; confirming creates the job and returns the request
POST   /chains/{network}/external/jobs/{id}/signed   the wallet's result: Helm checks it matches and follows it
POST   /chains/{network}/external/jobs/{id}/declined declined in the wallet: nothing was sent
POST   /chains/{network}/external/jobs/{id}/unknown  the wallet errored after approval: it may have been sent
"""

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.base import AdapterError
from ..chains.networks import EXTRA_NETWORKS, KNOWN_TOKENS, extra_enabled, require_enabled
from ..db import get_db
from ..deps import SpendConfirm, begin_spend, current_user, find_replay, get_executor, log_activity, owned_wallet, run_job, \
    valid_address_for
from ..models import Funder, Job, LaunchWallet, Transaction, User, Wallet
from ..services import extra_chains as xc
from ..services.executor import Executor, SubmitResult, _finish, _record
from ..services.preflight import build_preflight
from .ton import PROOF_TTL_S, check_payload, make_payload, proof_domains

router = APIRouter()

SIGNED_FAMILIES = ("tron", "near", "evm")  # TON links through TON Connect (routes/ton.py)
_used_nonces: dict[str, float] = {}  # payload -> expiry: each challenge links once


def _net(network: str):
    net = require_enabled(network)
    if net.family not in SIGNED_FAMILIES:
        raise HTTPException(400, f"{net.name} wallets link through TON Connect")
    return net


def canonical(network: str, address: str) -> str:
    fam = EXTRA_NETWORKS[network].family
    a = address.strip()
    if fam == "evm":
        from eth_utils import to_checksum_address

        return to_checksum_address(a)
    return a.lower() if fam == "near" else a


def _checked(network: str, address: str) -> str:
    """The address in its one stored form, or 400 if it isn't valid on this network."""
    try:
        a = canonical(network, address)
    except ValueError:
        a = ""
    if not a or not valid_address_for(network, a):
        raise HTTPException(400, f"that isn't a valid {EXTRA_NETWORKS[network].name} address")
    return a


def proof_message(domain: str, network: str, address: str, payload: str) -> str:
    """The exact text the wallet signs. Rebuilt from the challenge, never taken from the client."""
    net = EXTRA_NETWORKS[network]
    issued = int.from_bytes(bytes.fromhex(payload)[:8], "big")
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    ident = f", chain ID {net.chain_id}" if net.chain_id else ""
    return (f"{domain} wants you to link this {net.name} wallet to your Helm Terminals account.\n\n"
            f"Network: {net.name} (mainnet{ident})\nAddress: {address}\nNonce: {payload}\n"
            f"Issued at: {iso(issued)}\nExpires at: {iso(issued + PROOF_TTL_S)}\n\n"
            "Linking a wallet doesn't authorize any transaction.")


class ChallengeBody(BaseModel):
    address: str = Field(max_length=80)
    domain: str = Field(max_length=120)


@router.post("/chains/{network}/wallet-proof/challenge")
async def challenge(network: str, body: ChallengeBody, user: User = Depends(current_user)):
    net = _net(network)
    if body.domain not in proof_domains():
        raise HTTPException(400, "wallets can only be linked from Helm's own site")
    address = _checked(network, body.address)
    payload = make_payload(user.id, network)
    return {"message": proof_message(body.domain, network, address, payload), "nonce": payload, "address": address,
            "recipient": body.domain, "expires_in": PROOF_TTL_S, "chain_id": net.chain_id}


class VerifyBody(BaseModel):
    address: str = Field(max_length=80)
    domain: str = Field(max_length=120)
    nonce: str = Field(max_length=64)
    signature: str = Field(max_length=200)
    public_key: str | None = Field(default=None, max_length=100)  # NEAR: "ed25519:<base58>"
    chain_id: int | None = None  # Arc: the chain the wallet was on when it signed
    label: str = Field(default="", max_length=80)


def _spend_nonce(payload: str) -> None:
    now = time.time()
    for k in [k for k, exp in _used_nonces.items() if exp < now]:
        _used_nonces.pop(k, None)
    if payload in _used_nonces:
        raise HTTPException(400, "this link request was already used; start again")
    _used_nonces[payload] = now + PROOF_TTL_S


@router.post("/chains/{network}/wallet-proof/verify", status_code=201)
async def verify(network: str, body: VerifyBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    net = _net(network)
    if body.domain not in proof_domains():
        raise HTTPException(400, "wallets can only be linked from Helm's own site")
    address = _checked(network, body.address)
    check_payload(body.nonce, user.id, network)
    message = proof_message(body.domain, network, address, body.nonce)
    if net.family == "tron":
        from ..chains import tron

        try:
            signer = tron.recover_address(tron.message_hash_v2(message), body.signature)
        except AdapterError as e:
            raise HTTPException(400, str(e))
        if signer != address:
            raise HTTPException(400, "the signature was made by a different TRON account")
    elif net.family == "evm":
        if body.chain_id != net.chain_id:
            raise HTTPException(400, f"the wallet is on chain {body.chain_id}, not {net.name} ({net.chain_id}); "
                                     "switch networks in the wallet and try again")
        from eth_account import Account
        from eth_account.messages import encode_defunct

        try:
            signer = Account.recover_message(encode_defunct(text=message), signature=body.signature)
        except Exception:
            raise HTTPException(400, "the signature couldn't be read")
        if signer.lower() != address.lower():
            raise HTTPException(400, "the signature was made by a different account")
    else:  # NEAR, NEP-413
        from ..chains import near

        if not body.public_key:
            raise HTTPException(400, "the wallet didn't return the key it signed with")
        try:
            pk = near.verify_nep413(message, bytes.fromhex(body.nonce), body.domain, body.public_key, body.signature)
        except ValueError as e:
            raise HTTPException(400, str(e))
        try:
            key = await xc.near_client(network).access_key(address, pk)
        except AdapterError:
            raise HTTPException(400, "that key isn't an access key of this NEAR account")
        if key.get("permission") != "FullAccess":
            raise HTTPException(400, "NEAR ownership needs a full-access key (NEP-413)")
    _spend_nonce(body.nonce)
    w = (await db.execute(select(Wallet).where(Wallet.user_id == user.id, Wallet.network == network,
                                               func.lower(Wallet.address) == address.lower()))).scalar_one_or_none()
    if w is None:
        w = Wallet(user_id=user.id, network=network, address=address, kind="connected",
                   label=body.label or f"My {net.name} wallet")
        db.add(w)
    elif w.kind == "watch":
        w.kind = "connected"  # a watched address, now proven to be the user's own
    await log_activity(db, user.id, "wallets.external_linked", payload={"network": network, "address": address})
    await db.commit()
    return {"id": w.id, "network": network, "address": w.address, "kind": w.kind, "label": w.label}


# --- managing wallets -------------------------------------------------------------------------------------------


class RenameBody(BaseModel):
    label: str = Field(min_length=1, max_length=80)


@router.patch("/wallets/{wallet_id}")
async def rename(wallet_id: str, body: RenameBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    w = await owned_wallet(db, user, wallet_id, managed=False)
    w.label = body.label.strip()
    await db.commit()
    return {"id": w.id, "label": w.label}


@router.post("/wallets/{wallet_id}/disconnect")
async def disconnect(wallet_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    w = await owned_wallet(db, user, wallet_id, managed=False)
    if w.kind != "connected":
        raise HTTPException(400, "only a linked external wallet can be disconnected")
    w.kind = "watch"  # its history and balances stay visible; it can no longer be sent from
    await log_activity(db, user.id, "wallets.external_unlinked", payload={"network": w.network, "address": w.address})
    await db.commit()
    return {"id": w.id, "kind": w.kind}


@router.delete("/wallets/{wallet_id}")
async def remove(wallet_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    w = await owned_wallet(db, user, wallet_id, managed=False)
    if w.kind != "watch" or w.network not in EXTRA_NETWORKS:
        raise HTTPException(400, "only watch-only TRON, NEAR, Arc or TON wallets can be removed here")
    for model, col in ((Transaction, Transaction.wallet_id), (LaunchWallet, LaunchWallet.wallet_id),
                       (Funder, Funder.wallet_id)):
        if (await db.execute(select(func.count()).select_from(model).where(col == w.id))).scalar():
            raise HTTPException(409, "this wallet has history in Helm, so it's kept (it can't sign anything)")
    await db.delete(w)
    await db.commit()
    return {"removed": wallet_id}


# --- overview ---------------------------------------------------------------------------------------------------


async def _fees(ex, network: str, address: str) -> dict:
    """What a transfer from this wallet costs, in the network's own terms."""
    net = EXTRA_NETWORKS[network]
    if net.family == "tron":
        c = xc.tron_client(network)
        r = await c.resources(address)
        p = await c.chain_parameters()
        return {"kind": "resources", "bandwidth_available": r["free_bandwidth"] + r["staked_bandwidth"],
                "energy_available": r["energy"],
                "trx_transfer": "about 270 bandwidth (free up to 600 a day), else about "
                                f"{xc.human(270 * p.get('getTransactionFee', 1000), 6)} TRX burned",
                "token_transfer": "about 65,000 energy; without staked energy about "
                                  f"{xc.human(65_000 * p.get('getEnergyFee', 100), 6)} TRX is burned",
                "activation": "sending TRX to a new address costs 1 TRX to activate it"}
    if net.family == "near":
        from ..chains import near

        acct = await xc.near_client(network).account(address)
        locked = int((acct or {}).get("storage_usage", 0)) * 10**19
        return {"kind": "gas_and_storage", "storage_locked": xc.human(locked, 24),
                "transfer": "gas is about 0.0003 NEAR; storage keeps part of the balance locked",
                "token_transfer": "about 0.003 NEAR of gas, plus 0.00125 NEAR if the recipient isn't registered"}
    if net.family == "evm":
        fee = await ex.evm(network).max_gas_cost(21_000)
        return {"kind": "gas", "transfer": f"up to {xc.human(fee, 18)} USDC of gas (Arc charges gas in USDC)",
                "token_transfer": f"up to {xc.human(fee * 90_000 // 21_000, 18)} USDC of gas"}
    return {"kind": "ton", "transfer": "about 0.005 TON of network fees",
            "token_transfer": "0.05 TON attached to a Jetton transfer; the unused part comes back"}


@router.get("/chains/wallets/overview")
async def overview(user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                   ex: Executor = Depends(get_executor)):
    enabled = extra_enabled()
    rows = (await db.execute(select(Wallet).where(Wallet.user_id == user.id, Wallet.network.in_(list(EXTRA_NETWORKS)))
                             .order_by(Wallet.created_at))).scalars().all()
    wallets = []
    for w in rows:
        net = EXTRA_NETWORKS[w.network]
        out = {"id": w.id, "network": w.network, "family": net.family, "network_name": net.name,
               "enabled": w.network in enabled, "address": w.address, "kind": w.kind,
               "label": w.label, "explorer": net.explorer["account"] + w.address, "native": None, "tokens": [],
               "fees": None, "error": None,
               "can_send": w.kind == "managed" or (w.kind == "connected"), "signer": {
                   "managed": "Helm signer", "connected": "Your wallet" if net.family != "ton" else "TON Connect",
                   "watch": "Watch-only"}.get(w.kind, w.kind)}
        if w.network in enabled:
            try:
                raw = await xc.native_balance(ex, w.network, w.address)
                out["native"] = {"asset": net.native, "amount": xc.human(raw, net.decimals)}
                for t in KNOWN_TOKENS.get(w.network, []):
                    tr = await xc.token_balance(ex, w.network, t["address"], w.address)
                    out["tokens"].append({"symbol": t["symbol"], "address": t["address"],
                                          "amount": xc.human(tr, t["decimals"])})
                out["fees"] = await _fees(ex, w.network, w.address)
            except Exception as e:
                out["error"] = (str(e) if isinstance(e, AdapterError) else type(e).__name__)[:160]
        wallets.append(out)
    networks = [{"id": n.id, "name": n.name, "family": n.family, "enabled": n.id in enabled,
                 "chain_id": n.chain_id, "network_id": n.network_id, "native": n.native,
                 "managed": n.family != "ton", "external": {"tron": "TronLink", "evm": "Browser EVM wallet",
                                                            "near": "NEAR wallet (NEAR Connect)",
                                                            "ton": "TON Connect"}[n.family]}
                for n in EXTRA_NETWORKS.values()]
    return {"networks": networks, "wallets": wallets}


# --- sending from a linked wallet --------------------------------------------------------------------------------


class ExternalTransferBody(SpendConfirm):
    wallet_id: str
    to: str = Field(max_length=100)
    amount: str = Field(max_length=60)
    token: str | None = Field(default=None, max_length=100)


async def _plan(ex, network: str, w: Wallet, to: str, token: str | None, amount: str) -> dict:
    from .chains import _arc_plan, _near_plan, _token_ok, _tron_plan

    net = EXTRA_NETWORKS[network]
    token = _token_ok(network, token) if token else None
    try:
        if net.family == "tron":
            return await _tron_plan(network, w, to, token, amount), token
        if net.family == "near":
            return await _near_plan(network, w, to, token, amount), token
        return await _arc_plan(ex, network, w, to, token, amount), token
    except AdapterError as e:
        raise HTTPException(502, str(e))


@router.post("/chains/{network}/external/transfer")
async def external_transfer(network: str, body: ExternalTransferBody, user: User = Depends(current_user),
                            db: AsyncSession = Depends(get_db), ex: Executor = Depends(get_executor)):
    if (prior := await find_replay(db, user, body)):
        j = await db.get(Job, prior["job_id"])
        return {**prior, "wallet_request": (j.params or {}).get("wallet_request") if j.status == "pending" else None}
    net = _net(network)
    w = await owned_wallet(db, user, body.wallet_id, managed=False)
    if w.network != network:
        raise HTTPException(400, f"that wallet is on {w.network}, not {network}")
    if w.kind != "connected":
        raise HTTPException(400, "link this wallet (sign the ownership message) before sending from it")
    to = body.to.strip()
    if not valid_address_for(network, to):
        raise HTTPException(400, f"that isn't a valid {net.name} address")
    if canonical(network, to).lower() == w.address.lower():
        raise HTTPException(400, "the recipient is the sending wallet")
    plan, token = await _plan(ex, network, w, to, body.token, body.amount)
    kind = "external_transfer"  # approved and signed in the user's own wallet; Helm holds no key
    summary = {"network": network, "chain": net.name, "from": w.address, "to": to,
               "asset": plan["symbol"], "token": token, "amount": plan["amount"], "amount_raw": str(plan["raw"]),
               "decimals": plan["decimals"], "platform_fee": "none", "external_wallet": True,
               **plan.get("hashed", {})}
    pf = build_preflight(kind, summary, plan["warnings"])
    pf["preflight"].update(plan["display"])
    if not body.confirm:
        return pf
    if plan["warnings"]:
        raise HTTPException(400, plan["warnings"][0])
    if net.family == "tron":
        try:
            tx = await plan["unsigned"]()
        except AdapterError as e:
            raise HTTPException(502, str(e))
        request = {"tx": tx}
    else:
        request = plan["wallet_tx"]
    job, _ = await begin_spend(db, user, body, pf, kind, None,
                               {**pf["preflight"], "kind": kind, "wallet_request": request})
    return {"job_id": job.id, "status": job.status, "wallet_request": request if job.status == "pending" else None}


async def _external_job(db: AsyncSession, user: User, network: str, job_id: str) -> Job:
    _net(network)
    j = await db.get(Job, job_id)
    if not j or j.user_id != user.id or j.kind != "external_transfer" or (j.params or {}).get("network") != network:
        raise HTTPException(404, "transfer not found")
    return j


class SignedBody(BaseModel):
    signed_tx: dict | None = None  # TRON: the transaction TronLink signed
    tx_hash: str | None = Field(default=None, max_length=100)  # Arc / NEAR: what the wallet sent


def _near_actions(tx: dict) -> list:
    """NEAR actions from an indexed transaction, in comparable form."""
    import base64
    import json

    out = []
    for a in tx.get("actions") or []:
        if isinstance(a, dict) and "Transfer" in a:
            out.append(("Transfer", str(a["Transfer"]["deposit"])))
        elif isinstance(a, dict) and "FunctionCall" in a:
            f = a["FunctionCall"]
            try:
                args = json.loads(base64.b64decode(f.get("args") or ""))
            except ValueError:
                args = None
            out.append(("FunctionCall", f.get("method_name"), args, str(f.get("deposit"))))
        else:
            out.append(("Other",))
    return out


def _expected_near(req: dict) -> list:
    out = []
    for a in req["actions"]:
        if a["type"] == "Transfer":
            out.append(("Transfer", a["params"]["deposit"]))
        else:
            p = a["params"]
            out.append(("FunctionCall", p["methodName"], p["args"], p["deposit"]))
    return out


@router.post("/chains/{network}/external/jobs/{job_id}/signed")
async def signed(network: str, job_id: str, body: SignedBody, user: User = Depends(current_user),
                 db: AsyncSession = Depends(get_db), ex: Executor = Depends(get_executor)):
    j = await _external_job(db, user, network, job_id)
    if j.status != "pending":
        return {"job_id": j.id, "status": j.status}
    net = EXTRA_NETWORKS[network]
    req = j.params["wallet_request"]
    sender = j.params["from"]
    w = (await db.execute(select(Wallet).where(Wallet.user_id == user.id, Wallet.network == network,
                                               Wallet.address == sender))).scalar_one_or_none()
    if net.family == "tron":
        from ..chains import tron

        s = body.signed_tx or {}
        if s.get("txID") != req["tx"]["txID"] or s.get("raw_data_hex") != req["tx"]["raw_data_hex"]:
            raise HTTPException(400, "the wallet signed a different transaction than the one reviewed")
        try:
            who = tron.signer_of(s)
        except (AdapterError, KeyError, ValueError) as e:
            raise HTTPException(400, f"the signature couldn't be checked: {e}")
        if who != sender:
            raise HTTPException(400, "the transaction was signed by a different account")
        signed_tx = {**req["tx"], "signature": list(s.get("signature") or [])}  # our transaction, their signature

        async def follow() -> SubmitResult:
            txid, status, err, _ = await tron.submit_signed(xc.tron_client(network), signed_tx)
            return SubmitResult(txid, status, err)
    else:
        h = (body.tx_hash or "").strip()
        if not h:
            raise HTTPException(400, "the wallet didn't return a transaction")
        if (await db.execute(select(Transaction.id).where(func.lower(Transaction.signature) == h.lower()))).first():
            raise HTTPException(400, "that transaction is already recorded for another transfer")
        created = j.created_at.replace(tzinfo=timezone.utc).timestamp() if j.created_at else 0
        if net.family == "evm":
            if not (h.startswith("0x") and len(h) == 66):
                raise HTTPException(400, "that isn't a transaction hash")

            async def follow() -> SubmitResult:
                rpc = ex.evm(network).rpc
                try:
                    tx = await rpc.call("eth_getTransactionByHash", [h])
                except Exception:
                    tx = None
                if tx is None:
                    r = await ex.evm(network).wait(h)
                    return SubmitResult(h, r.status if r.status != "confirmed" else "timeout",
                                        r.error or "the transaction couldn't be checked; see the explorer")
                same = ((tx.get("from") or "").lower() == sender.lower() and (tx.get("to") or "").lower() == req["to"].lower()
                        and (tx.get("input") or "0x").lower() == req["data"].lower()
                        and int(tx.get("value") or "0x0", 16) == int(req["value"], 16)
                        and int(tx.get("chainId") or hex(net.chain_id), 16) == net.chain_id)
                r = await ex.evm(network).wait(h)
                if same and r.status == "confirmed" and tx.get("blockNumber"):
                    try:  # an older transfer with the same details isn't this one
                        block = await rpc.call("eth_getBlockByNumber", [tx["blockNumber"], False])
                        if int((block or {}).get("timestamp") or "0x0", 16) < created - 120:
                            same = False
                    except Exception:
                        pass
                if not same:  # money may have moved, so never "failed": it needs a look on the explorer
                    return SubmitResult(h, "timeout", "the wallet reported a different transaction than the one "
                                                      "reviewed; check the explorer before sending again")
                return SubmitResult(h, r.status, r.error)
        else:
            async def follow() -> SubmitResult:
                from ..chains import near

                try:
                    res = await xc.near_client(network).status(h, sender)
                except AdapterError as e:
                    return SubmitResult(h, "timeout", f"couldn't read the transaction yet ({e}); check the explorer")
                tx = res.get("transaction") or {}
                status, err = near.outcome_status(res)
                if tx.get("signer_id") != sender or tx.get("receiver_id") != req["receiver_id"] \
                        or _near_actions(tx) != _expected_near(req):
                    return SubmitResult(h, "timeout", "the wallet reported a different transaction than the one "
                                                      "reviewed; check the explorer before sending again")
                return SubmitResult(h, status, err)
    from sqlalchemy import update

    claimed = await db.execute(update(Job).where(Job.id == j.id, Job.status == "pending").values(status="submitted"))
    await db.commit()
    if claimed.rowcount != 1:  # another request already took this job
        await db.refresh(j)
        return {"job_id": j.id, "status": j.status}

    async def work(session, job):
        res = await follow()
        await _record(session, job, w, "external_transfer", res, None)
        _finish(job, [res])
        await session.commit()

    await run_job(j.id, work)
    return {"job_id": j.id, "status": j.status}


@router.post("/chains/{network}/external/jobs/{job_id}/unknown")
async def unknown(network: str, job_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """The wallet errored after approval: it may have sent the transfer. Never "failed", which would invite a resend."""
    j = await _external_job(db, user, network, job_id)
    if j.status == "pending":
        j.status = "unconfirmed"
        j.params = {**j.params, "error": "The wallet reported an error after you approved, so the transfer may have "
                                         "been sent. Check the wallet's history before sending again."}
        await db.commit()
    return {"job_id": j.id, "status": j.status}


@router.post("/chains/{network}/external/jobs/{job_id}/declined")
async def declined(network: str, job_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    j = await _external_job(db, user, network, job_id)
    if j.status == "pending":
        j.status = "failed"
        j.params = {**j.params, "error": "Declined in the wallet (or it expired there). Nothing was sent."}
        await db.commit()
    return {"job_id": j.id, "status": j.status}
