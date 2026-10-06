"""Seed a disposable SQLite database through the real API (fake RPC and local test signer) and record the JSON
responses the web app reads, for offline visual and accessibility checks of every page.

    cd backend && SIGNER=local python ../tools/visual-qa/capture_fixtures.py ../tools/visual-qa/fixtures.json

No network, keys or real funds are involved: chain reads go to the test suite's FakeRPC, EVM/extra-chain reads are
blocked by design and show the app's honest "unavailable" states.
"""
import asyncio
import json
import os
import sys
import tempfile

TMP = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite+aiosqlite:///{TMP}/visual.db", RUN_JOBS_INLINE="true", ENV="development",
                  SECRET_KEY="visual-qa-only", SIGNER="local", EXTRA_NETWORKS="")
sys.path.insert(0, os.getcwd())

import httpx  # noqa: E402

from app import db as dbmod  # noqa: E402
from app.chains import registry  # noqa: E402
from app.deps import get_executor, get_rpc  # noqa: E402
from app.main import app  # noqa: E402
from app.services.executor import Executor  # noqa: E402
from app.services.market import Market, get_market  # noqa: E402
from app.signing import get_signer  # noqa: E402
from tests.conftest import FakeAdapter, FakeRPC  # noqa: E402

PATHS = ["/public-info", "/health", "/auth/me", "/telegram/status", *(f"/dashboard?period={p}" for p in ("all", "1y", "1m", "7d", "1d")),
         "/portfolio", "/portfolio?fresh=true", "/launches", "/launches?status=archived", "/launchpads", "/wallets",
         "/wallets?with_balances=true", "/folders", "/funders", "/chains", "/chains/wallets/overview", "/activity?limit=300",
         "/tracker", "/profile", "/referrals", "/auth/sessions", "/automations"]


class OfflineMarket(Market):
    """Codex is not reachable from QA: answer like a configured deployment with an indexed token and candles."""

    async def snapshot(self, network, token):
        from tests.test_market import snapshot_body
        from app.services.market import normalize_snapshot
        body = snapshot_body()["data"]
        body["filterTokens"]["results"][0]["token"]["address"] = token
        body["listPairsWithMetadataForToken"]["results"][0]["token"]["address"] = token
        return {**normalize_snapshot(body, network, token), "provider": "Codex", "fetched_at": __import__("time").time(),
                "stale": False, "age_s": 0}

    async def candles(self, network, token, resolution, pool=None, quote_side=None):
        import math, time
        now = int(time.time()) // 900 * 900
        bars, price = [], 0.00042
        for i in range(120):
            o = price
            price = max(1e-6, price * (1 + 0.04 * math.sin(i / 7) * math.cos(i / 13) + 0.01 * math.sin(i)))
            h, l = max(o, price) * 1.01, min(o, price) * 0.99
            bars.append({"t": now - (119 - i) * 900, "o": o, "h": h, "l": l, "c": price, "v": 1000 + 400 * abs(math.sin(i))})
        return {"state": "ok", "bars": bars, "resolution": resolution, "bar_seconds": 900, "currency": "USD",
                "scope": "pool" if pool else "token", "pool": pool, "last_bar_at": bars[-1]["t"], "provider": "Codex",
                "fetched_at": time.time(), "stale": False, "age_s": 0}


async def main(out_path):
    await dbmod.create_all()
    rpc = FakeRPC()
    registry._cache["pumpfun"] = FakeAdapter()
    app.dependency_overrides[get_rpc] = lambda: rpc
    app.dependency_overrides[get_executor] = lambda: Executor(rpc, get_signer())
    market = OfflineMarket(api_key="offline")
    app.dependency_overrides[get_market] = lambda: market
    out = {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as c:
        await c.post("/auth/register", json={"email": "operator@example.com", "password": "visual-qa-password"})
        tok = (await c.post("/auth/login", json={"email": "operator@example.com", "password": "visual-qa-password"})).json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        await c.post("/wallets/main", headers=h)
        ws = (await c.post("/wallets", json={"count": 4, "label_prefix": "Desk"}, headers=h)).json()
        await c.post("/folders", json={"name": "Launch desk"}, headers=h)
        lid = (await c.post("/launches", json={"name": "Harbor Signal", "symbol": "HRBR", "dev_wallet_id": ws[0]["id"],
                                                "dev_buy_sol": 0.25, "description": "Disposable QA launch"}, headers=h)).json()["id"]
        pre = (await c.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
        await c.post(f"/launches/{lid}/deploy", json={"confirm": True, "preflight_hash": pre["preflight_hash"],
                                                       "idempotency_key": "qa-deploy"}, headers=h)
        await c.post(f"/launches/{lid}/wallets", json={"wallet_ids": [w["id"] for w in ws[1:]]}, headers=h)
        await c.post("/launches", json={"name": "Quiet Draft", "symbol": "QDFT", "dev_wallet_id": ws[1]["id"]}, headers=h)
        await c.post("/tracker", json={"network": "solana", "address": ws[3]["address"], "label": "Watched desk"}, headers=h)
        paths = PATHS + [f"/launches/{lid}", f"/launches/{lid}/workspace", f"/launches/{lid}/market",
                         f"/launches/{lid}/market/candles?resolution=15", f"/launches/{lid}/automations",
                         f"/launches/{lid}/allocations", "/launchpads/pumpfun/options", "/launchpads/pumpfun/protections",
                         "/launchpads/pumpfun/quote-assets"]
        for p in paths:
            r = await c.get(p, headers=h)
            out[p] = {"status": r.status_code, "body": r.json() if r.content else None}
        registry._cache.pop("pumpfun", None)  # the real adapters' option schemas for the launch forms
        for pad in ("pumpfun", "letsbonk", "bags", "meteora-dbc", "flap-sh", "clanker", "four-meme", "uniswap-v2", "pons"):
            for suffix in ("options", "protections", "quote-assets"):
                p = f"/launchpads/{pad}/{suffix}"
                r = await c.get(p, headers=h)
                out[p] = {"status": r.status_code, "body": r.json() if r.content else None}
        out["_launch_id"] = lid
        out["_token"] = tok
    with open(out_path, "w") as f:
        json.dump(out, f, indent=1, default=str)
    print(f"captured {len(out) - 2} responses for launch {lid}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
