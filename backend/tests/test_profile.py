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
    assert d["stats"]["wallets"]["managed"] == 3
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
