from app.deps import get_executor
from app.main import app
from app.services import portfolio as pf_mod
from app.services.portfolio import coin_prices as load_coin_prices
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
        h = await signup(client, main=False)
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
        h = await signup(client, main=False)
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
        pf_mod._cache.clear()  # simulate the next refresh window after the provider changes
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

    h = await signup(client, main=False)
    today = utcnow()
    yday = today - timedelta(days=1)

    from app.services import market as mk

    async def history(self, inputs):
        return {i: 100.0 for i in inputs}  # yesterday at $100 from Codex; today uses the live $150

    monkeypatch.setattr(mk.Market, "prices", history)
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


def wallet(network, ident=None, kind="managed"):
    from types import SimpleNamespace

    return SimpleNamespace(id=ident or network, network=network, address=f"address-{ident or network}",
                           kind=kind, label=network)


async def test_new_chains_are_counted_and_arc_usdc_is_not_double_counted(monkeypatch):
    async def raw(ex, network, address):
        return {"near": 2 * 10**24, "tron": 3 * 10**6, "arc": 4 * 10**18, "ton": 5 * 10**9}[network]

    async def prices():
        return {"NEAR": 5, "TRX": 0.25, "TON": 2}

    monkeypatch.setattr(pf_mod.extra_chains, "native_balance", raw)
    monkeypatch.setattr(pf_mod, "coin_prices", prices)
    monkeypatch.setattr(pf_mod, "extra_enabled", lambda: ["near", "tron", "arc", "ton"])
    p = await pf_mod.build_portfolio(None, "new-chains", [wallet(n) for n in ("near", "tron", "arc", "ton")]
                                   + [wallet("ton", "connected", "external"), wallet("near", "watch", "watch")], True)
    chains = {c["network"]: c for c in p["chains"]}
    assert p["wallets"] == 4 and p["complete"]
    assert chains["near"]["native"] == 2 and chains["tron"]["native"] == 3 and chains["ton"]["native"] == 5
    assert chains["arc"]["usd"] == 4 and chains["arc"]["native_is_stable"]
    assert chains["arc"]["stable_tracked"] is False
    assert p["totals"] == {"usd": 24.75, "stable_usd": 4, "native_usd": 20.75}


async def test_disabled_and_unknown_chains_remain_counted_without_being_read(monkeypatch):
    async def forbidden(*args):
        raise AssertionError("disabled chains must not be queried")

    monkeypatch.setattr(pf_mod, "extra_enabled", lambda: [])
    monkeypatch.setattr(pf_mod.extra_chains, "native_balance", forbidden)
    p = await pf_mod.build_portfolio(None, "disabled-chains", [wallet("near"), wallet("future-chain")], True)
    rows = {c["network"]: c for c in p["chains"]}
    assert p["wallets"] == 2 and not p["complete"]
    assert set(p["incomplete_chains"]) == {"near", "future-chain"}
    assert p["totals"]["usd"] is None and p["totals"]["native_usd"] is None
    assert rows["near"]["native"] is None and "disabled" in rows["near"]["message"]
    assert "not supported" in rows["future-chain"]["message"]


async def test_missing_prices_and_failed_reads_do_not_become_zero(monkeypatch):
    async def raw(ex, network, address):
        if network == "tron":
            raise RuntimeError("HTTP 429")
        return 2 * 10**24

    monkeypatch.setattr(pf_mod.extra_chains, "native_balance", raw)
    monkeypatch.setattr(pf_mod, "extra_enabled", lambda: ["near", "tron"])
    p = await pf_mod.build_portfolio(None, "missing-prices", [wallet("near"), wallet("tron")], True)
    rows = {c["network"]: c for c in p["chains"]}
    assert not p["complete"] and not p["native_complete"] and not p["priced"]
    assert rows["near"]["native"] == 2 and rows["near"]["usd"] is None
    assert rows["near"]["top"][0]["stable"] is None
    assert rows["tron"]["native"] is None and "provider is busy" in rows["tron"]["message"]
    assert "429" not in rows["tron"]["message"]
    assert p["totals"]["usd"] is None


async def test_partial_stablecoin_totals_and_scan_limit_are_explicit(monkeypatch):
    async def native(ex, network, address):
        return 0.0

    async def stable(ex, network, token, address):
        raise RuntimeError("eth_call not on its plan")

    monkeypatch.setattr(pf_mod, "native_balance", native)
    monkeypatch.setattr(pf_mod, "token_balance_raw", stable)
    monkeypatch.setattr(pf_mod, "MAX_WALLETS", 1)
    p = await pf_mod.build_portfolio(None, "scan-limit", [wallet("robinhood", "one"), wallet("robinhood", "two")], True)
    rh = next(c for c in p["chains"] if c["network"] == "robinhood")
    assert p["wallets"] == 2 and rh["wallets"] == 2 and rh["native_read"] == 1
    assert not p["stable_complete"] and not p["native_complete"] and not p["complete"]
    assert p["totals"]["stable_usd"] is None
    assert p["totals"]["usd"] == 0 and rh["status"] == "partial"
    assert "scan limit" in rh["message"]


async def test_refreshes_share_work_and_new_wallets_invalidate_the_cache(monkeypatch):
    import asyncio

    calls = []
    started, release = asyncio.Event(), asyncio.Event()

    async def raw(ex, network, address):
        calls.append(address)
        started.set()
        await release.wait()
        return 0

    monkeypatch.setattr(pf_mod.extra_chains, "native_balance", raw)
    monkeypatch.setattr(pf_mod, "extra_enabled", lambda: ["near"])
    ws = [wallet("near")]
    first = asyncio.create_task(pf_mod.build_portfolio(None, "shared-refresh", ws, True))
    await started.wait()
    second = asyncio.create_task(pf_mod.build_portfolio(None, "shared-refresh", ws, True))
    release.set()
    a, b = await asyncio.gather(first, second)
    assert a == b and len(calls) == 1
    await pf_mod.build_portfolio(None, "shared-refresh", ws, True)
    assert len(calls) == 1  # rapid manual refresh is cached too
    p = await pf_mod.build_portfolio(None, "shared-refresh", ws + [wallet("near", "second")], True)
    assert p["wallets"] == 2 and len(calls) == 3


async def test_slow_token_read_preserves_native_balance_and_does_not_hold_up_other_chains(monkeypatch):
    import asyncio

    cancelled = asyncio.Event()

    async def native(ex, network, address):
        return 1.0

    async def stable(ex, network, token, address):
        if network == "robinhood":
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return 0

    monkeypatch.setattr(pf_mod, "native_balance", native)
    monkeypatch.setattr(pf_mod, "token_balance_raw", stable)
    monkeypatch.setattr(pf_mod, "CHAIN_TIMEOUT_S", 0.02)
    p = await pf_mod.build_portfolio(None, "slow-chain", [wallet("robinhood"), wallet("base")], True)
    rows = {c["network"]: c for c in p["chains"]}
    assert rows["base"]["status"] == "ok"
    assert rows["robinhood"]["native"] == 1 and rows["robinhood"]["stable"] is None
    assert rows["robinhood"]["status"] == "partial" and "timed out" in rows["robinhood"]["message"]
    assert cancelled.is_set() and not p["complete"]


async def test_price_provider_failure_leaves_coins_unpriced(monkeypatch):
    class Down:
        async def prices(self, inputs):
            raise RuntimeError("codex down")

    monkeypatch.undo()  # use the real coin_prices, not the suite's fixed prices
    assert await load_coin_prices(Down()) == {}


async def test_only_positive_codex_prices_are_used_and_near_ton_stay_unpriced(monkeypatch):
    from app.services.portfolio import PRICE_TOKENS

    class Codex:
        async def prices(self, inputs):
            values = {"SOL": 150.0, "ETH": -1.0, "BNB": float("nan")}
            return {(n, a, ts): values[sym] for sym, (n, a) in PRICE_TOKENS.items() for (n2, a2, ts) in inputs
                    if (n2, a2) == (n, a) and sym in values}

    monkeypatch.undo()
    assert await load_coin_prices(Codex()) == {"SOL": 150.0}
    assert "NEAR" not in PRICE_TOKENS and "TON" not in PRICE_TOKENS  # not Codex networks: shown without USD
