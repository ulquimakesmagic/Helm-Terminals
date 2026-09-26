"""KyberSwap DEX route: request shape, Helm fee params, approvals, and fallback when a token has graduated."""

import base64
import json

import httpx
from eth_abi import decode as abi_decode

from app.chains import registry
from app.chains.kyber import NATIVE, KyberSwap
from app.chains.pons import PonsAdapter
from app.deps import get_executor
from app.main import app
from app.services.executor import Executor
from app.signing import get_signer

from .conftest import signup
from .test_evm_wallets import ETH
from .test_pons import TOKEN, PonsNode

ROUTER = "0x6131B5fae19EA4f9D964eAc0408E4408b66337b5"


def kyber_transport(log: list, amount_out: int = 12345):
    def handler(req: httpx.Request):
        log.append(req)
        if req.url.path.endswith("/routes"):
            return httpx.Response(200, json={"code": 0, "data": {"routerAddress": ROUTER, "routeSummary": {
                "tokenIn": req.url.params["tokenIn"], "amountIn": req.url.params["amountIn"], "amountOut": str(amount_out)}}})
        body = json.loads(req.content)
        assert body["routeSummary"]["amountOut"] == str(amount_out)
        return httpx.Response(200, json={"code": 0, "data": {"routerAddress": ROUTER, "data": "0xdeadbeef", "amountOut": str(amount_out)}})
    return httpx.MockTransport(handler)


async def test_kyber_buy_and_sell_plans_with_fee():
    log = []
    k = KyberSwap(fee_bps=100, fee_receiver="0x" + "fe" * 20, client=httpx.AsyncClient(transport=kyber_transport(log)))
    node = PonsNode()
    buy = await k.prepare_trade("robinhood", node, TOKEN, "0x" + "ab" * 20, "buy", 10**15, 5)
    assert [s.label for s in buy.steps] == ["buy"] and buy.steps[0].to == ROUTER and buy.steps[0].value == 10**15
    assert await buy.steps[0].build() == bytes.fromhex("deadbeef")
    q = log[0].url.params
    assert log[0].url.path == "/robinhood/api/v1/routes" and q["tokenIn"] == NATIVE and q["tokenOut"] == TOKEN
    assert q["feeAmount"] == "100" and q["isInBps"] == "true" and q["chargeFeeBy"] == "currency_in"
    assert log[0].headers["x-client-id"] == "HelmTerminals"
    assert json.loads(log[-1].content)["slippageTolerance"] == 500

    sell = await k.prepare_trade("robinhood", node, TOKEN, "0x" + "ab" * 20, "sell", 777, 5)
    assert [s.label for s in sell.steps] == ["approve", "sell"] and sell.steps[1].value == 0
    assert abi_decode(["address", "uint256"], sell.steps[0].data[4:]) == (ROUTER.lower(), 777)
    assert log[-1].url.params["chargeFeeBy"] == "currency_out"
    # no fee wallet: no fee params at all
    k2 = KyberSwap(fee_bps=100, fee_receiver="", client=httpx.AsyncClient(transport=kyber_transport(log)))
    await k2.prepare_trade("bsc", node, TOKEN, "0x" + "ab" * 20, "buy", 10**15, 5)
    assert "feeAmount" not in log[-1].url.params and k2.fee_note == "none"


async def test_graduated_pons_token_trades_through_the_dex(client, fake_rpc):
    node = PonsNode()
    node.graduated = 1
    registry._cache["pons"] = PonsAdapter(node)
    log = []
    dex = KyberSwap(client=httpx.AsyncClient(transport=kyber_transport(log)))
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"robinhood": node}, dex=dex)
    try:
        h = await signup(client)
        (w,) = (await client.post("/wallets", json={"network": "robinhood", "count": 1}, headers=h)).json()
        node.balances[w["address"].lower()] = ETH
        from app.db import get_sessionmaker
        from app.models import Launch, LaunchWallet, User
        from sqlalchemy import select
        async with get_sessionmaker()() as s:
            u = (await s.execute(select(User))).scalar_one()
            l = Launch(user_id=u.id, network="robinhood", launchpad="pons", status="live", name="P", symbol="P",
                       mint_address=TOKEN)
            s.add(l)
            await s.flush()
            s.add(LaunchWallet(launch_id=l.id, wallet_id=w["id"], role="holder"))
            await s.commit()
            lid = l.id
        body = {"side": "buy", "wallet_ids": [w["id"]], "amount": 0.001, "mode": "stagger", "stagger_ms": 0}
        pf = (await client.post(f"/launches/{lid}/trade", json=body, headers=h)).json()
        r = await client.post(f"/launches/{lid}/trade", json={**body, "confirm": True, "preflight_hash": pf["preflight_hash"],
                                                              "idempotency_key": "g1"}, headers=h)
        job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
        assert job["status"] == "confirmed", job
        assert node.sent[-1]["to"].lower() == ROUTER.lower()
    finally:
        registry._cache.pop("pons", None)
