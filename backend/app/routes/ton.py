"""TON through TON Connect: the user's own wallet, bound to the Helm account by a signed ton_proof.

POST /chains/{network}/ton/proof-payload   a single-purpose, short-lived payload for the wallet to sign
POST /chains/{network}/ton/connect         verify the proof and link the wallet (kind "connected")
POST /chains/{network}/ton/transfer        preview the wallet request; confirming creates the job and returns it
POST /chains/{network}/ton/jobs/{id}/signed    the wallet's signed message: Helm follows it and checks the result
POST /chains/{network}/ton/jobs/{id}/declined  the user declined in the wallet: nothing was sent

Helm never sees a TON private key. The wallet shows every request for approval, signs it and broadcasts it; Helm
only builds the messages (and Jetton payloads) and follows the result on the index by the message's normalized hash.
"""

import asyncio
import base64
import hashlib
import hmac
import os
import struct
import time
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains import ton
from ..chains.base import AdapterError
from ..chains.networks import EXTRA_NETWORKS, require_enabled
from ..config import get_settings
from ..db import get_db
from ..deps import SpendConfirm, begin_spend, current_user, find_replay, log_activity, owned_wallet, run_job
from ..models import Job, Transaction, User, Wallet
from ..services import extra_chains as xc
from ..services.executor import SubmitResult, _finish, _record
from ..services.preflight import build_preflight

router = APIRouter()

PROOF_TTL_S = 900
SEND_FEE_RESERVE = 10_000_000  # ~0.01 TON kept aside for the wallet's own fees in the balance check
VALID_FOR_S = 300  # how long the wallet may take to approve before the request expires
CONFIRM_TIMEOUT_S = 120


def _ton_net(network: str):
    net = require_enabled(network)
    if net.family != "ton":
        raise HTTPException(404, "TON Connect is only for TON networks")
    return net


def _addr(s: str) -> ton.TonAddress:
    try:
        a = ton.parse_address(s)
    except ValueError as e:
        raise HTTPException(400, f"invalid TON address: {e}")
    if a.wc not in (0, -1):
        raise HTTPException(400, "invalid TON address: only the basechain (0) and masterchain (-1) exist")
    return a


# --- proof payloads -------------------------------------------------------------------------------------------


def _mac(user_id: str, network: str, head: bytes) -> bytes:
    key = get_settings().secret_key.encode()
    return hmac.new(key, b"ton-proof|" + user_id.encode() + b"|" + network.encode() + b"|" + head, hashlib.sha256).digest()[:16]


def make_payload(user_id: str, network: str, now: float | None = None) -> str:
    head = struct.pack(">Q", int(now or time.time())) + os.urandom(8)
    return (head + _mac(user_id, network, head)).hex()


def check_payload(payload: str, user_id: str, network: str, now: float | None = None) -> None:
    try:
        raw = bytes.fromhex(payload)
    except (ValueError, TypeError):
        raise HTTPException(400, "unknown sign-in request")
    if len(raw) != 32 or not hmac.compare_digest(raw[16:], _mac(user_id, network, raw[:16])):
        raise HTTPException(400, "this sign-in request wasn't issued to you")
    if (now or time.time()) - struct.unpack(">Q", raw[:8])[0] > PROOF_TTL_S:
        raise HTTPException(400, "the sign-in request expired; connect again")


def proof_domains() -> set[str]:
    s = get_settings()
    if s.ton_proof_domains.strip():
        return {d.strip() for d in s.ton_proof_domains.split(",") if d.strip()}
    hosts = {"helmterminals.xyz", "www.helmterminals.xyz"}
    for o in (s.cors_origins or "").split(","):
        if o.strip():
            hosts.add(urlparse(o.strip()).netloc)
    return hosts


@router.post("/chains/{network}/ton/proof-payload")
async def proof_payload(network: str, user: User = Depends(current_user)):
    _ton_net(network)
    return {"payload": make_payload(user.id, network), "expires_in": PROOF_TTL_S}


class ConnectBody(BaseModel):
    address: str = Field(max_length=80)
    chain: str = Field(max_length=8)
    public_key: str | None = Field(default=None, max_length=64)
    state_init: str | None = Field(default=None, max_length=8000)
    proof: dict
    label: str = Field(default="TON wallet", max_length=80)


async def _wallet_public_key(network: str, address: ton.TonAddress, state_init: str | None) -> bytes:
    if state_init:
        try:
            si = ton.cell_from_b64(state_init)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if si.hash() != address.hash:
            raise HTTPException(400, "the wallet's state doesn't belong to this address")
        try:
            return ton.wallet_public_key(*ton.state_init_parts(si))
        except ValueError as e:
            raise HTTPException(400, str(e))
    try:
        acct = await xc.ton_client(network).account(address.raw(), include_boc=True)
    except AdapterError as e:
        raise HTTPException(502, str(e))
    if acct["status"] != "active" or not acct.get("code_boc") or not acct.get("data_boc"):
        raise HTTPException(400, "the wallet isn't deployed and sent no state to check the proof against")
    try:
        return ton.wallet_public_key(ton.cell_from_b64(acct["code_boc"]), ton.cell_from_b64(acct["data_boc"]))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/chains/{network}/ton/connect", status_code=201)
async def connect(network: str, body: ConnectBody, user: User = Depends(current_user),
                  db: AsyncSession = Depends(get_db)):
    net = _ton_net(network)
    if body.chain != net.network_id:
        raise HTTPException(400, f"the wallet is on another TON network (expected {net.name})")
    address = _addr(body.address)
    payload = str(body.proof.get("payload") or "")
    check_payload(payload, user.id, network)
    dom = body.proof.get("domain")
    domain = str(dom.get("value") or "") if isinstance(dom, dict) else ""
    if domain not in proof_domains():
        raise HTTPException(400, "the proof names a site other than Helm")
    pk = await _wallet_public_key(network, address, body.state_init)
    if body.public_key and body.public_key.lower() != pk.hex():
        raise HTTPException(400, "the wallet's reported key doesn't match its contract")
    try:
        ton.verify_proof(address, body.proof, pk, domain, payload, max_age_s=PROOF_TTL_S)
    except ValueError as e:
        raise HTTPException(400, str(e))
    canonical = xc.ton_address(network, address.raw())
    w = (await db.execute(select(Wallet).where(Wallet.user_id == user.id, Wallet.network == network,
                                               Wallet.address == canonical))).scalar_one_or_none()
    if w is None:
        w = Wallet(user_id=user.id, network=network, address=canonical, kind="connected", label=body.label)
        db.add(w)
    elif w.kind != "connected":
        w.kind = "connected"  # a watched address, now proven to be the user's wallet
    await log_activity(db, user.id, "wallets.ton_connected", payload={"network": network, "address": canonical})
    await db.commit()
    return {"id": w.id, "network": network, "address": canonical, "kind": w.kind, "label": w.label}


# --- transfers ------------------------------------------------------------------------------------------------


class TonTransferBody(SpendConfirm):
    wallet_id: str
    to: str = Field(max_length=80)
    amount: str = Field(max_length=60)
    token: str | None = Field(default=None, max_length=80)  # Jetton master address


def _query_id(*parts: str) -> int:
    return int.from_bytes(hashlib.sha256("|".join(parts).encode()).digest()[:8], "big")


def _hex_hash(h: str | None) -> str | None:
    if not h:
        return None
    if len(h) == 64:
        try:
            return bytes.fromhex(h).hex()
        except ValueError:
            pass
    try:
        b = base64.b64decode(h.replace("-", "+").replace("_", "/") + "==")
        return b.hex() if len(b) == 32 else None
    except ValueError:
        return None


async def _plan(network: str, w: Wallet, to: str, token: str | None, amount: str, user_id: str) -> dict:
    from .chains import _raw_amount

    net = EXTRA_NETWORKS[network]
    c = xc.ton_client(network)
    sender = _addr(w.address)
    dest = _addr(to)
    if dest.same(sender):
        raise HTTPException(400, "the recipient is the sending wallet")
    me = await c.account(sender.raw())
    recipient = await c.account(dest.raw())
    warnings, display = [], {}
    to_wallet = dest.friendly(bounceable=False, testnet=False)
    if token:
        master = _addr(token)
        meta = await c.jetton_master(master.raw())
        raw = _raw_amount(amount, meta["decimals"])
        jw = await c.jetton_wallet(sender.raw(), master.raw())
        if jw is None:
            raise HTTPException(400, f"this wallet holds no {meta['symbol'] or 'tokens'} of that Jetton")
        jw_addr = _addr(jw["address"])
        if not _addr(jw["owner"]).same(sender) or not _addr(jw["jetton"]).same(master):
            raise HTTPException(502, "the index returned a jetton wallet for someone else; not building the transfer")
        if raw > jw["balance"]:
            warnings.append(f"The wallet holds {xc.human(jw['balance'], meta['decimals'])} {meta['symbol']}, "
                            f"less than {amount}.")
        if me["balance"] < ton.JETTON_ATTACH + SEND_FEE_RESERVE:
            warnings.append(f"A Jetton transfer needs about {xc.human(ton.JETTON_ATTACH + SEND_FEE_RESERVE, 9)} TON "
                            f"for fees; the wallet has {xc.human(me['balance'], 9)} TON.")
        try:
            body = ton.jetton_transfer_body(_query_id(user_id, w.id, dest.raw(), master.raw(), str(raw)), raw, dest,
                                            sender)
        except ValueError as e:
            raise HTTPException(400, f"that amount can't be sent: {e}")
        messages = [{"address": jw_addr.friendly(bounceable=True, testnet=False),
                     "amount": str(ton.JETTON_ATTACH), "payload": body.boc_b64()}]
        symbol, decimals, token_out = meta["symbol"], meta["decimals"], master.friendly(True, False)
        display = {"ton_attached": xc.human(ton.JETTON_ATTACH, 9),
                   "fee_note": "0.05 TON is attached to pay the Jetton wallets; the unused part returns to you."}
        if recipient["status"] != "active":
            display["recipient_note"] = ("The recipient's wallet isn't deployed yet; its Jetton wallet is created "
                                         "by this transfer.")
    else:
        raw = _raw_amount(amount, 9)
        bounce = recipient["status"] == "active" and dest.bounceable
        if raw + SEND_FEE_RESERVE > me["balance"]:
            warnings.append(f"The wallet holds {xc.human(me['balance'], 9)} TON, less than {amount} TON plus about "
                            f"{xc.human(SEND_FEE_RESERVE, 9)} TON in fees.")
        messages = [{"address": dest.friendly(bounceable=bounce, testnet=False), "amount": str(raw)}]
        symbol, decimals, token_out = "TON", 9, None
        display = {"fee_note": "Network fees (about 0.005 TON) are paid by your wallet on top of the amount."}
        if recipient["status"] != "active":
            display["recipient_note"] = ("The recipient isn't deployed yet, so this is sent non-bounceable and "
                                         "arrives anyway.")
    if me["status"] == "frozen":
        warnings.append("This wallet is frozen for unpaid storage; top it up before sending.")
    display["finality"] = "Final once the index shows the wallet's transaction (usually seconds)."
    request = {"network": net.network_id, "from": sender.raw(), "messages": messages}
    return {"symbol": symbol, "decimals": decimals, "raw": raw, "to": to_wallet, "token": token_out,
            "warnings": warnings, "display": display, "request": request}


async def _find(c, msg_hash: str, deadline: float) -> dict | None:
    """The transaction that received this message (matched on its normalized hash), or None by the deadline."""
    while True:
        try:
            txs = await c.by_message(msg_hash)
        except AdapterError:
            txs = []
        for tx in txs:
            inm = tx.get("in_msg") or {}
            got = _hex_hash(inm.get("hash_norm")) or _hex_hash(inm.get("hash"))
            if got is None or got == _hex_hash(msg_hash):
                return tx
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(3)


def _mismatch(tx: dict, request: dict) -> str | None:
    """Why the wallet's outgoing messages differ from the request the user reviewed, or None if they match."""
    want = request.get("messages") or []
    outs = tx.get("out_msgs") or []
    if len(outs) != len(want):
        return f"the wallet sent {len(outs)} message(s), not {len(want)}"
    for m, o in zip(want, outs):
        try:
            same_dest = ton.parse_address(o.get("destination") or "").same(ton.parse_address(m["address"]))
        except ValueError:
            same_dest = False
        if not same_dest or str(o.get("value")) != str(m["amount"]):
            return "a message went to a different address or carried a different amount"
        if m.get("payload"):
            body = ((o.get("message_content") or {}).get("body")) or ""
            try:
                if ton.cell_from_b64(body).hash() != ton.cell_from_b64(m["payload"]).hash():
                    return "the Jetton transfer inside differs from the one you reviewed"
            except ValueError:
                return "the Jetton transfer inside couldn't be read"
    return None


async def _follow(network: str, msg_hash: str, request: dict, db_seen) -> SubmitResult:
    """Find the wallet's transaction, check it sent exactly what was reviewed, and for Jettons follow the transfer
    through the sender's and the recipient's jetton wallets, where it can still fail."""
    c = xc.ton_client(network)
    deadline = time.monotonic() + CONFIRM_TIMEOUT_S
    tx = await _find(c, msg_hash, deadline)
    if tx is None:
        return SubmitResult(None, "timeout", f"not on the index yet (message {msg_hash[:16]}…); check your wallet "
                                             "before sending again")
    ref = _hex_hash(tx.get("hash")) or msg_hash
    if await db_seen(ref):
        return SubmitResult(None, "failed", "that signed message was already recorded for another transfer")
    status, err = ton.tx_outcome(tx)
    if status != "confirmed":
        return SubmitResult(ref, status, err)
    why = _mismatch(tx, request)
    if why:
        return SubmitResult(ref, "failed", f"{why}; check the explorer: this isn't the transfer you reviewed")
    if any(m.get("payload") for m in request.get("messages") or []):
        hop = tx
        for step in ("your jetton wallet", "the recipient's jetton wallet"):
            out = (hop.get("out_msgs") or [{}])[0]
            nxt = await _find(c, out.get("hash") or "", deadline) if out.get("hash") else None
            if nxt is None:
                return SubmitResult(ref, "timeout", f"sent; waiting on {step} (check the explorer before retrying)")
            st, e = ton.tx_outcome(nxt)
            if st != "confirmed":
                return SubmitResult(ref, "failed", f"the tokens didn't move: {step} failed ({e}); the attached TON "
                                                   "comes back to you")
            hop = nxt
    return SubmitResult(ref, "confirmed", None)


@router.post("/chains/{network}/ton/transfer")
async def transfer(network: str, body: TonTransferBody, user: User = Depends(current_user),
                   db: AsyncSession = Depends(get_db)):
    if (prior := await find_replay(db, user, body)):
        j = await db.get(Job, prior["job_id"])  # a retried confirm gets the same job and, while pending, its request
        return {**prior, "wallet_request": (j.params or {}).get("wallet_request") if j.status == "pending" else None,
                "valid_for_s": VALID_FOR_S}
    net = _ton_net(network)
    w = await owned_wallet(db, user, body.wallet_id, managed=False)
    if w.network != network:
        raise HTTPException(400, f"that wallet is on {w.network}, not {network}")
    if w.kind != "connected":
        raise HTTPException(400, "connect this wallet with TON Connect to send from it")
    try:
        plan = await _plan(network, w, body.to.strip(), body.token.strip() if body.token else None, body.amount,
                           user.id)
    except AdapterError as e:
        raise HTTPException(502, str(e))
    kind = "ton_transfer"  # the wallet's own approval authorises it; Helm holds no key
    summary = {"network": network, "chain": net.name, "from": w.address, "to": plan["to"],
               "asset": plan["symbol"], "token": plan["token"], "amount": xc.human(plan["raw"], plan["decimals"]),
               "amount_raw": str(plan["raw"]), "decimals": plan["decimals"], "platform_fee": "none",
               "wallet_request": plan["request"], "external_wallet": True}
    pf = build_preflight(kind, summary, plan["warnings"])
    pf["preflight"].update(plan["display"])
    pf["valid_for_s"] = VALID_FOR_S
    if not body.confirm:
        return pf
    if plan["warnings"]:
        raise HTTPException(400, plan["warnings"][0])
    # The job exists before the wallet is asked, so a transfer the wallet broadcasts is always recorded, even if the
    # balance it spends changes before Helm hears back. It waits in "pending" for the signed message.
    job, _ = await begin_spend(db, user, body, pf, kind, None, {**pf["preflight"], "kind": kind})
    return {"job_id": job.id, "status": job.status, "wallet_request": plan["request"] if job.status == "pending"
            else None, "valid_for_s": VALID_FOR_S}


async def _ton_job(db: AsyncSession, user: User, network: str, job_id: str) -> Job:
    _ton_net(network)
    j = await db.get(Job, job_id)
    if not j or j.user_id != user.id or j.kind != "ton_transfer" or (j.params or {}).get("network") != network:
        raise HTTPException(404, "transfer not found")
    return j


class SignedBody(BaseModel):
    signed_boc: str = Field(max_length=20000)  # the wallet's sendTransaction result


@router.post("/chains/{network}/ton/jobs/{job_id}/signed")
async def signed(network: str, job_id: str, body: SignedBody, user: User = Depends(current_user),
                 db: AsyncSession = Depends(get_db)):
    j = await _ton_job(db, user, network, job_id)
    if j.status != "pending":
        return {"job_id": j.id, "status": j.status, "message_hash": (j.params or {}).get("message_hash")}
    try:
        dest, msg_hash = ton.normalized_external_hash(body.signed_boc)
    except ValueError as e:
        raise HTTPException(400, f"the wallet's answer isn't a signed message: {e}")
    if not dest.same(_addr(j.params["from"])):
        raise HTTPException(400, "the signed message is for a different wallet")
    w = (await db.execute(select(Wallet).where(Wallet.user_id == user.id, Wallet.network == network,
                                               Wallet.address == j.params["from"]))).scalar_one_or_none()
    j.params = {**j.params, "message_hash": msg_hash}
    j.status = "submitted"
    await db.commit()
    request = j.params["wallet_request"]

    async def work(session, job):
        async def seen(ref: str) -> bool:
            return (await session.execute(select(Transaction.id).where(Transaction.signature == ref))).first() is not None

        res = await _follow(network, msg_hash, request, seen)
        await _record(session, job, w, "ton_transfer", res, None)
        _finish(job, [res])
        await session.commit()

    await run_job(j.id, work)
    return {"job_id": j.id, "status": j.status, "message_hash": msg_hash}


@router.post("/chains/{network}/ton/jobs/{job_id}/unknown")
async def unknown(network: str, job_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """The wallet errored after approval: it may have sent the transfer. Never "failed", which would invite a resend."""
    j = await _ton_job(db, user, network, job_id)
    if j.status == "pending":
        j.status = "unconfirmed"
        j.params = {**j.params, "error": "The wallet reported an error after you approved, so the transfer may have "
                                         "been sent. Check the wallet's history before sending again."}
        await db.commit()
    return {"job_id": j.id, "status": j.status}


@router.post("/chains/{network}/ton/jobs/{job_id}/declined")
async def declined(network: str, job_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    j = await _ton_job(db, user, network, job_id)
    if j.status == "pending":
        j.status = "failed"
        j.params = {**j.params, "error": "Declined in the wallet (or it expired there). Nothing was sent."}
        await db.commit()
    return {"job_id": j.id, "status": j.status}
