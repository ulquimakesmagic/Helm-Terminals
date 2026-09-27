"""Routes through Relay: USDC on one chain into gas on another, with Helm's route fee disclosed and passed on."""

import json

import httpx

from app.chains.relay import EVM_NATIVE, RELAY_SOLANA_CHAIN_ID, Relay, relay_chain_id
from app.deps import get_executor
from app.main import app
from app.services.chain_balances import stable_for
from app.services.executor import Executor
from app.signing import get_signer

from .conftest import signup
from .test_evm_wallets import ETH, FakeEvm

BASE_USDC = stable_for("base")[1]


class TokenNode(FakeEvm):
    def __init__(self, network):
        super().__init__(network)
        self.tokens: dict[str, int] = {}

    async def erc20_decimals(self, token):
        return (await self.erc20_meta(token))["decimals"]

    async def erc20_meta(self, token):
        return {"name": "USD Coin", "symbol": "USDC", "decimals": 6, "total_supply": 10**15}

    async def erc20_balance(self, token, owner):
        return self.tokens.get(owner.lower(), 0)


def relay_mock(seen: list, statuses: list[str], fail_quote: str | None = None):
    def handler(req: httpx.Request):
        if req.url.path == "/quote/v2":
            body = json.loads(req.read())
            seen.append(body)
            if fail_quote:
                return httpx.Response(400, json={"message": fail_quote})
            return httpx.Response(200, json={
                "requestId": "0xreq1",
                "steps": [
                    {"id": "approve", "kind": "transaction", "items": [{"status": "incomplete", "data": {
                        "to": BASE_USDC, "data": "0x095ea7b3" + "00" * 64, "value": "0", "chainId": 8453}}]},
                    {"id": "deposit", "kind": "transaction", "requestId": "0xreq1", "items": [{"status": "incomplete", "data": {
                        "to": "0x" + "cd" * 20, "data": "0xabcdef", "value": "0", "chainId": 8453}}]},
                ],
                "fees": {"relayer": {"amountUsd": "0.04"}, "gas": {"amountUsd": "0.01"}, "app": {"amountUsd": "0.06"}},
                "details": {"timeEstimate": 20, "currencyOut": {
                    "currency": {"symbol": "BNB", "decimals": 18}, "amount": str(3 * 10**16), "minimumAmount": str(29 * 10**15),
                    "amountUsd": "19.8"}},
            })
        if req.url.path == "/intents/status/v3":
            return httpx.Response(200, json={"status": statuses.pop(0) if len(statuses) > 1 else statuses[0],
                                             "txHashes": ["0xfill"]})
        raise AssertionError(req.url.path)
    return httpx.MockTransport(handler)


def test_chain_ids():
    assert relay_chain_id("solana") == RELAY_SOLANA_CHAIN_ID and relay_chain_id("robinhood") == 4663


async def test_usdc_on_base_routes_to_bnb_with_disclosed_fee(client, fake_rpc, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "route_poll_seconds", 0)
    base, bsc = TokenNode("base"), TokenNode("bsc")
    seen: list = []
    relay = Relay("https://relay.test", fee_bps=30, fee_recipient="0x" + "11" * 20,
                  client=httpx.AsyncClient(transport=relay_mock(seen, ["pending", "success"])))
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"base": base, "bsc": bsc},
                                                              relay=relay)
    try:
        h = await signup(client)
        src = (await client.post("/wallets", json={"network": "base", "count": 1}, headers=h)).json()[0]
        dst = (await client.post("/wallets", json={"network": "bsc", "count": 1}, headers=h)).json()[0]
        base.balances[src["address"].lower()] = ETH // 100
        base.tokens[src["address"].lower()] = 50 * 10**6

        body = {"from_wallet_id": src["id"], "to_wallet_id": dst["id"], "amount": 20}
        pf = (await client.post("/bridge", json=body, headers=h)).json()
        p = pf["preflight"]
        assert p["send_asset"] == "USDC" and p["receive_asset"] == "BNB" and p["platform_fee"] == "0.3% of the amount sent"
        assert p["quote"]["receive_estimate"] == 0.03 and p["quote"]["receive_minimum"] == 0.029
        assert not p["warnings"], p
        q = seen[-1]
        assert q["originChainId"] == 8453 and q["destinationChainId"] == 56 and q["amount"] == str(20 * 10**6)
        assert q["originCurrency"] == BASE_USDC and q["destinationCurrency"] == EVM_NATIVE
        assert q["appFees"] == [{"recipient": "0x" + "11" * 20, "fee": "30"}] and q["recipient"] == dst["address"]

        r = await client.post("/bridge", json={**body, "confirm": True, "preflight_hash": pf["preflight_hash"],
                                               "idempotency_key": "route-one"}, headers=h)
        assert r.status_code == 200, r.text
        job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
        assert job["status"] == "confirmed", job
        assert [t["kind"] for t in job["transactions"]] == ["bridge_approve", "bridge"]
        assert base.sent[-1]["to"].lower() == "0x" + "cd" * 20 and base.sent[-1]["data"] == bytes.fromhex("abcdef")
        st = (await client.get(f"/bridge/{job['id']}", headers=h)).json()
        assert st["route_status"] == "success" and st["request_id"] == "0xreq1"

        # Too little USDC, and a route Relay can't fill, both block confirmation with a clear reason.
        pf = (await client.post("/bridge", json={**body, "amount": 80}, headers=h)).json()
        assert "less than the 80 USDC" in pf["preflight"]["warnings"][0]
    finally:
        app.dependency_overrides.pop(get_executor, None)


async def test_relay_errors_become_warnings(client, fake_rpc):
    base, sol_seen = TokenNode("base"), []
    relay = Relay("https://relay.test", client=httpx.AsyncClient(transport=relay_mock(sol_seen, ["success"], "Amount too low")))
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"base": base}, relay=relay)
    try:
        h = await signup(client)
        src = (await client.post("/wallets", json={"network": "base", "count": 1}, headers=h)).json()[0]
        dst = (await client.post("/wallets", json={"count": 1}, headers=h)).json()[0]  # Solana
        base.balances[src["address"].lower()] = ETH // 100
        base.tokens[src["address"].lower()] = 10**6
        pf = (await client.post("/bridge", json={"from_wallet_id": src["id"], "to_wallet_id": dst["id"], "amount": 1},
                                headers=h)).json()
        assert "Amount too low" in pf["preflight"]["warnings"][-1] and pf["preflight"]["platform_fee"] == "none"
        assert sol_seen[-1]["destinationChainId"] == RELAY_SOLANA_CHAIN_ID
        assert sol_seen[-1]["destinationCurrency"] == "11111111111111111111111111111111"
    finally:
        app.dependency_overrides.pop(get_executor, None)


async def test_solana_step_is_compiled_from_relay_instructions(fake_rpc):
    from solders.keypair import Keypair
    from solders.transaction import VersionedTransaction

    from app.models import Wallet
    from app.routes.bridge import _send_item

    signer = get_signer()
    ref = await signer.create_wallet("u1", "solana", "W")
    w = Wallet(user_id="u1", network="solana", address=ref.address, kind="managed", signer_ref=ref.signer_ref)
    other = str(Keypair().pubkey())
    fake_rpc.get_lookup_table = lambda addr: _none()
    data = {"instructions": [{"programId": "11111111111111111111111111111111", "data": "02000000e803000000000000",
                              "keys": [{"pubkey": ref.address, "isSigner": True, "isWritable": True},
                                       {"pubkey": other, "isSigner": False, "isWritable": True}]}],
            "addressLookupTableAddresses": [str(Keypair().pubkey())]}
    res = await _send_item(Executor(fake_rpc, signer), w, data)
    assert res.status == "confirmed"
    tx = VersionedTransaction.from_bytes(fake_rpc.sent[-1])
    assert str(tx.message.account_keys[0]) == ref.address and len(tx.message.instructions) == 1


async def _none():
    return None
