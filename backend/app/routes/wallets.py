import asyncio
import logging
import math
from typing import Literal

ERC20_TRANSFER_GAS = 90_000

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr
from solders.pubkey import Pubkey
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.solana import LAMPORTS_PER_SOL, SolanaRPC, build_unsigned, sol_transfer_ix
from ..db import get_db
from ..deps import (
    SpendConfirm, begin_spend, current_user, web_user, find_replay, get_executor, get_rpc, log_activity, owned_wallet, run_job,
    two_fa_warnings, valid_address, valid_address_for,
)
from ..models import AdminAudit, Folder, Funder, Job, Transaction, User, Wallet
from ..services.executor import Executor, _finish, _record, run_single, send_evm_tracked, submit_tracked
from ..services import portfolio
from ..services.admin import request_context
from ..services.chain_balances import STABLE_DECIMALS, chain_name, fee_reserve, native_coin, stable_for
from ..services.preflight import build_preflight
from ..chains.base import AdapterError
from ..chains.evm import EVM_CHAINS, WEI
from ..chains.jupiter import Jupiter
from ..chains.relay import DONE
from ..services.routing import (ROUTE_GAS, execute_route, currency as route_currency, send_item,
                                provider_label, provider_terms)
from ..signing import get_signer
from ..services.evm_exec import EvmCall
from ..services.guard import check_password, check_totp_once
from ..services.main_wallet import ensure_main_wallets, main_wallet_ids
from ..signing.base import SigningError, parse_private_key

router = APIRouter(tags=["wallets"])
MAX_WALLETS = 500  # per account: each managed wallet is a key in the signing service, which has quotas and costs


async def _check_room(db: AsyncSession, user: User, adding: int) -> None:
    have = (await db.execute(select(func.count()).select_from(Wallet).where(Wallet.user_id == user.id))).scalar_one()
    if have + adding > MAX_WALLETS:
        raise HTTPException(400, f"an account can hold up to {MAX_WALLETS} wallets; you have {have}")
log = logging.getLogger(__name__)
TX_FEE_RESERVE = 10_000  # lamports kept back for fees


def _wallet_out(w: Wallet, balance: int | None = None, stable: float | None = None,
                price: float | None = None, mains: set[str] | None = None) -> dict:
    out = {"id": w.id, "network": w.network, "address": w.address, "kind": w.kind, "label": w.label,
           "folder_id": w.folder_id, "is_main": bool(mains and w.id in mains)}
    if balance is not None:
        native = balance / (LAMPORTS_PER_SOL if w.network == "solana" else WEI)
        out["balance"] = native  # in the chain's own coin: SOL, ETH or BNB
        out["balance_sol"] = native  # kept for older clients; same value
        out["coin_usd"] = native * price if price is not None else None
    if stable is not None:
        out["stable"] = stable  # the chain's dollar stablecoin (USDC; USDG on Robinhood Chain)
        out["stable_symbol"] = stable_for(w.network)[0]
    if balance is not None and stable is not None and price is not None:
        out["usd"] = out["coin_usd"] + stable  # the wallet's value in dollars
    return out  # signer_ref is never returned


class CreateWallets(BaseModel):
    network: str = "solana"
    count: int = Field(default=1, ge=1, le=50)
    label_prefix: str = "Wallet"
    folder_id: str | None = None


class WatchWallet(BaseModel):
    network: str = "solana"
    address: str
    label: str = ""
    folder_id: str | None = None


def _core_only(network: str) -> None:
    """TRON, NEAR and Arc wallets move funds through /chains/{network}/transfer, which knows their fee models."""
    from ..chains.networks import is_extra

    if is_extra(network):
        raise HTTPException(400, "use the network's own transfer for TRON, NEAR and Arc wallets")


@router.get("/wallets")
async def list_wallets(with_balances: bool = False, user: User = Depends(current_user),
                       db: AsyncSession = Depends(get_db), rpc: SolanaRPC = Depends(get_rpc),
                       ex: Executor = Depends(get_executor)):
    from ..chains.networks import offered

    rows = [w for w in (await db.execute(select(Wallet).where(Wallet.user_id == user.id))).scalars().all()
            if offered(w.network)]
    mains = await main_wallet_ids(db, user.id)
    # Main wallets first, so they're easy to find and are the first place people look for USDC.
    rows = sorted(rows, key=lambda w: w.id not in mains)
    if not with_balances:
        return [_wallet_out(w, mains=mains) for w in rows]
    prices = await portfolio.coin_prices()
    sem = asyncio.Semaphore(8)

    async def read(w: Wallet):
        bal = stable = None
        from ..chains.networks import EXTRA_NETWORKS, extra_enabled

        if w.network in EXTRA_NETWORKS:  # TRON, NEAR, Arc: native balance in its own units
            out = _wallet_out(w, mains=mains)
            if w.network in extra_enabled():
                from ..services import extra_chains as xc

                async with sem:
                    try:
                        raw = await xc.native_balance(ex, w.network, w.address)
                        out["balance_native"] = xc.human(raw, EXTRA_NETWORKS[w.network].decimals)
                    except Exception:
                        out["balance_native"] = None
            return out
        if w.kind != "managed" and w.network not in ("solana", *EVM_CHAINS):
            return _wallet_out(w, mains=mains)
        async with sem:
            # Each read is separate so one chain (or one call) being down doesn't hide the rest.
            try:
                bal = await (rpc.get_balance(w.address) if w.network == "solana"
                             else ex.evm(w.network).rpc.get_balance(w.address))
            except Exception:
                bal = None
            try:
                token = stable_for(w.network)[1]
                raw = await (rpc.get_token_balance(w.address, token) if w.network == "solana"
                             else ex.evm(w.network).rpc.erc20_balance(token, w.address))
                stable = raw / 10 ** STABLE_DECIMALS[w.network]
            except Exception:
                stable = None
        return _wallet_out(w, bal, stable, prices.get(native_coin(w.network)), mains)

    return list(await asyncio.gather(*(read(w) for w in rows)))


@router.post("/wallets", status_code=201)
async def create_wallets(body: CreateWallets, user: User = Depends(current_user),
                         db: AsyncSession = Depends(get_db), signer=Depends(get_signer)):
    if body.network != "solana" and body.network not in EVM_CHAINS:
        from ..chains.networks import require_enabled

        try:
            net = require_enabled(body.network)
        except HTTPException:
            raise HTTPException(400, f"unsupported network {body.network}")
        if net.family == "ton":
            raise HTTPException(400, "Helm doesn't hold TON keys: connect your own wallet with TON Connect instead")
    if body.folder_id:
        await _owned_folder(db, user, body.folder_id)
    await _check_room(db, user, body.count)
    created = []
    for i in range(body.count):
        label = f"{body.label_prefix} {i + 1}" if body.count > 1 else body.label_prefix
        ref = await signer.create_wallet(user.id, body.network, label)
        w = Wallet(user_id=user.id, network=body.network, address=ref.address, kind="managed",
                   signer_ref=ref.signer_ref, label=label, folder_id=body.folder_id)
        db.add(w)
        created.append(w)
    await log_activity(db, user.id, "wallets.created", payload={"count": body.count, "network": body.network})
    await db.commit()
    return [_wallet_out(w) for w in created]


@router.post("/wallets/watch", status_code=201)
async def watch_wallet(body: WatchWallet, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from ..chains.networks import is_extra, require_enabled

    if is_extra(body.network):
        require_enabled(body.network)
    if not valid_address_for(body.network, body.address):
        raise HTTPException(400, f"invalid {body.network} address")
    if body.folder_id:
        await _owned_folder(db, user, body.folder_id)
    address = body.address.strip()
    from ..chains.networks import EXTRA_NETWORKS

    if EXTRA_NETWORKS.get(body.network) and EXTRA_NETWORKS[body.network].family == "ton":
        from ..services.extra_chains import ton_address

        address = ton_address(body.network, address)  # one canonical form, so the same wallet isn't added twice
    await _check_room(db, user, 1)
    existing = (await db.execute(select(Wallet).where(
        Wallet.user_id == user.id, Wallet.network == body.network, Wallet.address == address))).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "that address is already in your wallets")
    w = Wallet(user_id=user.id, network=body.network, address=address, kind="watch", label=body.label,
               folder_id=body.folder_id)
    db.add(w)
    await db.commit()
    return _wallet_out(w)


class ImportWallet(BaseModel):
    network: str = "solana"
    private_key: SecretStr  # SecretStr so it's masked if it ever appears in an error or log
    label: str = Field(default="Imported", max_length=80)
    all_evm: bool = False  # an EVM key works on every EVM chain: add it to each one
    folder_id: str | None = None
    password: SecretStr  # re-entered: an imported wallet becomes a destination that needs no 2FA
    totp: str | None = None  # required when the account has 2FA


@router.post("/wallets/import", status_code=201)
async def import_wallet(body: ImportWallet, user: User = Depends(web_user), db: AsyncSession = Depends(get_db),
                        signer=Depends(get_signer)):
    """Bring an existing wallet under Helm's signer. The key is encrypted by the signer and never returned or logged.

    An imported wallet is one of the user's own wallets from then on, so funds can move to it without 2FA. Someone
    with a stolen session could import a key they hold and drain into it that way, so importing asks for the
    password (and the 2FA code when 2FA is on), and only from the web app."""
    if not await check_password(db, user, body.password.get_secret_value()):
        raise HTTPException(400, "wrong password")
    if user.totp_enabled and not await check_totp_once(db, user, body.totp):
        raise HTTPException(401, "totp_required: enter your 2FA code to import a wallet")
    evm = body.network in EVM_CHAINS
    if body.network != "solana" and not evm:
        from ..chains.networks import EXTRA_NETWORKS, require_enabled

        if body.network not in EXTRA_NETWORKS:
            raise HTTPException(400, f"unsupported network {body.network}")
        if require_enabled(body.network).family == "ton":
            raise HTTPException(400, "Helm doesn't hold TON keys: connect your own wallet with TON Connect instead")
    if body.folder_id:
        await _owned_folder(db, user, body.folder_id)
    networks = list(EVM_CHAINS) if evm and body.all_evm else [body.network]
    secret = body.private_key.get_secret_value()
    try:
        _, address = parse_private_key(body.network, secret)
    except SigningError as e:
        raise HTTPException(400, str(e))  # the message describes the expected format only, never the key
    # Refuse before the key goes anywhere if the account already manages this address everywhere asked.
    managed = (await db.execute(select(Wallet.network).where(
        Wallet.user_id == user.id, Wallet.kind == "managed", Wallet.network.in_(networks),
        Wallet.address == address))).scalars().all()
    if set(managed) >= set(networks):
        raise HTTPException(409, "that wallet is already in your account")
    try:
        ref = await signer.import_wallet(user.id, body.network, secret)
    except NotImplementedError as e:
        raise HTTPException(501, str(e))
    except SigningError as e:
        log.warning("wallet import failed: %s", e)
        raise HTTPException(502, f"couldn't import the wallet: {e}")
    finally:
        del secret
    created, skipped = [], []
    for n in networks:
        existing = (await db.execute(select(Wallet).where(
            Wallet.user_id == user.id, Wallet.network == n, Wallet.address == ref.address))).scalar_one_or_none()
        if existing and existing.kind == "managed":
            skipped.append(n)
            continue
        if existing:  # a watched address becomes a managed one now that its key is here
            existing.kind, existing.signer_ref = "managed", ref.signer_ref
            existing.label = existing.label or body.label
            created.append(existing)
            continue
        w = Wallet(user_id=user.id, network=n, address=ref.address, kind="managed", signer_ref=ref.signer_ref,
                   label=body.label, folder_id=body.folder_id)
        db.add(w)
        created.append(w)
    if not created:
        raise HTTPException(409, "that wallet is already in your account")
    await log_activity(db, user.id, "wallets.imported",
                       payload={"count": len(created), "network": body.network, "address": ref.address})
    await db.commit()
    return [_wallet_out(w) for w in created]


@router.post("/wallets/main", status_code=201)
async def setup_main_wallets(user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                             signer=Depends(get_signer)):
    """Create the account's main wallets if any are missing (accounts made before main wallets, or a signer outage
    at sign-up). Safe to call again."""
    try:
        created = await ensure_main_wallets(db, signer, user.id)
    except SigningError as e:
        await db.rollback()
        log.warning("main wallet setup failed: %s", e)
        # The signer's message says what went wrong (a Turnkey refusal, say) and never contains key material.
        raise HTTPException(502, f"couldn't create the main wallet: {e}")
    except Exception as e:
        await db.rollback()
        log.exception("main wallet setup failed")
        raise HTTPException(502, f"couldn't create the main wallet right now ({type(e).__name__}); try again shortly")
    if created:
        await log_activity(db, user.id, "wallets.main_created", payload={"count": len(created)})
    await db.commit()
    mains = await main_wallet_ids(db, user.id)
    return [_wallet_out(w, mains=mains) for w in created]


# --- folders -------------------------------------------------------------------------------------


class FolderBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = "#6366f1"


def _folder_out(f: Folder, count: int = 0) -> dict:
    return {"id": f.id, "name": f.name, "color": f.color, "wallet_count": count}


async def _user_folders(db: AsyncSession, user: User) -> list[Folder]:
    return list((await db.execute(select(Folder).where(Folder.user_id == user.id).order_by(Folder.name))).scalars())


async def _owned_folder(db: AsyncSession, user: User, folder_id: str) -> Folder:
    f = await db.get(Folder, folder_id)
    if not f or f.user_id != user.id:
        raise HTTPException(404, "folder not found")
    return f


@router.get("/folders")
async def list_folders(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    folders = await _user_folders(db, user)
    counts: dict[str, int] = {}
    for fid in (await db.execute(select(Wallet.folder_id).where(Wallet.user_id == user.id))).scalars():
        if fid:
            counts[fid] = counts.get(fid, 0) + 1
    return [_folder_out(f, counts.get(f.id, 0)) for f in folders]


_folder_locks: dict[str, asyncio.Lock] = {}


@router.post("/folders", status_code=201)
async def create_folder(body: FolderBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(400, "folder name can't be empty")
    # One folder create at a time per user, so a double-click can't slip two identical folders past the check.
    async with _folder_locks.setdefault(user.id, asyncio.Lock()):
        if any(f.name.lower() == name.lower() for f in await _user_folders(db, user)):
            raise HTTPException(409, f'you already have a folder called "{name}"')
        f = Folder(user_id=user.id, name=name, color=body.color)
        db.add(f)
        await db.commit()
    return _folder_out(f)


@router.patch("/folders/{folder_id}")
async def update_folder(folder_id: str, body: FolderBody, user: User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)):
    f = await _owned_folder(db, user, folder_id)
    name = " ".join(body.name.split())
    if any(o.id != f.id and o.name.lower() == name.lower() for o in await _user_folders(db, user)):
        raise HTTPException(409, f'you already have a folder called "{name}"')
    f.name, f.color = name, body.color
    await db.commit()
    return _folder_out(f)


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Delete a folder. Its wallets are never deleted: they just move to "No folder"."""
    f = await _owned_folder(db, user, folder_id)
    wallets = (await db.execute(select(Wallet).where(Wallet.user_id == user.id, Wallet.folder_id == f.id))).scalars().all()
    for w in wallets:
        w.folder_id = None
    await db.flush()
    await db.delete(f)
    await log_activity(db, user.id, "folder.deleted", payload={"name": f.name, "wallets_unfiled": len(wallets)})
    await db.commit()
    return {"deleted": f.id, "wallets_unfiled": len(wallets)}


@router.post("/folders/merge-duplicates")
async def merge_duplicate_folders(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Folders with the same name (ignoring case) are merged into the one holding the most wallets (ties: the lowest
    ID), so the fewest wallets move and the result does not depend on row order; wallets move with them."""
    groups: dict[str, list[Folder]] = {}
    for f in (await db.execute(select(Folder).where(Folder.user_id == user.id).order_by(Folder.id))).scalars():
        groups.setdefault(f.name.strip().lower(), []).append(f)
    counts = dict((await db.execute(select(Wallet.folder_id, func.count()).where(
        Wallet.user_id == user.id, Wallet.folder_id.is_not(None)).group_by(Wallet.folder_id))).all())
    removed = moved = 0
    for same in groups.values():
        same.sort(key=lambda f: (-counts.get(f.id, 0), f.id))
        keep, extra = same[0], same[1:]
        for f in extra:
            for w in (await db.execute(select(Wallet).where(Wallet.user_id == user.id, Wallet.folder_id == f.id))).scalars():
                w.folder_id = keep.id
                moved += 1
            await db.flush()
            await db.delete(f)
            removed += 1
    await db.commit()
    return {"removed": removed, "wallets_moved": moved}


class MoveWallets(BaseModel):
    wallet_ids: list[str] = Field(min_length=1, max_length=500)
    folder_id: str | None = None  # None = no folder


@router.post("/wallets/move")
async def move_wallets(body: MoveWallets, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if body.folder_id:
        await _owned_folder(db, user, body.folder_id)
    for wid in body.wallet_ids:
        w = await owned_wallet(db, user, wid, managed=False)
        w.folder_id = body.folder_id
    await db.commit()
    return {"moved": len(body.wallet_ids), "folder_id": body.folder_id}


# --- funders -------------------------------------------------------------------------------------


class FunderBody(BaseModel):
    wallet_id: str
    label: str = "Funder"
    is_default: bool = False


@router.get("/funders")
async def list_funders(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Funder).where(Funder.user_id == user.id))).scalars().all()
    return [{"id": f.id, "wallet_id": f.wallet_id, "label": f.label, "is_default": f.is_default,
             "network": f.network} for f in rows]


@router.post("/funders", status_code=201)
async def create_funder(body: FunderBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    w = await owned_wallet(db, user, body.wallet_id)
    if body.is_default:
        for f in (await db.execute(select(Funder).where(Funder.user_id == user.id, Funder.network == w.network))).scalars():
            f.is_default = False
    f = Funder(user_id=user.id, network=w.network, wallet_id=w.id, label=body.label, is_default=body.is_default)
    db.add(f)
    await db.commit()
    return {"id": f.id, "wallet_id": f.wallet_id, "label": f.label, "is_default": f.is_default}


# --- fund / withdraw -----------------------------------------------------------------------------


class Allocation(BaseModel):
    wallet_id: str
    sol: float = Field(gt=0)


class FundBody(SpendConfirm):
    funder_wallet_id: str
    allocations: list[Allocation] = Field(min_length=1, max_length=20)


class FundCashBody(SpendConfirm):
    wallet_id: str  # destination, managed, on any supported chain
    amount: float = Field(gt=0)  # in the destination chain's own coin


@router.post("/wallets/fund-cash")
async def fund_cash(body: FundCashBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                    ex: Executor = Depends(get_executor)):
    """Fund a wallet from your cash balance: the USDC (USDG on Robinhood Chain) sitting in any of your
    wallets. Helm picks the wallet that can cover it best — the destination's own USDC first, then its
    chain, then main wallets — and routes stable to the destination's coin through Relay, same chain or
    across chains. The source is a decision the server makes from live balances and is shown in full
    before anything is confirmed."""
    from ..services import auto_fund

    if (prior := await find_replay(db, user, body)):
        return prior
    dst = await owned_wallet(db, user, body.wallet_id)
    _core_only(dst.network)
    coin = native_coin(dst.network)
    price = (await portfolio.coin_prices()).get(coin)
    if not price:
        raise HTTPException(400, f"{coin}'s price couldn't be read, so a cash route can't be sized right now.")
    amount_raw = int(round(body.amount * 10 ** (9 if dst.network == "solana" else 18)))
    wallets = list((await db.execute(select(Wallet).where(Wallet.user_id == user.id, Wallet.kind == "managed"))).scalars())
    mains = await main_wallet_ids(db, user.id)
    ordered = sorted(wallets, key=lambda w: (w.id != dst.id, w.id not in mains, w.network != dst.network))[:60]
    _, src, usd = await auto_fund.find_cash_source(ex, ordered, set(mains), dst, amount_raw, price)
    warnings: list[str] = []
    if src is None:
        usd_needed = math.ceil(body.amount * price * auto_fund.BUFFER / (1 - ex.relay.fee_bps / 10_000) + auto_fund.FLAT_USD)
        warnings.append(f"Your cash balance can't cover {body.amount:g} {coin} (about ${usd_needed} with fees and "
                        "headroom). Add USDC to any wallet and try again.")
    summary = {
        "network": dst.network,
        "to": {"label": dst.label or "Wallet", "address": dst.address}, "to_chain": chain_name(dst.network),
        "send": (f"{usd} {stable_for(src.network)[0]}" if src else None),
        "receive": f"{body.amount:g} {coin}",
        "provider": getattr(ex.relay, "label", "Relay (relay.link)"),
        "route_fee": ex.relay.fee_note,
        "funding_detail": {"covers": f"{body.amount:g} {coin} in the destination wallet"},
        "own_wallets_only": True,
        "platform_fee": "none",
    }
    if src is not None:
        summary.update({
            "from": {"label": src.label or "Wallet", "address": src.address}, "from_chain": chain_name(src.network),
            "amount_usd": usd, "quote": None,
        })
    from ..services.quote_approval import issue, verify, relay_bounds, check_relay_bounds
    scope = {"kind": "fund_cash", "source": src.id if src else None, "destination": dst.id,
             "amount_usd": usd, "need_raw": str(amount_raw), "fee_bps": ex.relay.fee_bps,
             "fee_recipient": ex.relay.fee_recipient}
    approved = verify(body.quote_approval, user.id, scope) if body.confirm else None
    bounds = None
    quote_info = None
    if src is not None:
        try:
            pinned = {"provider": approved["provider"]} if approved and approved.get("provider") else {}
            q = await ex.relay.quote(user=src.address, recipient=dst.address, from_network=src.network,
                                     to_network=dst.network, from_currency=route_currency(src.network, "stable")[1],
                                     to_currency=route_currency(dst.network, "native")[1],
                                     amount=int(usd) * 10 ** STABLE_DECIMALS[src.network],
                                     slippage_pct=auto_fund.SLIPPAGE_PCT, **pinned)
            summary["provider"] = provider_label(ex, q)
            flat_fee, fee_note = provider_terms(q, src.network)
            if fee_note is not None:
                summary["route_fee"] = fee_note
            if flat_fee:
                coin_src = native_coin(src.network)
                summary["provider_fee"] = f"{flat_fee:.6g} {coin_src}"
                have = await (ex.rpc.get_balance(src.address) if src.network == "solana"
                              else ex.evm(src.network).rpc.get_balance(src.address))
                need = flat_fee + await fee_reserve(ex, src.network, 2, ROUTE_GAS)
                if have / 10 ** (9 if src.network == "solana" else 18) < need:
                    warnings.append(f"{src.label or src.address[:8]} needs about {need:.6f} {coin_src} for network fees "
                                    f"and the route provider's {flat_fee:.6g} {coin_src} fee.")
            bounds = relay_bounds(q, destination_asset=route_currency(dst.network, "native")[1], destination_network=dst.network)
            if int(bounds["minimum_raw"]) < amount_raw:
                raise AdapterError("The quote cannot deliver the requested amount. Request a new preview.")
            if approved:
                check_relay_bounds(q, approved, route_currency(dst.network, "native")[1], dst.network)
                bounds = {k: v for k, v in approved.items() if k != "expires"}
            quote_info = ex.relay.summarize(q)
        except AdapterError as e:
            warnings.append(str(e))
    summary["quote_bounds"] = bounds
    pf = build_preflight("fund_cash", summary, warnings)
    pf["preflight"]["quote"] = quote_info
    if not body.confirm:
        if bounds and not warnings:
            pf["quote_approval"] = issue(user.id, scope, bounds)
        return pf
    if pf["preflight"]["warnings"]:
        raise HTTPException(400, pf["preflight"]["warnings"][0])
    job, created = await begin_spend(db, user, body, pf, "fund_cash", None,
                                     {**pf["preflight"], "network": dst.network, "source_wallet_id": src.id,
                                      "amount_usd": usd, "need_raw": amount_raw})
    if created:
        async def work(session, j):
            j.status = "submitted"
            await session.commit()
            amount_usd_raw = int(usd) * 10 ** STABLE_DECIMALS[src.network]
            res, status = await execute_route(session, ex, j, src, dst, "stable", "native", amount_usd_raw,
                                              auto_fund.SLIPPAGE_PCT, kind="fund_route", key="funding", approved_bounds=approved)
            if res.status == "confirmed" and status not in DONE:
                j.error = f"The route provider reported it as {status}; check the destination wallet's balance."
            _finish(j, [res])
            await session.commit()
        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


class WithdrawBody(SpendConfirm):
    wallet_ids: list[str] = Field(min_length=1, max_length=50)
    destination: str
    all: bool = True
    amount_sol: float | None = Field(default=None, gt=0, allow_inf_nan=False)  # native coin per wallet (asset="native")
    asset: Literal["native", "stable"] = "native"  # "stable": the chain's dollar stablecoin (USDC, USDG)
    amount: float | None = Field(default=None, gt=0, allow_inf_nan=False)  # stablecoin per wallet (asset="stable")


@router.post("/wallets/fund")
async def fund(body: FundBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
               rpc: SolanaRPC = Depends(get_rpc), ex: Executor = Depends(get_executor)):
    if (prior := await find_replay(db, user, body)):
        return prior
    src = await owned_wallet(db, user, body.funder_wallet_id)
    _core_only(src.network)
    targets = [(await owned_wallet(db, user, a.wallet_id, managed=False), a.sol) for a in body.allocations]
    # A watch-only target is an address someone typed in, not a wallet Helm holds the key to: sending there is money
    # leaving the account, so it needs 2FA exactly like a withdrawal.
    kind = "fund" if all(w.kind == "managed" for w, _ in targets) else "fund_external"
    if any(w.network != src.network for w, _ in targets):
        raise HTTPException(400, f"every wallet must be on {src.network}, the funder's network")
    if src.network in EVM_CHAINS:
        return await _fund_evm(body, user, db, ex, src, targets, kind)
    total = sum(sol for _, sol in targets)
    bal = await rpc.get_balance(src.address)
    warnings = []
    if bal < int(total * LAMPORTS_PER_SOL) + TX_FEE_RESERVE:
        warnings.append(f"Funder balance {bal / LAMPORTS_PER_SOL:.4f} SOL is below the {total:.4f} SOL required plus fees.")
    pf = build_preflight(kind, {
        "from": src.address,
        "to": [{"address": w.address, "sol": sol} for w, sol in targets],
        "total_sol": total, "platform_fee": "none",
    }, warnings)
    if not body.confirm:
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, kind, None, pf["preflight"])
    if created:
        async def work(session, j):
            blockhash = await ex.rpc.get_latest_blockhash()
            ixs = [sol_transfer_ix(Pubkey.from_string(src.address), Pubkey.from_string(w.address), int(sol * LAMPORTS_PER_SOL))
                   for w, sol in targets]
            raw = build_unsigned(Pubkey.from_string(src.address), ixs, blockhash)
            await run_single(session, ex, j, src, "fund", raw, {src.address: src.signer_ref or ""})
        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


@router.post("/wallets/withdraw")
async def withdraw(body: WithdrawBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                   rpc: SolanaRPC = Depends(get_rpc), ex: Executor = Depends(get_executor)):
    if (prior := await find_replay(db, user, body)):
        return prior
    if not body.all and (body.amount if body.asset == "stable" else body.amount_sol) is None:
        raise HTTPException(400, "set all=true or an amount")
    if len(set(body.wallet_ids)) != len(body.wallet_ids):
        raise HTTPException(400, "each wallet can only be listed once")
    wallets = [await owned_wallet(db, user, wid) for wid in body.wallet_ids]
    network = wallets[0].network
    _core_only(network)
    if any(w.network != network for w in wallets):
        raise HTTPException(400, "withdraw from one network at a time")
    if not valid_address_for(network, body.destination):
        raise HTTPException(400, f"invalid {network} destination address")
    if body.asset == "stable":
        return await _withdraw_stable(body, user, db, ex, wallets)
    if network in EVM_CHAINS:
        return await _withdraw_evm(body, user, db, ex, wallets)
    plan = []
    for w in wallets:
        bal = await rpc.get_balance(w.address)
        lamports = max(bal - TX_FEE_RESERVE, 0) if body.all else int((body.amount_sol or 0) * LAMPORTS_PER_SOL)
        plan.append((w, lamports, bal))
    warnings = [f"{w.address} has too little SOL to send {lam / LAMPORTS_PER_SOL:.4f}" for w, lam, bal in plan
                if lam <= 0 or lam + TX_FEE_RESERVE > bal]
    warnings += two_fa_warnings(user, "withdraw")
    pf = build_preflight("withdraw", {
        "destination": body.destination,
        "from": [{"address": w.address, "sol": lam / LAMPORTS_PER_SOL} for w, lam, _ in plan],
        "platform_fee": "none",
    }, warnings)
    if not body.confirm:
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, "withdraw", None, pf["preflight"])
    if created:
        async def work(session, j):
            results = []
            for w, lam, _ in plan:
                blockhash = await ex.rpc.get_latest_blockhash()
                raw = build_unsigned(
                    Pubkey.from_string(w.address),
                    [sol_transfer_ix(Pubkey.from_string(w.address), Pubkey.from_string(body.destination), lam)],
                    blockhash,
                )
                res = await submit_tracked(ex, j, w, "withdraw", raw, {w.address: w.signer_ref or ""})
                await _record(session, j, w, "withdraw", res, None)
                results.append(res)
            _finish(j, results)
            await session.commit()
        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


# --- EVM chains (Base, BNB Chain, Ethereum, Robinhood Chain) ---------------------------------------
# Amounts in the request bodies (`sol`, `amount_sol`) are in the chain's own coin here: ETH or BNB.


async def _fund_evm(body: FundBody, user: User, db: AsyncSession, ex: Executor, src: Wallet, targets: list,
                    kind: str = "fund"):
    sender = ex.evm(src.network)
    coin = EVM_CHAINS[src.network]["native"]
    wei = [(w, int(round(amt * WEI))) for w, amt in targets]
    total = sum(v for _, v in wei)
    bal = await sender.rpc.get_balance(src.address)
    gas = await sender.max_gas_cost() * len(wei)
    warnings = []
    if bal < total + gas:
        warnings.append(f"Funder balance {bal / WEI:.6f} {coin} is below the {total / WEI:.6f} {coin} required "
                        f"plus about {gas / WEI:.6f} {coin} gas.")
    pf = build_preflight(kind, {
        "network": src.network, "from": src.address,
        "to": [{"address": w.address, "amount": v / WEI, "coin": coin} for w, v in wei],
        "total": total / WEI, "coin": coin, "max_gas": gas / WEI, "platform_fee": "none",
    }, warnings)
    if not body.confirm:
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, kind, None, pf["preflight"])
    if created:
        async def work(session, j):
            j.status = "submitted"
            await session.commit()
            results = []
            for w, v in wei:
                res = await send_evm_tracked(sender, j, src, "fund", EvmCall(to=w.address, value=v))
                await _record(session, j, src, "fund", res, None)
                results.append(res)
                await session.commit()
            _finish(j, results)
            await session.commit()
        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


async def _withdraw_stable(body: WithdrawBody, user: User, db: AsyncSession, ex: Executor, wallets: list[Wallet]):
    """Withdraw the chain's stablecoin (USDC; USDG on Robinhood Chain) to an outside address.

    Solana: the recipient's token account rent is only counted if it doesn't exist yet, and a wallet without enough
    SOL for the fee has another of your Helm wallets pay it (shown in the preview). EVM: each wallet pays its own
    gas in the chain's coin; the preview says how much is missing."""
    from ..services.sol_token_send import (ATA_RENT, SOL_FEE, build_token_send, choose_fee_payer,
                                           missing_token_accounts, refs_for)
    from .distribute import _asset, _erc20_transfer

    network = wallets[0].network
    a = await _asset(db, user, ex, network, "stable")
    d, sym = a["decimals"], a["symbol"]
    from decimal import Decimal
    per_wallet = None
    if not body.all:
        units = Decimal(str(body.amount or 0)) * 10**d
        if not units.is_finite() or units <= 0 or units != units.to_integral_value():
            raise HTTPException(400, "amount must be a positive whole token base unit")
        per_wallet = int(units)
    plan: list[tuple[Wallet, int, Wallet | None]] = []  # (source, raw amount, fee payer on Solana)
    warnings: list[str] = []
    fees_paid_by = []
    try:
        if network == "solana":
            # The destination must be a wallet that can sign for its USDC later: an on-curve address that is
            # either new or a plain system account. A token account, mint or program address would lock the USDC.
            dest_key = Pubkey.from_string(body.destination)
            owner = await ex.rpc.account_owner(body.destination)
            if not dest_key.is_on_curve() or (owner is not None and owner != "11111111111111111111111111111111"):
                warnings.append(f"{body.destination[:8]}… isn't a wallet address (it looks like a token account, mint "
                                f"or program). {sym} sent there could never be moved. Use the recipient's wallet address.")
            needs_account = bool(await missing_token_accounts(ex.rpc, [body.destination], a["token"], a["program"]))
            committed: dict[str, int] = {}
            balance_cache: dict[str, int] = {}
            gas_total = 0
            for w in wallets:
                bal = int(await ex.rpc.get_token_balance(w.address, a["token"]))
                raw = bal if per_wallet is None else per_wallet
                if raw <= 0 or raw > bal:
                    warnings.append(f"{w.label or w.address[:8]} has {bal / 10**d:,.6g} {sym}, "
                                    + ("nothing to withdraw." if raw <= 0 else f"less than {raw / 10**d:,.6g}."))
                    plan.append((w, raw, None))
                    continue
                need = SOL_FEE + (ATA_RENT if needs_account and gas_total == 0 else 0)
                gas_total += need
                choice = await choose_fee_payer(db, ex.rpc, user.id, w, need, committed=committed, cache=balance_cache)
                if choice.wallet is None:
                    warnings.append(f"{w.label or w.address[:8]} needs about {need / LAMPORTS_PER_SOL:.6f} SOL for the "
                                    "network fee, and none of your other Solana wallets has enough to pay it. "
                                    "Add a little SOL to any of your Solana wallets.")
                elif not choice.own:
                    fees_paid_by.append({"from": w.address, "payer": {"label": choice.wallet.label or "Wallet",
                                                                      "address": choice.wallet.address}})
                plan.append((w, raw, choice.wallet))
            fees, fee_coin = gas_total / LAMPORTS_PER_SOL, "SOL"
        else:
            sender = ex.evm(network)
            gas = await sender.max_gas_cost(ERC20_TRANSFER_GAS)
            coin = EVM_CHAINS[network]["native"]
            for w in wallets:
                bal = int(await sender.rpc.erc20_balance(a["token"], w.address))
                raw = bal if per_wallet is None else per_wallet
                if raw <= 0 or raw > bal:
                    warnings.append(f"{w.label or w.address[:8]} has {bal / 10**d:,.6g} {sym}, "
                                    + ("nothing to withdraw." if raw <= 0 else f"less than {raw / 10**d:,.6g}."))
                elif (native := int(await sender.rpc.get_balance(w.address))) < gas:
                    warnings.append(f"{w.label or w.address[:8]} needs about {(gas - native) / WEI:.6f} more {coin} "
                                    f"for gas to send {sym}. Fund it with {coin} first (Fund from cash works too).")
                plan.append((w, raw, None))
            fees, fee_coin = gas * len(wallets) / WEI, coin
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Couldn't read balances on {chain_name(network)}: {type(e).__name__}")
    warnings += two_fa_warnings(user, "withdraw")
    pf = build_preflight("withdraw", {
        "network": network, "destination": body.destination, "coin": sym, "asset": sym, "token": a["token"],
        "from": [{"address": w.address, "amount": raw / 10**d} for w, raw, _ in plan],
        "network_fees_max": fees, "network_fee_coin": fee_coin, "fees_paid_by": fees_paid_by,
        "platform_fee": "none",
    }, warnings)
    if not body.confirm:
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, "withdraw", None, pf["preflight"])
    if created:
        async def work(session, j):
            j.status = "submitted"
            await session.commit()
            results = []
            for w, raw, payer in plan:
                if network == "solana":
                    tx = build_token_send(w.address, payer.address, [(body.destination, raw)], a["token"], d,
                                          a["program"], await ex.rpc.get_latest_blockhash())
                    res = await submit_tracked(ex, j, w, "withdraw", tx, refs_for(w, payer))
                else:
                    res = await send_evm_tracked(ex.evm(network), j, w, "withdraw",
                                                 EvmCall(to=a["token"], data=_erc20_transfer(body.destination, raw)))
                await _record(session, j, w, "withdraw", res, None)
                await session.commit()
                results.append(res)
            _finish(j, results)
            await session.commit()
        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


async def _withdraw_evm(body: WithdrawBody, user: User, db: AsyncSession, ex: Executor, wallets: list[Wallet]):
    network = wallets[0].network
    sender = ex.evm(network)
    coin = EVM_CHAINS[network]["native"]
    gas = await sender.max_gas_cost()
    plan = []
    for w in wallets:
        bal = await sender.rpc.get_balance(w.address)
        amount = await sender.spendable(w.address) if body.all else int(round((body.amount_sol or 0) * WEI))
        plan.append((w, amount, bal))
    warnings = [f"{w.address} has too little {coin} to send {amt / WEI:.6f}" for w, amt, bal in plan
                if amt <= 0 or amt + gas > bal]
    warnings += two_fa_warnings(user, "withdraw")
    pf = build_preflight("withdraw", {
        "network": network, "destination": body.destination, "coin": coin,
        "from": [{"address": w.address, "amount": amt / WEI} for w, amt, _ in plan],
        "max_gas_each": gas / WEI, "platform_fee": "none",
    }, warnings)
    if not body.confirm:
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, "withdraw", None, pf["preflight"])
    if created:
        async def work(session, j):
            results = []
            for w, amt, _ in plan:
                res = await send_evm_tracked(sender, j, w, "withdraw", EvmCall(to=body.destination, value=amt))
                await _record(session, j, w, "withdraw", res, None)
                results.append(res)
            _finish(j, results)
            await session.commit()
        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


# --- Jupiter swaps (Solana) -----------------------------------------------------------------------

WSOL = "So11111111111111111111111111111111111111112"


class SwapBody(SpendConfirm):
    wallet_id: str
    input_mint: str = "native"  # "native" = SOL; anything else is a token mint address
    output_mint: str  # "native" or a mint address
    amount: float = Field(gt=0)
    slippage_pct: float = Field(default=1.0, ge=0.01, le=50)


@router.post("/wallets/swap")
async def swap(body: SwapBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
               rpc: SolanaRPC = Depends(get_rpc), ex: Executor = Depends(get_executor)):
    """Same-chain swaps from any managed wallet: Solana routes through Jupiter's aggregator, EVM chains
    through Relay's same-chain routing. Quote -> preflight -> confirm -> a fresh quote at execution time;
    tx_guard checks every transaction's signers and the executor simulates before broadcast. Cross-chain
    conversion keeps using Bridge routes."""
    if (prior := await find_replay(db, user, body)):
        return prior
    w = await owned_wallet(db, user, body.wallet_id)
    if w.kind != "managed":
        raise HTTPException(400, "watch-only and connected wallets can't sign")
    if w.network == "solana":
        return await _swap_solana(body, user, db, rpc, ex, w)
    if w.network in EVM_CHAINS:
        return await _swap_evm(body, user, db, ex, w)
    raise HTTPException(400, f"swaps aren't available on {chain_name(w.network)} yet; use a Bridge route")


async def _swap_solana(body: SwapBody, user: User, db: AsyncSession, rpc: SolanaRPC, ex: Executor, w: Wallet):
    """Swap through Jupiter: quote -> preflight -> confirm -> a fresh quote and an unsigned transaction
    Helm signs for the wallet (the executor simulates before broadcast)."""
    jup = Jupiter()
    in_mint = WSOL if body.input_mint == "native" else body.input_mint
    out_mint = WSOL if body.output_mint == "native" else body.output_mint
    if in_mint == out_mint:
        raise HTTPException(400, "input and output are the same token")
    in_meta = {"symbol": "SOL", "decimals": 9} if body.input_mint == "native" else await jup.token(in_mint)
    out_meta = {"symbol": "SOL", "decimals": 9} if body.output_mint == "native" else await jup.token(out_mint)
    if not in_meta or not out_meta:
        raise HTTPException(400, "one of these tokens isn't on Jupiter's list; check the mint address")
    amount_raw = int(round(body.amount * 10 ** int(in_meta["decimals"])))
    slippage_bps = int(body.slippage_pct * 100)

    async def quote():
        return await jup.quote(in_mint, out_mint, amount_raw, slippage_bps)

    q = await quote()
    from ..services.quote_approval import issue, verify, jupiter_bounds, check_jupiter_bounds
    scope = {"purpose": "swap", "wallet_id": w.id, "input": in_mint, "output": out_mint,
             "amount_raw": str(amount_raw), "slippage_bps": slippage_bps}
    bounds = verify(body.quote_approval, user.id, scope) if body.confirm else jupiter_bounds(q, in_mint, out_mint, amount_raw)
    if body.confirm:
        check_jupiter_bounds(q, bounds, in_mint, out_mint, amount_raw)
    out_dec = int(out_meta["decimals"])
    out_amount = int(q["outAmount"]) / 10 ** out_dec
    min_out = int(bounds["minimum_raw"]) / 10 ** out_dec
    route = " → ".join(dict.fromkeys(
        (s.get("swapInfo") or {}).get("label") or "pool" for s in q.get("routePlan") or [])) or "Jupiter"
    impact = float(q.get("priceImpactPct") or 0)
    from ..services.charges import trade_terms
    fee_terms = await trade_terms(db, w.network)
    warnings = []
    needed = amount_raw + amount_raw * 250 // 10_000
    if body.input_mint == "native":
        if await rpc.get_balance(w.address) < needed + 10_000_000:
            warnings.append("Keep the swap amount, its 2.5% fee and SOL for network costs in this wallet.")
    elif await rpc.get_token_balance(w.address, in_mint) < needed:
        warnings.append("Keep the input amount plus its 2.5% fee in this wallet.")
    if impact >= 0.1:
        warnings.append(f"price impact is {impact * 100:.1f}% — check the route before confirming")
    warnings += two_fa_warnings(user, "swap")
    pf = build_preflight("swap", {
        "wallet": w.address, "network": "solana",
        "input": f"{body.amount:g} {in_meta['symbol']}",
        "output": f"at least {min_out:g} {out_meta['symbol']}",
        "approved_minimum_raw": bounds["minimum_raw"], "slippage": f"{body.slippage_pct:g}%",
        "platform_fee": "2.5% of the input amount, paid in the input asset after the swap confirms",
        "trade_fee_terms": fee_terms,
    }, warnings)
    pf["preflight"].update(estimated_output=f"{out_amount:g} {out_meta['symbol']}",
                            route=route, price_impact=f"{impact * 100:.2f}%")
    if not body.confirm:
        pf["quote_approval"] = issue(user.id, scope, bounds)
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, "swap", None, {
        "wallet_id": w.id, "input_mint": in_mint, "output_mint": out_mint, "amount_raw": amount_raw,
        "slippage_bps": slippage_bps, "trade_fee_terms": fee_terms,
    })
    if created:
        from ..services.solana_guard import SpendLimits, budget, TRADE_OVERHEAD_LAMPORTS
        floor_raw = int(bounds["minimum_raw"])
        limits = SpendLimits(w.address,
            budget(amount_raw) if body.input_mint == "native" else TRADE_OVERHEAD_LAMPORTS,
            token_out={} if body.input_mint == "native" else {in_mint: amount_raw},
            token_in_min={} if body.output_mint == "native" else {out_mint: floor_raw},
            min_lamports_in=max(0, floor_raw - TRADE_OVERHEAD_LAMPORTS) if body.output_mint == "native" else None,
            watch_mints=(in_mint, out_mint), label="swap")
        async def work(session, j):
            fresh = await quote()  # quotes expire; a new one is taken at execution time
            check_jupiter_bounds(fresh, bounds, in_mint, out_mint, amount_raw)
            raw = await jup.swap_tx(fresh, w.address)
            swap_result = await run_single(session, ex, j, w, "swap", raw, {w.address: w.signer_ref or ""}, limits=limits)
            if j.status == "confirmed":
                from ..services.charges import charge_trade
                # The execution journal is extended before settling the explicitly reviewed fee.
                j.params = {**j.params, "execution": {**j.params["execution"], "dispatch_complete": False}}
                fee = await charge_trade(session, ex, j, w, amount_raw,
                    token=None if body.input_mint == "native" else in_mint,
                    asset=in_meta["symbol"], decimals=int(in_meta["decimals"]))
                _finish(j, [swap_result, fee])
                if fee.status != "confirmed":
                    j.status = "unconfirmed" if fee.status == "timeout" else "partial"
                    j.params = {**j.params, "fee_outstanding": True, "error": fee.error}
                await session.commit()
        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


async def _swap_evm(body: SwapBody, user: User, db: AsyncSession, ex: Executor, w: Wallet):
    """Same-chain swap on an EVM chain through Relay's routing: the input can be the native coin, the
    chain's dollar stablecoin or any token address. The route is one or two transactions (approve plus
    swap) sent from the wallet itself; the swap completes with those transactions, so unlike a bridge
    there is no separate solver fill to wait for."""
    net = w.network
    sender = ex.evm(net)

    def relay_currency(mint: str) -> str:
        if mint in ("native", "stable"):
            return route_currency(net, mint)[1]
        token = mint.strip()
        if not valid_address_for(net, token):
            raise HTTPException(400, f"that's not a valid token address on {chain_name(net)}")
        return token

    in_cur, out_cur = relay_currency(body.input_mint), relay_currency(body.output_mint)
    if in_cur.lower() == out_cur.lower():
        raise HTTPException(400, "input and output are the same token")

    async def token_meta(cur: str, mint: str) -> tuple[str, int]:
        if mint == "native":
            return native_coin(net), 18
        if mint == "stable":
            return route_currency(net, "stable")[0], STABLE_DECIMALS[net]
        try:
            meta = await sender.rpc.erc20_meta(cur)
        except Exception as e:
            raise HTTPException(400, f"couldn't read that token on {chain_name(net)} ({type(e).__name__})") from e
        return meta.get("symbol") or cur[:6], int(meta["decimals"])

    in_sym, in_dec = await token_meta(in_cur, body.input_mint)
    out_sym, out_dec = await token_meta(out_cur, body.output_mint)
    amount_raw = int(round(body.amount * 10 ** in_dec))

    async def quote():
        return await ex.relay.quote(user=w.address, recipient=w.address, from_network=net, to_network=net,
                                    from_currency=in_cur, to_currency=out_cur, amount=amount_raw,
                                    slippage_pct=body.slippage_pct)

    from ..services.charges import trade_terms
    fee_terms = await trade_terms(db, w.network)
    from ..services.quote_approval import issue, verify, relay_bounds, check_relay_bounds
    scope = {"purpose": "swap", "wallet_id": w.id, "input": in_cur, "output": out_cur,
             "amount_raw": str(amount_raw), "slippage_pct": body.slippage_pct}
    bounds = verify(body.quote_approval, user.id, scope) if body.confirm else None
    warnings: list[str] = []
    try:
        q = await quote()
        summary = ex.relay.summarize(q)
        if body.confirm:
            check_relay_bounds(q, bounds, out_cur, net)
        else:
            bounds = relay_bounds(q, destination_asset=out_cur, destination_network=net)
    except AdapterError as e:
        q, summary = None, None
        warnings.append(str(e))
    try:
        gas = await fee_reserve(ex, net, 2, ROUTE_GAS)
        native_bal = await sender.rpc.get_balance(w.address) / WEI
        need = gas + (body.amount * 1.025 if body.input_mint == "native" else 0)
        if body.input_mint != "native" and await sender.rpc.erc20_balance(in_cur, w.address) < amount_raw + amount_raw * 250 // 10_000:
            warnings.append("Keep the input amount plus its 2.5% fee in this wallet.")
        if native_bal < need:
            warnings.append(f"{w.label or w.address[:8]} needs about {need:.6f} {native_coin(net)} on "
                            f"{chain_name(net)} for network fees" + (" plus the amount" if body.input_mint == "native" else "") + ".")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Couldn't read balances on {chain_name(net)}: {type(e).__name__}")
    warnings += two_fa_warnings(user, "swap")
    pf = build_preflight("swap", {
        "wallet": w.address, "network": net, "chain": chain_name(net),
        "input": f"{body.amount:g} {in_sym}",
        "output": (f"at least {summary['receive_minimum']:g} {out_sym}"
                   if summary and summary.get("receive_minimum") is not None else f"at least some {out_sym}"),
        "estimated_output": (f"{summary['receive_estimate']:g} {out_sym}"
                             if summary and summary.get("receive_estimate") is not None else None),
        "route": "Relay routing", "slippage": f"{body.slippage_pct:g}%",
        "quote": summary, "provider": "Relay (relay.link)",
        "platform_fee": "2.5% of the input amount, paid in the input asset after the swap confirms",
        "trade_fee_terms": fee_terms,
    }, warnings)
    if not body.confirm:
        if bounds:
            pf["quote_approval"] = issue(user.id, scope, bounds)
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, "swap", None, {
        "wallet_id": w.id, "network": net, "input_currency": in_cur, "output_currency": out_cur,
        "amount_raw": amount_raw, "slippage_pct": body.slippage_pct, "trade_fee_terms": fee_terms,
    })
    if created:
        async def work(session, j):
            j.status = "submitted"
            await session.commit()
            fresh = await quote()  # re-quote at execution time
            check_relay_bounds(fresh, bounds, out_cur, net)
            info = ex.relay.summarize(fresh)
            j.params = {**(j.params or {}), "request_id": info["request_id"], "quote": info}
            await session.commit()
            results = []
            for step in fresh["steps"]:
                for item in step.get("items") or []:
                    if item.get("status") == "complete":
                        continue
                    tx_kind = "swap" if step.get("id") == "deposit" else f"swap_{step.get('id')}"[:24]
                    res = await send_item(ex, w, item.get("data") or {}, j, tx_kind, max_allowance=amount_raw)
                    await _record(session, j, w, tx_kind, res, None)
                    await session.commit()
                    results.append(res)
                    if res.status != "confirmed":
                        break
                else:
                    continue
                break
            if results and all(result.status == "confirmed" for result in results):
                from ..services.charges import charge_trade
                fee = await charge_trade(session, ex, j, w, amount_raw,
                    token=None if body.input_mint == "native" else in_cur, asset=in_sym, decimals=in_dec)
                results.append(fee)
                if fee.status != "confirmed":
                    j.params = {**j.params, "fee_outstanding": True, "error": fee.error}
            _finish(j, results)
            await session.commit()
        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}
