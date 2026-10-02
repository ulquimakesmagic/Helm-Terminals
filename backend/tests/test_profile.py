from .test_api import live_launch
from .conftest import signup


async def test_profile_defaults_and_stats(client, fake_rpc):
    h = await signup(client, "maker@example.com")
    r = await client.get("/profile", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["profile"]["display_name"] == "maker" and d["profile"]["is_public"] is False
    assert d["account"]["email"] == "maker@example.com" and d["account"]["active_sessions"] == 1
    assert len(d["heatmap"]["days"]) == 182 and d["heatmap"]["days"][-1] >= 1 and d["heatmap"]["streak"] >= 1

    await live_launch(client, h, fake_rpc)
    d = (await client.get("/profile", headers=h)).json()
    assert d["stats"]["launches"]["total"] == 1 and d["stats"]["launches"]["live"] == 1
    assert d["stats"]["launchpads"][0]["count"] == 1
    assert d["stats"]["wallets"]["managed"] == 3 + 5  # three made here, plus the main wallet on Solana and 4 EVM chains
    assert next(m for m in d["milestones"] if m["id"] == "first_launch")["done"]
    assert d["recent_launches"][0]["mint_address"]


async def test_profile_update_validation_and_unique_handle(client):
    h1 = await signup(client, "one@example.com")
    h2 = await signup(client, "two@example.com")
    r = await client.patch("/profile", json={"is_public": True}, headers=h1)
    assert r.status_code == 400 and "handle" in r.json()["detail"]
    for bad in ("ab", "Has Space", "admin"):
        assert (await client.patch("/profile", json={"handle": bad}, headers=h1)).status_code == 400
    r = await client.patch("/profile", json={"handle": "@MemeGoat", "x_handle": "@thememegoat", "bio": " gm ",
                                             "website": "https://helmterminals.xyz", "avatar_hue": 120}, headers=h1)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["handle"] == "memegoat" and p["x_handle"] == "thememegoat" and p["bio"] == "gm"
    assert (await client.patch("/profile", json={"website": "http://x.io"}, headers=h1)).status_code == 400
    assert (await client.patch("/profile", json={"handle": "memegoat"}, headers=h2)).status_code == 409


async def test_public_page_is_opt_in_and_hides_private_data(client, fake_rpc):
    h = await signup(client, "pub@example.com")
    await live_launch(client, h, fake_rpc)
    await client.patch("/profile", json={"handle": "builder"}, headers=h)
    assert (await client.get("/u/builder")).status_code == 404  # not public yet
    r = await client.patch("/profile", json={"is_public": True}, headers=h)
    assert r.json()["public_path"] == "/u/builder"
    r = await client.get("/u/BUILDER")
    assert r.status_code == 200
    d = r.json()
    assert d["stats"]["deployed"] == 1 and d["launches"][0]["mint_address"]
    text = str(d).lower()
    assert "pub@example.com" not in text and "wallet" not in text and "sol_flow" not in text
    # clearing the handle takes the page down
    await client.patch("/profile", json={"handle": ""}, headers=h)
    assert (await client.get("/u/builder")).status_code == 404


async def test_profile_edit_is_web_only(client):
    h = await signup(client, "bot@example.com")
    from sqlalchemy import update
    from app.db import get_sessionmaker
    from app.models import Session
    async with get_sessionmaker()() as s:
        await s.execute(update(Session).values(channel="bot"))
        await s.commit()
    assert (await client.patch("/profile", json={"bio": "x"}, headers=h)).status_code == 403
    assert (await client.get("/profile", headers=h)).status_code == 200


async def test_profile_net_flow_is_usd_valued_per_chain(client, fake_rpc):
    """The headline net flow is USD-valued per chain at each chain's own coin price, with the
    per-chain amounts kept beside it; chains without recorded flows are simply absent."""
    h = await signup(client, "flow@example.com", main=False)
    await live_launch(client, h, fake_rpc)
    d = (await client.get("/profile", headers=h)).json()
    nf = d["stats"]["net_flow"]
    assert nf["flows"], "the launch's dev buy records a measured flow"
    sol = next(f for f in nf["flows"] if f["network"] == "solana")
    assert sol["symbol"] == "SOL" and sol["amount"] < 0 and sol["usd"] is not None
    assert nf["priced"] and isinstance(nf["usd"], (int, float))
    assert abs(nf["usd"] - sum(f["usd"] for f in nf["flows"])) < 1e-9
    assert abs(sum(f["amount"] for f in nf["flows"]) - d["stats"]["net_sol_flow"]) < 1e-9
    # No chain's flow is ever valued at another chain's coin price: EVM deltas aren't recorded yet.
    assert all(f["network"] == "solana" for f in nf["flows"])
