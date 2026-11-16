"""Disperse (split) and consolidate: move a coin or token between your own wallets on any chain.

Disperse sends from one wallet to several of yours, split evenly or by explicit amounts. Consolidate gathers a
share of each selected wallet's balance into one of yours. Both work for the native coin, the chain's dollar
stablecoin (USDC, or USDG on Robinhood Chain) and any token, including your launch tokens.

Only wallets on your own account can be destinations. Random allocations are frozen by a request seed and exact
base-unit amounts in the preview. When the token is one of your launches, every destination wallet is attached to that launch
so its transparency panel keeps counting the supply you control.
"""

from typing import Annotated, Literal
from decimal import Decimal

from eth_abi import encode as abi_encode
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, model_validator
from solders.pubkey import Pubkey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.flap import selector
from ..chains.solana import (TOKEN_PROGRAM, build_unsigned, create_ata_idempotent_ix,
                             sol_transfer_ix, token_transfer_checked_ix)
from ..db import get_db
from ..deps import (SpendConfirm, begin_spend, current_user, find_replay, get_executor, owned_wallet, run_job,
                    valid_address_for)
from ..models import Launch, LaunchWallet, User, Wallet
from ..services.chain_balances import chain_name, native_coin, stable_for
from ..services.evm_exec import NATIVE_RESERVE_WEI, EvmCall
from ..services.executor import Executor, SubmitResult, _finish, _record, send_evm_tracked, submit_tracked
from ..services.preflight import build_preflight
from ..services.sol_transfer_support import describe as describe_fee_payer

router = APIRouter()

SOL_FEE = 10_000  # lamports per Solana transaction, kept generous for priority fees
ATA_RENT = 2_039_280  # lamports to open a token account for a recipient that has none
SOL_NATIVE_PER_TX = 15  # recipients per Solana transaction for SOL
SOL_TOKEN_PER_TX = 5  # recipients per Solana transaction for tokens (each also opens its token account)
ERC20_TRANSFER_GAS = 90_000
SOL_RENT_MIN = 890_880  # lamports a Solana account must hold to exist


class DisperseBody(SpendConfirm):
    from_wallet_id: str
    to_wallet_ids: list[str] = Field(min_length=1, max_length=50)
    asset: str = "native"  # "native", "stable", or a token mint/contract address
    split: Literal["even", "custom", "weighted", "random"] = "even"
    fee_wallet_id: str | None = Field(default=None, max_length=32)
    weights: list[Annotated[int, Field(strict=True, ge=1, le=10000)]] | None = Field(default=None, max_length=50)
    split_seed: str | None = Field(default=None, min_length=16, max_length=64)
    total: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False, max_digits=78, decimal_places=36)
    percent: Decimal | None = Field(default=None, gt=0, le=100, allow_inf_nan=False, decimal_places=2)
    amounts: list[Decimal] | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def check(self):
        if self.split != "custom" and (self.total is None) == (self.percent is None):
            raise ValueError("a split needs exactly one of total or percent")
        if self.split == 'weighted' and (not self.weights or len(self.weights) != len(self.to_wallet_ids)
                or any(type(weight) is not int or not 1 <= weight <= 10000 for weight in self.weights)):
            raise ValueError('Provide one integer weight (1–10000) per recipient')
        if self.split == 'random' and not self.split_seed:
            raise ValueError('A random split needs a seed so review and confirmation use the same allocation')
        if self.split == "custom":
            if not self.amounts or len(self.amounts) != len(self.to_wallet_ids):
                raise ValueError("a custom split needs one amount per destination wallet")
            if any(not a.is_finite() or a <= 0 for a in self.amounts):
                raise ValueError("every custom amount must be above zero")
        return self


class ConsolidateBody(SpendConfirm):
    from_wallet_ids: list[str] = Field(min_length=1, max_length=50)
    to_wallet_id: str
    asset: str = "native"
    percent: float = Field(default=100, gt=0, le=100)
    fee_wallet_id: str | None = Field(default=None, max_length=32)


# --- asset helpers --------------------------------------------------------------------------------


async def _asset(db: AsyncSession, user: User, ex: Executor, network: str, asset: str) -> dict:
    """What is being moved: kind (native|token), symbol, token address and decimals."""
    if asset == "native":
        return {"kind": "native", "symbol": native_coin(network), "token": None,
                "decimals": 9 if network == "solana" else 18}
    if asset == "stable":
        symbol, token = stable_for(network)
    else:
        token = asset.strip()
        if not valid_address_for(network, token):
            raise HTTPException(400, f"invalid {chain_name(network)} token address")
        symbol = None
    launch = (await db.execute(select(Launch).where(
        Launch.user_id == user.id, Launch.network == network, Launch.mint_address.is_not(None),
        Launch.mint_address.ilike(token)))).scalars().first()
    try:
        if network == "solana":
            _, decimals = await ex.rpc.get_token_supply(token)
            program = await ex.rpc.get_mint_program(token) if hasattr(ex.rpc, "get_mint_program") else TOKEN_PROGRAM
        else:
            meta = await ex.evm(network).rpc.erc20_meta(token)
            decimals, symbol, program = int(meta["decimals"]), symbol or meta.get("symbol"), None
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Couldn't read that token on {chain_name(network)} ({type(e).__name__})")
    symbol = symbol or (launch.symbol.upper() if launch else token[:6])
    return {"kind": "token", "symbol": symbol, "token": token, "decimals": decimals, "program": program,
            "launch": launch}


async def _balance(ex: Executor, network: str, a: dict, address: str) -> int:
    if a["kind"] == "native":
        if network == "solana":
            return int(await ex.rpc.get_balance(address))
        return int(await ex.evm(network).rpc.get_balance(address))
    if network == "solana":
        return int(await ex.rpc.get_token_balance(address, a["token"]))
    return int(await ex.evm(network).rpc.erc20_balance(a["token"], address))


async def _native_raw(ex: Executor, network: str, address: str) -> int:
    return int(await (ex.rpc.get_balance(address) if network == "solana" else ex.evm(network).rpc.get_balance(address)))


def _to_raw(amount: Decimal | float, decimals: int) -> int:
    from decimal import localcontext
    with localcontext() as ctx:
        ctx.prec = 100
        raw = Decimal(str(amount)).scaleb(decimals)
        if not raw.is_finite() or raw != raw.to_integral_value():
            raise HTTPException(400, "Amount must be a whole number of token base units")
        return int(raw)


def _human(raw: int, decimals: int) -> float:
    return raw / 10**decimals


def _label(w: Wallet) -> str:
    return w.label or w.address[:8]


async def _attach(db: AsyncSession, launch: Launch | None, wallets: list[Wallet]) -> list[str]:
    """Attach wallets receiving a launch's tokens to that launch, so its transparency panel counts them."""
    if not launch:
        return []
    added = []
    for w in wallets:
        if await db.get(LaunchWallet, (launch.id, w.id)) is None:
            db.add(LaunchWallet(launch_id=launch.id, wallet_id=w.id, role="holder"))
            added.append(w.address)
    return added


def _erc20_transfer(to: str, amount: int) -> bytes:
    return selector("transfer(address,uint256)") + abi_encode(["address", "uint256"], [to, amount])


async def _send_solana(ex: Executor, job, src: Wallet, kind: str, a: dict,
                       legs: list[tuple[Wallet, int]], payer: Wallet | None = None) -> SubmitResult:
    owner = Pubkey.from_string(src.address)
    payer = payer or src
    paying = Pubkey.from_string(payer.address)
    ixs = []
    for dst, raw in legs:
        dest = Pubkey.from_string(dst.address)
        if a["kind"] == "native":
            ixs.append(sol_transfer_ix(owner, dest, raw))
        else:
            mint, program = Pubkey.from_string(a["token"]), a["program"]
            ixs += [create_ata_idempotent_ix(paying, dest, mint, program),
                    token_transfer_checked_ix(owner, dest, mint, raw, a["decimals"], program)]
    tx = build_unsigned(paying, ixs, await ex.rpc.get_latest_blockhash())
    from ..services.sol_token_send import refs_for
    return await submit_tracked(ex, job, src, kind, tx, refs_for(src, payer))


async def _send_evm(ex: Executor, job, src: Wallet, kind: str, a: dict, dst: Wallet, raw: int) -> SubmitResult:
    sender = ex.evm(src.network)
    call = EvmCall(to=dst.address, value=raw) if a["kind"] == "native" else \
        EvmCall(to=a["token"], data=_erc20_transfer(dst.address, raw))
    return await send_evm_tracked(sender, job, src, kind, call)


# --- disperse -------------------------------------------------------------------------------------


@router.post("/wallets/disperse")
async def disperse(body: DisperseBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                   ex: Executor = Depends(get_executor)):
    if (prior := await find_replay(db, user, body)):
        return prior
    src = await owned_wallet(db, user, body.from_wallet_id)
    from .wallets import _core_only

    _core_only(src.network)
    if len(set(body.to_wallet_ids)) != len(body.to_wallet_ids):
        raise HTTPException(400, "each destination wallet can only be listed once")
    if src.id in body.to_wallet_ids:
        raise HTTPException(400, "the source wallet can't also be a destination")
    targets = [await owned_wallet(db, user, wid) for wid in body.to_wallet_ids]
    net = src.network
    if any(t.network != net for t in targets):
        raise HTTPException(400, f"all destination wallets must be on {chain_name(net)}")
    a = await _asset(db, user, ex, net, body.asset)
    coin = native_coin(net)
    n = len(targets)
    payer, fee_warnings, funding = src, [], []
    try:
        bal = await _balance(ex, net, a, src.address)
        native_bal = await _native_raw(ex, net, src.address)
        if net == "solana":
            from ..services.sol_transfer_support import plan as sol_fee_plan
            principal = (sum(_to_raw(x, a['decimals']) for x in body.amounts or []) if body.split == 'custom'
                         else _to_raw(body.total, a['decimals']) if body.total is not None else 0) if a['kind'] == 'native' else 0
            payer, gas, fee_warnings = await sol_fee_plan(db, ex, user, src, a, [t.address for t in targets],
                fee_wallet_id=body.fee_wallet_id, principal=principal)
        else:
            sender = ex.evm(net)
            from ..services.gas_funding import allowance
            gas = n * await allowance(ex, net, 21_000 if a["kind"] == "native" else ERC20_TRANSFER_GAS)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Couldn't read balances on {chain_name(net)}: {type(e).__name__}")

    separate = payer.id != src.id if net == 'solana' else bool(body.fee_wallet_id and body.fee_wallet_id != src.id)
    available = max(bal - gas, 0) if a["kind"] == "native" and not separate else bal
    if body.split == "custom":
        raws = [_to_raw(x, a["decimals"]) for x in body.amounts or []]
    else:
        total = _to_raw(body.total, a["decimals"]) if body.total is not None \
            else available * int(round((body.percent or 0) * 100)) // 10_000
        if body.split in ('weighted', 'random'):
            from ..services.split_amounts import allocate, random_weights
            weights = body.weights if body.split == 'weighted' else random_weights(body.split_seed, src.id, body.to_wallet_ids)
            raws = allocate(total, weights)
        else:
            raws = [total // n] * n
            raws[0] += total - sum(raws)  # any indivisible remainder goes to the first wallet
    total = sum(raws)
    d = a["decimals"]
    if net != 'solana':
        from ..services.gas_funding import plan as gas_plan
        funding, fee_warnings = await gas_plan(db, ex, user, [(src, gas, total if a['kind'] == 'native' else 0)], body.fee_wallet_id)
        if funding and a['kind'] == 'native':
            available = bal
    warnings = list(fee_warnings)
    if any(r <= 0 for r in raws):
        warnings.append(f"The split is too small: at least one wallet would get 0 {a['symbol']}.")
    if total > available:
        warnings.append(f"{_label(src)} has {_human(available, d):,.6g} {a['symbol']} available"
                        + (" after network fees" if a["kind"] == "native" else "")
                        + f", less than the {_human(total, d):,.6g} {a['symbol']} to send.")
    if net == "solana" and a["kind"] == "native":
        for t, r in zip(targets, raws):
            if 0 < r < SOL_RENT_MIN and await ex.rpc.get_balance(t.address) + r < SOL_RENT_MIN:
                warnings.append(f"{_label(t)} would hold less than 0.00089 SOL, the minimum a Solana wallet "
                                "needs, so that transfer would fail. Send more to each wallet.")
                break
    if a["kind"] == "token" and native_bal < gas and not (net == 'solana' and payer.id != src.id) and not funding and not fee_warnings:
        warnings.append(f"{_label(src)} needs about {_human(gas, 9 if net == 'solana' else 18):.6f} {coin} for "
                        f"network fees{' and new token accounts' if net == 'solana' else ''}.")
    launch = a.get("launch")
    if launch is not None:
        from ..models import LaunchAllocation
        from ..services.allocations import lock_warning

        if (msg := await lock_warning(db, launch, src, bal, total, d)):
            warnings.append(msg)
        locked_to = {r.lower() for r in (await db.execute(select(LaunchAllocation.recipient).where(
            LaunchAllocation.launch_id == launch.id, LaunchAllocation.lock.is_(True)))).scalars()}
        for t in targets:
            if t.address.lower() in locked_to:
                warnings.append(f"{_label(t)} has a locked allocation line; tokens sent to it would be locked too. "
                                "Pick another wallet or change the plan first.")
    pf = build_preflight("disperse", {
        "network": net, "chain": chain_name(net), "asset": a["symbol"], "token": a["token"],
        "from": {"label": src.label or "Wallet", "address": src.address},
        "to": [{"label": t.label or "Wallet", "address": t.address, "amount": _human(r, d), "amount_raw": str(r)} for t, r in zip(targets, raws)],
        "total": _human(total, d), "total_raw": str(total), "decimals": d, "split": body.split,
        "split_seed": body.split_seed if body.split == 'random' else None,
        "fee_funding": funding,
        "fee_payers": [describe_fee_payer(src, payer)] if net == 'solana' else [],
        "network_fees_max": _human(gas + sum(int(f['funding_fee_max_raw']) for f in funding), 9 if net == "solana" else 18), "network_fee_coin": coin,
        "transactions": (-(-n // (SOL_NATIVE_PER_TX if a["kind"] == "native" else SOL_TOKEN_PER_TX)))
        if net == "solana" else n,
        "own_wallets_only": True,
        "transparency": (f"Destination wallets are attached to {launch.name} (${launch.symbol}) so its "
                         "transparency panel counts this supply as yours") if launch else None,
        "platform_fee": "none",
    }, warnings)
    if not body.confirm:
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, "disperse", launch.id if launch else None, pf["preflight"])
    if created:
        await _attach(db, launch, targets)
        await db.commit()
        legs = list(zip(targets, raws))

        async def work(session, j):
            j.status = "submitted"
            await session.commit()
            results = []
            if funding:
                from ..services.gas_funding import execute
                results = await execute(session, ex, j, funding)
                if any(r.status != 'confirmed' for r in results):
                    _finish(j, results); await session.commit(); return
            if net == "solana":
                size = SOL_NATIVE_PER_TX if a["kind"] == "native" else SOL_TOKEN_PER_TX
                chunks = [legs[i:i + size] for i in range(0, len(legs), size)]
            else:
                chunks = [[leg] for leg in legs]
            for chunk in chunks:
                if net == "solana":
                    res = await _send_solana(ex, j, src, "disperse", a, chunk, payer)
                else:
                    res = await _send_evm(ex, j, src, "disperse", a, chunk[0][0], chunk[0][1])
                await _record(session, j, src, "disperse", res, None)
                if res.status == "confirmed" and launch is not None:
                    # The supply moved to these wallets: record it in the launch's allocation plan and trail.
                    from decimal import Decimal

                    from ..deps import log_activity
                    from .allocations import record_dispersal

                    moved = [(t.address, Decimal(r) / (Decimal(10) ** d)) for t, r in chunk]
                    await record_dispersal(session, launch, user.id, src.address, moved, j.id)
                    await log_activity(session, user.id, "supply.dispersed", "launch", launch.id, {
                        "job_id": j.id, "from": src.address, "signature": res.signature,
                        "to": [{"address": a, "amount": str(x)} for a, x in moved]})
                await session.commit()
                results.append(res)
                if res.status != "confirmed":
                    break  # don't keep spending from a wallet whose last send went wrong
            if len(results) == len(funding) + len(chunks):
                from ..services.gas_funding import assets_dispatched
                assets_dispatched(j)
            _finish(j, results)
            await session.commit()

        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


# --- consolidate ----------------------------------------------------------------------------------


@router.post("/wallets/consolidate")
async def consolidate(body: ConsolidateBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                      ex: Executor = Depends(get_executor)):
    if (prior := await find_replay(db, user, body)):
        return prior
    if len(set(body.from_wallet_ids)) != len(body.from_wallet_ids):
        raise HTTPException(400, "each source wallet can only be listed once")
    if body.to_wallet_id in body.from_wallet_ids:
        raise HTTPException(400, "the destination wallet can't also be a source")
    if body.asset == 'native' and len(body.from_wallet_ids) > 1 and body.fee_wallet_id in body.from_wallet_ids:
        raise HTTPException(400, 'Choose a fee wallet outside the source wallets when consolidating native coin')
    dest = await owned_wallet(db, user, body.to_wallet_id)
    from .wallets import _core_only

    _core_only(dest.network)
    sources = [await owned_wallet(db, user, wid) for wid in body.from_wallet_ids]
    net = dest.network
    if any(s.network != net for s in sources):
        raise HTTPException(400, f"all source wallets must be on {chain_name(net)}")
    a = await _asset(db, user, ex, net, body.asset)
    coin = native_coin(net)
    d = a["decimals"]
    nd = 9 if net == "solana" else 18
    plan: list[tuple[Wallet, int]] = []
    skipped: list[str] = []
    warnings: list[str] = []
    payers, gas_by_source, funding = {}, {}, []
    committed, payer_balances = {}, {}
    try:
        if net == "solana":
            gas_each = SOL_FEE + (ATA_RENT if a["kind"] == "token" else 0)
        else:
            sender = ex.evm(net)
            from ..services.gas_funding import allowance
            gas_each = await allowance(ex, net, 21_000 if a["kind"] == "native" else ERC20_TRANSFER_GAS)
        for s in sources:
            bal = await _balance(ex, net, a, s.address)
            if bal <= 0:
                skipped.append(s.address)
                continue
            if net == 'solana':
                from ..services.sol_transfer_support import plan as sol_fee_plan
                principal = bal * int(round(body.percent * 100)) // 10_000 if a['kind'] == 'native' and body.percent < 100 else 0
                payer, gas_each, issues = await sol_fee_plan(db, ex, user, s, a, [dest.address],
                    fee_wallet_id=body.fee_wallet_id, principal=principal, committed=committed,
                    cache=payer_balances, exclude_ids={w.id for w in sources}, prefer=[dest])
                payers[s.id] = payer
                warnings += issues
            gas_by_source[s.id] = gas_each
            if a["kind"] == "native":
                if net == "solana":
                    spendable = max(bal - gas_each, 0) if payers[s.id].id == s.id else bal
                else:
                    spendable = bal if body.fee_wallet_id and body.fee_wallet_id != s.id else await sender.spendable(s.address)
                raw = spendable if body.percent >= 100 else min(bal * int(round(body.percent * 100)) // 10_000, spendable)
            else:
                raw = bal * int(round(body.percent * 100)) // 10_000
                if a.get("launch") is not None:
                    from ..services.allocations import lock_warning

                    if (msg := await lock_warning(db, a["launch"], s, bal, raw, d)):
                        warnings.append(msg)
            if raw <= 0:
                skipped.append(s.address)
            else:
                plan.append((s, raw))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Couldn't read balances on {chain_name(net)}: {type(e).__name__}")
    if not plan:
        warnings.append(f"None of the selected wallets has {a['symbol']} to move.")
    if net != 'solana' and plan:
        from ..services.gas_funding import plan as gas_plan
        funding, issues = await gas_plan(db, ex, user, [(s, gas_by_source[s.id], raw if a['kind'] == 'native' else 0) for s, raw in plan], body.fee_wallet_id)
        warnings += issues
    launch = a.get("launch")
    pf = build_preflight("consolidate", {
        "network": net, "chain": chain_name(net), "asset": a["symbol"], "token": a["token"],
        "to": {"label": dest.label or "Wallet", "address": dest.address},
        "from": [{"label": s.label or "Wallet", "address": s.address, "amount": _human(r, d), "amount_raw": str(r)} for s, r in plan],
        "skipped_empty": skipped, "percent": body.percent, "total": _human(sum(r for _, r in plan), d),
        "total_raw": str(sum(r for _, r in plan)), "decimals": d,
        "fee_funding": funding,
        "fee_payers": [describe_fee_payer(s, payers[s.id]) for s, _ in plan] if net == 'solana' else [],
        "network_fees_max": _human(sum(gas_by_source[s.id] for s, _ in plan) + sum(int(f['funding_fee_max_raw']) for f in funding), nd), "network_fee_coin": coin,
        "own_wallets_only": True,
        "transparency": (f"The destination wallet is attached to {launch.name} (${launch.symbol}) so its "
                         "transparency panel counts this supply as yours") if launch else None,
        "platform_fee": "none",
    }, warnings)
    if not body.confirm:
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, "consolidate", launch.id if launch else None, pf["preflight"])
    if created:
        await _attach(db, launch, [dest])
        await db.commit()

        async def work(session, j):
            j.status = "submitted"
            await session.commit()
            results = []
            if funding:
                from ..services.gas_funding import execute
                results = await execute(session, ex, j, funding)
                if any(r.status != 'confirmed' for r in results):
                    _finish(j, results); await session.commit(); return
            for s, raw in plan:  # each source signs its own transfer; one failing doesn't stop the others
                if net == "solana":
                    res = await _send_solana(ex, j, s, "consolidate", a, [(dest, raw)], payers[s.id])
                else:
                    res = await _send_evm(ex, j, s, "consolidate", a, dest, raw)
                await _record(session, j, s, "consolidate", res, None)
                await session.commit()
                results.append(res)
            from ..services.gas_funding import assets_dispatched
            assets_dispatched(j)
            _finish(j, results)
            await session.commit()

        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}


# --- sweep ----------------------------------------------------------------------------------------


class SweepBody(SpendConfirm):
    from_wallet_ids: list[str] = Field(min_length=1, max_length=50)
    to_wallet_id: str


@router.post("/wallets/sweep")
async def sweep(body: SweepBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                ex: Executor = Depends(get_executor)):
    """Sweep dust: move everything from several of your wallets into one — the full stablecoin balance
    and the whole native coin minus what its own transfer needs for fees. Stablecoins go first (each
    wallet still holds gas for its own send), then the native coin empties. Wallets with nothing to
    move are listed, not failed."""
    from .wallets import _core_only

    if (prior := await find_replay(db, user, body)):
        return prior
    if len(set(body.from_wallet_ids)) != len(body.from_wallet_ids):
        raise HTTPException(400, "each source wallet can only be listed once")
    if body.to_wallet_id in body.from_wallet_ids:
        raise HTTPException(400, "the destination wallet can't also be a source")
    dest = await owned_wallet(db, user, body.to_wallet_id)
    _core_only(dest.network)
    sources = [await owned_wallet(db, user, wid) for wid in body.from_wallet_ids]
    net = dest.network
    if any(s.network != net for s in sources):
        raise HTTPException(400, f"all source wallets must be on {chain_name(net)}")

    a_native = {"kind": "native", "symbol": native_coin(net), "token": None, "decimals": 9 if net == "solana" else 18}
    if net == "solana":
        from ..services.cash_operations import cash_operation
        return await cash_operation(body, user, db, ex, sources, dest.address, sweep=True)
    a_stable = await _asset(db, user, ex, net, "stable")
    nd = 9 if net == "solana" else 18
    warnings: list[str] = []
    try:
        sender = ex.evm(net) if net != "solana" else None
        gas_each = (SOL_FEE + ATA_RENT) if net == "solana" else await sender.max_gas_cost(ERC20_TRANSFER_GAS)
        plan_stable: list[tuple[Wallet, int]] = []
        plan_native: list[tuple[Wallet, int]] = []
        skipped: list[str] = []
        for s in sources:
            stable_bal = await _balance(ex, net, a_stable, s.address)
            native_bal = await _balance(ex, net, a_native, s.address)
            if net == "solana":
                native_spendable = max(native_bal - SOL_FEE, 0)
            else:
                native_spendable = await sender.spendable(s.address)
            if stable_bal > 0:
                plan_stable.append((s, stable_bal))
                native_spendable = max(native_spendable - gas_each, 0)
                if native_bal < gas_each:  # stablecoins go first, so every wallet pays its own gas
                    warnings.append(f"{_label(s)} holds {a_stable['symbol']} but needs about "
                                    f"{_human(gas_each, nd):.6f} {native_coin(net)} for the network fee to send it.")
            if native_spendable > 0:
                plan_native.append((s, native_spendable))
            if stable_bal <= 0 and native_spendable <= 0:
                skipped.append(s.address)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Couldn't read balances on {chain_name(net)}: {type(e).__name__}")

    if not plan_stable and not plan_native:
        warnings.append("None of the selected wallets has anything to sweep.")
    legs = len(plan_stable) + len(plan_native)
    if net == "solana":
        gas = legs * SOL_FEE + len(plan_stable) * ATA_RENT
    else:
        gas = len(plan_stable) * await sender.max_gas_cost(ERC20_TRANSFER_GAS) + len(plan_native) * await sender.max_gas_cost()
    pf = build_preflight("sweep", {
        "network": net, "chain": chain_name(net),
        "to": {"label": dest.label or "Wallet", "address": dest.address},
        "from": [{"label": s.label or "Wallet", "address": s.address,
                  "amount": _human(r, a_stable["decimals"]), "asset": a_stable["symbol"]} for s, r in plan_stable]
        + [{"label": s.label or "Wallet", "address": s.address,
            "amount": _human(r, a_native["decimals"]), "asset": a_native["symbol"]} for s, r in plan_native],
        "skipped_empty": skipped,
        "wallets_swept": len({s.id for s, _ in plan_stable} | {s.id for s, _ in plan_native}),
        "total_stable": _human(sum(r for _, r in plan_stable), a_stable["decimals"]),
        "total_native": _human(sum(r for _, r in plan_native), nd),
        "network_fees_max": _human(gas, nd), "network_fee_coin": native_coin(net),
        "own_wallets_only": True, "platform_fee": "none",
    }, warnings)
    if not body.confirm:
        return pf
    if warnings:
        raise HTTPException(400, warnings[0])
    job, created = await begin_spend(db, user, body, pf, "sweep", None, {**pf["preflight"], "network": net})
    if created:
        stable_legs = list(zip(plan_stable, [a_stable] * len(plan_stable)))

        async def work(session, j):
            j.status = "submitted"
            await session.commit()
            results = []
            blocked_sources = set()

            async def leg(s: Wallet, raw: int, asset: dict, kind: str):
                if net == "solana":
                    return await _send_solana(ex, j, s, kind, asset, [(dest, raw)])
                return await _send_evm(ex, j, s, kind, asset, dest, raw)

            for (s, raw), asset in stable_legs:  # stablecoins first: every wallet still has its gas
                res = await leg(s, raw, asset, "sweep_stable")
                await _record(session, j, s, "sweep_stable", res, None)
                await session.commit()
                results.append(res)
                if res.status != "confirmed":
                    blocked_sources.add(s.id)
            for s, raw in plan_native:  # then the coin empties, keeping only fee reserves
                if s.id in blocked_sources:
                    continue
                # The stablecoin transfer has already consumed gas. Never reuse its pre-send balance.
                raw = min(raw, await sender.spendable(s.address))
                if raw <= 0:
                    continue
                res = await leg(s, raw, a_native, "sweep")
                await _record(session, j, s, "sweep", res, None)
                await session.commit()
                results.append(res)
            _finish(j, results)
            await session.commit()
        await run_job(job.id, work)
    return {"job_id": job.id, "status": job.status}
