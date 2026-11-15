"""Helm's flat launch fee: priced in dollars, paid in the chain's coin after the token is created."""

from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from sqlalchemy import select

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import RevenueEvent

from .conftest import SOL, signup
from .test_api import confirm, make_wallets


def sol_transfers(raw: bytes) -> list[tuple[str, int]]:
    tx = VersionedTransaction.from_bytes(raw)
    keys = [str(k) for k in tx.message.account_keys]
    out = []
    for ix in tx.message.instructions:
        if keys[ix.program_id_index] == "11111111111111111111111111111111" and ix.data[:4] == bytes([2, 0, 0, 0]):
            out.append((keys[ix.accounts[1]], int.from_bytes(ix.data[4:12], "little")))
    return out


def fee_on(monkeypatch, usd=10.0, sol_price: float | None = 200.0):
    s = get_settings()
    treasury = str(Keypair().pubkey())
    monkeypatch.setattr(s, "launch_fee_usd", usd)
    monkeypatch.setattr(s, "platform_fee_wallet", treasury)

    async def prices(market=None):
        return {"SOL": sol_price} if sol_price else {}
    monkeypatch.setattr("app.services.portfolio.coin_prices", prices)
    return treasury


async def new_launch(client, h, dev_buy=0.5):
    dev = (await make_wallets(client, h, 1))[0]
    r = await client.post("/launches", json={"name": "Fee", "symbol": "FEE", "dev_wallet_id": dev["id"],
                                             "dev_buy_sol": dev_buy}, headers=h)
    return r.json()["id"], dev


async def test_launch_fee_is_shown_in_dollars_and_paid_after_the_token_is_created(client, fake_rpc, monkeypatch):
    treasury = fee_on(monkeypatch)
    h = await signup(client)
    lid, dev = await new_launch(client, h)
    pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
    p = pf["preflight"]
    assert p["platform_fee"] == "$10, paid in SOL at the live price after the token is created"
    assert p["helm_fees"].startswith("$10 launch fee, paid in SOL")
    assert p["launch_fee_estimate"] == "about 0.05 SOL right now" and not p["warnings"]
    pf, r = await confirm(client, h, f"/launches/{lid}/deploy", {})
    assert r.status_code == 200, r.text
    launch = (await client.get(f"/launches/{lid}", headers=h)).json()
    assert launch["status"] == "live"
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    kinds = [t["kind"] for t in job["transactions"]]
    assert kinds[-1] == "launch_fee" and "deploy" in kinds
    assert sol_transfers(fake_rpc.sent[-1]) == [(treasury, 50_000_000)]  # $10 / $200 = 0.05 SOL
    async with get_sessionmaker()() as db:
        ev = (await db.execute(select(RevenueEvent).where(RevenueEvent.source == "launch_fee"))).scalar_one()
    assert ev.status == "received" and int(ev.amount_raw) == 50_000_000 and ev.destination == treasury
    assert ev.launch_id == lid and ev.asset == "SOL"


async def test_a_small_price_move_is_charged_but_never_more_than_the_signed_ceiling(client, fake_rpc, monkeypatch):
    """Review finding: the coin amount must be bounded by what the preview showed. The preview signs a ceiling (the
    estimate +10%, two significant figures); a small move is charged at the live price, a big one needs a new review,
    and an implausible price is refused outright."""
    treasury = fee_on(monkeypatch, sol_price=200.0)
    h = await signup(client)
    lid, _ = await new_launch(client, h)
    pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
    assert pf["preflight"]["launch_fee_max"] == "at most 0.055 SOL" and pf.get("quote_approval")

    def confirm_body(key):
        return {"confirm": True, "preflight_hash": pf["preflight_hash"], "quote_approval": pf["quote_approval"],
                "idempotency_key": key}

    fee_on(monkeypatch, sol_price=100.0)  # halved: 0.1 SOL, far above the approved 0.055
    monkeypatch.setattr(get_settings(), "platform_fee_wallet", treasury)
    r = await client.post(f"/launches/{lid}/deploy", json=confirm_body("fee-jump"), headers=h)
    assert r.status_code == 400 and "moved more than 10%" in r.json()["detail"] and fake_rpc.sent == []
    no_approval = await client.post(f"/launches/{lid}/deploy", json={**confirm_body("fee-none"), "quote_approval": None},
                                    headers=h)
    assert no_approval.status_code == 409

    fee_on(monkeypatch, sol_price=190.0)  # a 5% dip: 0.0526 SOL, within the ceiling
    monkeypatch.setattr(get_settings(), "platform_fee_wallet", treasury)
    r = await client.post(f"/launches/{lid}/deploy", json=confirm_body("fee-small"), headers=h)
    assert r.status_code == 200, r.text
    assert sol_transfers(fake_rpc.sent[-1]) == [(treasury, 52_631_579)]  # $10 / $190, rounded up

    lid2, _ = await new_launch(client, h)
    fee_on(monkeypatch, sol_price=0.2)  # a broken feed, not a market move
    pf = (await client.post(f"/launches/{lid2}/deploy", json={}, headers=h)).json()
    assert any("looks wrong" in w for w in pf["preflight"]["warnings"]) and not pf.get("quote_approval")


async def test_a_fee_error_never_strands_the_launch(client, fake_rpc, monkeypatch):
    """Review finding: an exception while sending the fee left the launch 'deploying'. It must stay live, with the
    fee recorded as failed."""
    fee_on(monkeypatch)
    h = await signup(client)
    lid, _ = await new_launch(client, h)

    async def boom(*a, **k):
        raise RuntimeError("rpc down")
    monkeypatch.setattr("app.services.launch_fee._charge", boom)
    pf, r = await confirm(client, h, f"/launches/{lid}/deploy", {})
    assert r.status_code == 200, r.text
    assert (await client.get(f"/launches/{lid}", headers=h)).json()["status"] == "live"
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "confirmed", job
    async with get_sessionmaker()() as db:
        ev = (await db.execute(select(RevenueEvent).where(RevenueEvent.source == "launch_fee"))).scalar_one()
    assert ev.status == "failed" and "RuntimeError" in ev.note


async def test_no_price_no_launch_and_the_fee_counts_in_the_balance_check(client, fake_rpc, monkeypatch):
    fee_on(monkeypatch, sol_price=None)
    h = await signup(client)
    lid, dev = await new_launch(client, h)
    pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
    assert any("launch fee can't be sized" in w for w in pf["preflight"]["warnings"])

    fee_on(monkeypatch, sol_price=200.0)
    fake_rpc.balances[dev["address"]] = int(0.55 * SOL)  # enough for the dev buy and rent, not the $10 fee too
    pf = (await client.post(f"/launches/{lid}/deploy", json={"auto_fund": False}, headers=h)).json()
    assert any("and the launch fee" in w for w in pf["preflight"]["warnings"]), pf["preflight"]["warnings"]


async def test_no_fee_when_turned_off_or_no_treasury(client, fake_rpc, monkeypatch):
    fee_on(monkeypatch, usd=0)
    h = await signup(client)
    lid, _ = await new_launch(client, h)
    pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
    assert pf["preflight"]["platform_fee"] == "none on deploy" and "launch_fee_estimate" not in pf["preflight"]
    fee_on(monkeypatch)
    monkeypatch.setattr(get_settings(), "platform_fee_wallet", "")
    pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
    assert pf["preflight"]["platform_fee"] == "none on deploy"


async def test_evm_launch_fee_goes_to_the_evm_treasury_after_the_token_exists(client, fake_rpc, monkeypatch):
    import base64

    import httpx

    from app.chains import registry
    from app.chains.flap import FlapAdapter
    from app.deps import get_executor
    from app.main import app
    from app.services.executor import Executor
    from app.signing import get_signer

    from .test_evm_wallets import ETH
    from .test_flap import PNG, FlapNode, upload_transport

    s = get_settings()
    treasury = "0x" + "77" * 20
    monkeypatch.setattr(s, "launch_fee_usd", 10.0)
    monkeypatch.setattr(s, "platform_fee_wallet_evm", treasury)

    async def prices(market=None):
        return {"BNB": 500.0}
    monkeypatch.setattr("app.services.portfolio.coin_prices", prices)
    node = FlapNode("bsc")
    registry._cache["flap-sh"] = FlapAdapter("flap-sh", "bsc", node, client=httpx.AsyncClient(transport=upload_transport([])))
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"bsc": node})
    try:
        h = await signup(client)
        dev = (await client.post("/wallets", json={"network": "bsc", "count": 1}, headers=h)).json()[0]
        node.balances[dev["address"].lower()] = ETH
        r = await client.post("/launches", json={"launchpad": "flap-sh", "name": "Fee Cat", "symbol": "fcat",
                                                 "dev_wallet_id": dev["id"], "dev_buy_sol": 0.01,
                                                 "image_b64": base64.b64encode(PNG).decode()}, headers=h)
        lid = r.json()["id"]
        pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
        assert pf["preflight"]["platform_fee"] == "$10, paid in BNB at the live price after the token is created"
        assert not pf["preflight"]["warnings"], pf["preflight"]["warnings"]
        r = await client.post(f"/launches/{lid}/deploy", json={"confirm": True, "preflight_hash": pf["preflight_hash"],
                                                              "quote_approval": pf["quote_approval"],
                                                              "idempotency_key": "evm-fee"}, headers=h)
        job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
        assert job["status"] == "confirmed", job
        assert [t["kind"] for t in job["transactions"]][-1] == "launch_fee"
        sent = node.sent[-1]
        assert sent["to"].lower() == treasury and sent["value"] == 2 * 10**16  # $10 / $500 = 0.02 BNB
    finally:
        app.dependency_overrides.pop(get_executor, None)
        registry._cache.pop("flap-sh", None)
