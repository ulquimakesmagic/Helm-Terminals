"""Market view for a launch: indicative GMGN data and hosted-chart capabilities.

Display only. These numbers never feed a trade preview, an execution quote or a confirmation.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import current_user
from ..models import User
from ..services.market import CHART_CHAIN, Market, MarketError, get_market, valid_address
from ..models import Launch
from .launches import _cloned_from, _owned_launch

router = APIRouter()
NOTE = "Indicative third-party market data. Not used for Helm's previews, quotes or execution."


def _explorer(network: str, token: str) -> str:
    urls = {"solana": "https://solscan.io/token/", "base": "https://basescan.org/token/",
            "bsc": "https://bscscan.com/token/", "ethereum": "https://etherscan.io/token/",
            "robinhood": "https://robinhoodchain.blockscout.com/token/", "arc": "https://explorer.arc.io/token/",
            "tron": "https://tronscan.org/token20/", "near": "https://nearblocks.io/token/", "ton": "https://tonviewer.com/"}
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
    base = {"note": NOTE, "network": network, "token": token, "whose": whose, "source": "GMGN",
            "chart_chain": CHART_CHAIN.get(network) if token and valid_address(network, token) else None,
            "explorer_url": _explorer(network, token) if token else None}
    if not token:
        return {**base, "state": "no_token", "pool": None, "metrics": None}
    try:
        res = await market.snapshot(network, token)
    except MarketError as e:
        return {**base, "state": "error", **e.details(), "pool": None, "metrics": None}
    return {**base, **res}


@router.get("/launches/{launch_id}/market/candles")
async def launch_candles(launch_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    # Explicit retirement for old clients: never silently use another chart provider.
    await _owned_launch(db, user, launch_id)
    raise HTTPException(410, "Helm now displays GMGN's hosted chart. Reload the terminal to update.")
