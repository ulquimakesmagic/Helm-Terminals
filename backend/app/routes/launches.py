import base64
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from solders.pubkey import Pubkey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.base import AdapterError, ClaimRequest, CreateRequest
from ..chains.evm import EVM_CHAINS, WEI
from ..chains.registry import IMPLEMENTED, get_adapter, is_evm, list_launchpads, needs_setup
from ..chains.solana import (
    LAMPORTS_PER_SOL, RPCError, RPCUnavailable, SolanaRPC, build_unsigned, create_ata_idempotent_ix, token_burn_checked_ix,
    token_transfer_checked_ix,
)
from ..config import get_settings
from ..db import get_db
from ..deps import (
    SpendConfirm, begin_spend, current_user, find_replay, get_executor, get_rpc, log_activity, owned_wallet, run_job,
    two_fa_warnings, valid_address, valid_address_for,
)
from ..models import Job, Launch, LaunchWallet, Transaction, User, Wallet, utcnow
from ..services.executor import Executor, run_built, run_evm_plan, run_evm_trade, run_single, run_trade
from ..services.fees import describe, fee_plan_for
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
        "quote": (l.launchpad_params or {}).get("quote"),
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


SOL_QUOTE = {"mint": "So11111111111111111111111111111111111111112", "symbol": "SOL", "name": "Solana",
             "decimals": 9, "kind": "native", "pool": "Bonding curve"}
PAIR_NOTICE = ("Pair tokens can include tokenized stocks (for example xStocks). Tokenized stocks give price exposure, "
               "not ownership of the shares, and are not available to people in the US, UK, Canada, Australia or "
               "sanctioned countries.")


@router.get("/launchpads/{launchpad_id}/quote-assets")
async def quote_assets(launchpad_id: str):
    """What a new launch on this launchpad can be paired with. SOL always; Bags also offers pair tokens."""
    if launchpad_id not in IMPLEMENTED:
        raise HTTPException(404, "unknown launchpad")
    adapter = get_adapter(launchpad_id)
    if is_evm(adapter):
        chain = EVM_CHAINS[adapter.network]
        return {"assets": [{"mint": "0x0000000000000000000000000000000000000000", "symbol": chain["native"],
                            "name": chain["name"], "decimals": 18, "kind": "native", "pool": "Bonding curve"}],
                "notice": None}
    out = [SOL_QUOTE]
    if launchpad_id == "bags" and not needs_setup("bags"):
        try:
            pairs = await get_adapter("bags").supported_quotes()
        except AdapterError as e:
            return {"assets": out, "error": str(e), "notice": PAIR_NOTICE}
        out += [{**t, "kind": "pair", "pool": "Meteora pool, no bonding curve"} for t in pairs]
    return {"assets": out, "notice": PAIR_NOTICE if len(out) > 1 else None}


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
    quote_mint: str | None = None  # pair the token with this instead of SOL (Bags only)
    eligibility_confirmed: bool = False  # required for pair-token launches (tokenized-stock restrictions)
    options: dict = Field(default_factory=dict)  # launchpad-specific settings from /launchpads/{id}/options


@router.post("/launches", status_code=201)
async def create_launch(body: CreateLaunch, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if body.launchpad not in IMPLEMENTED:
        raise HTTPException(400, f"launchpad '{body.launchpad}' is not implemented yet")
    if (reason := needs_setup(body.launchpad)):
        raise HTTPException(400, reason)
    image = None
    if body.image_b64:
        try:
            image = base64.b64decode(body.image_b64, validate=True)
        except Exception:
            raise HTTPException(400, "image_b64 is not valid base64")
        if len(image) > MAX_IMAGE_BYTES:
            raise HTTPException(400, "image too large (max 2 MB)")
    adapter = get_adapter(body.launchpad)
    network = adapter.network
    params: dict = {"slippage_pct": body.slippage_pct}
    allowed = {f["key"]: f for f in adapter.options_schema().get("fields", [])}
    for k, v in (body.options or {}).items():
        f = allowed.get(k)
        if not f or k in ("dev_buy_sol", "slippage_pct"):
            continue
        if f.get("type") == "select" and v not in f.get("options", []):
            raise HTTPException(400, f"{f.get('label', k)} must be one of {f.get('options')}")
        params[k] = v
    if body.quote_mint and body.quote_mint != SOL_QUOTE["mint"]:
        if body.launchpad != "bags":
            raise HTTPException(400, "pairing with a token other than SOL is only available on Bags")
        if not body.eligibility_confirmed:
            raise HTTPException(400, "confirm you are eligible to use tokenized-stock pairs before creating this launch")
        if body.dev_buy_sol:
            raise HTTPException(400, "pair-token launches don't support a dev buy yet; set it to 0")
        try:
            pairs = await get_adapter("bags").supported_quotes()
        except AdapterError as e:
            raise HTTPException(502, f"could not load Bags pair tokens: {e}")
        quote = next((t for t in pairs if t["mint"] == body.quote_mint), None)
        if not quote:
            raise HTTPException(400, "Bags does not support that pair token")
        params["quote"] = {k: quote[k] for k in ("mint", "symbol", "name", "decimals", "image")}
        params["eligibility_confirmed_at"] = utcnow().isoformat()
    if body.dev_wallet_id:
        dev = await owned_wallet(db, user, body.dev_wallet_id)
        if dev.network != network:
            raise HTTPException(400, f"dev wallet is on {dev.network} but {body.launchpad} launches on {network}")
    l = Launch(user_id=user.id, network=network, launchpad=body.launchpad, name=body.name, symbol=body.symbol.upper(),
               description=body.description, image_blob=image, socials=body.socials, dev_wallet_id=body.dev_wallet_id,
               dev_buy_sol=body.dev_buy_sol, launchpad_params=params)
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
    except RPCError as e:
        # The node answered: the address exists but isn't a token mint, or doesn't exist at all.
        raise HTTPException(404, f"that address isn't a token mint on Solana mainnet ({str(e)[:120]})")
    except Exception as e:
        # The node didn't answer properly (rate limit, blocked, bad URL): not the user's fault.
        detail = str(e)[:200] if isinstance(e, RPCUnavailable) else type(e).__name__  # never echo URLs or keys
        raise HTTPException(502, f"couldn't reach the Solana RPC to check this token ({detail}). "
                                 "Check SOLANA_RPC_URL on the server; the public endpoint often blocks cloud hosts.")
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
    if is_evm(adapter):
        return await _deploy_evm(l, body, user, db, ex, dev, adapter)
    bal = await rpc.get_balance(dev.address)
    need = l.dev_buy_sol + MIN_DEPLOY_BUFFER_SOL
    warnings = []
    if bal < int(need * LAMPORTS_PER_SOL):
        warnings.append(f"Dev wallet has {bal / LAMPORTS_PER_SOL:.4f} SOL; needs about {need:.4f} SOL (dev buy + rent and fees).")
    if adapter.options_schema().get("requires_image") and not l.image_blob:
        warnings.append(f"{l.launchpad} needs a token image. Add one to the launch first.")
    if (reason := needs_setup(l.launchpad)):
        warnings.append(reason)
    if hasattr(adapter, "deploy_warnings"):
        warnings += adapter.deploy_warnings(l.dev_buy_sol, dict(l.launchpad_params or {}))
    summary = {
        "launch_id": l.id, "launchpad": l.launchpad, "name": l.name, "symbol": l.symbol,
        "dev_wallet": dev.address, "dev_buy_sol": l.dev_buy_sol, "platform_fee": "none on deploy",
    }
    if hasattr(adapter, "deploy_notes"):
        summary.update(adapter.deploy_notes(dict(l.launchpad_params or {})))
    quote = (l.launchpad_params or {}).get("quote")
    if l.launchpad == "bags" and quote:
        s = get_settings()
        summary["paired_with"] = f"{quote['symbol']} ({quote.get('name') or quote['mint']})"
        summary["pool"] = f"Meteora DAMM v2 pool, no bonding curve; liquidity is locked; a 2% pool fee accrues in {quote['symbol']}"
        summary["creator_fees"] = f"Your share of pool fees is paid in {quote['symbol']} and claimed on bags.fm for now"
        if s.bags_partner_wallet:
            summary["partner_share"] = "Bags pays Helm a partner share of this pool's fees (from Bags' side, not yours)"
        summary["steps"] = "up to 4 transactions: lookup table, token, launch, then lookup-table clean-up"
    elif l.launchpad == "bags":
        s = get_settings()
        summary["creator_fees"] = "100% of this token's creator fees go to your dev wallet"
        if s.bags_partner_wallet and s.bags_partner_config:
            summary["partner_share"] = "Bags pays Helm a partner share of this token's trading fees (from Bags' side, not yours)"
        summary["steps"] = "2 transactions: fee-share setup, then the launch"
    pf = build_preflight("deploy", summary, warnings)
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
                                slippage_pct=slippage, options=dict(l.launchpad_params or {}))
            try:
                built = await adapter.build_create_tx(req)
            except AdapterError as e:
                j.status, j.params = "failed", {**j.params, "error": str(e)}
                launch.status = "failed"
                await session.commit()
                return
            res = await run_built(session, ex, j, dev, "deploy", built, {dev.address: dev.signer_ref or ""})
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
    if l.network in EVM_CHAINS:
        return await _trade_evm(l, body, user, db, ex, wallets)
    s = get_settings()
    plan = await fee_plan_for(db, user, l.network)
    warnings = []
    if body.side == "buy":
        fee_sol = body.amount * plan.bps / 10_000 if plan.active else 0.0
        for w in wallets:
            if await rpc.get_balance(w.address) < int((body.amount + fee_sol + 0.01) * LAMPORTS_PER_SOL):
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
    fee_note = describe(plan, body.side, body.amount if body.side == "buy" else None)
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
                            body.side == "buy", body.mode, body.stagger_ms, body.slippage_pct, plan)

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
    if created and is_evm(adapter):
        async def work(session, j):
            try:
                plan = await adapter.prepare_claim(ClaimRequest(wallet_pubkey=w.address, mint=l.mint_address,
                                                                options=dict(l.launchpad_params or {})))
            except AdapterError as e:
                j.status, j.params = "failed", {**j.params, "error": str(e)}
                await session.commit()
                return
            await run_evm_plan(session, ex, j, w, "claim_fees", plan)

        await run_job(job.id, work)
    elif created:
        async def work(session, j):
            try:
                built = await adapter.build_claim_fees_tx(ClaimRequest(wallet_pubkey=w.address, mint=l.mint_address,
                                                                       options=dict(l.launchpad_params or {})))
            except AdapterError as e:
                j.status, j.params = "failed", {**j.params, "error": str(e)}
                await session.commit()
                return
            if built is None:
                j.status, j.params = "failed", {**j.params, "error": "nothing to claim"}
                await session.commit()
                return
            await run_built(session, ex, j, w, "claim_fees", built, {w.address: w.signer_ref or ""})

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
    if l.network in EVM_CHAINS:
        return await _token_out_evm(l, body, user, db, ex, w, BURN_ADDRESS, "burn")
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
    w = await owned_wallet(db, user, body.wallet_id)
    if l.network in EVM_CHAINS:
        if not valid_address_for(l.network, body.recipient):
            raise HTTPException(400, f"invalid {l.network} recipient address")
        return await _token_out_evm(l, body, user, db, ex, w, body.recipient, "send_tokens")
    if not valid_address(body.recipient):
        raise HTTPException(400, "invalid recipient address")
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
                    rpc: SolanaRPC = Depends(get_rpc), ex: Executor = Depends(get_executor)):
    l = await _owned_launch(db, user, launch_id)
    links = (await db.execute(select(LaunchWallet).where(LaunchWallet.launch_id == l.id))).scalars().all()
    wallets = [await db.get(Wallet, lk.wallet_id) for lk in links]
    out: dict = {"launch": _launch_out(l), "wallets": [], "transactions": [], "transparency": None, "rpc_error": None}
    controlled: dict[str, int] = {}
    decimals = 6
    evm = l.network in EVM_CHAINS
    try:
        if evm:
            erpc = ex.evm(l.network).rpc
            for w, lk in zip(wallets, links):
                native_bal = await erpc.get_balance(w.address)
                tok = await erpc.erc20_balance(l.mint_address, w.address) if l.mint_address else 0
                controlled[w.address] = tok
                out["wallets"].append({"id": w.id, "address": w.address, "label": w.label, "role": lk.role,
                                       "sol": native_bal / WEI, "token_raw": tok})
            if l.mint_address:
                meta = await erpc.erc20_meta(l.mint_address)
                decimals = meta["decimals"]
                # EVM nodes can't list a token's largest holders, so only your own wallets' share is shown.
                out["transparency"] = compute_transparency(meta["total_supply"], decimals, controlled, [],
                                                          get_settings().controlled_supply_warn_pct)
        else:
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
    if evm:
        out["pnl"]["note"] = "Net flow isn't tracked for EVM launches yet. Wallet balances above are live."
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


# --- EVM launches (Flap and other EVM launchpads) --------------------------------------------------
# Amounts named `*_sol` in requests and previews are in the chain's own coin here (ETH or BNB).

BURN_ADDRESS = "0x000000000000000000000000000000000000dEaD"
EVM_DEPLOY_GAS = 3_000_000  # generous upper bound for a launchpad create, used only for the balance check
EVM_TRADE_GAS = 400_000


async def _deploy_evm(l: Launch, body: DeployBody, user: User, db: AsyncSession, ex: Executor, dev: Wallet, adapter):
    sender = ex.evm(l.network)
    coin = EVM_CHAINS[l.network]["native"]
    params = dict(l.launchpad_params or {})
    gas = await sender.max_gas_cost(EVM_DEPLOY_GAS)
    dev_buy = int(round(l.dev_buy_sol * WEI))
    launch_fee = 0
    if hasattr(adapter, "launch_fee"):
        try:
            launch_fee = await adapter.launch_fee()
        except Exception:
            launch_fee = 0
    bal = await sender.rpc.get_balance(dev.address)
    warnings = []
    if bal < dev_buy + launch_fee + gas:
        warnings.append(f"Dev wallet has {bal / WEI:.6f} {coin}; needs about {(dev_buy + launch_fee + gas) / WEI:.6f} "
                        f"{coin} (dev buy, launch fee and gas).")
    if adapter.options_schema().get("requires_image") and not l.image_blob:
        warnings.append(f"{l.launchpad} needs a token image. Add one to the launch first.")
    summary = {
        "launch_id": l.id, "network": l.network, "launchpad": l.launchpad, "name": l.name, "symbol": l.symbol,
        "dev_wallet": dev.address, "dev_buy_sol": l.dev_buy_sol, "max_gas": gas / WEI,
        "platform_fee": "none on deploy",
    }
    if hasattr(adapter, "deploy_notes"):
        summary.update(adapter.deploy_notes(params))
    if hasattr(adapter, "deploy_warnings"):
        warnings += adapter.deploy_warnings(l.dev_buy_sol, params)
    if launch_fee:
        summary["launch_fee"] = f"{launch_fee / WEI:g} {coin}, paid to {l.launchpad}"
    pf = build_preflight("deploy", summary, warnings)
    if not body.confirm:
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, "deploy", l.id, pf["preflight"])
    if created:
        l.status = "deploying"
        await db.commit()

        async def work(session, j):
            launch = await session.get(Launch, l.id)
            opts = dict(params)
            if l.image_blob:
                opts["logo_url"] = f"{get_settings().public_api_url.rstrip('/')}/public/launch-images/{l.id}"
            from ..services.evm_exec import sign_text

            async def sign_login(message: str) -> str:
                return await sign_text(ex.signer, dev.signer_ref or "", message)

            req = CreateRequest(name=l.name, symbol=l.symbol, description=l.description, image_bytes=l.image_blob,
                                socials=l.socials or {}, dev_pubkey=dev.address, dev_buy_sol=l.dev_buy_sol,
                                slippage_pct=params.get("slippage_pct", 10.0), options=opts, sign_text=sign_login)
            try:
                plan = await adapter.prepare_create(req)
            except AdapterError as e:
                j.status, j.params = "failed", {**j.params, "error": str(e)}
                launch.status = "failed"
                await session.commit()
                return
            res = await run_evm_plan(session, ex, j, dev, "deploy", plan)
            token = plan.token
            if res.status == "confirmed" and plan.resolve_token:
                token = await plan.resolve_token(getattr(res, "logs", None) or []) or token
            if res.status == "confirmed" and not token:
                j.params = {**j.params, "error": "launched, but the token address couldn't be read; check the explorer"}
            if res.status == "confirmed":
                launch.status, launch.mint_address, launch.deployed_at = "live", token, utcnow()
            else:
                launch.status = "failed"
            await session.commit()

        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


async def _trade_evm(l: Launch, body: TradeBody, user: User, db: AsyncSession, ex: Executor, wallets: list[Wallet]):
    sender = ex.evm(l.network)
    coin = EVM_CHAINS[l.network]["native"]
    warnings = []
    if getattr(user, "channel", "web") == "bot":
        warnings.append(f"Trading on {EVM_CHAINS[l.network]['name']} from Telegram isn't enabled yet. Use the web app.")
    gas = await sender.max_gas_cost(EVM_TRADE_GAS)
    if body.side == "buy":
        for w in wallets:
            if await sender.rpc.get_balance(w.address) < int(round(body.amount * WEI)) + gas:
                warnings.append(f"{w.address} has too little {coin} for a {body.amount} {coin} buy plus gas.")
    pf = build_preflight("trade", {
        "launch_id": l.id, "network": l.network, "mint": l.mint_address, "side": body.side, "amount": body.amount,
        "amount_unit": f"{coin} per wallet" if body.side == "buy" else "% of each wallet's tokens",
        "wallets": [w.address for w in wallets], "mode": body.mode, "stagger_ms": body.stagger_ms,
        "slippage_pct": body.slippage_pct, "max_gas_each": gas / WEI,
        "platform_fee": (f"none on bonding-curve trades; {ex.dex.fee_note} if the trade is routed through a DEX"
                         if ex.dex.fee_bps else "none"),
    }, warnings)
    if not body.confirm:
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, f"trade_{body.side}", l.id, pf["preflight"])
    if created:
        adapter = get_adapter(l.launchpad)

        async def work(session, j):
            await run_evm_trade(session, ex, adapter, j, l.mint_address, wallets, body.side, body.amount,
                                body.mode, body.stagger_ms, body.slippage_pct)

        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


async def _token_out_evm(l: Launch, body, user: User, db: AsyncSession, ex: Executor, w: Wallet, to: str, kind: str):
    """Burn (send to the dead address) or send a share of an ERC-20 balance."""
    from eth_abi import encode as abi_encode

    from ..chains.base import EvmPlan, EvmStep
    from ..chains.flap import selector

    erpc = ex.evm(l.network).rpc
    raw_bal = await erpc.erc20_balance(l.mint_address, w.address)
    meta = await erpc.erc20_meta(l.mint_address)
    amount = raw_bal * int(round(body.percent * 100)) // 10_000
    warnings = [] if amount > 0 else [f"Nothing to {'burn' if kind == 'burn' else 'send'}: token balance is zero."]
    if kind == "send_tokens":
        warnings += two_fa_warnings(user, "send_tokens")
        summary = {"launch_id": l.id, "network": l.network, "from": w.address, "to": to, "mint": l.mint_address,
                   "tokens": amount / 10 ** meta["decimals"], "platform_fee": "none"}
    else:
        summary = {"launch_id": l.id, "network": l.network, "wallet": w.address, "mint": l.mint_address,
                   "tokens": amount / 10 ** meta["decimals"], "percent": body.percent,
                   "burn_address": to, "irreversible": True, "platform_fee": "none"}
    pf = build_preflight(kind, summary, warnings)
    if not body.confirm:
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, kind, l.id, pf["preflight"])
    if created:
        data = selector("transfer(address,uint256)") + abi_encode(["address", "uint256"], [to, amount])

        async def work(session, j):
            await run_evm_plan(session, ex, j, w, kind, EvmPlan([EvmStep(kind, l.mint_address, data)]))

        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


@router.get("/public/launch-images/{launch_id}")
async def launch_image(launch_id: str, db: AsyncSession = Depends(get_db)):
    """Token logo for launchpads that store an image URL on-chain (e.g. Pons). Only served once the launch is
    being deployed or is live, and only its image, nothing else about it."""
    from fastapi import Response

    l = await db.get(Launch, launch_id)
    if not l or not l.image_blob or l.status not in ("deploying", "live"):
        raise HTTPException(404, "not found")
    head = l.image_blob[:12]
    kind = ("image/png" if head.startswith(b"\x89PNG") else "image/gif" if head[:4] == b"GIF8"
            else "image/webp" if head[8:12] == b"WEBP" else "image/jpeg")
    return Response(l.image_blob, media_type=kind, headers={"Cache-Control": "public, max-age=31536000, immutable"})
