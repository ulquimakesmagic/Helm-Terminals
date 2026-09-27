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

    async def broken_meta(token):
        raise RuntimeError("down")

    base.erc20_balance = flaky
    eth.erc20_meta = broken_meta
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
        assert chains["ethereum"]["status"] == "unavailable" and "Couldn't read" in chains["ethereum"]["error"]
        assert chains["solana"]["status"] == "no_wallets"
        assert p["complete"] is False and set(p["incomplete_chains"]) == {"base", "ethereum"} and p["checked_at"]
    finally:
        app.dependency_overrides.pop(get_executor, None)
        pf_mod._decimals.clear()
