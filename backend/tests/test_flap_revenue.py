"""Commission configuration is not income and remains tied to the submitted recipient."""
import base64

import httpx
import pytest
from sqlalchemy import select

from app.chains import registry
from app.chains.flap import FlapAdapter
from app.db import get_sessionmaker
from app.deps import get_executor
from app.main import app
from app.models import RevenueEvent
from app.services.executor import Executor
from app.services import evm_exec, platform_config as pc
from app.signing import get_signer
from .conftest import signup
from .test_flap import FlapNode, PNG, upload_transport
from .test_evm_wallets import ETH
from .test_admin import boss


@pytest.mark.parametrize("delayed", [False, True])
async def test_base_commission_configuration_survives_treasury_change_and_late_confirmation(client, fake_rpc, monkeypatch, delayed):
    async def no_sleep(_):
        pass
    monkeypatch.setattr(evm_exec.asyncio, "sleep", no_sleep)
    class Node(FlapNode):
        ready = not delayed
        async def receipt(self, h):
            if not self.ready:
                raise httpx.ConnectError("temporarily unavailable")
            return await super().receipt(h)
        async def call(self, method, params):
            if method == "eth_getTransactionByHash":
                return {"hash": params[0]}
            return await super().call(method, params)
    node = Node("base")
    adapter = FlapAdapter("flap-sh-base", "base", node, commission_receiver="0x" + "cd" * 20,
                          client=httpx.AsyncClient(transport=upload_transport([])))
    registry._cache["flap-sh-base"] = adapter
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"base": node})
    try:
        h = await signup(client)
        pads = {p["id"]: p for p in (await client.get("/launchpads")).json()}
        assert pads["flap-sh-base"]["network"] == "base"
        dev = (await client.post("/wallets", json={"network": "base", "count": 1}, headers=h)).json()[0]
        node.balances[dev["address"].lower()] = ETH
        body = {"launchpad": "flap-sh-base", "name": "Base test", "symbol": "BT", "dev_wallet_id": dev["id"],
                "image_b64": base64.b64encode(PNG).decode(), "options": {"tax_rate_pct": 1}}
        bad = await client.post("/launches", json={**body, "options": {"tax_rate_pct": 0}}, headers=h)
        assert bad.status_code == 400
        created = await client.post("/launches", json=body, headers=h)
        assert created.status_code == 201, created.text
        lid = created.json()["id"]
        pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
        assert pf["preflight"]["commission_receiver"].lower() == "0x" + "cd" * 20
        sent = await client.post(f"/launches/{lid}/deploy", headers=h, json={"confirm": True,
            "preflight_hash": pf["preflight_hash"], "idempotency_key": "base-tax"})
        assert sent.status_code == 200, sent.text
        if delayed:
            async with get_sessionmaker()() as db:
                assert not (await db.execute(select(RevenueEvent).where(RevenueEvent.launch_id == lid))).scalars().all()
        adapter._commission = "0x" + "ef" * 20
        node.ready = True
        for _ in range(2):
            result = await client.post(f"/launches/{lid}/recheck", json={}, headers=h)
            assert result.status_code == 200 and result.json()["status"] == "live", result.text
        async with get_sessionmaker()() as db:
            rows = (await db.execute(select(RevenueEvent).where(RevenueEvent.launch_id == lid))).scalars().all()
            assert len(rows) == 1
            row = rows[0]
            assert row.destination.lower() == "0x" + "cd" * 20
            assert row.status == "configured" and row.amount_raw is None and row.estimated
            assert row.job_id == sent.json()["job_id"]
    finally:
        registry._cache.pop("flap-sh-base", None)
        app.dependency_overrides.pop(get_executor, None)


async def test_configured_commission_does_not_count_as_received_or_claimable(client):
    h, _ = await boss(client)
    async with get_sessionmaker()() as db:
        db.add(RevenueEvent(source="partner", network="base", asset="various", decimals=0,
                            status="configured", amount_raw=None, estimated=True))
        await db.commit()
    response = await client.get("/admin/revenue/summary", headers=h)
    assert response.status_code == 200, response.text
    bucket = response.json()["totals"][0]
    assert bucket["received"] == bucket["claimable"] == bucket["pending"] == 0
    assert bucket["unknown_amount_entries"] == 1
    ledger = await client.get("/admin/revenue/ledger?status=configured", headers=h)
    assert ledger.status_code == 200
    assert "pons" not in pc.CREATOR_SHARE_LAUNCHPADS
    assert next(p for p in pc.FEE_PATHS if p["id"] == "creator_share_pons")["supported"] is False
