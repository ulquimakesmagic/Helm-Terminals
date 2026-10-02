"""Profile: how a user presents themselves, plus a summary of their own track record.

`GET /profile` is private (the signed-in user's own account, stats and activity).
`GET /u/{handle}` is a public creator page, and exists only when the user turns it on. It lists the tokens they
deployed through Helm (these are public on-chain anyway) and never shows wallets, balances, P&L or email.
"""

import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.evm import EVM_CHAINS
from ..chains.solana import LAMPORTS_PER_SOL
from ..db import get_db
from ..deps import current_user, log_activity, web_user
from ..models import (
    Activity, FeeRecord, Job, Launch, Profile, ReferralLink, Session, TelegramLink, Transaction, User, Wallet,
    Watch, utcnow,
)
from ..services.portfolio import coin_prices
from .misc import referral_summary

router = APIRouter(tags=["profile"])

HEATMAP_DAYS = 182  # 26 weeks
HANDLE_RE = re.compile(r"^[a-z0-9_]{3,20}$")
X_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
TG_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")
RESERVED = {
    "admin", "administrator", "api", "helm", "helmterminals", "helm_terminals", "support", "help", "login",
    "logout", "settings", "profile", "me", "root", "official", "team", "staff", "mod", "moderator", "system",
    "u", "www", "null", "undefined",
}


async def get_profile(db: AsyncSession, user: User) -> Profile:
    p = (await db.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one_or_none()
    if p is None:
        p = Profile(user_id=user.id, display_name=user.email.split("@")[0][:40])
        db.add(p)
        await db.flush()
    return p


def _profile_out(p: Profile) -> dict:
    return {"display_name": p.display_name, "handle": p.handle, "bio": p.bio, "x_handle": p.x_handle,
            "telegram_handle": p.telegram_handle, "website": p.website, "avatar_hue": p.avatar_hue,
            "is_public": p.is_public, "public_path": f"/u/{p.handle}" if p.is_public and p.handle else None}


def _launch_out(l: Launch) -> dict:
    quote = (l.launchpad_params or {}).get("quote") or {}
    return {"id": l.id, "name": l.name, "symbol": l.symbol, "network": l.network, "launchpad": l.launchpad,
            "status": l.status, "mint_address": l.mint_address, "quote": quote.get("symbol"),
            "created_at": l.created_at.isoformat(),
            "deployed_at": l.deployed_at.isoformat() if l.deployed_at else None}


def _launchpad_breakdown(launches: list[Launch]) -> list[dict]:
    pads: dict[tuple[str, str], dict] = {}
    for l in launches:
        row = pads.setdefault((l.network, l.launchpad), {"network": l.network, "launchpad": l.launchpad,
                                                          "count": 0, "live": 0})
        row["count"] += 1
        row["live"] += l.status == "live"
    return sorted(pads.values(), key=lambda r: -r["count"])


@router.get("/profile")
async def profile(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    p = await get_profile(db, user)
    await db.commit()
    now = utcnow()

    launches = (await db.execute(select(Launch).where(Launch.user_id == user.id)
                                 .order_by(Launch.created_at.desc()))).scalars().all()
    own = [l for l in launches if not l.imported]
    count = lambda st: sum(1 for l in own if l.status == st)  # noqa: E731

    trades = (await db.execute(
        select(Transaction.kind, Transaction.sol_delta_lamports)
        .join(Job, Job.id == Transaction.job_id)
        .where(Job.user_id == user.id, Transaction.status == "confirmed", Transaction.kind.in_(("buy", "sell")))
    )).all()
    # Flows per chain: deltas are recorded per transaction and joined to the wallet's network, so the
    # summary becomes all-chain as soon as a chain records native deltas (EVM deltas are the known gap).
    flow_rows = (await db.execute(
        select(Wallet.network, func.coalesce(func.sum(Transaction.sol_delta_lamports), 0))
        .join(Job, Job.id == Transaction.job_id)
        .join(Wallet, Wallet.id == Transaction.wallet_id)
        .where(Job.user_id == user.id, Transaction.status == "confirmed", Transaction.sol_delta_lamports.is_not(None))
        .group_by(Wallet.network)
    )).all()
    fees = (await db.execute(
        select(func.coalesce(func.sum(FeeRecord.platform_lamports + FeeRecord.referral_lamports), 0))
        .where(FeeRecord.payer_user_id == user.id, FeeRecord.status == "confirmed")
    )).scalar_one()

    wallets = (await db.execute(select(Wallet.kind, Wallet.network).where(Wallet.user_id == user.id))).all()
    tracked = (await db.execute(select(func.count(Watch.id)).where(Watch.user_id == user.id))).scalar_one()
    sessions = (await db.execute(select(func.count(Session.id)).where(
        Session.user_id == user.id, Session.revoked_at.is_(None), Session.expires_at > now))).scalar_one()
    last_login = (await db.execute(select(func.max(Session.created_at)).where(
        Session.user_id == user.id, Session.channel == "web"))).scalar_one()
    tg = (await db.execute(select(TelegramLink.id).where(TelegramLink.user_id == user.id))).scalar_one_or_none()
    referred = (await db.execute(select(ReferralLink.id).where(
        ReferralLink.referred_user_id == user.id))).scalar_one_or_none()

    # Activity heatmap: one count per day for the last 26 weeks (oldest first).
    start = (now - timedelta(days=HEATMAP_DAYS - 1)).date()
    stamps = (await db.execute(select(Activity.created_at).where(
        Activity.user_id == user.id, Activity.created_at >= now - timedelta(days=HEATMAP_DAYS)))).scalars().all()
    days = [0] * HEATMAP_DAYS
    for ts in stamps:
        i = (ts.date() - start).days
        if 0 <= i < HEATMAP_DAYS:
            days[i] += 1
    streak = 0
    for n in reversed(days):
        if not n:
            break
        streak += 1

    networks: dict[str, dict] = {}
    for l in own:
        networks.setdefault(l.network, {"network": l.network, "launches": 0, "wallets": 0})["launches"] += 1
    for kind, net in wallets:
        if kind == "managed":
            networks.setdefault(net, {"network": net, "launches": 0, "wallets": 0})["wallets"] += 1

    referral = await referral_summary(db, user)

    # Net flow valued in USD: each chain's measured native delta at its own coin's price. A chain
    # without a price right now is left out of the sum and reported, never valued at another coin's price.
    prices = await coin_prices()
    flows = []
    for net, delta in flow_rows:
        if not delta:
            continue
        symbol = EVM_CHAINS[net]["native"] if net in EVM_CHAINS else "SOL"
        amount = int(delta) / LAMPORTS_PER_SOL
        price = prices.get(symbol)
        flows.append({"network": net, "symbol": symbol, "amount": amount,
                      "usd": amount * price if price else None})
    priced = bool(flows) and all(f["usd"] is not None for f in flows)
    net_flow_usd = sum(f["usd"] for f in flows if f["usd"] is not None)

    live_networks = {l.network for l in own if l.deployed_at}
    milestones = [
        {"id": "secured", "label": "Account secured", "hint": "Two-factor authentication on", "done": user.totp_enabled},
        {"id": "telegram", "label": "Terminal linked", "hint": "Telegram bot connected", "done": tg is not None},
        {"id": "first_launch", "label": "First launch", "hint": "Deployed a token", "done": any(l.deployed_at for l in own)},
        {"id": "multichain", "label": "Multichain", "hint": "Launched on 2 or more chains", "done": len(live_networks) >= 2},
        {"id": "referrer", "label": "Brought a friend", "hint": "Someone joined with your link", "done": referral["referred_users"] > 0},
        {"id": "public", "label": "Went public", "hint": "Public creator page on", "done": p.is_public and bool(p.handle)},
    ]

    return {
        "profile": _profile_out(p),
        "account": {
            "email": user.email, "created_at": user.created_at.isoformat(), "totp_enabled": user.totp_enabled,
            "telegram_linked": tg is not None, "active_sessions": sessions, "referred": referred is not None,
            "last_sign_in": last_login.isoformat() if last_login else None,
        },
        "stats": {
            "launches": {"total": len(own), "live": count("live"), "failed": count("failed"), "draft": count("draft"),
                         "archived": count("archived"), "imported": len(launches) - len(own)},
            "launchpads": _launchpad_breakdown(own),
            "networks": sorted(networks.values(), key=lambda r: -(r["launches"] + r["wallets"])),
            "trades": {"count": len(trades), "buys": sum(1 for k, _ in trades if k == "buy"),
                       "sells": sum(1 for k, _ in trades if k == "sell"),
                       "volume_sol": sum(abs(d or 0) for _, d in trades) / LAMPORTS_PER_SOL},
            "net_sol_flow": sum(int(d) for _, d in flow_rows) / LAMPORTS_PER_SOL,
            "net_flow": {"usd": net_flow_usd if flows else None, "priced": priced,
                         "flows": flows,
                         "note": "Measured coin in and out of your wallets, valued in USD at each chain's coin price. "
                                 "Chains without recorded flows aren't included."},
            "fees_paid_sol": int(fees) / LAMPORTS_PER_SOL,
            "wallets": {"managed": sum(1 for k, _ in wallets if k == "managed"),
                        "watch": sum(1 for k, _ in wallets if k == "watch")},
            "tracked": tracked,
        },
        "heatmap": {"start": start.isoformat(), "days": days, "total": sum(days), "active_days": sum(1 for d in days if d),
                    "streak": streak},
        "milestones": milestones,
        "recent_launches": [_launch_out(l) for l in own[:6]],
        "referral": referral,
    }


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=40)
    handle: str | None = Field(default=None, max_length=24)
    bio: str | None = Field(default=None, max_length=280)
    x_handle: str | None = Field(default=None, max_length=20)
    telegram_handle: str | None = Field(default=None, max_length=36)
    website: str | None = Field(default=None, max_length=120)
    avatar_hue: int | None = Field(default=None, ge=0, le=359)
    is_public: bool | None = None


@router.patch("/profile")
async def update_profile(body: ProfileUpdate, user: User = Depends(web_user), db: AsyncSession = Depends(get_db)):
    """Web app only, like other account settings."""
    p = await get_profile(db, user)
    changed: list[str] = []

    if body.display_name is not None:
        name = " ".join(body.display_name.split())
        if not name:
            raise HTTPException(400, "display name can't be empty")
        p.display_name = name
        changed.append("display_name")
    if body.handle is not None:
        h = body.handle.strip().lstrip("@").lower()
        if h == "":
            p.handle, p.is_public = None, False
        else:
            if not HANDLE_RE.match(h):
                raise HTTPException(400, "handle must be 3–20 characters: lowercase letters, numbers and _")
            if h in RESERVED:
                raise HTTPException(400, "that handle is reserved")
            taken = (await db.execute(select(Profile.id).where(Profile.handle == h, Profile.user_id != user.id))).first()
            if taken:
                raise HTTPException(409, "that handle is taken")
            p.handle = h
        changed.append("handle")
    if body.bio is not None:
        p.bio = body.bio.strip()
        changed.append("bio")
    if body.x_handle is not None:
        x = body.x_handle.strip().lstrip("@")
        if x and not X_RE.match(x):
            raise HTTPException(400, "X handle must be up to 15 letters, numbers or _")
        p.x_handle = x
        changed.append("x_handle")
    if body.telegram_handle is not None:
        t = body.telegram_handle.strip().lstrip("@")
        if t and not TG_RE.match(t):
            raise HTTPException(400, "Telegram username must be 5–32 letters, numbers or _")
        p.telegram_handle = t
        changed.append("telegram_handle")
    if body.website is not None:
        w = body.website.strip()
        if w and not re.match(r"^https://[^\s/$.?#][^\s]*$", w):
            raise HTTPException(400, "website must be a full https:// link")
        p.website = w
        changed.append("website")
    if body.avatar_hue is not None:
        p.avatar_hue = body.avatar_hue
        changed.append("avatar")
    if body.is_public is not None:
        if body.is_public and not p.handle:
            raise HTTPException(400, "pick a handle before making your profile public")
        p.is_public = body.is_public
        changed.append("is_public")

    if changed:
        await log_activity(db, user.id, "profile.updated", payload={"fields": changed,
                                                                    **({"public": p.is_public} if "is_public" in changed else {})})
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "that handle is taken")
    return _profile_out(p)


@router.get("/u/{handle}")
async def public_profile(handle: str, db: AsyncSession = Depends(get_db)):
    """Public creator page. No auth. Only for profiles that opted in; never shows wallets, balances or email."""
    p = (await db.execute(select(Profile).where(Profile.handle == handle.strip().lower()))).scalar_one_or_none()
    if not p or not p.is_public:
        raise HTTPException(404, "profile not found")
    user = await db.get(User, p.user_id)
    launches = (await db.execute(
        select(Launch).where(Launch.user_id == p.user_id, Launch.imported.is_(False), Launch.mint_address.is_not(None),
                             Launch.deployed_at.is_not(None))
        .order_by(Launch.deployed_at.desc()).limit(100)
    )).scalars().all()
    return {
        "display_name": p.display_name, "handle": p.handle, "bio": p.bio, "x_handle": p.x_handle,
        "telegram_handle": p.telegram_handle, "website": p.website, "avatar_hue": p.avatar_hue,
        "joined": user.created_at.strftime("%Y-%m") if user else None,
        "stats": {"deployed": len(launches), "live": sum(1 for l in launches if l.status == "live"),
                  "networks": sorted({l.network for l in launches}), "launchpads": _launchpad_breakdown(launches)},
        "launches": [{k: v for k, v in _launch_out(l).items() if k not in ("id", "created_at")} for l in launches],
    }
