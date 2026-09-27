"""Market view for a launch: indicative third-party data (DEX Screener pools, GeckoTerminal candles).

Display only. These numbers never feed a trade preview, an execution quote or a confirmation.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import current_user
from ..models import User
from ..services.market import TIMEFRAMES, Market, MarketError, get_market
from ..models import Launch
from .launches import _cloned_from, _owned_launch

router = APIRouter()
NOTE = "Indicative third-party market data. Not used for Helm's previews, quotes or execution."


def _explorer(network: str, token: str) -> str:
    urls = {"solana": "https://solscan.io/token/", "base": "https://basescan.org/token/",
            "bsc": "https://bscscan.com/token/", "ethereum": "https://etherscan.io/token/",
            "robinhood": "https://robinhoodchain.blockscout.com/token/"}
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
    base = {"note": NOTE, "network": network, "token": token, "whose": whose,
            "explorer_url": _explorer(network, token) if token else None}
    if not token:
        return {**base, "state": "no_token", "pairs": []}
    try:
        res = await market.pairs(network, token)
    except MarketError as e:
        return {**base, "state": "error", "error_kind": e.kind, "error": str(e), "pairs": [], "source": "DEX Screener"}
    if not res["supported"]:
        return {**base, "state": "unsupported", "pairs": [], "source": res["source"]}
    return {**base, "state": "ok" if res["pairs"] else "no_pool", **res}


@router.get("/launches/{launch_id}/market/candles")
async def launch_candles(launch_id: str, pool: str, tf: str = "15m", user: User = Depends(current_user),
                         db: AsyncSession = Depends(get_db), market: Market = Depends(get_market)):
    if tf not in TIMEFRAMES:
        raise HTTPException(400, f"tf must be one of {list(TIMEFRAMES)}")
    l = await _owned_launch(db, user, launch_id)
    network, token, _ = _target(l)
    if not token:
        raise HTTPException(404, "this launch has no token yet")
    # Only pools that actually trade this token are charted: the pool must come from the token's own list.
    try:
        pools = await market.pairs(network, token)
    except MarketError as e:
        return {"state": "error", "error_kind": e.kind, "error": str(e), "candles": [], "note": NOTE}
    match = next((p for p in pools.get("pairs", []) if p["pair_address"] == pool), None)
    if not match:
        raise HTTPException(404, "that pool doesn't trade this launch's token")
    try:
        res = await market.candles(network, pool, tf, match["token_side"])
    except MarketError as e:
        return {"state": "error", "error_kind": e.kind, "error": str(e), "candles": [], "source": "GeckoTerminal",
                "note": NOTE}
    if not res["supported"]:
        return {"state": "unsupported", "candles": [], "source": res["source"], "note": NOTE}
    return {"state": "ok" if res["candles"] else "no_candles", "note": NOTE, **res}
