"""Hardening from the 6 October 2026 audit: request limits, schema exposure, idempotency races, link validation."""
import asyncio

import httpx

from app.main import MAX_BODY_BYTES, BodyLimit, create_app

from .conftest import signup
from .test_api import make_wallets


async def test_oversized_bodies_are_refused_without_buffering(client):
    h = await signup(client, "big@example.com", main=False)
    huge = "A" * (MAX_BODY_BYTES + 10)
    r = await client.post("/launches", json={"name": "Big", "symbol": "BIG", "image_b64": huge}, headers=h)
    assert r.status_code == 413


async def test_body_limit_also_counts_streamed_chunks():
    seen = []

    async def app(scope, receive, send):
        while True:
            message = await receive()
            seen.append(len(message.get("body", b"")))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    limited = BodyLimit(app, limit=10)
    chunks = [b"x" * 6, b"x" * 6]
    sent = []

    async def receive():
        body = chunks.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(chunks)}

    async def send(message):
        sent.append(message)

    await limited({"type": "http", "headers": []}, receive, send)
    assert sent[0]["status"] == 413


async def test_image_field_has_a_size_limit(client):
    h = await signup(client, "img@example.com", main=False)
    r = await client.post("/launches", json={"name": "Img", "symbol": "IMG", "image_b64": "A" * 2_800_001}, headers=h)
    assert r.status_code == 422


async def test_api_schema_is_not_published_in_production(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "env", "production")
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await c.get(path)).status_code == 404


async def test_concurrent_confirmations_share_one_job(client, fake_rpc):
    h = await signup(client, "race@example.com", main=False)
    src, dst = await make_wallets(client, h, 2)
    body = {"funder_wallet_id": src["id"], "allocations": [{"wallet_id": dst["id"], "sol": 0.1}]}
    pre = (await client.post("/wallets/fund", json=body, headers=h)).json()
    confirm = {**body, "confirm": True, "preflight_hash": pre["preflight_hash"], "idempotency_key": "same-key"}
    rs = await asyncio.gather(*(client.post("/wallets/fund", json=confirm, headers=h) for _ in range(4)))
    assert all(r.status_code == 200 for r in rs), [r.text for r in rs]
    assert len({r.json()["job_id"] for r in rs}) == 1


async def test_launch_social_links_must_be_https(client):
    h = await signup(client, "links@example.com", main=False)
    for bad in ("javascript:alert(1)", "http://example.com", "https://user:pw@example.com", "x.com/helm"):
        r = await client.post("/launches", json={"name": "L", "symbol": "L", "socials": {"website": bad}}, headers=h)
        assert r.status_code == 400, bad
    ok = await client.post("/launches", json={"name": "L", "symbol": "L",
                                              "socials": {"website": "https://helm.example", "twitter": ""}}, headers=h)
    assert ok.status_code == 201 and ok.json()["socials"] == {"website": "https://helm.example"}
