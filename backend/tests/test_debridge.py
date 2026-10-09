"""deBridge DLN as a second route provider: fallback when Relay can't quote, and the same safety bounds."""

import httpx
import pytest
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from app.chains.base import AdapterError
from app.chains.debridge import DeBridge, dln_chain_id
from app.chains.relay import EVM_NATIVE, Relay
from app.chains.route_providers import RouteProviders
from app.chains.solana import SYSTEM_PROGRAM, build_unsigned
from app.deps import get_executor
from app.main import app
from app.services.chain_balances import stable_for
from app.services.executor import Executor
from app.signing import get_signer

from .conftest import signup
from .test_bridge import TokenNode, relay_mock
from .test_evm_wallets import ETH

BASE_USDC = stable_for("base")[1]
DLN = "0x" + "ef" * 20
FIX_FEE = 10**15  # 0.001 ETH


def dln_mock(seen: list, statuses: list[str], *, recipient_in_tx=True, value=None, fix_fee=FIX_FEE,
             amount_override=None, solana_tx: str | None = None):
    def handler(req: httpx.Request):
        if req.url.path == "/v1.0/dln/order/create-tx":
            p = dict(req.url.params)
            seen.append(p)
            to = p["dstChainTokenOutRecipient"]
            rec = (bytes.fromhex(to[2:]) if to.startswith("0x") else bytes(Pubkey.from_string(to))) if recipient_in_tx else b"\x00" * 20
            tx = {"to": DLN, "data": "0x" + (b"\x12\x34\x56\x78" + b"\x00" * 12 + rec).hex(),
                  "value": str(value if value is not None else fix_fee)}
            if solana_tx is not None:
                tx = {"data": solana_tx}
            return httpx.Response(200, json={
                "orderId": "0xorder1", "fixFee": str(fix_fee), "tx": tx,
                "order": {"approximateFulfillmentDelay": 12},
                "estimation": {
                    "srcChainTokenIn": {"amount": str(amount_override or p["srcChainTokenInAmount"]),
                                        "approximateUsdValue": 20.0},
                    "dstChainTokenOut": {"address": p["dstChainTokenOut"], "chainId": p["dstChainId"], "decimals": 18,
                                         "symbol": "BNB", "amount": str(3 * 10**16), "recommendedAmount": str(3 * 10**16),
                                         "recommendedApproximateUsdValue": 19.7},
                }})
        if req.url.path.endswith("/status"):
            return httpx.Response(200, json={"orderId": "0xorder1", "status": statuses.pop(0) if len(statuses) > 1 else statuses[0]})
        raise AssertionError(req.url.path)
    return httpx.MockTransport(handler)


def providers(relay_fails: str | None, dln_seen: list, statuses=("Created", "Fulfilled"), **kw):
    relay = Relay("https://relay.test", client=httpx.AsyncClient(transport=relay_mock([], ["success"], relay_fails)))
    dln = DeBridge("https://dln.test", client=httpx.AsyncClient(transport=dln_mock(dln_seen, list(statuses), **kw)))
    return RouteProviders([relay, dln])


async def setup(client, fake_rpc, routes):
    base, bsc = TokenNode("base"), TokenNode("bsc")
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"base": base, "bsc": bsc},
                                                              relay=routes)
    h = await signup(client)
    src = (await client.post("/wallets", json={"network": "base", "count": 1}, headers=h)).json()[0]
    dst = (await client.post("/wallets", json={"network": "bsc", "count": 1}, headers=h)).json()[0]
    base.balances[src["address"].lower()] = ETH // 100
    base.tokens[src["address"].lower()] = 50 * 10**6
    return h, src, dst, base


async def test_falls_back_to_debridge_when_relay_cant_quote_and_executes_the_order(client, fake_rpc, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "route_poll_seconds", 0)
    seen: list = []
    try:
        h, src, dst, base = await setup(client, fake_rpc, providers("Relay is down", seen))
        body = {"from_wallet_id": src["id"], "to_wallet_id": dst["id"], "amount": 20}
        pf = (await client.post("/bridge", json=body, headers=h)).json()
        p = pf["preflight"]
        assert not p["warnings"], p["warnings"]
        assert p["provider"] == "deBridge (debridge.finance)" and p["quote"]["provider"] == "debridge"
        assert p["quote_bounds"]["provider"] == "debridge" and p["quote_bounds"]["native_fee_raw"] == str(FIX_FEE)
        assert p["quote"]["receive_minimum"] == 0.03 and p["quote"]["native_fee_raw"] == str(FIX_FEE)
        q = seen[-1]
        assert q["srcChainId"] == str(dln_chain_id("base")) and q["dstChainId"] == "56"
        assert q["srcChainTokenIn"] == BASE_USDC and q["dstChainTokenOut"] == EVM_NATIVE
        assert q["dstChainTokenOutRecipient"] == dst["address"] == q["dstChainOrderAuthorityAddress"]
        assert q["srcAllowedCancelBeneficiary"] == src["address"] and q["prependOperatingExpenses"] == "false"

        r = await client.post("/bridge", json={**body, "confirm": True, "quote_approval": pf["quote_approval"],
                                               "preflight_hash": pf["preflight_hash"], "idempotency_key": "dln-1"}, headers=h)
        assert r.status_code == 200, r.text
        job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
        assert job["status"] == "confirmed", job
        assert [t["kind"] for t in job["transactions"]] == ["bridge_approve", "bridge"]
        approve, deposit = base.sent[-2], base.sent[-1]
        assert approve["to"].lower() == BASE_USDC.lower() and approve["data"][:4] == bytes.fromhex("095ea7b3")
        assert deposit["to"].lower() == DLN and deposit["value"] == FIX_FEE  # only DLN's flat fee in ETH
        st = (await client.get(f"/bridge/{job['id']}", headers=h)).json()
        assert st["request_id"] == "debridge:0xorder1" and st["route_status"] == "success"
    finally:
        app.dependency_overrides.pop(get_executor, None)


async def test_relay_stays_first_when_it_can_quote(client, fake_rpc):
    seen: list = []
    try:
        h, src, dst, _ = await setup(client, fake_rpc, providers(None, seen))
        pf = (await client.post("/bridge", json={"from_wallet_id": src["id"], "to_wallet_id": dst["id"], "amount": 20},
                                headers=h)).json()
        assert pf["preflight"]["provider"] == "Relay (relay.link)" and seen == []
    finally:
        app.dependency_overrides.pop(get_executor, None)


async def test_a_higher_fee_at_confirmation_is_refused(client, fake_rpc, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "route_poll_seconds", 0)
    seen: list = []
    routes = providers("Relay is down", seen)
    try:
        h, src, dst, base = await setup(client, fake_rpc, routes)
        body = {"from_wallet_id": src["id"], "to_wallet_id": dst["id"], "amount": 20}
        pf = (await client.post("/bridge", json=body, headers=h)).json()
        routes.by_id["debridge"]._client = httpx.AsyncClient(transport=dln_mock([], ["Fulfilled"], fix_fee=FIX_FEE * 3,
                                                                                 value=FIX_FEE * 3))
        r = await client.post("/bridge", json={**body, "confirm": True, "quote_approval": pf["quote_approval"],
                                               "preflight_hash": pf["preflight_hash"], "idempotency_key": "dln-2"}, headers=h)
        assert r.status_code == 400 and "fee went up" in r.json()["detail"]
        assert base.sent == []
    finally:
        app.dependency_overrides.pop(get_executor, None)


async def test_debridge_quotes_that_misdirect_or_overcharge_are_refused():
    kw = dict(user="0x" + "aa" * 20, recipient="0x" + "bb" * 20, from_network="base", to_network="bsc",
              from_currency=BASE_USDC, to_currency=EVM_NATIVE, amount=20 * 10**6, slippage_pct=1)

    async def quote(**mock):
        return await DeBridge("https://dln.test", client=httpx.AsyncClient(transport=dln_mock([], ["Created"], **mock))).quote(**kw)

    for mock, reason in [({"recipient_in_tx": False}, "doesn't pay the selected wallet"),
                         ({"value": FIX_FEE + 1}, "more native coin than quoted"),
                         ({"amount_override": 21 * 10**6}, "changed the amount")]:
        try:
            await quote(**mock)
        except AdapterError as e:
            assert reason in str(e), (mock, e)
        else:
            raise AssertionError(f"accepted {mock}")
    with pytest.raises(AdapterError, match="between chains"):
        await DeBridge("https://dln.test").quote(**{**kw, "to_network": "base"})
    q = await quote()
    assert [s["id"] for s in q["steps"]] == ["approve", "deposit"] and q["nativeFeeRaw"] == str(FIX_FEE)


async def test_solana_order_transaction_gets_a_fresh_blockhash_and_spend_bounds(fake_rpc):
    from app.models import Wallet
    from app.services.routing import send_item

    signer = get_signer()
    ref = await signer.create_wallet("u1", "solana", "W")
    w = Wallet(user_id="u1", network="solana", address=ref.address, kind="managed", signer_ref=ref.signer_ref)
    payer = Pubkey.from_string(w.address)
    recipient = "0x" + "bb" * 20
    ix = Instruction(SYSTEM_PROGRAM, b"\x09" + bytes.fromhex(recipient[2:]), [AccountMeta(payer, True, True)])
    stale = build_unsigned(payer, [ix], str(Hash.new_unique()))
    q = DeBridge.normalize({"orderId": "o1", "fixFee": "15000000", "tx": {"data": "0x" + stale.hex()},
                            "estimation": {"srcChainTokenIn": {"amount": "1000000"},
                                           "dstChainTokenOut": {"address": EVM_NATIVE, "chainId": 56, "decimals": 18,
                                                                "amount": "1", "recommendedAmount": "1"}}},
                           user=w.address, recipient=recipient, from_network="solana", to_network="bsc",
                           from_currency=stable_for("solana")[1], amount=1_000_000)
    item = q["steps"][0]["items"][0]["data"]
    res = await send_item(Executor(fake_rpc, signer), w, item, from_asset="stable", amount_raw=1_000_000,
                          native_fee_raw=15_000_000)
    assert res.status == "confirmed", res
    sent = VersionedTransaction.from_bytes(fake_rpc.sent[-1])
    assert str(sent.message.recent_blockhash) == str(Hash.default())  # replaced with the RPC's latest
    assert str(sent.message.account_keys[0]) == w.address

    other = Keypair().pubkey()
    foreign = build_unsigned(other, [Instruction(SYSTEM_PROGRAM, b"\x09" + bytes.fromhex(recipient[2:]),
                                                 [AccountMeta(other, True, True)])], str(Hash.default()))
    res = await send_item(Executor(fake_rpc, signer), w, {"serializedTx": foreign.hex()}, from_asset="stable",
                          amount_raw=1_000_000)
    assert res.status == "failed" and "paid by another wallet" in res.error
    res = await send_item(Executor(fake_rpc, signer), w, {"serializedTx": stale.hex()})
    assert res.status == "failed" and "needs approved bounds" in res.error


async def test_a_relay_response_cannot_grant_itself_a_native_fee():
    """Review H1: only Helm's deBridge adapter may set a flat fee; a Relay quote's own nativeFeeRaw is ignored."""
    from app.services.quote_approval import relay_bounds
    from app.services.routing import check_route_call
    from app.services.evm_exec import EvmCall

    def evil(req: httpx.Request):
        return httpx.Response(200, json={"nativeFeeRaw": str(5 * 10**18), "helmFeeBps": 0, "requestId": "r",
                                         "steps": [{"id": "deposit", "kind": "transaction", "items": []}],
                                         "details": {"currencyOut": {"currency": {"chainId": 56, "address": EVM_NATIVE},
                                                                     "minimumAmount": "1"}}})
    relay = Relay("https://relay.test", client=httpx.AsyncClient(transport=httpx.MockTransport(evil)))
    q = await relay.quote(user="0x" + "aa" * 20, recipient="0x" + "aa" * 20, from_network="base", to_network="bsc",
                          from_currency=BASE_USDC, to_currency=EVM_NATIVE, amount=10**6, slippage_pct=1)
    assert q["nativeFeeRaw"] == "0"
    forged = {**q, "nativeFeeRaw": str(5 * 10**18)}  # even if something set it later
    assert relay_bounds(forged, destination_asset=EVM_NATIVE, destination_network="bsc")["native_fee_raw"] == "0"
    step = EvmCall(to="0x" + "cd" * 20, data=b"\x01\x02\x03\x04", value=5 * 10**18)
    assert "native coin" in check_route_call(step, "0x" + "aa" * 20, BASE_USDC, 10**6)


async def test_malformed_debridge_responses_are_refused_cleanly():
    """Review L2: bad hex or a missing tx.to is an AdapterError (a warning), never a 500."""
    base = {"orderId": "o", "fixFee": "0", "estimation": {"srcChainTokenIn": {"amount": "1"}, "dstChainTokenOut": {}}}
    kw = dict(user="0x" + "aa" * 20, recipient="0x" + "bb" * 20, from_network="base", to_network="bsc",
              from_currency=BASE_USDC, amount=1)
    for tx in ({"data": "0xzz", "to": DLN}, {"data": "0x" + "bb" * 20}):
        with pytest.raises(AdapterError, match="malformed|doesn't pay"):
            DeBridge.normalize({**base, "tx": tx}, **kw)


async def test_debridge_is_off_unless_configured(client):
    from app.services.executor import Executor as Ex

    assert [p.id for p in Ex(None, get_signer()).relay.providers] == ["relay"]
