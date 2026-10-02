"""Launching from a USDC balance: the deploy preview plans a Relay route into the dev wallet's coin, and confirming
runs the route before the deploy (and deploys nothing if the route doesn't complete)."""

import json

import httpx

from app.chains.relay import Relay
from app.deps import get_executor
from app.main import app
from app.services.executor import Executor
from app.signing import get_signer

from .conftest import SOL, signup
from .test_bridge import BASE_USDC, TokenNode
from .test_evm_wallets import ETH


def relay_mock(seen: list, on_status, status: str = "success"):
    def handler(req: httpx.Request):
        if req.url.path == "/quote/v2":
            seen.append(json.loads(req.read()))
            return httpx.Response(200, json={
                "requestId": "0xfund",
                "steps": [
                    {"id": "approve", "kind": "transaction", "items": [{"status": "incomplete", "data": {
                        "to": BASE_USDC, "data": "0x095ea7b3" + "00" * 12 + "cd" * 20 + "00" * 32, "value": "0", "chainId": 8453}}]},
                    {"id": "deposit", "kind": "transaction", "requestId": "0xfund", "items": [{"status": "incomplete", "data": {
                        "to": "0x" + "cd" * 20, "data": "0xabcdef", "value": "0", "chainId": 8453}}]},
                ],
                "fees": {"relayer": {"amountUsd": "0.04"}, "gas": {"amountUsd": "0.01"}, "app": {"amountUsd": "0.30"}},
                "details": {"timeEstimate": 10, "currencyOut": {"currency": {"symbol": "SOL", "decimals": 9},
                                                                "amount": str(SOL), "minimumAmount": str(SOL)}},
            })
        if req.url.path == "/intents/status/v3":
            on_status()
            return httpx.Response(200, json={"status": status, "txHashes": ["fill"]})
        raise AssertionError(req.url.path)
    return httpx.MockTransport(handler)


async def _setup(client, fake_rpc, monkeypatch, status="success", credit=True):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "route_poll_seconds", 0)
    monkeypatch.setattr(get_settings(), "route_wait_seconds", 0)
    base = TokenNode("base")
    seen: list = []
    state: dict = {}

    def on_status():
        if credit and state.get("dev"):
            fake_rpc.balances[state["dev"]] = 2 * SOL

    relay = Relay("https://relay.test", fee_bps=30, fee_recipient="0x" + "11" * 20,
                  client=httpx.AsyncClient(transport=relay_mock(seen, on_status, status)))
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"base": base}, relay=relay)
    h = await signup(client)
    dev = (await client.post("/wallets", json={"count": 1}, headers=h)).json()[0]
    src = (await client.post("/wallets", json={"network": "base", "count": 1}, headers=h)).json()[0]
    fake_rpc.balances[dev["address"]] = 1_000  # effectively empty
    state["dev"] = dev["address"]
    base.balances[src["address"].lower()] = ETH // 100  # gas to send the route
    base.tokens[src["address"].lower()] = 500 * 10**6  # $500 USDC
    lid = (await client.post("/launches", json={"name": "T", "symbol": "T", "dev_wallet_id": dev["id"],
                                                "dev_buy_sol": 0.5}, headers=h)).json()["id"]
    return h, lid, dev, src, base, seen


async def test_deploy_funds_the_dev_wallet_from_usdc_then_launches(client, fake_rpc, monkeypatch):
    try:
        h, lid, dev, src, base, seen = await _setup(client, fake_rpc, monkeypatch)
        pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
        assert not pf["preflight"]["warnings"], pf
        f = pf["preflight"]["funding"]
        # 0.53 SOL short at $150, +5% and the 0.3% route fee, +$1 headroom, rounded up to whole dollars
        assert f["from_address"] == src["address"] and f["from_chain"] == "Base" and f["amount_usd"] == 85
        assert f["send"] == "85 USDC" and f["route_fee"] == "0.3% of the amount sent"
        assert "SOL short" in pf["preflight"]["funding_detail"]["covers"]
        r = await client.post(f"/launches/{lid}/deploy", json={"confirm": True, "preflight_hash": pf["preflight_hash"],
                                                              "idempotency_key": "af1"}, headers=h)
        job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
        assert job["status"] == "confirmed", job
        kinds = [t["kind"] for t in job["transactions"]]
        assert kinds[:2] == ["fund_route_approve", "fund_route"] and "deploy" in kinds
        assert seen[0]["originChainId"] == 8453 and seen[0]["amount"] == str(85 * 10**6)
        assert seen[0]["recipient"] == dev["address"] and seen[0]["destinationCurrency"] == "1" * 32
        assert (await client.get(f"/launches/{lid}", headers=h)).json()["status"] == "live"
    finally:
        app.dependency_overrides.pop(get_executor, None)


async def test_failed_route_deploys_nothing_and_keeps_the_draft(client, fake_rpc, monkeypatch):
    try:
        h, lid, dev, src, base, seen = await _setup(client, fake_rpc, monkeypatch, status="refund", credit=False)
        pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
        r = await client.post(f"/launches/{lid}/deploy", json={"confirm": True, "preflight_hash": pf["preflight_hash"],
                                                              "idempotency_key": "af2"}, headers=h)
        job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
        assert job["status"] == "failed" and "Nothing was deployed" in str(job), job
        assert "deploy" not in [t["kind"] for t in job["transactions"]]
        assert (await client.get(f"/launches/{lid}", headers=h)).json()["status"] == "draft"
    finally:
        app.dependency_overrides.pop(get_executor, None)


async def test_no_usdc_source_keeps_the_shortfall_warning_and_opt_out_works(client, fake_rpc, monkeypatch):
    try:
        h, lid, dev, src, base, seen = await _setup(client, fake_rpc, monkeypatch)
        base.balances[src["address"].lower()] = 0  # USDC but no gas to send it
        pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
        assert "funding" not in pf["preflight"] and "No wallet holds about $85 in USDC" in pf["preflight"]["warnings"][0]
        base.balances[src["address"].lower()] = ETH // 100
        pf = (await client.post(f"/launches/{lid}/deploy", json={"auto_fund": False}, headers=h)).json()
        assert "funding" not in pf["preflight"] and pf["preflight"]["warnings"]
    finally:
        app.dependency_overrides.pop(get_executor, None)
