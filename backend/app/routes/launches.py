import asyncio
import base64
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from solders.pubkey import Pubkey
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.base import AdapterError, ClaimRequest, CreateRequest
from ..chains.evm import EVM_CHAINS, WEI, EvmRPCUnavailable
from ..chains.registry import IMPLEMENTED, get_adapter, is_evm, is_extra, list_launchpads, needs_setup
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
from ..models import Automation, Job, Launch, LaunchWallet, Transaction, User, Wallet, utcnow
from ..services.executor import Executor, run_built, run_evm_plan, run_evm_trade, run_single, run_trade
from ..services.auto_fund import detail as funding_detail
from ..services.auto_fund import plan_funding, run_funding, usd_value
from ..services.fees import describe, fee_plan_for
from ..services.preflight import build_preflight
from ..services.token_meta import source_metadata
from ..services.transparency import compute_transparency

router = APIRouter(tags=["launches"])
MIN_DEPLOY_BUFFER_SOL = 0.03  # rent for mint/metadata/ATA accounts plus fees
MAX_IMAGE_BYTES = 2_000_000


def _core_only(network: str) -> None:
    from .wallets import _core_only as check

    check(network)


def _launch_out(l: Launch) -> dict:
    return {
        "id": l.id, "network": l.network, "launchpad": l.launchpad, "status": l.status, "name": l.name,
        "symbol": l.symbol, "description": l.description, "socials": l.socials, "mint_address": l.mint_address,
        "dev_wallet_id": l.dev_wallet_id, "dev_buy_sol": l.dev_buy_sol, "imported": l.imported,
        "has_image": l.image_blob is not None, "created_at": l.created_at.isoformat(),
        "quote": (l.launchpad_params or {}).get("quote"),
        "cloned_from": _cloned_from(l),
        "deployed_at": l.deployed_at.isoformat() if l.deployed_at else None,
        "settings": _settings(l),
    }


def _settings(l: Launch) -> dict:
    """The launchpad settings chosen for this launch (select options such as anti-sniping), for display."""
    from ..services.protections import SPECIFIC

    keys = {o for p in SPECIFIC.get(l.launchpad, []) for o in p.get("options", [])}
    return {k: v for k, v in (l.launchpad_params or {}).items() if k in keys}


def _cloned_from(l: Launch) -> dict | None:
    """The token a draft's details were copied from, as {'network', 'address'}, or None."""
    c = (l.launchpad_params or {}).get("cloned_from")
    if isinstance(c, dict) and isinstance(c.get("address"), str) and isinstance(c.get("network"), str):
        return {"network": c["network"], "address": c["address"]}
    return None


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


@router.get("/launchpads/{launchpad_id}/protections")
async def launchpad_protections(launchpad_id: str):
    """Anti-sniping and fair-launch settings this launchpad really offers through Helm (and the ones it doesn't)."""
    if launchpad_id not in IMPLEMENTED:
        raise HTTPException(404, "launchpad not available yet (see /launchpads for the roadmap)")
    from ..services.protections import protections_for

    return protections_for(launchpad_id)


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
        if _image_type(image) is None:  # served publicly and sent to launchpads, so it must really be an image
            raise HTTPException(400, "the token image must be a PNG, JPEG, GIF or WebP file")
    adapter = get_adapter(body.launchpad)
    network = adapter.network
    params: dict = {"slippage_pct": body.slippage_pct}
    allowed = {f["key"]: f for f in adapter.options_schema().get("fields", [])}
    for k, v in (body.options or {}).items():
        f = allowed.get(k)
        if k in ("dev_buy_sol", "slippage_pct"):
            continue
        if not f:
            raise HTTPException(400, f"'{k}' isn't a setting {body.launchpad} offers")
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
    mint: str  # the existing token's address (mint on Solana, contract on EVM chains)
    network: str = "solana"  # where that token lives
    launchpad: str = "pumpfun"  # where the relaunch will go
    dev_wallet_id: str | None = None


# Import and clone are the same thing: copy an existing token's metadata into a new draft for relaunch. Nothing is
# deployed and the draft is never treated as live; it goes through the normal deploy review like any other draft.
@router.post("/launches/clone", status_code=201)
@router.post("/launches/import", status_code=201)
async def clone_launch(body: CloneBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                       ex: Executor = Depends(get_executor)):
    network = body.network
    if network == "solana":
        if not valid_address(body.mint):
            raise HTTPException(400, "that isn't a valid Solana token address")
    elif network in EVM_CHAINS:
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", body.mint):
            raise HTTPException(400, f"that isn't a valid {EVM_CHAINS[network]['name']} token address")
    else:
        raise HTTPException(400, f"unsupported network {network}")
    if body.launchpad not in IMPLEMENTED:
        raise HTTPException(400, f"launchpad '{body.launchpad}' is not implemented yet")
    target = get_adapter(body.launchpad).network
    if body.dev_wallet_id:
        dev = await owned_wallet(db, user, body.dev_wallet_id)
        if dev.network != target:
            raise HTTPException(400, f"dev wallet is on {dev.network} but {body.launchpad} launches on {target}")
    try:
        meta = await source_metadata(ex, network, body.mint)
    except RPCError as e:
        raise HTTPException(404, f"no token found at that address ({str(e)[:120]})")
    except Exception as e:
        detail = str(e)[:160] if isinstance(e, (RPCUnavailable, EvmRPCUnavailable)) else type(e).__name__
        raise HTTPException(502, f"couldn't read that token's metadata ({detail}). Solana needs a DAS-capable RPC such as Helius.")
    if not meta["name"] and not meta["symbol"]:
        raise HTTPException(404, "that address has no token name or ticker to copy")
    l = Launch(user_id=user.id, network=target, launchpad=body.launchpad, status="draft",
               name=(meta["name"] or "Untitled")[:32], symbol=(meta["symbol"] or "TKN")[:10].upper(),
               description=meta["description"] or "", image_blob=meta["image"] if meta["image"] and _image_type(meta["image"]) else None, socials=meta["socials"],
               dev_wallet_id=body.dev_wallet_id,
               launchpad_params={"slippage_pct": 10.0, "cloned_from": {"network": network, "address": body.mint,
                                                                      "copied": meta["copied"]}})
    db.add(l)
    await log_activity(db, user.id, "launch.created", "launch", l.id,
                       {"launchpad": body.launchpad, "cloned_from": {"network": network, "address": body.mint}})
    await db.commit()
    if body.dev_wallet_id:
        db.add(LaunchWallet(launch_id=l.id, wallet_id=body.dev_wallet_id, role="dev"))
        await db.commit()
    return _launch_out(l)


@router.delete("/launches/{launch_id}")
async def delete_launch(launch_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Delete a draft that never ran anything (or a legacy import). Anything with Helm jobs or plans is archived instead."""
    l = await _owned_launch(db, user, launch_id)
    if l.status != "draft" and not l.imported:
        raise HTTPException(409, "only drafts can be deleted; archive a deployed launch instead")
    used = (await db.execute(select(Job.id).where(Job.launch_id == l.id).limit(1))).first() or \
           (await db.execute(select(Automation.id).where(Automation.launch_id == l.id).limit(1))).first()
    if used:
        raise HTTPException(409, "this launch has transactions or plans in Helm; archive it instead")
    for lw in (await db.execute(select(LaunchWallet).where(LaunchWallet.launch_id == l.id))).scalars().all():
        await db.delete(lw)
    await log_activity(db, user.id, "launch.deleted", "launch", None, {"name": l.name, "symbol": l.symbol})
    await db.delete(l)
    await db.commit()
    return {"deleted": True}


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
    # When the dev wallet is short of its chain's coin, fund it from your USDC through Relay as part of the deploy.
    auto_fund: bool = True


def _creator_share_terms(launchpad: str, params: dict, dev_address: str = "") -> dict:
    """Helm's share of this new launch's creator rewards, as shown in the deploy preview (and so part of the
    confirmation hash). Only new launches get it, and only where the launchpad lets Helm set a reward split."""
    from ..services import platform_config as pc

    sh = pc.creator_share(launchpad)
    quote = (params or {}).get("quote") or {}
    if launchpad == "bags" and quote.get("mint") and quote["mint"] != "So11111111111111111111111111111111111111112":
        sh = {"bps": 0, "recipient": "", "supported": False,
              "note": "Helm takes no share of creator rewards on pair-token launches (Bags has no fee-share step for them)"}
    if sh["bps"] and dev_address and sh["recipient"].lower() == dev_address.lower():
        sh = {**sh, "bps": 0, "note": "Helm takes no share of your creator rewards"}  # nothing to split
    return {"bps": sh["bps"], "recipient": sh["recipient"] if sh["bps"] else "", "note": sh["note"]}


def _helm_fees(share: dict, funding: dict | None) -> str:
    """Every Helm fee this deploy involves, in one line, so overlapping fees are never hidden: the deploy itself is
    free, a USDC funding route pays only the route fee, and a creator-reward share applies to future rewards only."""
    parts = ["no Helm fee on the deploy itself"]
    if funding:
        parts.append(f"route fee on the USDC funding: {funding.get('route_fee', 'none')}")
    if share.get("bps"):
        parts.append(f"{share['bps'] / 100:g}% of this token's future creator rewards")
    return "; ".join(parts)


async def _note_creator_share(session, launch: Launch, job: Job, share: dict) -> None:
    """Ledger marker for a launch that gives Helm a share of its creator rewards. The amount isn't known here: the
    rewards accrue on the launchpad and are claimed there, so the entry is claimable with no amount until reconciled."""
    if not share.get("bps"):
        return
    from ..models import RevenueEvent
    from ..services.platform_config import CREATOR_SHARE_LAUNCHPADS

    session.add(RevenueEvent(
        source="creator_share", network=CREATOR_SHARE_LAUNCHPADS.get(launch.launchpad, launch.network),
        asset="various", amount_raw=None, decimals=0, estimated=True, status="claimable",
        destination=share["recipient"], launch_id=launch.id, job_id=job.id, payer_user_id=launch.user_id,
        reference=launch.mint_address,
        note=(f"{share['bps'] / 100:g}% of this launch's creator rewards on {launch.launchpad}; accrues and is claimed "
              "on the launchpad, so the amount isn't directly verifiable from Helm")))
    launch.launchpad_params = {**(launch.launchpad_params or {}), "helm_creator_share": share}


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
    if is_extra(adapter):  # SunPump, Meme.Cooking, Tolly
        from ..services import extra_launch

        if (reason := needs_setup(l.launchpad)):
            raise HTTPException(400, reason)
        return await extra_launch.deploy(l, body, user, db, ex, dev, adapter)
    if is_evm(adapter):
        return await _deploy_evm(l, body, user, db, ex, dev, adapter)
    bal = await rpc.get_balance(dev.address)
    need = l.dev_buy_sol + MIN_DEPLOY_BUFFER_SOL
    need_raw = int(need * LAMPORTS_PER_SOL)
    warnings = []
    funding = None
    if bal < need_raw:
        short = f"Dev wallet has {bal / LAMPORTS_PER_SOL:.4f} SOL; needs about {need:.4f} SOL (dev buy + rent and fees)."
        funding, why = await plan_funding(db, ex, user.id, dev, need_raw - bal) if body.auto_fund else (None, None)
        if not funding:
            warnings.append(short + (f" {why}" if why else ""))
    if adapter.options_schema().get("requires_image") and not l.image_blob:
        warnings.append(f"{l.launchpad} needs a token image. Add one to the launch first.")
    if (reason := needs_setup(l.launchpad)):
        warnings.append(reason)
    if hasattr(adapter, "deploy_warnings"):
        warnings += adapter.deploy_warnings(l.dev_buy_sol, dict(l.launchpad_params or {}))
    share = _creator_share_terms(l.launchpad, dict(l.launchpad_params or {}), dev.address)
    summary = {
        "launch_id": l.id, "launchpad": l.launchpad, "name": l.name, "symbol": l.symbol,
        "dev_wallet": dev.address, "dev_buy_sol": l.dev_buy_sol, "platform_fee": "none on deploy",
        "helm_creator_share": share,
    }
    if hasattr(adapter, "deploy_notes"):
        summary.update(adapter.deploy_notes({**(l.launchpad_params or {}), "helm_creator_share": share}))
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
        summary["creator_fees"] = (f"{100 - share['bps'] / 100:g}% of this token's creator fees go to your dev wallet, "
                                   f"{share['bps'] / 100:g}% to Helm's treasury" if share["bps"]
                                   else "100% of this token's creator fees go to your dev wallet")
        if s.bags_partner_wallet and s.bags_partner_config:
            summary["partner_share"] = "Bags pays Helm a partner share of this token's trading fees (from Bags' side, not yours)"
        summary["steps"] = "2 transactions: fee-share setup, then the launch"
    if funding:
        summary["funding"] = funding
    summary["helm_fees"] = _helm_fees(share, funding)
    from ..services.protections import protections_for

    summary["launch_protections"] = protections_for(l.launchpad)["summary"]
    pf = build_preflight("deploy", summary, warnings)
    if funding:
        pf["preflight"]["funding_detail"] = funding_detail(funding, need_raw - bal, dev.network)
    pf["preflight"]["dev_buy_usd"] = await usd_value(dev.network, l.dev_buy_sol)  # display only, not hashed
    if not body.confirm:
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, "deploy", l.id, pf["preflight"])
    if created:
        prior_status = l.status
        l.status = "deploying"
        await db.commit()
        slippage = (l.launchpad_params or {}).get("slippage_pct", 10.0)

        async def work(session, j):
            launch = await session.get(Launch, l.id)
            if funding and not await _fund_first(session, ex, j, launch, funding, dev, need_raw, prior_status):
                return
            req = CreateRequest(name=l.name, symbol=l.symbol, description=l.description, image_bytes=l.image_blob,
                                socials=l.socials or {}, dev_pubkey=dev.address, dev_buy_sol=l.dev_buy_sol,
                                slippage_pct=slippage,
                                options={**(l.launchpad_params or {}), "helm_creator_share": share})
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
                await _note_creator_share(session, launch, j, share)
            elif res.status == "timeout" and built.mint:
                # Sent but not confirmed in time: it may still land. Keep the mint so Helm can check for it, and
                # don't offer a redeploy, which would create a second token.
                launch.status, launch.mint_address = "deploying", built.mint
                launch.launchpad_params = {**(launch.launchpad_params or {}), "unconfirmed_since": utcnow().isoformat()}
            else:
                launch.status = "failed"
            await session.commit()

        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


async def _fund_first(session, ex: Executor, job: Job, launch: Launch, funding: dict, dev: Wallet, need_raw: int,
                      prior_status: str) -> bool:
    """Run the deploy's USDC funding route. On failure the job fails, the launch goes back to how it was, and
    nothing is deployed."""
    job.status = "submitted"
    await session.commit()
    err = await run_funding(session, ex, job, funding, dev, need_raw)
    if err is None:
        return True
    job.status, job.params = "failed", {**job.params, "error": err}
    job.updated_at = utcnow()
    launch.status = prior_status
    await session.commit()
    return False


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
        if not (w.network == launch.network and is_extra(get_adapter(launch.launchpad))):
            _core_only(w.network)
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
    if is_extra(get_adapter(l.launchpad)):
        from ..services import extra_launch

        return await extra_launch.trade(l, body, user, db, ex, wallets, get_adapter(l.launchpad))
    if l.network in EVM_CHAINS:
        return await _trade_evm(l, body, user, db, ex, wallets)
    s = get_settings()
    plan = await fee_plan_for(db, user, l.network)
    warnings = []
    if body.side == "buy":
        fee_sol = body.amount * plan.bps / 10_000 if plan.active else 0.0
        need = int((body.amount + fee_sol + 0.01) * LAMPORTS_PER_SOL)
        for w, bal in zip(wallets, await _balances(rpc, [w.address for w in wallets])):
            if bal < need:
                warnings.append(f"{w.address} has too little SOL for a {body.amount} SOL buy plus fees.")
    if body.side == "sell":
        warnings += await _sell_lock_warnings(db, l, wallets, body.amount,
                                              lambda w: rpc.get_token_balance(w.address, l.mint_address),
                                              lambda: rpc.get_token_supply(l.mint_address))
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


async def _sell_lock_warnings(db: AsyncSession, l: Launch, wallets: list[Wallet], percent: float, balance_of,
                              supply_of) -> list[str]:
    """Refusals for sells that would dip into tokens locked by the launch's allocation plan."""
    from ..models import LaunchAllocation
    from ..services.allocations import lock_warning

    locks = (await db.execute(select(LaunchAllocation.recipient).where(
        LaunchAllocation.launch_id == l.id, LaunchAllocation.lock.is_(True)))).scalars().all()
    targets = {r.lower() for r in locks}
    out = []
    decimals = None
    for w in wallets:
        if w.address.lower() not in targets:
            continue
        if decimals is None:
            s = await supply_of()
            decimals = s[1] if isinstance(s, tuple) else s["decimals"]
        bal = int(await balance_of(w))
        if (msg := await lock_warning(db, l, w, bal, bal * int(round(percent * 100)) // 10_000, decimals)):
            out.append(msg)
    return out


async def _balances(rpc: SolanaRPC, addresses: list[str]) -> list[int]:
    """Balances read concurrently (a few at a time), not one round trip after another."""
    sem = asyncio.Semaphore(8)

    async def one(a: str) -> int:
        async with sem:
            return await rpc.get_balance(a)

    return list(await asyncio.gather(*(one(a) for a in addresses)))


@router.post("/launches/{launch_id}/recheck")
async def recheck_deploy(launch_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                         rpc: SolanaRPC = Depends(get_rpc), ex: Executor = Depends(get_executor)):
    """For a Solana deploy that was sent but not confirmed in time: look for the token on-chain."""
    l = await _owned_launch(db, user, launch_id)
    if l.launchpad in IMPLEMENTED and is_extra(get_adapter(l.launchpad)):
        from ..services import extra_launch

        await extra_launch.recheck(l, db, ex, get_adapter(l.launchpad))
        return _launch_out(l)
    if l.status != "deploying" or not l.mint_address or l.network != "solana":
        return _launch_out(l)
    try:
        await rpc.get_token_supply(l.mint_address)
    except RPCError as e:
        missing = "could not find account" in str(e).lower()  # the node's answer for a mint that doesn't exist
        since = (l.launchpad_params or {}).get("unconfirmed_since")
        if missing and since and utcnow() - datetime.fromisoformat(since) > timedelta(minutes=10):
            # a create transaction's blockhash expires in about a minute; ten minutes on, it can't land any more
            l.status, l.mint_address = "failed", None
            await db.commit()
        return _launch_out(l)
    l.status, l.deployed_at = "live", l.deployed_at or utcnow()
    await log_activity(db, user.id, "launch.confirmed_late", "launch", l.id, {"mint": l.mint_address})
    await db.commit()
    return _launch_out(l)


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
    if is_extra(adapter):
        from ..services import extra_launch

        if w.network != l.network:
            raise HTTPException(400, f"that wallet is on {w.network}, not {l.network}")
        if l.status != "live" or not l.mint_address:
            raise HTTPException(409, "launch is not live")
        return await extra_launch.claim(l, body, user, db, ex, w, adapter)
    _core_only(w.network)
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
    _core_only(w.network)
    if l.network in EVM_CHAINS:
        return await _token_out_evm(l, body, user, db, ex, w, BURN_ADDRESS, "burn")
    raw_bal = await rpc.get_token_balance(w.address, l.mint_address)
    _, decimals = await rpc.get_token_supply(l.mint_address)
    amount = int(raw_bal * body.percent / 100)
    warnings = [] if amount > 0 else ["Nothing to burn: token balance is zero."]
    from ..services.allocations import lock_warning

    if (msg := await lock_warning(db, l, w, int(raw_bal), amount, decimals)):
        warnings.append(msg)
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
    _core_only(w.network)
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
    from ..services.allocations import lock_warning

    if (msg := await lock_warning(db, l, w, int(raw_bal), amount, decimals)):
        warnings.append(msg)
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
    if is_extra(get_adapter(l.launchpad)):
        from ..services import extra_launch

        out.update(await extra_launch.workspace(l, ex, links, wallets))
        txs = (await db.execute(
            select(Transaction).join(Job, Job.id == Transaction.job_id).where(Job.launch_id == l.id)
            .order_by(Transaction.created_at.desc()).limit(50))).scalars().all()
        out["transactions"] = [{"id": t.id, "kind": t.kind, "status": t.status, "signature": t.signature,
                                "error": t.error, "sol_delta": 0, "created_at": t.created_at.isoformat()} for t in txs]
        out["pnl"] = {"net_sol_flow": 0, "confirmed_txs": 0,
                      "note": "Net flow isn't tracked on this network yet. Wallet balances above are live."}
        return out
    controlled: dict[str, int] = {}
    decimals = 6
    evm = l.network in EVM_CHAINS
    try:
        if evm:
            erpc = ex.evm(l.network).rpc
            meta = await erpc.erc20_meta(l.mint_address) if l.mint_address else None
            if meta:
                decimals = meta["decimals"]
            for w, lk in zip(wallets, links):
                native_bal = await erpc.get_balance(w.address)
                tok = await erpc.erc20_balance(l.mint_address, w.address) if l.mint_address else 0
                controlled[w.address] = tok
                out["wallets"].append({"id": w.id, "address": w.address, "label": w.label, "role": lk.role,
                                       "sol": native_bal / WEI, "token_raw": tok, "tokens": tok / 10 ** decimals})
            if meta:
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


STALE_JOB = timedelta(minutes=15)  # far beyond any confirmation or route wait


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    j = await db.get(Job, job_id)
    if not j or j.user_id != user.id:
        raise HTTPException(404, "job not found")
    last_tx = (await db.execute(select(func.max(Transaction.created_at)).where(Transaction.job_id == j.id))).scalar()
    quiet_since = max(j.updated_at, last_tx) if last_tx else j.updated_at
    if j.status in ("pending", "submitted") and quiet_since < utcnow() - STALE_JOB:
        # Work runs inside the API process; a restart (a deploy) mid-job stops it. Don't leave the user waiting
        # forever: say it's unconfirmed, and that the explorer is the source of truth.
        j.status = "unconfirmed"
        j.params = {**(j.params or {}), "lost_track": True,
                    "error": "Helm lost track of this job (the server restarted while it ran). "
                    "Check the transactions on the explorer before trying again."}
        await db.commit()
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
    need_raw = dev_buy + launch_fee + gas
    warnings = []
    funding = None
    if bal < need_raw:
        short = (f"Dev wallet has {bal / WEI:.6f} {coin}; needs about {need_raw / WEI:.6f} "
                 f"{coin} (dev buy, launch fee and gas).")
        funding, why = await plan_funding(db, ex, user.id, dev, need_raw - bal) if body.auto_fund else (None, None)
        if not funding:
            warnings.append(short + (f" {why}" if why else ""))
    if adapter.options_schema().get("requires_image") and not l.image_blob:
        warnings.append(f"{l.launchpad} needs a token image. Add one to the launch first.")
    share = _creator_share_terms(l.launchpad, params, dev.address)
    summary = {
        "launch_id": l.id, "network": l.network, "launchpad": l.launchpad, "name": l.name, "symbol": l.symbol,
        "dev_wallet": dev.address, "dev_buy_sol": l.dev_buy_sol, "max_gas": gas / WEI,
        "platform_fee": "none on deploy", "helm_creator_share": share,
    }
    if hasattr(adapter, "deploy_notes"):
        summary.update(adapter.deploy_notes({**params, "helm_creator_share": share}))
    if hasattr(adapter, "deploy_warnings"):
        warnings += adapter.deploy_warnings(l.dev_buy_sol, params)
    if launch_fee:
        summary["launch_fee"] = f"{launch_fee / WEI:g} {coin}, paid to {l.launchpad}"
    if funding:
        summary["funding"] = funding
    summary["helm_fees"] = _helm_fees(share, funding)
    from ..services.protections import protections_for

    summary["launch_protections"] = protections_for(l.launchpad)["summary"]
    pf = build_preflight("deploy", summary, warnings)
    if funding:
        pf["preflight"]["funding_detail"] = funding_detail(funding, need_raw - bal, dev.network)
    pf["preflight"]["dev_buy_usd"] = await usd_value(dev.network, l.dev_buy_sol)  # display only, not hashed
    if not body.confirm:
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, "deploy", l.id, pf["preflight"])
    if created:
        prior_status = l.status
        l.status = "deploying"
        await db.commit()

        async def work(session, j):
            launch = await session.get(Launch, l.id)
            if funding and not await _fund_first(session, ex, j, launch, funding, dev, need_raw, prior_status):
                return
            opts = {**params, "helm_creator_share": share}
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
                await _note_creator_share(session, launch, j, share)
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
    else:
        warnings += await _sell_lock_warnings(db, l, wallets, body.amount,
                                              lambda w: sender.rpc.erc20_balance(l.mint_address, w.address),
                                              lambda: sender.rpc.erc20_meta(l.mint_address))
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
    from ..services.allocations import lock_warning

    if (msg := await lock_warning(db, l, w, raw_bal, amount, meta["decimals"])):
        warnings.append(msg)
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
    kind = _image_type(l.image_blob)
    if kind is None:  # stored before uploads were checked; never serve arbitrary bytes from our domain
        raise HTTPException(404, "not found")
    return Response(l.image_blob, media_type=kind, headers={"Cache-Control": "public, max-age=31536000, immutable",
                                                            "Content-Security-Policy": "default-src 'none'"})


def _image_type(data: bytes) -> str | None:
    head = data[:12]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None
