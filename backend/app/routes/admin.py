"""Admin dashboard API: Helm's revenue, treasury addresses, fee settings, users and the audit log.

Every route here checks the caller's admin role on the server (services/admin.py): the frontend hiding the admin
pages is only cosmetic. Nothing here can read or use a user's wallet keys, sign, or move funds: treasury addresses
are receive-only, and withdrawals from them are signed outside Helm.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from eth_utils import is_checksum_address, to_checksum_address
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.evm import EVM_CHAINS
from ..db import get_db
from ..deps import get_executor, valid_address
from ..models import (AccountStatus, AdminAudit, AdminRole, Automation, FeeRecord, Job, Launch, PlatformSetting, Profile,
                      ReferralLink, RevenueEvent, Session, TreasuryAddress, User, Wallet, utcnow)
from ..services import platform_config as pc
from ..services.admin import StepUp, any_admin, audit, role_of, step_up, superadmin
from ..services.executor import Executor

router = APIRouter(prefix="/admin", tags=["admin"])

TREASURY_NETWORKS = ("solana", "evm", *EVM_CHAINS)
EXPLORERS = {"solana": "https://solscan.io/account/", "base": "https://basescan.org/address/",
             "bsc": "https://bscscan.com/address/", "ethereum": "https://etherscan.io/address/",
             "robinhood": "https://robinhoodchain.blockscout.com/address/"}
SOURCES = ("trade_fee", "dex_trade_fee", "route_fee", "creator_share", "partner")
STATUSES = ("received", "pending", "claimable", "failed", "configured")


def iso(dt: datetime | None) -> str | None:
    """UTC timestamps, always marked as UTC."""
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def mask(addr: str | None) -> str:
    if not addr:
        return ""
    return addr if len(addr) <= 10 else f"{addr[:4]}…{addr[-4:]}"


def mask_email(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:1]}***@{domain}"


def check_treasury_address(network: str, address: str) -> str:
    """The address in canonical form if it is valid for the network, else a 400. Solana addresses may be off-curve
    (a multisig vault is a program-derived address), so only the format is checked."""
    address = address.strip()
    if network not in TREASURY_NETWORKS:
        raise HTTPException(400, f"unknown network; use one of {', '.join(TREASURY_NETWORKS)}")
    if network == "solana":
        if address.startswith("0x") or not valid_address(address):
            raise HTTPException(400, "not a valid Solana address")
        if address == "11111111111111111111111111111111":
            raise HTTPException(400, "that's the system program, not a treasury")
        return address
    if len(address) != 42 or address[:2] != "0x" or any(c not in "0123456789abcdefABCDEF" for c in address[2:]):
        raise HTTPException(400, "not a valid EVM address (0x followed by 40 hex characters)")
    if int(address, 16) == 0 or address.lower() == "0x000000000000000000000000000000000000dead":
        raise HTTPException(400, "that's a burn address, not a treasury")
    body = address[2:]
    if body != body.lower() and body != body.upper() and not is_checksum_address(address):
        raise HTTPException(400, "the address checksum doesn't match; check for a typo")
    return to_checksum_address(address)


# --- who am I ------------------------------------------------------------------------------------------


@router.get("/me")
async def admin_me(user: User = Depends(any_admin)):
    return {"id": user.id, "role": user.admin_role,  # type: ignore[attr-defined]
            "can_change": user.admin_role == "superadmin"}  # type: ignore[attr-defined]


@router.get("/config")
async def config_diagnostics(user: User = Depends(any_admin)):
    """Non-secret operational diagnostics. Never return endpoint URLs, keys, recipient values or credentials."""
    from ..config import get_settings

    s = get_settings()
    return {
        "signer": s.signer,
        "superadmin_id_configured": bool(s.superadmin_user_id.strip()),
        "trusted_proxy_ranges_configured": bool(s.trusted_proxy_cidrs.strip()),
        "rpc_configured": {
            "solana": bool(s.solana_rpc_url),
            **{name: bool(getattr(s, f"evm_rpc_{name}", "")) for name in EVM_CHAINS},
            "arc": bool(s.arc_rpc_url),
        },
        "external_provider_keys_configured": {
            "turnkey": bool(s.turnkey_org_id and s.turnkey_api_public_key and s.turnkey_api_private_key),
            "bags": bool(s.bags_api_key), "jupiter": bool(s.jupiter_api_key),
        },
    }


# --- revenue -------------------------------------------------------------------------------------------


def _revenue_filters(q, start: str | None, end: str | None, network: str | None, asset: str | None,
                     source: str | None, status: str | None):
    if start:
        q = q.where(RevenueEvent.created_at >= _parse_day(start))
    if end:
        q = q.where(RevenueEvent.created_at < _parse_day(end) + timedelta(days=1))
    if network:
        q = q.where(RevenueEvent.network == network)
    if asset:
        q = q.where(RevenueEvent.asset == asset)
    if source:
        q = q.where(RevenueEvent.source == source)
    if status == "unreconciled":
        q = q.where(RevenueEvent.reconciled_at.is_(None))
    elif status:
        q = q.where(RevenueEvent.status == status)
    return q


def _parse_day(s: str) -> datetime:
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "dates are YYYY-MM-DD (UTC)")


@router.get("/revenue/summary")
async def revenue_summary(start: str | None = None, end: str | None = None, network: str | None = None,
                          asset: str | None = None, source: str | None = None,
                          user: User = Depends(any_admin), db: AsyncSession = Depends(get_db)):
    """Totals per asset and network (never converted or added across assets), split by status and source."""
    q = select(RevenueEvent.network, RevenueEvent.asset, RevenueEvent.decimals, RevenueEvent.source,
               RevenueEvent.status, (RevenueEvent.reconciled_at.is_(None)).label("unreconciled"),
               func.count(), func.sum(RevenueEvent.amount_raw),
               func.sum(case((RevenueEvent.estimated, 1), else_=0)),
               func.sum(case((RevenueEvent.amount_raw.is_(None), 1), else_=0)),
               func.sum(case((RevenueEvent.estimated, RevenueEvent.amount_raw), else_=0)))
    q = _revenue_filters(q, start, end, network, asset, source, None)
    q = q.group_by(RevenueEvent.network, RevenueEvent.asset, RevenueEvent.decimals, RevenueEvent.source,
                   RevenueEvent.status, "unreconciled")
    buckets: dict[tuple, dict] = {}
    by_source: dict[str, dict] = {}
    for net, a, dec, src, st, unrec, n, total, n_est, n_unknown, estimate_total in (await db.execute(q)).all():
        key = (net, a, dec)
        b = buckets.setdefault(key, {"network": net, "asset": a, "decimals": dec, "received": 0, "pending": 0,
                                     "claimable": 0, "failed": 0, "estimated": 0, "unreconciled": 0,
                                     "entries": 0, "estimated_entries": 0,
                                     "unknown_amount_entries": 0,
                                     "estimated_by_status": {"received": 0, "pending": 0, "claimable": 0, "failed": 0}})
        estimate_raw = int(estimate_total or 0)
        measured_raw = int(total or 0) - estimate_raw
        b[st] = b.get(st, 0) + measured_raw
        b["estimated"] += estimate_raw
        b["estimated_by_status"][st] = b["estimated_by_status"].get(st, 0) + estimate_raw
        if unrec:
            b["unreconciled"] += measured_raw
        b["entries"] += n
        b["estimated_entries"] += int(n_est or 0)
        b["unknown_amount_entries"] += int(n_unknown or 0)
        s = by_source.setdefault(src, {"source": src, "entries": 0, "by_asset": {}})
        s["entries"] += n
        k = f"{a} on {net}"
        s["by_asset"].setdefault(k, {"network": net, "asset": a, "decimals": dec, "received": 0, "pending": 0,
                                     "claimable": 0, "failed": 0, "estimated": 0,
                                     "estimated_by_status": {"received": 0, "pending": 0, "claimable": 0, "failed": 0}})
        s["by_asset"][k][st] = s["by_asset"][k].get(st, 0) + measured_raw
        s["by_asset"][k]["estimated"] += estimate_raw
        s["by_asset"][k]["estimated_by_status"][st] = s["by_asset"][k]["estimated_by_status"].get(st, 0) + estimate_raw
    for s in by_source.values():
        s["by_asset"] = list(s["by_asset"].values())
    owed_rows = (await db.execute(select(FeeRecord.network, func.sum(FeeRecord.referral_lamports), func.count())
                                  .where(FeeRecord.status == "confirmed", FeeRecord.referral_status == "owed",
                                         FeeRecord.referral_lamports > 0)
                                  .group_by(FeeRecord.network))).all()
    return {
        "totals": sorted(buckets.values(), key=lambda b: (b["network"], b["asset"])),
        "by_source": [by_source.get(src, {"source": src, "entries": 0, "by_asset": []}) for src in SOURCES],
        "referral_liabilities": [{"network": net, "asset": "SOL" if net == "solana" else net.upper(),
                                  "amount_raw": str(int(total or 0)),
                                  "decimals": 9 if net == "solana" else 18, "entries": n}
                                 for net, total, n in owed_rows],
        "valuation": {"method": "none: amounts are shown in their original asset and network, never converted or "
                                "added across assets",
                      "as_of": iso(utcnow())},
        "notes": {
            "configured": "Provider commission recipient set at launch; no accrued or received amount verified.",
            "received": "Moved on-chain to a treasury address in a confirmed transaction Helm sent.",
            "pending": "In a transaction that hadn't confirmed when it was recorded.",
            "claimable": "Held by a provider (Relay, a launchpad fee locker, Bags, Flap) until claimed. Not received "
                         "treasury funds.",
            "failed": "The signed transaction reached a terminal failed outcome; this amount was not received.",
            "referral_liabilities": "Confirmed referral shares held in Helm's treasury and owed to referrers; excluded from Helm revenue.",
            "unreconciled": "Not yet matched by an admin against the treasury's on-chain history.",
            "estimated": "Computed from a quote rather than read on-chain. Estimated amounts are excluded from the status totals.",
            "unknown_amount": "The provider doesn't report the amount to Helm (e.g. creator-reward shares); check the "
                              "provider's claim page.",
        },
        "warnings": _warnings(),
    }


def _warnings() -> list[str]:
    out = []
    for key, (lo, hi) in pc.BOUNDS.items():
        effective = pc.bps(key)
        if not lo <= effective <= hi:
            out.append(f"{key} is configured at {effective} bps, outside the admin policy range of {lo}–{hi} bps; review the owner-approved configuration.")
    for p in pc.FEE_PATHS:
        if not p["supported"]:
            out.append(f"{p['label']}: {p['how']}")
        elif p["lands"] == "claimable":
            out.append(f"{p['label']}: provider-held until claimed; not directly verifiable from Helm.")
    t = pc.current().treasury
    if "solana" not in t:
        out.append("No Solana treasury address is set, so Solana trading fees are off.")
    if "evm" not in t:
        out.append("No default EVM treasury address is set, so EVM DEX fees, Relay route fees and Flap commission "
                   "are off.")
    return out


@router.get("/revenue/ledger")
async def revenue_ledger(start: str | None = None, end: str | None = None, network: str | None = None,
                         asset: str | None = None, source: str | None = None, status: str | None = None,
                         page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
                         user: User = Depends(any_admin), db: AsyncSession = Depends(get_db)):
    if status and status not in (*STATUSES, "unreconciled"):
        raise HTTPException(400, "unknown revenue status")
    base = _revenue_filters(select(RevenueEvent), start, end, network, asset, source, status)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.order_by(RevenueEvent.created_at.desc())
                             .offset((page - 1) * size).limit(size))).scalars().all()
    launches = {l.id: l for l in (await db.execute(select(Launch).where(
        Launch.id.in_([r.launch_id for r in rows if r.launch_id])))).scalars()} if rows else {}
    return {"total": total, "page": page, "size": size, "items": [{
        "id": r.id, "source": r.source, "network": r.network, "asset": r.asset,
        "amount_raw": str(int(r.amount_raw)) if r.amount_raw is not None else None, "decimals": r.decimals,
        "estimated": r.estimated, "status": r.status, "reconciled_at": iso(r.reconciled_at),
        "destination": r.destination, "reference": r.reference, "created_at": iso(r.created_at),
        "launch": ({"id": r.launch_id, "name": launches[r.launch_id].name, "symbol": launches[r.launch_id].symbol,
                    "token": launches[r.launch_id].mint_address} if r.launch_id in launches else None),
        "note": r.note, "receipt_evidence": r.receipt_evidence, "reviewed_at": iso(r.reviewed_at),
    } for r in rows]}


class Reconcile(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=200)


@router.post("/revenue/reconcile")
async def reconcile(body: Reconcile, request: Request, user: User = Depends(superadmin),
                    db: AsyncSession = Depends(get_db)):
    """Record manual review. This does not prove receipt or alter accounting status."""
    rows = (await db.execute(select(RevenueEvent).where(RevenueEvent.id.in_(body.ids),
                                                        RevenueEvent.reviewed_at.is_(None)))).scalars().all()
    now = utcnow()
    for r in rows:
        r.reviewed_at = now
    await audit(db, user, "revenue.reconcile", ",".join(r.id for r in rows)[:120],
                {"entries": [None, len(rows)], "note": [None, body.note]}, request)
    await db.commit()
    return {"reconciled": len(rows)}


@router.post("/revenue/verify-dex")
async def verify_dex(request: Request, user: User = Depends(superadmin), db: AsyncSession = Depends(get_db),
                     ex: Executor = Depends(get_executor)):
    from ..services.revenue import verify_dex_fee
    # Row locks make concurrent reconciliation idempotent. No transaction is sent.
    rows = (await db.execute(select(RevenueEvent).where(
        RevenueEvent.source == "dex_trade_fee", RevenueEvent.receipt_evidence.is_(None))
        .order_by(RevenueEvent.created_at).limit(2).with_for_update(skip_locked=True))).scalars().all()
    counts = {}
    for row in rows:
        outcome = await verify_dex_fee(ex.evm(row.network).rpc, row)
        counts[outcome] = counts.get(outcome, 0) + 1
    await audit(db, user, "revenue.verify_dex", None, {k: [None, v] for k, v in counts.items()}, request)
    await db.commit()
    return {"checked": len(rows), **counts}


# --- Relay app fees ------------------------------------------------------------------------------------


@router.get("/relay-fees")
async def relay_fees(user: User = Depends(any_admin), db: AsyncSession = Depends(get_db),
                     ex: Executor = Depends(get_executor)):
    """What Relay actually holds for the EVM treasury (read from Relay, not estimated). Claimed at relay.link."""
    await pc.refresh(db)
    wallet = pc.treasury_for("evm")
    out = {"wallet": wallet or None, "claim_url": "https://relay.link/claim-app-fees", "as_of": iso(utcnow()),
           "source": "Relay API /app-fees/{wallet}/balances"}
    if not wallet:
        return {**out, "unavailable": "No EVM treasury is set, so Relay route fees are off."}
    try:
        data = await asyncio.wait_for(ex.relay.app_fee_balances(wallet), 12)
    except Exception as e:
        return {**out, "unavailable": f"Relay's balance couldn't be read right now ({type(e).__name__})"}
    return {**out, "total_usd": data.get("totalBalanceUsd"), "available_usd": data.get("availableBalanceUsd"),
            "balances": [{"asset": (b.get("currency") or {}).get("symbol"),
                          "chain_id": (b.get("currency") or {}).get("chainId"),
                          "amount": b.get("amountFormatted"), "amount_usd": b.get("amountUsd"),
                          "minimum": b.get("minimumAmount")} for b in data.get("balances") or []]}


@router.post("/revenue/verify-relay")
async def verify_relay(request: Request, user: User = Depends(superadmin), db: AsyncSession = Depends(get_db),
                       ex: Executor = Depends(get_executor)):
    """Check unreconciled Relay route-fee entries against what Relay reports it collected (up to 50 per run)."""
    from ..services.revenue import verify_route_fee

    rows = (await db.execute(select(RevenueEvent).where(
        RevenueEvent.source == "route_fee", RevenueEvent.reconciled_at.is_(None))
        .order_by(RevenueEvent.created_at).limit(50))).scalars().all()
    counts: dict[str, int] = {}
    for ev in rows:
        try:
            r = await asyncio.wait_for(verify_route_fee(ex.relay, ev), 12)
        except Exception:
            r = "error"
        counts[r] = counts.get(r, 0) + 1
    await audit(db, user, "revenue.verify_relay", None, {"checked": [None, len(rows)], **{k: [None, v] for k, v in counts.items()}},
                request)
    await db.commit()
    return {"checked": len(rows), **counts}


# --- treasury ------------------------------------------------------------------------------------------


async def _balances(ex: Executor, network: str, address: str) -> list[dict]:
    from ..services.chain_balances import native_balance, native_coin, stable_for, token_balance_raw, STABLE_DECIMALS

    nets = list(EVM_CHAINS) if network == "evm" else [network]
    out = []

    async def one(net: str):
        row = {"network": net, "explorer": EXPLORERS.get(net, "") + address}
        try:
            row["native"] = {"asset": native_coin(net),
                             "amount": await asyncio.wait_for(native_balance(ex, net, address), 8)}
            sym, token = stable_for(net)
            raw = await asyncio.wait_for(token_balance_raw(ex, net, token, address), 8)
            row["stable"] = {"asset": sym, "amount": raw / 10 ** STABLE_DECIMALS[net]}
        except Exception:
            row["unavailable"] = "balance couldn't be read from this chain's RPC right now"
        return row

    out = await asyncio.gather(*(one(n) for n in nets))
    return list(out)


@router.get("/treasury")
async def treasury(balances: bool = False, user: User = Depends(any_admin), db: AsyncSession = Depends(get_db),
                   ex: Executor = Depends(get_executor)):
    snap = await pc.refresh(db, force=True)
    items = []
    for net in TREASURY_NETWORKS:
        t = snap.treasury.get(net)
        # KyberSwap and Relay fees always pay the shared "evm" address; per-chain entries only override Clanker
        # reward shares and Flap commission on that chain.
        shared_only = ("trade_fee_evm_dex", "route_fee")
        if net == "evm":
            used_for = [p["label"] for p in pc.FEE_PATHS if p["supported"] and (
                p["id"] in shared_only or any(n in EVM_CHAINS for n in p["networks"]))]
        else:
            used_for = [p["label"] for p in pc.FEE_PATHS if p["supported"] and net in p["networks"]
                        and p["id"] not in shared_only]
        row = {"network": net, "address": t["address"] if t else None, "label": t.get("label", "") if t else "",
               "source": t["source"] if t else None, "updated_at": t.get("updated_at") if t else None,
               "effective": pc.treasury_for(net) or None, "used_for": used_for,
               "explorer": (EXPLORERS.get(net, "") + t["address"]) if t and net in EXPLORERS else None}
        if balances and t:
            row["balances"] = await _balances(ex, net, t["address"])
        items.append(row)
    return {"items": items, "custody": "Receive-only. Helm stores no keys for these addresses; withdrawals are "
                                       "signed outside the app (a multisig or hardware wallet is recommended)."}


class TreasuryChange(BaseModel):
    address: str = Field(min_length=0, max_length=64)  # empty = remove the admin entry (env value applies again)
    label: str = Field(default="", max_length=80)
    confirm: StepUp


@router.put("/treasury/{network}")
async def set_treasury(network: str, body: TreasuryChange, request: Request, user: User = Depends(superadmin),
                       db: AsyncSession = Depends(get_db)):
    await step_up(db, user, body.confirm)
    row = (await db.execute(select(TreasuryAddress).where(TreasuryAddress.network == network))).scalar_one_or_none()
    old = row.address if row else None
    if not body.address.strip():
        if network not in TREASURY_NETWORKS:
            raise HTTPException(400, "unknown network")
        if row:
            await db.delete(row)
        await audit(db, user, "treasury.remove", network, {"address": [old, None]}, request)
    else:
        address = check_treasury_address(network, body.address)
        held = (await db.execute(select(Wallet.id).where(func.lower(Wallet.address) == address.lower(),
                                                         Wallet.kind == "managed").limit(1))).scalar_one_or_none()
        if held:
            raise HTTPException(400, "that's a Helm-managed wallet (Helm's signer holds its key); the treasury must be "
                                     "an address only you control, ideally a multisig or hardware wallet")
        if row:
            row.address, row.label, row.updated_by = address, body.label, user.id
        else:
            db.add(TreasuryAddress(network=network, address=address, label=body.label, updated_by=user.id))
        await audit(db, user, "treasury.set", network, {"address": [old, address], "label": [None, body.label]},
                    request)
    await db.commit()
    await pc.refresh(db, force=True)
    return {"network": network, "effective": pc.treasury_for(network) or None}


# --- fee settings --------------------------------------------------------------------------------------


def _setting_requirements(key: str) -> tuple[list[dict], str | None]:
    """The fee paths a setting drives, and the treasury network it pays to."""
    paths = [p for p in pc.FEE_PATHS if p["setting"] == key]
    net = {"trade_fee_bps_solana": "solana", "trade_fee_bps_evm_dex": "evm", "route_fee_bps": "evm"}.get(key)
    return paths, net


@router.get("/settings")
async def settings(user: User = Depends(any_admin), db: AsyncSession = Depends(get_db)):
    snap = await pc.refresh(db, force=True)
    out = []
    for key, (lo, hi) in pc.BOUNDS.items():
        paths, net = _setting_requirements(key)
        cur = snap.settings.get(key, {})
        out.append({"key": key, "bps": cur.get("value", 0), "source": cur.get("source"),
                    "updated_at": cur.get("updated_at"), "min": lo, "max": hi, "treasury_network": net,
                    "treasury": pc.treasury_for(net) if net else None,
                    "paths": [{"id": p["id"], "label": p["label"], "supported": p["supported"]} for p in paths]})
    return {"settings": out, "paths": pc.FEE_PATHS,
            "creator_share_launchpads": {lp: {**pc.creator_share(lp)} for lp in pc.CREATOR_SHARE_LAUNCHPADS}}


class SettingChange(BaseModel):
    bps: int
    confirm: StepUp


@router.put("/settings/{key}")
async def set_setting(key: str, body: SettingChange, request: Request, user: User = Depends(superadmin),
                      db: AsyncSession = Depends(get_db)):
    if key not in pc.BOUNDS:
        raise HTTPException(404, "unknown setting")
    lo, hi = pc.BOUNDS[key]
    if not lo <= body.bps <= hi:
        raise HTTPException(400, f"{key} must be between {lo} and {hi} bps")
    await step_up(db, user, body.confirm)
    await pc.refresh(db, force=True)
    paths, net = _setting_requirements(key)
    if body.bps > 0:
        if not any(p["supported"] for p in paths):
            raise HTTPException(400, "no supported fee path uses this setting")
        if key == "creator_share_bps":
            if not any(pc.treasury_for(n) for n in set(pc.CREATOR_SHARE_LAUNCHPADS.values())):
                raise HTTPException(400, "set a treasury address for Solana or an EVM chain before enabling this")
        elif not pc.treasury_for(net):
            raise HTTPException(400, f"set the {net} treasury address before enabling this fee")
    row = (await db.execute(select(PlatformSetting).where(PlatformSetting.key == key))).scalar_one_or_none()
    old = pc.bps(key)
    if row:
        row.value, row.updated_by = {"bps": body.bps}, user.id
    else:
        db.add(PlatformSetting(key=key, value={"bps": body.bps}, updated_by=user.id))
    await audit(db, user, "setting.change", key, {"bps": [old, body.bps]}, request)
    await db.commit()
    await pc.refresh(db, force=True)
    return {"key": key, "bps": pc.bps(key),
            "applies_to": "new previews from now on (other server processes pick it up within "
                          f"{int(pc.CACHE_S)} seconds); existing launches keep the terms they were created with"}


# --- users ---------------------------------------------------------------------------------------------

SORTS = {"created_at", "email", "last_login", "launches", "wallets", "trades"}


def _user_columns():
    last_login = (select(func.max(Session.created_at)).where(Session.user_id == User.id)
                  .correlate(User).scalar_subquery())
    last_activity = (select(func.max(Job.updated_at)).where(Job.user_id == User.id).correlate(User).scalar_subquery())
    launches = select(func.count(Launch.id)).where(Launch.user_id == User.id).correlate(User).scalar_subquery()
    wallets = select(func.count(Wallet.id)).where(Wallet.user_id == User.id).correlate(User).scalar_subquery()
    trades = (select(func.count(Job.id)).where(Job.user_id == User.id, Job.kind.like("trade%"))
              .correlate(User).scalar_subquery())
    automations = (select(func.count(Automation.id)).where(Automation.user_id == User.id)
                   .correlate(User).scalar_subquery())
    return {"last_login": last_login, "last_activity": last_activity, "launches": launches, "wallets": wallets,
            "trades": trades, "automations": automations}


@router.get("/users")
async def users(request: Request, q: str | None = Query(None, max_length=120), sort: str = "created_at", order: str = "desc",
                status: str | None = None, page: int = Query(1, ge=1), size: int = Query(25, ge=1, le=100),
                user: User = Depends(any_admin), db: AsyncSession = Depends(get_db)):
    full = user.admin_role == "superadmin"  # type: ignore[attr-defined]
    if sort not in SORTS or (sort == "email" and not full):
        raise HTTPException(400, f"sort by one of {', '.join(sorted(SORTS - (set() if full else {'email'})))}")
    cols = _user_columns()
    stmt = (select(User, Profile.display_name, AccountStatus.status, AccountStatus.reason, AccountStatus.changed_at,
                   AdminRole.role, ReferralLink.code, *cols.values())
            .outerjoin(Profile, Profile.user_id == User.id)
            .outerjoin(AccountStatus, AccountStatus.user_id == User.id)
            .outerjoin(AdminRole, AdminRole.user_id == User.id)
            .outerjoin(ReferralLink, ReferralLink.referred_user_id == User.id))
    if q:
        term = q.strip().lower()
        like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        if full:
            stmt = stmt.where(or_(func.lower(User.email).like(like, escape="\\"), User.id == q.strip(),
                                  func.lower(Profile.display_name).like(like, escape="\\")))
        else:
            # Viewers see masked emails, so they can't search inside them either (that would unmask them by guessing):
            # only an exact email, an account ID, or a display name.
            stmt = stmt.where(or_(User.email == term, User.id == q.strip(),
                                  func.lower(Profile.display_name).like(like, escape="\\")))
    if status == "suspended":
        stmt = stmt.where(AccountStatus.status == "suspended")
    elif status == "active":
        stmt = stmt.where(or_(AccountStatus.status.is_(None), AccountStatus.status != "suspended"))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    key = {"created_at": User.created_at, "email": User.email}.get(sort) or cols[sort]
    stmt = stmt.order_by(key.desc() if order == "desc" else key.asc(), User.id)
    rows = (await db.execute(stmt.offset((page - 1) * size).limit(size))).all()
    items = []
    for u, display, st, reason, changed, role, ref_code, last_login, last_act, n_launch, n_wal, n_trade, n_auto in rows:
        items.append({
            "id": u.id, "email": u.email if full else mask_email(u.email),
            "display_name": display or u.email.split("@")[0][:40] if full else (display or ""),
            "registered_at": iso(u.created_at), "role": role or "user", "status": st or "active",
            "last_login": iso(last_login), "last_activity": iso(last_act),
            "plan": None,  # Helm has no plans or subscriptions
            "referred_by_code": ref_code, "launches": n_launch, "trades": n_trade, "automations": n_auto,
            "wallets": n_wal, "two_factor": u.totp_enabled,
            "flags": ([{"flag": "suspended", "reason": reason, "at": iso(changed)}] if st == "suspended" else []),
        })
    if q:  # Searches are audited without storing the query text, which may be an email.
        await audit(db, user, "users.searched", changes={"results": total, "query_chars": len(q.strip())},
                    request=request)
    else:
        await audit(db, user, "users.listed", changes={"page": page, "size": size, "results": total},
                    request=request)
    await db.commit()
    return {"total": total, "page": page, "size": size, "items": items, "timezone": "UTC"}


@router.get("/users/{user_id}")
async def user_detail(user_id: str, request: Request, user: User = Depends(any_admin),
                      db: AsyncSession = Depends(get_db)):
    u = await db.get(User, user_id)
    if not u:
        raise HTTPException(404, "no such user")
    full = user.admin_role == "superadmin"  # type: ignore[attr-defined]
    await audit(db, user, "user.view", u.id, request=request)
    await db.commit()
    prof = (await db.execute(select(Profile).where(Profile.user_id == u.id))).scalar_one_or_none()
    st = (await db.execute(select(AccountStatus).where(AccountStatus.user_id == u.id))).scalar_one_or_none()
    ref = (await db.execute(select(ReferralLink).where(ReferralLink.referred_user_id == u.id))).scalar_one_or_none()
    wallets = (await db.execute(select(Wallet.network, Wallet.kind, Wallet.address, Wallet.created_at)
                                .where(Wallet.user_id == u.id).order_by(Wallet.created_at))).all()
    jobs = (await db.execute(select(Job.status, func.count()).where(Job.user_id == u.id).group_by(Job.status))).all()
    launches = (await db.execute(select(Launch).where(Launch.user_id == u.id).order_by(Launch.created_at.desc())
                                 .limit(50))).scalars().all()
    last = (await db.execute(select(func.max(Session.created_at)).where(Session.user_id == u.id))).scalar_one()
    history = (await db.execute(select(AdminAudit).where(AdminAudit.target == u.id,
                                                         AdminAudit.action.in_(("user.suspend", "user.restore",
                                                                                "role.grant", "role.revoke")))
                                .order_by(AdminAudit.created_at.desc()).limit(20))).scalars().all()
    by_net: dict[str, dict] = {}
    for net, kind, addr, created in wallets:
        by_net.setdefault(net, {"network": net, "managed": 0, "watch": 0, "connected": 0, "sample": []})
        by_net[net][kind if kind in ("managed", "watch", "connected") else "managed"] += 1
        if len(by_net[net]["sample"]) < 5:
            by_net[net]["sample"].append({"address": mask(addr), "kind": kind, "created_at": iso(created)})
    return {
        "id": u.id, "email": u.email if full else mask_email(u.email),
        "display_name": (prof.display_name if prof else "") or "", "registered_at": iso(u.created_at),
        "role": await _role_label(db, u), "status": st.status if st else "active",
        "status_reason": st.reason if st else None, "status_changed_at": iso(st.changed_at) if st else None,
        "last_login": iso(last), "two_factor": u.totp_enabled, "plan": None,
        "referred_by_code": ref.code if ref else None,
        "wallets": {"total": len(wallets), "by_network": list(by_net.values()),
                    "note": "Addresses are masked. Keys and balances aren't available to admins."},
        "jobs": {s: n for s, n in jobs},
        "launches": [{"id": l.id, "name": l.name, "symbol": l.symbol, "launchpad": l.launchpad,
                      "network": l.network, "status": l.status, "token": l.mint_address,
                      "created_at": iso(l.created_at)} for l in launches],
        "history": [{"action": h.action, "at": iso(h.created_at), "changes": h.changes} for h in history],
        "timezone": "UTC",
    }


async def _role_label(db: AsyncSession, u: User) -> str:
    r = (await db.execute(select(AdminRole.role).where(AdminRole.user_id == u.id))).scalar_one_or_none()
    return r or "user"


class AccessChange(BaseModel):
    reason: str = Field(min_length=5, max_length=300)
    confirm: StepUp


async def _target(db: AsyncSession, actor: User, user_id: str) -> User:
    u = await db.get(User, user_id)
    if not u:
        raise HTTPException(404, "no such user")
    if u.id == actor.id or await role_of(db, u) == "superadmin":
        raise HTTPException(400, "the superadmin account can't be suspended or changed here")
    return u


@router.post("/users/{user_id}/suspend")
async def suspend(user_id: str, body: AccessChange, request: Request, user: User = Depends(superadmin),
                  db: AsyncSession = Depends(get_db)):
    await step_up(db, user, body.confirm)
    u = await _target(db, user, user_id)
    st = (await db.execute(select(AccountStatus).where(AccountStatus.user_id == u.id))).scalar_one_or_none()
    old = st.status if st else "active"
    if st:
        st.status, st.reason, st.changed_by, st.changed_at = "suspended", body.reason, user.id, utcnow()
    else:
        db.add(AccountStatus(user_id=u.id, status="suspended", reason=body.reason, changed_by=user.id))
    # End every session (web and bot) now; the suspension check blocks new ones from doing anything.
    for s in (await db.execute(select(Session).where(Session.user_id == u.id, Session.revoked_at.is_(None)))).scalars():
        s.revoked_at = utcnow()
    await audit(db, user, "user.suspend", u.id, {"status": [old, "suspended"], "reason": [None, body.reason]}, request)
    await db.commit()
    return {"id": u.id, "status": "suspended"}


@router.post("/users/{user_id}/restore")
async def restore(user_id: str, body: AccessChange, request: Request, user: User = Depends(superadmin),
                  db: AsyncSession = Depends(get_db)):
    await step_up(db, user, body.confirm)
    u = await _target(db, user, user_id)
    st = (await db.execute(select(AccountStatus).where(AccountStatus.user_id == u.id))).scalar_one_or_none()
    if not st or st.status != "suspended":
        raise HTTPException(400, "this account isn't suspended")
    st.status, st.reason, st.changed_by, st.changed_at = "active", body.reason, user.id, utcnow()
    await audit(db, user, "user.restore", u.id, {"status": ["suspended", "active"], "reason": [None, body.reason]},
                request)
    await db.commit()
    return {"id": u.id, "status": "active"}


# --- admin roles ---------------------------------------------------------------------------------------


@router.get("/roles")
async def roles(user: User = Depends(any_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(AdminRole, User.email).join(User, User.id == AdminRole.user_id)
                             .order_by(AdminRole.created_at))).all()
    full = user.admin_role == "superadmin"  # type: ignore[attr-defined]
    return {"items": [{"user_id": r.user_id, "email": e if full else mask_email(e), "role": r.role,
                       "granted_at": iso(r.created_at)} for r, e in rows],
            "roles": {"superadmin": "Everything, including changes. Only the designated account; can't be granted.",
                      "viewer": "Read-only dashboard: revenue, treasury, settings, users (emails masked), audit log."}}


class RoleChange(BaseModel):
    reason: str = Field(min_length=5, max_length=300)
    confirm: StepUp


@router.post("/roles/{user_id}/viewer")
async def grant_viewer(user_id: str, body: RoleChange, request: Request, user: User = Depends(superadmin),
                       db: AsyncSession = Depends(get_db)):
    await step_up(db, user, body.confirm)
    u = await _target(db, user, user_id)
    if (await db.execute(select(AdminRole).where(AdminRole.user_id == u.id))).scalar_one_or_none():
        raise HTTPException(409, "this account already has an admin role")
    db.add(AdminRole(user_id=u.id, role="viewer", granted_by=user.id))
    await audit(db, user, "role.grant", u.id, {"role": [None, "viewer"], "reason": [None, body.reason]}, request)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "this account already has an admin role")
    return {"user_id": u.id, "role": "viewer"}


@router.post("/roles/{user_id}/revoke")
async def revoke_role(user_id: str, body: RoleChange, request: Request, user: User = Depends(superadmin),
                      db: AsyncSession = Depends(get_db)):
    await step_up(db, user, body.confirm)
    u = await _target(db, user, user_id)
    row = (await db.execute(select(AdminRole).where(AdminRole.user_id == u.id))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "this account has no admin role")
    await db.delete(row)
    await audit(db, user, "role.revoke", u.id, {"role": [row.role, None], "reason": [None, body.reason]}, request)
    await db.commit()
    return {"user_id": u.id, "role": None}


# --- audit log and metrics ----------------------------------------------------------------------------


@router.get("/audit")
async def audit_log(action: str | None = None, page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
                    user: User = Depends(any_admin), db: AsyncSession = Depends(get_db)):
    q = select(AdminAudit)
    if action:
        q = q.where(AdminAudit.action.like(f"{action}%"))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(AdminAudit.created_at.desc()).offset((page - 1) * size).limit(size))).scalars()
    full = user.admin_role == "superadmin"  # type: ignore[attr-defined]
    return {"total": total, "items": [{"id": r.id, "at": iso(r.created_at), "actor": r.actor_user_id,
                                       "action": r.action, "target": r.target, "changes": r.changes,
                                       "context": r.context if full else {}} for r in rows]}


@router.get("/metrics")
async def metrics(days: int = Query(30, ge=1, le=365), user: User = Depends(any_admin),
                  db: AsyncSession = Depends(get_db)):
    since = utcnow() - timedelta(days=days)
    day = func.date(User.created_at)
    regs = (await db.execute(select(day, func.count()).where(User.created_at >= since).group_by(day)
                             .order_by(day))).all()
    active = (await db.execute(select(func.count(func.distinct(Session.user_id)))
                               .where(Session.created_at >= since))).scalar_one()
    job_status = (await db.execute(select(Job.status, func.count()).where(
        Job.created_at >= since, or_(Job.kind.like("trade%"), Job.kind == "deploy")).group_by(Job.status))).all()
    launches = (await db.execute(select(Launch.status, func.count()).where(Launch.created_at >= since)
                                 .group_by(Launch.status))).all()
    js = {s: n for s, n in job_status}
    settled = js.get("confirmed", 0) + js.get("failed", 0) + js.get("partial", 0)
    return {
        "window_days": days, "timezone": "UTC",
        "users_total": (await db.execute(select(func.count(User.id)))).scalar_one(),
        "registrations_by_day": [{"day": str(d), "count": n} for d, n in regs],
        "active_users": {"count": active, "definition": "accounts that signed in (web or Telegram) in the window"},
        "launches_by_status": {s: n for s, n in launches},
        "trade_and_deploy_jobs": {**js, "success_rate": round(js.get("confirmed", 0) / settled, 4) if settled else None,
                                  "definition": "jobs created in the window; success rate = confirmed / (confirmed + "
                                                "failed + partial), excluding pending and unconfirmed"},
    }
