"""Market view for a launch: indicative Codex data and Codex candles.

Display only. These numbers never feed a trade preview, an execution quote or a confirmation.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import current_user
from ..models import Launch, User
from ..services.market import CODEX_NETWORK, RESOLUTIONS, Market, MarketError, get_market, valid_address
from .launches import _cloned_from, _owned_launch

router = APIRouter()
NOTE = "Indicative market data from Codex. Not used for Helm's previews, quotes or execution."


def _explorer(network: str, token: str) -> str:
    urls = {"solana": "https://solscan.io/token/", "base": "https://basescan.org/token/",
            "bsc": "https://bscscan.com/token/", "ethereum": "https://etherscan.io/token/",
            "robinhood": "https://robinhoodchain.blockscout.com/token/", "arc": "https://explorer.arc.io/token/",
            "tron": "https://tronscan.org/#/token20/", "near": "https://nearblocks.io/token/", "ton": "https://tonviewer.com/"}
    return urls[network] + token if network in urls else ""


def _target(l: Launch) -> tuple[str, str | None, str]:
    """(network, token, whose): the launch's own token once it exists; before that, for a copied draft, the token it
    was copied from, so the terminal can show that token's market while the relaunch is prepared."""
    if l.mint_address:
        return l.network, l.mint_address, "launch"
    src = _cloned_from(l)
    if src and l.status == "draft":
        return src["network"], src["address"], "source"
    return l.network, None, "launch"


@router.get("/launches/{launch_id}/market")
async def launch_market(launch_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                        market: Market = Depends(get_market)):
    l = await _owned_launch(db, user, launch_id)
    network, token, whose = _target(l)
    base = {"note": NOTE, "network": network, "token": token, "whose": whose, "provider": "Codex",
            "explorer_url": _explorer(network, token) if token else None,
            "chart": {"available": bool(token) and network in CODEX_NETWORK and valid_address(network, token or ""),
                      "resolutions": list(RESOLUTIONS)}}
    if not token:
        return {**base, "state": "no_token", "pool": None, "pairs": [], "metrics": None}
    try:
        res = await market.snapshot(network, token)
    except MarketError as e:
        return {**base, "state": "error", **e.details(), "pool": None, "pairs": [], "metrics": None}
    return {**base, **res}


@router.get("/launches/{launch_id}/market/candles")
async def launch_candles(launch_id: str, resolution: str = Query("15", max_length=4),
                         pool: str | None = Query(None, max_length=64), user: User = Depends(current_user),
                         db: AsyncSession = Depends(get_db), market: Market = Depends(get_market)):
    """OHLCV candles from Codex for the launch's token (all pools) or one of its pools. Never synthesized."""
    l = await _owned_launch(db, user, launch_id)
    network, token, _ = _target(l)
    base = {"provider": "Codex", "network": network, "token": token, "resolution": resolution, "bars": []}
    if not token:
        return {**base, "state": "no_token"}
    quote_side = None
    if pool:
        try:
            snap = await market.snapshot(network, token)
        except MarketError as e:
            return {**base, "state": "error", **e.details()}
        listed = {p["address"]: p for p in snap.get("pairs") or []}
        if pool not in listed:  # only pools Codex lists for this token; never an arbitrary address
            return {**base, "state": "error", "error_kind": "unknown_pool", "error": "That pool is not listed for this token."}
        quote_side = listed[pool].get("quote_side")
    try:
        res = await market.candles(network, token, resolution, pool, quote_side)
    except MarketError as e:
        return {**base, "state": "error", **e.details()}
    return {**base, **res}
