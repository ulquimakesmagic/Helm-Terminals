import base64
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from solders.pubkey import Pubkey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.base import AdapterError, ClaimRequest, CreateRequest
from ..chains.registry import IMPLEMENTED, get_adapter, list_launchpads
from ..chains.solana import (
    LAMPORTS_PER_SOL, SolanaRPC, build_unsigned, create_ata_idempotent_ix, token_burn_checked_ix,
    token_transfer_checked_ix,
)
from ..config import get_settings
from ..db import get_db
from ..deps import (
    SpendConfirm, begin_spend, current_user, find_replay, get_executor, get_rpc, log_activity, owned_wallet, run_job,
    two_fa_warnings, valid_address,
)
from ..models import Job, Launch, LaunchWallet, Transaction, User, Wallet, utcnow
from ..services.executor import Executor, run_single, run_trade
from ..services.preflight import build_preflight
from ..services.transparency import compute_transparency

router = APIRouter(tags=["launches"])
MIN_DEPLOY_BUFFER_SOL = 0.03  # rent for mint/metadata/ATA accounts plus fees
MAX_IMAGE_BYTES = 2_000_000


def _launch_out(l: Launch) -> dict:
    return {
        "id": l.id, "network": l.network, "launchpad": l.launchpad, "status": l.status, "name": l.name,
        "symbol": l.symbol, "description": l.description, "socials": l.socials, "mint_address": l.mint_address,
        "dev_wallet_id": l.dev_wallet_id, "dev_buy_sol": l.dev_buy_sol, "imported": l.imported,
        "has_image": l.image_blob is not None, "created_at": l.created_at.isoformat(),
        "deployed_at": l.deployed_at.isoformat() if l.deployed_at else None,
    }


async def _owned_launch(db: AsyncSession, user: User, launch_id: str) -> Launch:
    l = await db.get(Launch, launch_id)
    if not l or l.user_id != user.id:
        raise HTTPException(404, "launch not found")
    return l


@router.get("/launchpads")
async def launchpads():
    return list_launchpads()


@router.get("/launchpads/{launchpad_id}/options")
async def launchpad_options(launchpad_id: str):
    if launchpad_id not in IMPLEMENTED:
        raise HTTPException(404, "launchpad not available yet (see /launchpads for the roadmap)")
    return get_adapter(launchpad_id).options_schema()


class CreateLaunch(BaseModel):
    launchpad: str = "pumpfun"
    name: str = Field(min_length=1, max_length=32)
    symbol: str = Field(min_length=1, max_length=10)
    description: str = Field(default="", max_length=500)
    image_b64: str | None = None
    socials: dict = Field(default_factory=dict)
    dev_wallet_id: str | None = None
    dev_buy_sol: float = Field(default=0.0, ge=0, le=100)
    slippage_pct: float = Field(default=10.0, ge=0, le=50)


@router.post("/launches", status_code=201)
async def create_launch(body: CreateLaunch, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if body.launchpad not in IMPLEMENTED:
        raise HTTPException(400, f"launchpad '{body.launchpad}' is not implemented yet")
    image = None
    if body.image_b64:
        try:
            image = base64.b64decode(body.image_b64, validate=True)
        except Exception:
            raise HTTPException(400, "image_b64 is not valid base64")
        if len(image) > MAX_IMAGE_BYTES:
            raise HTTPException(400, "image too large (max 2 MB)")
    network = get_adapter(body.launchpad).network
    if body.dev_wallet_id:
        dev = await owned_wallet(db, user, body.dev_wallet_id)
        if dev.network != network:
            raise HTTPException(400, f"dev wallet is on {dev.network} but {body.launchpad} launches on {network}")
    l = Launch(user_id=user.id, network=network, launchpad=body.launchpad, name=body.name, symbol=body.symbol.upper(),
               description=body.description, image_blob=image, socials=body.socials, dev_wallet_id=body.dev_wallet_id,
               dev_buy_sol=body.dev_buy_sol, launchpad_params={"slippage_pct": body.slippage_pct})
    db.add(l)
    await log_activity(db, user.id, "launch.created", "launch", l.id, {"launchpad": body.launchpad})
    await db.commit()
    if body.dev_wallet_id:
        db.add(LaunchWallet(launch_id=l.id, wallet_id=body.dev_wallet_id, role="dev"))
        await db.commit()
    return _launch_out(l)


class CloneBody(BaseModel):
    mint: str
    launchpad: str = "pumpfun"
    dev_wallet_id: str | None = None


@router.post("/launches/clone", status_code=201)
async def clone_launch(body: CloneBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                       rpc: SolanaRPC = Depends(get_rpc)):
    """Copy an existing token's name, symbol and description into a new draft. The image is not copied."""
    if not valid_address(body.mint):
        raise HTTPException(400, "invalid mint address")
    if body.launchpad not in IMPLEMENTED:
        raise HTTPException(400, f"launchpad '{body.launchpad}' is not implemented yet")
    try:
        asset = await rpc.get_asset(body.mint)
    except Exception as e:
        raise HTTPException(502, f"could not read token metadata (needs a DAS-capable RPC such as Helius): {e}")
    meta = (asset.get("content") or {}).get("metadata") or {}
    if body.dev_wallet_id:
        await owned_wallet(db, user, body.dev_wallet_id)
    l = Launch(user_id=user.id, network="solana", launchpad=body.launchpad, name=(meta.get("name") or "Untitled")[:32],
               symbol=(meta.get("symbol") or "TKN")[:10].upper(), description=(meta.get("description") or "")[:500],
               dev_wallet_id=body.dev_wallet_id, launchpad_params={"slippage_pct": 10.0})
    db.add(l)
    await db.commit()
    return _launch_out(l)


class ImportBody(BaseModel):
    mint: str
    network: str = "solana"


@router.post("/launches/import", status_code=201)
async def import_token(body: ImportBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                       rpc: SolanaRPC = Depends(get_rpc)):
    if body.network != "solana" or not valid_address(body.mint):
        raise HTTPException(400, "invalid solana mint")
    try:
        await rpc.get_token_supply(body.mint)
    except Exception:
        raise HTTPException(404, "token not found on chain")
    name, symbol = body.mint[:6], "TKN"
    try:
        meta = ((await rpc.get_asset(body.mint)).get("content") or {}).get("metadata") or {}
        name, symbol = meta.get("name") or name, (meta.get("symbol") or symbol)
    except Exception:
        pass
    l = Launch(user_id=user.id, network="solana", launchpad="pumpfun", status="live", name=name[:32],
               symbol=symbol[:10].upper(), mint_address=body.mint, imported=True, deployed_at=utcnow())
    db.add(l)
    await log_activity(db, user.id, "launch.imported", "launch", l.id, {"mint": body.mint})
    await db.commit()
    return _launch_out(l)


@router.get("/launches")
async def list_launches(status: str | None = None, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    q = select(Launch).where(Launch.user_id == user.id)
    q = q.where(Launch.status == status) if status else q.where(Launch.status != "archived")
    return [_launch_out(l) for l in (await db.execute(q.order_by(Launch.created_at.desc()))).scalars()]


@router.get("/launches/{launch_id}")
async def get_launch(launch_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return _launch_out(await _owned_launch(db, user, launch_id))


class AttachWallets(BaseModel):
    wallet_ids: list[str] = Field(min_length=1, max_length=50)
    role: str = "holder"


@router.post("/launches/{launch_id}/wallets")
async def attach_wallets(launch_id: str, body: AttachWallets, user: User = Depends(current_user),
                         db: AsyncSession = Depends(get_db)):
    l = await _owned_launch(db, user, launch_id)
    if body.role not in ("dev", "holder"):
        raise HTTPException(400, "role must be dev or holder")
    for wid in body.wallet_ids:
        await owned_wallet(db, user, wid, managed=False)
        if await db.get(LaunchWallet, (l.id, wid)) is None:
            db.add(LaunchWallet(launch_id=l.id, wallet_id=wid, role=body.role))
    await db.commit()
    return {"attached": len(body.wallet_ids)}


@router.post("/launches/{launch_id}/archive")
async def archive(launch_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    l = await _owned_launch(db, user, launch_id)
    l.status, l.archived_at = "archived", utcnow()
    await log_activity(db, user.id, "launch.archived", "launch", l.id)
    await db.commit()
    return _launch_out(l)


@router.post("/launches/{launch_id}/restore")
async def restore(launch_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    l = await _owned_launch(db, user, launch_id)
    if l.status != "archived":
        raise HTTPException(409, "launch is not archived")
    l.status = "live" if l.mint_address else "draft"
    l.archived_at = None
    await db.commit()
    return _launch_out(l)


# --- deploy --------------------------------------------------------------------------------------


class DeployBody(SpendConfirm):
    pass


@router.post("/launches/{launch_id}/deploy")
async def deploy(launch_id: str, body: DeployBody, user: User = Depends(current_user),
                 db: AsyncSession = Depends(get_db), rpc: SolanaRPC = Depends(get_rpc),
                 ex: Executor = Depends(get_executor)):
    if (prior := await find_replay(db, user, body)):
        return prior
    l = await _owned_launch(db, user, launch_id)
    if l.status not in ("draft", "failed"):
        raise HTTPException(409, f"launch is {l.status}")
    if not l.dev_wallet_id:
        raise HTTPException(400, "set a dev wallet before deploying")
    dev = await owned_wallet(db, user, l.dev_wallet_id)
    adapter = get_adapter(l.launchpad)
    bal = await rpc.get_balance(dev.address)
    need = l.dev_buy_sol + MIN_DEPLOY_BUFFER_SOL
    warnings = []
    if bal < int(need * LAMPORTS_PER_SOL):
        warnings.append(f"Dev wallet has {bal / LAMPORTS_PER_SOL:.4f} SOL; needs about {need:.4f} SOL (dev buy + rent and fees).")
    pf = build_preflight("deploy", {
        "launch_id": l.id, "launchpad": l.launchpad, "name": l.name, "symbol": l.symbol,
        "dev_wallet": dev.address, "dev_buy_sol": l.dev_buy_sol, "platform_fee": "none on deploy",
    }, warnings)
    if not body.confirm:
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, "deploy", l.id, pf["preflight"])
    if created:
        l.status = "deploying"
        await db.commit()
        slippage = (l.launchpad_params or {}).get("slippage_pct", 10.0)

        async def work(session, j):
            launch = await session.get(Launch, l.id)
            req = CreateRequest(name=l.name, symbol=l.symbol, description=l.description, image_bytes=l.image_blob,
                                socials=l.socials or {}, dev_pubkey=dev.address, dev_buy_sol=l.dev_buy_sol,
                                slippage_pct=slippage)
            try:
                built = await adapter.build_create_tx(req)
            except AdapterError as e:
                j.status, j.params = "failed", {**j.params, "error": str(e)}
                launch.status = "failed"
                await session.commit()
                return
            res = await run_single(session, ex, j, dev, "deploy", built.raw, {dev.address: dev.signer_ref or ""},
                                   built.extra_signers)
            if res.status == "confirmed":
                launch.status, launch.mint_address, launch.deployed_at = "live", built.mint, utcnow()
            else:
                launch.status = "failed"
            await session.commit()

        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


# --- trading -------------------------------------------------------------------------------------


class TradeBody(SpendConfirm):
    side: str = Field(pattern="^(buy|sell)$")
    wallet_ids: list[str] = Field(min_length=1, max_length=50)
    amount: float = Field(gt=0)  # SOL per wallet for buys; percent of each wallet's tokens for sells
    mode: str = Field(default="stagger", pattern="^(stagger|parallel)$")
    stagger_ms: int = Field(default=1000, ge=0, le=60_000)
    slippage_pct: float = Field(default=10.0, ge=0, le=50)


async def _attached_wallets(db: AsyncSession, user: User, launch: Launch, ids: list[str]) -> list[Wallet]:
    out = []
    for wid in ids:
        w = await owned_wallet(db, user, wid)
        if await db.get(LaunchWallet, (launch.id, wid)) is None:
            raise HTTPException(400, f"wallet {wid} is not attached to this launch")
        out.append(w)
    return out


@router.post("/launches/{launch_id}/trade")
async def trade(launch_id: str, body: TradeBody, user: User = Depends(current_user),
                db: AsyncSession = Depends(get_db), rpc: SolanaRPC = Depends(get_rpc),
                ex: Executor = Depends(get_executor)):
    """Manual, user-initiated buy or sell from wallets you own. No automated dumping, no wash trading."""
    if (prior := await find_replay(db, user, body)):
        return prior
    l = await _owned_launch(db, user, launch_id)
    if l.status != "live" or not l.mint_address:
        raise HTTPException(409, "launch is not live")
    if body.side == "sell" and body.amount > 100:
        raise HTTPException(400, "sell amount is a percentage (1-100)")
    wallets = await _attached_wallets(db, user, l, body.wallet_ids)
    s = get_settings()
    warnings = []
    if body.side == "buy":
        for w in wallets:
            if await rpc.get_balance(w.address) < int((body.amount + 0.01) * LAMPORTS_PER_SOL):
                warnings.append(f"{w.address} has too little SOL for a {body.amount} SOL buy plus fees.")
    if body.side == "buy" and getattr(user, "channel", "web") == "bot":
        # Cap how much a hijacked or mistaken chat session can spend on buys. Larger orders go through the web app.
        since = utcnow() - timedelta(hours=24)
        prior = (await db.execute(select(Job).where(
            Job.user_id == user.id, Job.channel == "bot", Job.kind == "trade_buy",
            Job.status != "failed", Job.created_at >= since))).scalars().all()
        spent = sum(float(j.params.get("amount", 0)) * len(j.params.get("wallets", [])) for j in prior)
        new = body.amount * len(wallets)
        if spent + new > s.bot_daily_buy_limit_sol:
            warnings.append(
                f"Telegram daily buy limit: {spent:.2f} of {s.bot_daily_buy_limit_sol:.2f} SOL used in the last 24h; "
                f"this order adds {new:.2f} SOL. Use the web app for larger orders.")
    fee_note = f"{s.platform_fee_bps / 100:.2f}% of SOL received" if body.side == "sell" and s.platform_fee_bps else "none"
    pf = build_preflight("trade", {
        "launch_id": l.id, "mint": l.mint_address, "side": body.side, "amount": body.amount,
        "amount_unit": "SOL per wallet" if body.side == "buy" else "% of each wallet's tokens",
        "wallets": [w.address for w in wallets], "mode": body.mode, "stagger_ms": body.stagger_ms,
        "slippage_pct": body.slippage_pct, "platform_fee": fee_note,
    }, warnings)
    if not body.confirm:
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, f"trade_{body.side}", l.id, pf["preflight"])
    if created:
        adapter = get_adapter(l.launchpad)
        amount = body.amount if body.side == "buy" else f"{body.amount:g}%"

        async def work(session, j):
            await run_trade(session, ex, adapter, j, l.mint_address, wallets, body.side, amount,
                            body.side == "buy", body.mode, body.stagger_ms, body.slippage_pct,
                            s.platform_fee_bps, s.platform_fee_wallet)

        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


class ClaimBody(SpendConfirm):
    wallet_id: str


@router.post("/launches/{launch_id}/claim-fees")
async def claim_fees(launch_id: str, body: ClaimBody, user: User = Depends(current_user),
                     db: AsyncSession = Depends(get_db), ex: Executor = Depends(get_executor)):
    if (prior := await find_replay(db, user, body)):
        return prior
    l = await _owned_launch(db, user, launch_id)
    w = await owned_wallet(db, user, body.wallet_id)
    adapter = get_adapter(l.launchpad)
    pf = build_preflight("claim_fees", {"launch_id": l.id, "wallet": w.address, "platform_fee": "none"})
    if not body.confirm:
        return pf
    job, created = await begin_spend(db, user, body, pf, "claim_fees", l.id, pf["preflight"])
    if created:
        async def work(session, j):
            built = await adapter.build_claim_fees_tx(ClaimRequest(wallet_pubkey=w.address, mint=l.mint_address))
            if built is None:
                j.status = "failed"
                await session.commit()
                return
            await run_single(session, ex, j, w, "claim_fees", built.raw, {w.address: w.signer_ref or ""})

        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


class BurnBody(SpendConfirm):
    wallet_id: str
    percent: float = Field(gt=0, le=100)


@router.post("/launches/{launch_id}/burn")
async def burn(launch_id: str, body: BurnBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
               rpc: SolanaRPC = Depends(get_rpc), ex: Executor = Depends(get_executor)):
    if (prior := await find_replay(db, user, body)):
        return prior
    l = await _owned_launch(db, user, launch_id)
    if not l.mint_address:
        raise HTTPException(409, "launch has no mint yet")
    w = await owned_wallet(db, user, body.wallet_id)
    raw_bal = await rpc.get_token_balance(w.address, l.mint_address)
    _, decimals = await rpc.get_token_supply(l.mint_address)
    amount = int(raw_bal * body.percent / 100)
    warnings = [] if amount > 0 else ["Nothing to burn: token balance is zero."]
    pf = build_preflight("burn", {"launch_id": l.id, "wallet": w.address, "mint": l.mint_address,
                                  "tokens": amount / 10**decimals, "percent": body.percent,
                                  "irreversible": True, "platform_fee": "none"}, warnings)
    if not body.confirm:
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, "burn", l.id, pf["preflight"])
    if created:
        async def work(session, j):
            owner, mint = Pubkey.from_string(w.address), Pubkey.from_string(l.mint_address)
            raw = build_unsigned(owner, [token_burn_checked_ix(owner, mint, amount, decimals)],
                                 await ex.rpc.get_latest_blockhash())
            await run_single(session, ex, j, w, "burn", raw, {w.address: w.signer_ref or ""})

        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


class SendBody(SpendConfirm):
    wallet_id: str
    recipient: str
    percent: float = Field(gt=0, le=100)


@router.post("/launches/{launch_id}/send")
async def send_tokens(launch_id: str, body: SendBody, user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_db), rpc: SolanaRPC = Depends(get_rpc),
                      ex: Executor = Depends(get_executor)):
    if (prior := await find_replay(db, user, body)):
        return prior
    l = await _owned_launch(db, user, launch_id)
    if not l.mint_address:
        raise HTTPException(409, "launch has no mint yet")
    if not valid_address(body.recipient):
        raise HTTPException(400, "invalid recipient address")
    w = await owned_wallet(db, user, body.wallet_id)
    raw_bal = await rpc.get_token_balance(w.address, l.mint_address)
    _, decimals = await rpc.get_token_supply(l.mint_address)
    amount = int(raw_bal * body.percent / 100)
    warnings = [] if amount > 0 else ["Nothing to send: token balance is zero."]
    warnings += two_fa_warnings(user, "send_tokens")
    pf = build_preflight("send_tokens", {"launch_id": l.id, "from": w.address, "to": body.recipient,
                                         "mint": l.mint_address, "tokens": amount / 10**decimals,
                                         "platform_fee": "none"}, warnings)
    if not body.confirm:
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, "send_tokens", l.id, pf["preflight"])
    if created:
        async def work(session, j):
            owner, dest = Pubkey.from_string(w.address), Pubkey.from_string(body.recipient)
            mint = Pubkey.from_string(l.mint_address)
            ixs = [create_ata_idempotent_ix(owner, dest, mint),
                   token_transfer_checked_ix(owner, dest, mint, amount, decimals)]
            raw = build_unsigned(owner, ixs, await ex.rpc.get_latest_blockhash())
            await run_single(session, ex, j, w, "send_tokens", raw, {w.address: w.signer_ref or ""})

        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


@router.post("/launches/{launch_id}/lock", status_code=501)
async def lock_supply(launch_id: str):
    raise HTTPException(501, "Streamflow lock is planned for milestone 4; it needs Streamflow's SDK/program integration.")


# --- workspace -----------------------------------------------------------------------------------


@router.get("/launches/{launch_id}/workspace")
async def workspace(launch_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                    rpc: SolanaRPC = Depends(get_rpc)):
    l = await _owned_launch(db, user, launch_id)
    links = (await db.execute(select(LaunchWallet).where(LaunchWallet.launch_id == l.id))).scalars().all()
    wallets = [await db.get(Wallet, lk.wallet_id) for lk in links]
    out: dict = {"launch": _launch_out(l), "wallets": [], "transactions": [], "transparency": None, "rpc_error": None}
    controlled: dict[str, int] = {}
    decimals = 6
    try:
        for w, lk in zip(wallets, links):
            sol = await rpc.get_balance(w.address)
            tok = await rpc.get_token_balance(w.address, l.mint_address) if l.mint_address else 0
            controlled[w.address] = tok
            out["wallets"].append({"id": w.id, "address": w.address, "label": w.label, "role": lk.role,
                                   "sol": sol / LAMPORTS_PER_SOL, "token_raw": tok})
        if l.mint_address:
            supply, decimals = await rpc.get_token_supply(l.mint_address)
            holders = await rpc.get_largest_owners(l.mint_address)
            out["transparency"] = compute_transparency(supply, decimals, controlled, holders,
                                                      get_settings().controlled_supply_warn_pct)
    except Exception as e:
        out["rpc_error"] = str(e)[:200]
    for ww in out["wallets"]:
        ww["tokens"] = ww["token_raw"] / 10**decimals
    txs = (await db.execute(
        select(Transaction).join(Job, Job.id == Transaction.job_id).where(Job.launch_id == l.id)
        .order_by(Transaction.created_at.desc()).limit(50))).scalars().all()
    out["transactions"] = [{"id": t.id, "kind": t.kind, "status": t.status, "signature": t.signature,
                            "error": t.error, "sol_delta": (t.sol_delta_lamports or 0) / LAMPORTS_PER_SOL,
                            "created_at": t.created_at.isoformat()} for t in txs]
    confirmed = [t for t in txs if t.status == "confirmed" and t.sol_delta_lamports is not None]
    out["pnl"] = {"net_sol_flow": sum(t.sol_delta_lamports for t in confirmed) / LAMPORTS_PER_SOL,
                  "confirmed_txs": len(confirmed),
                  "note": "Net SOL flow from confirmed transactions, including fees. Unsold tokens are not valued."}
    return out


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    j = await db.get(Job, job_id)
    if not j or j.user_id != user.id:
        raise HTTPException(404, "job not found")
    txs = (await db.execute(select(Transaction).where(Transaction.job_id == j.id))).scalars().all()
    return {"id": j.id, "kind": j.kind, "status": j.status, "error": (j.params or {}).get("error"),
            "transactions": [{"id": t.id, "kind": t.kind, "status": t.status, "signature": t.signature,
                              "error": t.error} for t in txs]}
