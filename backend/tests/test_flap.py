"""Flap adapter: CREATE2 vanity salts, calldata encoding, dry-run checks, trading, and the full route flow."""

import json

import httpx
import pytest
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak, to_checksum_address

from app.chains import registry
from app.chains.base import CreateRequest, EvmTradeRequest
from app.chains.evm import EvmRPCError
from app.chains.flap import (
    FLAP_CHAINS, NEW_TOKEN_V6_TYPES, NEW_TOKEN_V7_TYPES, NEW_TOKEN_V7, STANDARD_V3_BSC, ZERO, FlapAdapter, create2_address, grind_salt, proxy_init_code_hash, selector,
)
from app.deps import get_executor
from app.main import app
from app.services.executor import Executor
from app.signing import get_signer

from .conftest import signup
from .test_evm_wallets import ETH, FakeEvm

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def upload_transport(seen: list):
    def handler(req: httpx.Request):
        seen.append(req.read())
        return httpx.Response(200, json={"data": {"create": "bafyTESTCID"}})
    return httpx.MockTransport(handler)


class FlapNode(FakeEvm):
    """A fake chain with just enough of Flap's Portal: newTokenV6 (as eth_call), quotes, ERC-20 reads."""

    def __init__(self, network="bsc", disabled_standard=False):
        super().__init__(network, eip1559=network != "bsc")
        self.cfg = FLAP_CHAINS[network]
        self.disabled_standard = disabled_standard
        self.allowance = 0
        self.tokens: dict[str, int] = {}
        self.calls: list[str] = []

    async def estimate_gas(self, tx):
        return 300_000

    async def call(self, method, params):
        assert method == "eth_call"
        tx = params[0]
        data = bytes.fromhex(tx["data"][2:])
        sel = data[:4]
        self.calls.append(sel.hex())
        if sel == selector(NEW_TOKEN_V7):
            p, = abi_decode(NEW_TOKEN_V7_TYPES, data[4:])
            if self.disabled_standard:
                raise EvmRPCError("execution reverted", "0xac5f6092")
            assert p[5] == 3 and p[17] == 7 and p[18][0][0:2] == (2, 10_000)
            addr = create2_address(self.cfg["portal"], p[4], proxy_init_code_hash(STANDARD_V3_BSC))
            return "0x" + abi_encode(["address"], [addr]).hex()
        if sel == selector("newTokenV6(" + NEW_TOKEN_V6_TYPES[0] + ")"):
            (p,) = abi_decode(NEW_TOKEN_V6_TYPES, data[4:])
            taxed = p[25] == 6
            if not taxed and self.disabled_standard:
                raise EvmRPCError("execution reverted", "0xac5f6092")
            impl = self.cfg["tax_impl"] if taxed else self.cfg["standard_impls"][0]
            addr = create2_address(self.cfg["portal"], p[4], proxy_init_code_hash(impl))
            return "0x" + abi_encode(["address"], [addr]).hex()
        if sel == selector("quoteExactInput((address,address,uint256))"):
            (q,) = abi_decode(["(address,address,uint256)"], data[4:])
            return hex(q[2] * 1000)
        raise AssertionError(f"unexpected call {sel.hex()}")

    async def erc20_allowance(self, token, owner, spender):
        return self.allowance

    async def erc20_balance(self, token, owner):
        return self.tokens.get(owner.lower(), 0)

    async def erc20_decimals(self, token):
        return (await self.erc20_meta(token))["decimals"]

    async def erc20_meta(self, token):
        return {"name": "T", "symbol": "T", "decimals": 18, "total_supply": 10**9 * 10**18}

    async def send_raw(self, raw_hex):
        h = await super().send_raw(raw_hex)
        d = self.sent[-1]
        if d["data"][:4] == selector("swapExactInput((address,address,uint256,uint256,bytes))"):
            q, = abi_decode(["(address,address,uint256,uint256,bytes)"], d["data"][4:])
            if int(q[1], 16) == 0:
                self.balances[d["from"].lower()] += q[2] * 1000
        return h


def req(**kw):
    base = dict(name="Helm Cat", symbol="HCAT", description="gm", image_bytes=PNG, socials={"twitter": "https://x.com/h"},
                dev_pubkey="0x" + "ab" * 20, dev_buy_sol=0.01)
    base.update(kw)
    return CreateRequest(**base)


def test_create2_matches_eip1014_vector_and_salt_grinding():
    # EIP-1014 example 1: deployer 0x0, salt 0, init code 0x00
    assert create2_address("0x" + "00" * 20, b"\x00" * 32, keccak(b"\x00")) == "0x4D1A2e2bB4F88F0250f26Ffff098B0b30B26BF38"
    cfg = FLAP_CHAINS["robinhood"]
    salt, addr = grind_salt(cfg["portal"], cfg["standard_impls"][0], "8888")
    assert addr.lower().endswith("8888")
    assert addr == create2_address(cfg["portal"], salt, proxy_init_code_hash(cfg["standard_impls"][0]))


async def test_prepare_create_standard_token_dry_runs_and_encodes():
    node = FlapNode("bsc")
    seen = []
    a = FlapAdapter("flap-sh", "bsc", node, client=httpx.AsyncClient(transport=upload_transport(seen)))
    plan = await a.prepare_create(req())
    assert plan.token.lower().endswith("8888") and len(plan.steps) == 1
    step = plan.steps[0]
    assert step.to == FLAP_CHAINS["bsc"]["portal"] and step.value == ETH // 100
    (p,) = abi_decode(NEW_TOKEN_V7_TYPES, step.data[4:])
    assert p[0] == "Helm Cat" and p[2] == "bafyTESTCID" and p[7] == ETH // 100
    assert p[5] == 3 and p[17] == 7 and p[12] == p[13] == 0 and p[16] == ZERO
    assert p[18][0] == (2, 10_000, ZERO, ZERO, 10_000 * 10**18)
    assert "holders" in plan.notes["token_type"]
    # the upload is a GraphQL multipart request carrying the metadata and the image
    body = seen[0]
    assert b'"variables.file"' in body and b"MetadataInput" in body and PNG in body and b"https://x.com/h" in body


async def test_tax_token_uses_7777_and_commission_receiver():
    node = FlapNode("robinhood")
    a = FlapAdapter("flap-sh-robinhood", "robinhood", node, commission_receiver="0x" + "cd" * 20,
                    client=httpx.AsyncClient(transport=upload_transport([])))
    plan = await a.prepare_create(req(options={"tax_rate_pct": 3}))
    (p,) = abi_decode(NEW_TOKEN_V6_TYPES, plan.steps[0].data[4:])
    assert plan.token.lower().endswith("7777") and p[25] == 6 and p[14] == p[15] == 300 and p[18] == 10_000
    assert p[24].lower() == "0x" + "cd" * 20 and "commission" in plan.notes
    assert p[5] == 1  # Tax Token V3 must migrate through V2, not the standard V3 path.


async def test_base_tax_launch_uses_official_addresses_and_frozen_receiver():
    seen = []
    a = FlapAdapter("flap-sh-base", "base", FlapNode("base"), commission_receiver="0x" + "cd" * 20,
                    client=httpx.AsyncClient(transport=upload_transport(seen)))
    tax = next(f for f in a.options_schema()["fields"] if f["key"] == "tax_rate_pct")
    assert 0 not in tax["options"] and tax["default"] == 1
    with pytest.raises(Exception, match="requires a tax token"):
        await a.prepare_create(req())
    assert not seen  # Rejected before an upload or chain call.
    frozen = "0x" + "ef" * 20
    plan = await a.prepare_create(req(options={"tax_rate_pct": 1, "flap_commission_receiver": frozen}))
    (p,) = abi_decode(NEW_TOKEN_V6_TYPES, plan.steps[0].data[4:])
    assert plan.steps[0].to == "0x0000BC1c4fD15Dd79029AF8F5d77D68ae4490000"
    assert p[5] == 1 and p[25] == 6 and p[14] == p[15] == 100
    assert p[24].lower() == frozen and plan.notes["commission_receiver"].lower() == frozen
    assert plan.token.lower().endswith("7777")


async def test_disabled_standard_tokens_give_a_clear_error_and_bad_tax_rejected():
    a = FlapAdapter("flap-sh", "bsc", FlapNode("bsc", disabled_standard=True),
                    client=httpx.AsyncClient(transport=upload_transport([])))
    try:
        await a.prepare_create(req())
        raise AssertionError("should fail")
    except Exception as e:
        assert "Choose a tax rate" in str(e)
    try:
        await a.prepare_create(req(options={"tax_rate_pct": 2}))
        raise AssertionError("should fail")
    except Exception as e:
        assert "must be one of" in str(e)


async def test_trades_quote_slippage_and_approve_only_when_needed():
    node = FlapNode("bsc")
    a = FlapAdapter("flap-sh", "bsc", node)
    token = "0x" + "88" * 20
    buy = await a.prepare_trade(EvmTradeRequest(token, "0x" + "ab" * 20, "buy", 10**15, 10))
    assert [s.label for s in buy.steps] == ["buy"] and buy.steps[0].value == 10**15
    (x,) = abi_decode(["(address,address,uint256,uint256,bytes)"], buy.steps[0].data[4:])
    assert x[0] == ZERO and x[1].lower() == token and x[3] == 10**18 * 9 // 10
    sell = await a.prepare_trade(EvmTradeRequest(token, "0x" + "ab" * 20, "sell", 500, 10))
    assert [s.label for s in sell.steps] == ["approve", "sell"] and sell.steps[0].to == token
    node.allowance = 2**256 - 1
    sell = await a.prepare_trade(EvmTradeRequest(token, "0x" + "ab" * 20, "sell", 500, 10))
    assert [s.label for s in sell.steps] == ["sell"]


async def test_flap_launch_and_trade_through_the_api(client, fake_rpc):
    node = FlapNode("bsc")
    registry._cache["flap-sh"] = FlapAdapter("flap-sh", "bsc", node, client=httpx.AsyncClient(transport=upload_transport([])))
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"bsc": node})
    try:
        h = await signup(client)
        pads = {p["id"]: p for p in (await client.get("/launchpads")).json()}
        assert pads["flap-sh"]["status"] == "available" and pads["flap-sh"]["network"] == "bsc"
        assert pads["flap-sh-robinhood"]["network"] == "robinhood"
        public_fields = (await client.get("/launchpads/flap-sh/options")).json()["fields"]
        assert next(f for f in public_fields if f["key"] == "tax_rate_pct")["options"] == [0]
        ws = (await client.post("/wallets", json={"network": "bsc", "count": 2}, headers=h)).json()
        dev, holder = ws
        for w in ws:
            node.balances[w["address"].lower()] = ETH
        import base64
        r = await client.post("/launches", json={"launchpad": "flap-sh", "name": "Helm Cat", "symbol": "hcat",
                                                 "dev_wallet_id": dev["id"], "dev_buy_sol": 0.01,
                                                 "image_b64": base64.b64encode(PNG).decode(),
                                                 "options": {"tax_rate_pct": 7}}, headers=h)
        assert r.status_code == 400  # 7% isn't a Flap tax rate
        r = await client.post("/launches", json={"launchpad": "flap-sh", "name": "Helm Cat", "symbol": "hcat",
                                                 "dev_wallet_id": dev["id"], "dev_buy_sol": 0.01,
                                                 "image_b64": base64.b64encode(PNG).decode()}, headers=h)
        assert r.status_code == 201, r.text
        lid = r.json()["id"]
        pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
        assert pf["preflight"]["network"] == "bsc" and "standard token" in pf["preflight"]["token_type"]
        assert not pf["preflight"]["warnings"], pf
        r = await client.post(f"/launches/{lid}/deploy", json={"confirm": True, "preflight_hash": pf["preflight_hash"],
                                                              "idempotency_key": "d1"}, headers=h)
        job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
        assert job["status"] == "confirmed", job
        launch = (await client.get(f"/launches/{lid}", headers=h)).json()
        assert launch["status"] == "live" and launch["mint_address"].lower().endswith("8888")

        await client.post(f"/launches/{lid}/wallets", json={"wallet_ids": [holder["id"]]}, headers=h)
        body = {"side": "buy", "wallet_ids": [holder["id"]], "amount": 0.001, "mode": "stagger", "stagger_ms": 0}
        pf = (await client.post(f"/launches/{lid}/trade", json=body, headers=h)).json()
        assert pf["preflight"]["amount_unit"] == "BNB per wallet"
        r = await client.post(f"/launches/{lid}/trade", json={**body, "confirm": True, "preflight_hash": pf["preflight_hash"],
                                                              "idempotency_key": "t1"}, headers=h)
        job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
        assert job["status"] == "confirmed" and [t["kind"] for t in job["transactions"]] == ["buy", "trade_fee"], job

        # sell 50%: approve first, then sell
        node.tokens[holder["address"].lower()] = 10**21
        body = {"side": "sell", "wallet_ids": [holder["id"]], "amount": 50, "mode": "parallel", "stagger_ms": 0}
        pf = (await client.post(f"/launches/{lid}/trade", json=body, headers=h)).json()
        r = await client.post(f"/launches/{lid}/trade", json={**body, "confirm": True, "preflight_hash": pf["preflight_hash"],
                                                              "idempotency_key": "t2"}, headers=h)
        job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
        assert job["status"] == "confirmed" and sorted(t["kind"] for t in job["transactions"]) == ["sell", "sell_approve", "trade_fee"], job

        ws_view = (await client.get(f"/launches/{lid}/workspace", headers=h)).json()
        assert ws_view["rpc_error"] is None, ws_view["rpc_error"]
        assert ws_view["transparency"]["total_supply"] == 10**9
        r = await client.post(f"/launches/{lid}/claim-fees", json={"wallet_id": dev["id"]}, headers=h)
        assert r.status_code == 200  # preview works; the claim itself explains there's nothing to claim
    finally:
        registry._cache.pop("flap-sh", None)
