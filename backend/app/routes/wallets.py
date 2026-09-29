import asyncio
import logging

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
from ..models import Folder, Funder, Job, Transaction, User, Wallet
from ..services.executor import Executor, _finish, _record, run_single, send_evm_tracked, submit_tracked
from ..services import portfolio
from ..services.chain_balances import STABLE_DECIMALS, native_coin, stable_for
from ..services.preflight import build_preflight
from ..chains.evm import EVM_CHAINS, WEI
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
    """Folders with the same name (ignoring case) are merged into the oldest one; wallets move with them."""
    groups: dict[str, list[Folder]] = {}
    for f in (await db.execute(select(Folder).where(Folder.user_id == user.id).order_by(Folder.id))).scalars():
        groups.setdefault(f.name.strip().lower(), []).append(f)
    removed = moved = 0
    for same in groups.values():
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


class WithdrawBody(SpendConfirm):
    wallet_ids: list[str] = Field(min_length=1, max_length=50)
    destination: str
    all: bool = True
    amount_sol: float | None = Field(default=None, gt=0)


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
    if not body.all and body.amount_sol is None:
        raise HTTPException(400, "set all=true or amount_sol")
    wallets = [await owned_wallet(db, user, wid) for wid in body.wallet_ids]
    network = wallets[0].network
    _core_only(network)
    if any(w.network != network for w in wallets):
        raise HTTPException(400, "withdraw from one network at a time")
    if not valid_address_for(network, body.destination):
        raise HTTPException(400, f"invalid {network} destination address")
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
