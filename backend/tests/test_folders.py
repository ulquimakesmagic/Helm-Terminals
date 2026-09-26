"""Folders: no duplicates, delete keeps wallets, merge duplicates, move wallets, create wallets into a folder."""

import asyncio

from app.db import get_sessionmaker
from app.models import Folder
from sqlalchemy import select

from .conftest import signup


async def test_folder_lifecycle(client):
    h = await signup(client)
    f = (await client.post("/folders", json={"name": " Launch  squad "}, headers=h)).json()
    assert f["name"] == "Launch squad" and f["wallet_count"] == 0
    r = await client.post("/folders", json={"name": "launch SQUAD"}, headers=h)
    assert r.status_code == 409  # same name, any case

    ws = (await client.post("/wallets", json={"count": 2, "folder_id": f["id"]}, headers=h)).json()
    assert all(w["folder_id"] == f["id"] for w in ws)
    watch = await client.post("/wallets/watch", json={"address": "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
                                                      "folder_id": f["id"]}, headers=h)
    assert watch.status_code == 201 and watch.json()["folder_id"] == f["id"]
    dup = await client.post("/wallets/watch", json={"address": "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"}, headers=h)
    assert dup.status_code == 409
    assert (await client.get("/folders", headers=h)).json()[0]["wallet_count"] == 3

    g = (await client.post("/folders", json={"name": "Snipers"}, headers=h)).json()
    r = await client.post("/wallets/move", json={"wallet_ids": [ws[0]["id"]], "folder_id": g["id"]}, headers=h)
    assert r.status_code == 200
    r = await client.post("/wallets/move", json={"wallet_ids": [ws[1]["id"]], "folder_id": None}, headers=h)
    listed = {w["id"]: w["folder_id"] for w in (await client.get("/wallets", headers=h)).json()}
    assert listed[ws[0]["id"]] == g["id"] and listed[ws[1]["id"]] is None

    r = await client.delete(f"/folders/{g['id']}", headers=h)
    assert r.json()["wallets_unfiled"] == 1
    listed = {w["id"]: w["folder_id"] for w in (await client.get("/wallets", headers=h)).json()}
    assert len(listed) == 3 and listed[ws[0]["id"]] is None  # wallets are never deleted with a folder


async def test_merge_duplicates_and_isolation(client):
    h = await signup(client, "m@example.com")
    other = await signup(client, "o@example.com")
    first = (await client.post("/folders", json={"name": "Dupes"}, headers=h)).json()
    async with get_sessionmaker()() as s:  # duplicates made before the name check existed
        uid = (await s.execute(select(Folder.user_id).where(Folder.id == first["id"]))).scalar_one()
        extra = [Folder(user_id=uid, name="dupes") for _ in range(10)]
        s.add_all(extra)
        await s.commit()
        extra_id = extra[3].id
    w = (await client.post("/wallets", json={"count": 1}, headers=h)).json()[0]
    await client.post("/wallets/move", json={"wallet_ids": [w["id"]], "folder_id": extra_id}, headers=h)
    r = (await client.post("/folders/merge-duplicates", headers=h)).json()
    assert r["removed"] == 10 and r["wallets_moved"] == 1
    folders = (await client.get("/folders", headers=h)).json()
    assert len(folders) == 1 and folders[0]["wallet_count"] == 1
    # other users can't touch these folders or wallets
    assert (await client.delete(f"/folders/{folders[0]['id']}", headers=other)).status_code == 404
    r = await client.post("/wallets/move", json={"wallet_ids": [w["id"]], "folder_id": None}, headers=other)
    assert r.status_code == 404


async def test_rapid_double_submit_creates_one_folder(client):
    h = await signup(client, "d@example.com")
    rs = await asyncio.gather(*(client.post("/folders", json={"name": "Fast"}, headers=h) for _ in range(5)))
    assert sum(r.status_code == 201 for r in rs) >= 1
    names = [f["name"] for f in (await client.get("/folders", headers=h)).json()]
    assert names.count("Fast") == 1, names
