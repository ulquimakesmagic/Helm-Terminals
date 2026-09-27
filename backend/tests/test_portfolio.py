from app.deps import get_executor
from app.main import app
from app.services import portfolio as pf_mod
from app.services.executor import Executor
from app.signing import get_signer

from .conftest import signup
from .test_bridge import TokenNode
from .test_evm_wallets import ETH


async def test_portfolio_sums_coins_and_stables_on_every_chain(client, fake_rpc, monkeypatch):
    async def prices(client=None):
        return {"SOL": 150.0, "ETH": 3000.0, "BNB": 600.0}

    monkeypatch.setattr(pf_mod, "coin_prices", prices)
    base = TokenNode("base")
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"base": base})
    try:
        h = await signup(client)
        sol = (await client.post("/wallets", json={"count": 2}, headers=h)).json()
        b = (await client.post("/wallets", json={"network": "base", "count": 1}, headers=h)).json()[0]
        fake_rpc.tokens[sol[0]["address"]] = 25 * 10**6  # 25 USDC (FakeRPC mints have 6 decimals)
        base.balances[b["address"].lower()] = ETH // 10
        base.tokens[b["address"].lower()] = 100 * 10**6
        p = (await client.get("/portfolio?fresh=true", headers=h)).json()
        chains = {c["network"]: c for c in p["chains"]}
        assert chains["solana"]["native"] == 10 and chains["solana"]["stable"] == 25 and chains["solana"]["usd"] == 1525
        assert chains["base"]["stable"] == 100 and abs(chains["base"]["native_usd"] - 300) < 1e-6
        assert chains["robinhood"]["stable_symbol"] == "USDG" and chains["robinhood"]["wallets"] == 0
        assert abs(p["totals"]["usd"] - 1925) < 1e-6 and p["totals"]["stable_usd"] == 125

        d = (await client.get("/dashboard", headers=h)).json()
        assert "by_chain" in d

        # Each wallet also reports its stablecoin and its value in dollars.
        ws = {w["address"]: w for w in (await client.get("/wallets?with_balances=true", headers=h)).json()}
        w0 = ws[sol[0]["address"]]
        assert w0["balance"] == 5 and w0["stable"] == 25 and w0["stable_symbol"] == "USDC" and w0["usd"] == 775
    finally:
        app.dependency_overrides.pop(get_executor, None)


async def test_portfolio_marks_unreadable_and_partial_chains(client, fake_rpc, monkeypatch):
    async def prices(client=None):
        return {"SOL": 150.0, "ETH": 3000.0, "BNB": 600.0}

    monkeypatch.setattr(pf_mod, "coin_prices", prices)
    base, eth = TokenNode("base"), TokenNode("ethereum")
    bad: set = set()

    async def flaky(token, owner, _orig=base.erc20_balance):
        if owner.lower() in bad:
            raise RuntimeError("timeout")
        return await _orig(token, owner)

    async def reverted(token, owner):
        raise RuntimeError("eth_call: execution reverted (see https://rpc.example/key=abc123)")

    async def no_native(address):
        raise RuntimeError("timeout")

    base.erc20_balance = flaky
    eth.erc20_balance = reverted
    pf_mod._decimals.clear()
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"base": base, "ethereum": eth})
    try:
        h = await signup(client)
        b1, b2 = (await client.post("/wallets", json={"network": "base", "count": 2}, headers=h)).json()
        await client.post("/wallets", json={"network": "ethereum", "count": 1}, headers=h)
        bad.add(b2["address"].lower())
        p = (await client.get("/portfolio?fresh=true", headers=h)).json()
        chains = {c["network"]: c for c in p["chains"]}
        assert chains["base"]["status"] == "partial" and chains["base"]["wallets_read"] == 1
        assert chains["base"]["native_read"] == 2 and chains["base"]["stable_read"] == 1
        # The stablecoin failing doesn't hide the coin: ETH is still read and counted, and the reason is shown
        # without the endpoint URL.
        e = chains["ethereum"]
        assert e["status"] == "partial" and e["native_read"] == 1 and e["stable_read"] == 0
        assert "USDC unread for 1 of 1 wallet" in e["error"] and "execution reverted" in e["error"]
        assert "rpc.example" not in e["error"] and "abc123" not in e["error"]
        assert chains["solana"]["status"] == "no_wallets"
        assert p["complete"] is False and set(p["incomplete_chains"]) == {"base", "ethereum"} and p["checked_at"]
        # Nothing readable at all is "unavailable".
        eth.get_balance = no_native
        e = {c["network"]: c for c in (await client.get("/portfolio?fresh=true", headers=h)).json()["chains"]}["ethereum"]
        assert e["status"] == "unavailable" and e["wallets_read"] == 0 and "ETH unread" in e["error"]
    finally:
        app.dependency_overrides.pop(get_executor, None)
        pf_mod._decimals.clear()


async def test_net_flow_is_valued_in_usd_at_each_days_price(client, fake_rpc, monkeypatch):
    from datetime import timedelta

    from app.db import get_sessionmaker
    from app.models import Job, Transaction, User
    from app.models import utcnow
    from sqlalchemy import select

    h = await signup(client)
    today = utcnow()
    yday = today - timedelta(days=1)

    async def history(coin, cid, days, client):
        return {d: 100.0 for d in days}  # yesterday at $100; today uses the live $150

    monkeypatch.setattr(pf_mod, "_fetch_history", history)
    async with get_sessionmaker()() as db:
        uid = (await db.execute(select(User.id))).scalars().first()
        job = Job(user_id=uid, kind="trade_buy", params={}, idempotency_key="nf1", status="confirmed")
        db.add(job)
        await db.flush()
        db.add(Transaction(job_id=job.id, kind="buy", status="confirmed", sol_delta_lamports=-2 * 10**9, created_at=yday))
        db.add(Transaction(job_id=job.id, kind="sell", status="confirmed", sol_delta_lamports=3 * 10**9, created_at=today))
        await db.commit()
    d = (await client.get("/dashboard?period=7d", headers=h)).json()
    s = d["series"]
    assert [p["daily_usd"] for p in s] == [-200.0, 450.0] and s[-1]["cumulative_usd"] == 250.0
    assert d["net_usd_flow"] == 250.0 and d["net_sol_flow"] == 1.0 and d["usd_priced"] is True
