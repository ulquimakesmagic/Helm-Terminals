"""Free launch policy overrides historical dollar fees, even when passed directly to retired helpers."""
import pytest
from app.config import get_settings
from app.services import launch_fee

@pytest.mark.parametrize("network", ["solana", "ethereum", "base", "bsc", "robinhood", "tron", "arc", "near"])
async def test_old_launch_quotes_are_disabled_without_price_or_treasury(client, monkeypatch, network):
    monkeypatch.setattr(get_settings(), "launch_fee_usd", 100)
    async def forbidden(*args, **kwargs):
        raise AssertionError("A free launch must not require a price feed")
    monkeypatch.setattr("app.services.portfolio.coin_prices", forbidden)
    assert await launch_fee.quote(None, network) == (None, None)
    assert launch_fee.headroom(None) == 0
    assert launch_fee.issue_approval("user", "launch", None) is None

async def test_old_quoted_launch_fee_cannot_be_collected(client, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Launches must never sign a platform payment")
    monkeypatch.setattr(launch_fee, "send_native", forbidden)
    historical = launch_fee.LaunchFee(20, 20_000_000, "SOL", 9, "unused", "solana")
    assert await launch_fee.charge(None, None, None, None, None, historical) is None
