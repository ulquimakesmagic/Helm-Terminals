from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from solders.pubkey import Pubkey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.solana import LAMPORTS_PER_SOL, SolanaRPC, build_unsigned, sol_transfer_ix
from ..db import get_db
from ..deps import (
    SpendConfirm, begin_spend, current_user, find_replay, get_executor, get_rpc, log_activity, owned_wallet, run_job,
    two_fa_warnings, valid_address, valid_address_for,
)
from ..models import Folder, Funder, Job, Transaction, User, Wallet
from ..services.executor import Executor, _finish, _record, run_single
from ..services.preflight import build_preflight
from ..chains.evm import EVM_CHAINS, WEI, shared_evm
from ..signing import get_signer

router = APIRouter(tags=["wallets"])
TX_FEE_RESERVE = 10_000  # lamports kept back for fees


def _wallet_out(w: Wallet, balance: int | None = None) -> dict:
    out = {"id": w.id, "network": w.network, "address": w.address, "kind": w.kind, "label": w.label,
           "folder_id": w.folder_id}
    if balance is not None:
        native = balance / (LAMPORTS_PER_SOL if w.network == "solana" else WEI)
        out["balance"] = native  # in the chain's own coin: SOL, ETH or BNB
        out["balance_sol"] = native  # kept for older clients; same value
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


@router.get("/wallets")
async def list_wallets(with_balances: bool = False, user: User = Depends(current_user),
                       db: AsyncSession = Depends(get_db), rpc: SolanaRPC = Depends(get_rpc)):
    rows = (await db.execute(select(Wallet).where(Wallet.user_id == user.id))).scalars().all()
    out = []
    for w in rows:
        bal = None
        if with_balances:
            try:
                if w.network == "solana":
                    bal = await rpc.get_balance(w.address)
                elif w.network in EVM_CHAINS:
                    bal = await shared_evm(w.network).get_balance(w.address)
            except Exception:
                bal = None  # one chain being down shouldn't hide the rest
        out.append(_wallet_out(w, bal))
    return out


@router.post("/wallets", status_code=201)
async def create_wallets(body: CreateWallets, user: User = Depends(current_user),
                         db: AsyncSession = Depends(get_db), signer=Depends(get_signer)):
    if body.network != "solana":
        raise HTTPException(400, "only solana wallets are supported in v1")
    if body.folder_id:
        f = await db.get(Folder, body.folder_id)
        if not f or f.user_id != user.id:
            raise HTTPException(404, "folder not found")
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
    if not valid_address_for(body.network, body.address):
        raise HTTPException(400, f"invalid {body.network} address")
    w = Wallet(user_id=user.id, network=body.network, address=body.address, kind="watch", label=body.label)
    db.add(w)
    await db.commit()
    return _wallet_out(w)


# --- folders -------------------------------------------------------------------------------------


class FolderBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = "#6366f1"


@router.get("/folders")
async def list_folders(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Folder).where(Folder.user_id == user.id))).scalars().all()
    return [{"id": f.id, "name": f.name, "color": f.color} for f in rows]


@router.post("/folders", status_code=201)
async def create_folder(body: FolderBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    f = Folder(user_id=user.id, name=body.name, color=body.color)
    db.add(f)
    await db.commit()
    return {"id": f.id, "name": f.name, "color": f.color}


@router.patch("/folders/{folder_id}")
async def update_folder(folder_id: str, body: FolderBody, user: User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)):
    f = await db.get(Folder, folder_id)
    if not f or f.user_id != user.id:
        raise HTTPException(404, "folder not found")
    f.name, f.color = body.name, body.color
    await db.commit()
    return {"id": f.id, "name": f.name, "color": f.color}


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
    targets = [(await owned_wallet(db, user, a.wallet_id, managed=False), a.sol) for a in body.allocations]
    total = sum(sol for _, sol in targets)
    bal = await rpc.get_balance(src.address)
    warnings = []
    if bal < int(total * LAMPORTS_PER_SOL) + TX_FEE_RESERVE:
        warnings.append(f"Funder balance {bal / LAMPORTS_PER_SOL:.4f} SOL is below the {total:.4f} SOL required plus fees.")
    pf = build_preflight("fund", {
        "from": src.address,
        "to": [{"address": w.address, "sol": sol} for w, sol in targets],
        "total_sol": total, "platform_fee": "none",
    }, warnings)
    if not body.confirm:
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, "fund", None, pf["preflight"])
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
    if not valid_address(body.destination):
        raise HTTPException(400, "invalid destination address")
    if not body.all and body.amount_sol is None:
        raise HTTPException(400, "set all=true or amount_sol")
    wallets = [await owned_wallet(db, user, wid) for wid in body.wallet_ids]
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
                res = await ex.submit(raw, {w.address: w.signer_ref or ""})
                await _record(session, j, w, "withdraw", res, None)
                results.append(res)
            _finish(j, results)
            await session.commit()
        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}
