"""Pons adapter: launch via factory or router, token address from logs, curve trading guards, fee claims."""

import base64

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode

from app.chains import registry
from app.chains.base import ClaimRequest, CreateRequest, EvmTradeRequest
from app.chains.pons import (
    FACTORY, FEE_ESCROW, LAUNCH_AND_BUY, LAUNCH_TOKEN, PARAMS_T, ROUTER, TOPIC_TOKEN_LAUNCHED, ZERO, PonsAdapter, selector,
)
from app.deps import get_executor
from app.main import app
from app.services.executor import Executor
from app.signing import get_signer

from .conftest import signup
from .test_evm_wallets import ETH, FakeEvm

TOKEN = "0x" + "70" * 20
CURVE = "0x" + "c0" * 20
FEE = 5 * 10**14
PNG = b"\x89PNG\r\n\x1a\n" + b"1" * 32


class PonsNode(FakeEvm):
    def __init__(self):
        super().__init__("robinhood")
        self.snipe = 0
        self.graduated = 0
        self.pending_fees = 0
        self.allowance = 0
        self.tokens: dict[str, int] = {}
        self.launch_calls: list[tuple] = []

    async def estimate_gas(self, tx):
        return 250_000

    async def call(self, method, params):
        tx = params[0]
        to = tx["to"].lower()
        data = bytes.fromhex(tx["data"][2:])
        sel, body = data[:4], data[4:]
        value = int(tx.get("value", "0x0"), 16)
        w = lambda n: "0x" + abi_encode(["uint256"], [n]).hex()  # noqa: E731
        if to == FACTORY.lower():
            if sel == selector("launchFee()"):
                return w(FEE)
            if sel == selector("previewLaunchEconomics(uint256,address)"):
                return "0x" + "ab" * 32
            if sel == selector(LAUNCH_TOKEN):
                assert value == FEE
                self.launch_calls.append(("factory", abi_decode([PARAMS_T, "uint256", "address"], body)))
                return "0x" + abi_encode(["address", "address"], [CURVE, TOKEN]).hex()  # order doesn't matter to Helm
            if sel == selector("getLaunchedToken(address)"):
                (addr,) = abi_decode(["address"], body)
                exists = addr.lower() == TOKEN
                return "0x" + abi_encode(
                    ["(address,address,address,address,address,uint256,uint24,int24,uint16,bool,uint8,uint256,uint256,uint256,bool)"],
                    [(addr, CURVE, ZERO, ZERO, ZERO, 0, 0, 0, 0, False, 1 if self.graduated else 0, 0, 0, 0, exists)]).hex()
        if to == ROUTER.lower() and sel == selector(LAUNCH_AND_BUY):
            args = abi_decode([PARAMS_T, "uint256", "address", "uint256", "uint256", "address", "address[]"], body)
            assert value == FEE + args[3] and args[6] == ()
            self.launch_calls.append(("router", args))
            return "0x" + abi_encode(["address", "address", "uint256"], [TOKEN, CURVE, args[3] * 1000]).hex()
        if to == CURVE.lower():
            if sel in (selector("graduated()"), selector("readyToGraduate()")):
                return w(self.graduated)
            if sel == selector("currentSnipeTaxBps(address)"):
                return w(self.snipe)
            if sel == selector("buy(uint256,uint256,address)"):
                return w(abi_decode(["uint256"], body[:32])[0] * 1000)
            if sel == selector("sell(uint256,uint256,address)"):
                assert self.allowance >= abi_decode(["uint256"], body[:32])[0], "sell simulated before approve"
                return w(abi_decode(["uint256"], body[:32])[0] // 1000)
        if to == FEE_ESCROW.lower() and sel == selector("balanceOf(address)"):
            return w(self.pending_fees)
        raise AssertionError(f"unexpected call to {to} {sel.hex()}")

    async def erc20_allowance(self, token, owner, spender):
        return self.allowance

    async def erc20_balance(self, token, owner):
        return self.tokens.get(owner.lower(), 0)

    async def erc20_decimals(self, token):
        return (await self.erc20_meta(token))["decimals"]

    async def erc20_meta(self, token):
        return {"name": "P", "symbol": "P", "decimals": 18, "total_supply": 10**27}

    async def send_raw(self, raw_hex):
        h = await super().send_raw(raw_hex)
        d = self.sent[-1]
        if d["to"].lower() == TOKEN:  # an approve
            self.allowance = 2**255
        return h

    async def receipt(self, h):
        logs = [{"address": FACTORY, "topics": [TOPIC_TOKEN_LAUNCHED, "0x" + "00" * 12 + TOKEN[2:],
                                                 "0x" + "00" * 12 + CURVE[2:], "0x" + "00" * 32]}]
        return {"status": "0x1", "gasUsed": hex(21_000), "effectiveGasPrice": hex(10**9), "logs": logs}


def req(**kw):
    base = dict(name="Hood Cat", symbol="HOOD", description="gm", image_bytes=None, socials={"website": "https://h.xyz"},
                dev_pubkey="0x" + "ab" * 20, dev_buy_sol=0.0, options={})
    base.update(kw)
    return CreateRequest(**base)


async def test_launch_without_dev_buy_uses_factory_and_reads_token_from_logs():
    node = PonsNode()
    plan = await PonsAdapter(node).prepare_create(req(options={"creator_tax_pct": 2, "logo_url": "https://api.x/img"}))
    step = plan.steps[0]
    assert step.to == FACTORY and step.value == FEE and plan.token is None
    params = node.launch_calls[0][1][0]
    assert params[2] == "https://api.x/img" and params[4][3] == "https://h.xyz" and params[6] == 200
    assert params[5].lower() == "0x" + "ab" * 20 and params[8] == bytes.fromhex("ab" * 32)
    logs = (await node.receipt("x"))["logs"]
    assert (await plan.resolve_token(logs)).lower() == TOKEN
    assert (await plan.resolve_token([])).lower() == TOKEN  # falls back to asking the factory


async def test_launch_with_dev_buy_uses_router_with_slippage_floor_and_no_extra_exemptions():
    node = PonsNode()
    plan = await PonsAdapter(node).prepare_create(req(dev_buy_sol=0.01, slippage_pct=10))
    step = plan.steps[0]
    assert step.to == ROUTER and step.value == FEE + ETH // 100 and plan.token.lower() == TOKEN
    args = abi_decode([PARAMS_T, "uint256", "address", "uint256", "uint256", "address", "address[]"], step.data[4:])
    assert args[4] == ETH // 100 * 1000 * 9 // 10 and args[6] == ()


async def test_trading_guards_and_lazy_sell():
    node = PonsNode()
    a = PonsAdapter(node)
    wallet = "0x" + "ab" * 20
    buy = await a.prepare_trade(EvmTradeRequest(TOKEN, wallet, "buy", 10**15, 10))
    assert buy.steps[0].to.lower() == CURVE and buy.steps[0].value == 10**15
    assert abi_decode(["uint256", "uint256", "address"], buy.steps[0].data[4:])[1] == 10**18 * 9 // 10
    node.snipe = 9900
    try:
        await a.prepare_trade(EvmTradeRequest(TOKEN, wallet, "buy", 10**15, 10))
        raise AssertionError("snipe tax should block")
    except Exception as e:
        assert "anti-snipe" in str(e)
    node.snipe = 0
    sell = await a.prepare_trade(EvmTradeRequest(TOKEN, wallet, "sell", 5000, 10))
    assert [s.label for s in sell.steps] == ["approve", "sell"]
    assert abi_decode(["address", "uint256"], sell.steps[0].data[4:])[1] == 5000  # exact approval
    node.allowance = 5000
    data = await sell.steps[1].build()
    assert abi_decode(["uint256", "uint256", "address"], data[4:])[:2] == (5000, 4)  # 5 * 0.9
    node.graduated = 1
    try:
        await a.prepare_trade(EvmTradeRequest(TOKEN, wallet, "buy", 10**15, 10))
        raise AssertionError("graduated should block")
    except Exception as e:
        assert "Uniswap V4" in str(e)


async def test_claims():
    node = PonsNode()
    a = PonsAdapter(node)
    try:
        await a.prepare_claim(ClaimRequest(wallet_pubkey="0x" + "ab" * 20))
        raise AssertionError
    except Exception as e:
        assert "no Pons creator fees" in str(e)
    node.pending_fees = 10**15
    plan = await a.prepare_claim(ClaimRequest(wallet_pubkey="0x" + "ab" * 20))
    assert plan.steps[0].to == FEE_ESCROW and plan.steps[0].data == selector("claim()")
    # An old stored setting must never deduct a launch-specific share from wallet-wide rewards.
    plan = await a.prepare_claim(ClaimRequest(wallet_pubkey="0x" + "ab" * 20,
        options={"helm_creator_share": {"bps": 600, "recipient": "0x" + "cd" * 20}}))
    assert len(plan.steps) == 1 and plan.steps[0].value == 0


async def test_pons_through_the_api(client, fake_rpc):
    node = PonsNode()
    registry._cache["pons"] = PonsAdapter(node)
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"robinhood": node})
    try:
        h = await signup(client)
        pads = {p["id"]: p for p in (await client.get("/launchpads")).json()}
        assert pads["pons"]["status"] == "available"
        dev, holder = (await client.post("/wallets", json={"network": "robinhood", "count": 2}, headers=h)).json()
        for w in (dev, holder):
            node.balances[w["address"].lower()] = ETH
        r = await client.post("/launches", json={"launchpad": "pons", "name": "Hood Cat", "symbol": "hood",
                                                 "dev_wallet_id": dev["id"], "image_b64": base64.b64encode(PNG).decode(),
                                                 "options": {"creator_tax_pct": 1}}, headers=h)
        assert r.status_code == 201, r.text
        lid = r.json()["id"]
        assert (await client.get(f"/public/launch-images/{lid}")).status_code == 404  # not public before deploy
        pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
        assert pf["preflight"]["launch_fee"].startswith("0.0005 ETH") and pf["preflight"]["creator_tax"].startswith("1%")
        r = await client.post(f"/launches/{lid}/deploy", json={"confirm": True, "preflight_hash": pf["preflight_hash"],
                                                              "idempotency_key": "p1"}, headers=h)
        job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
        assert job["status"] == "confirmed", job
        launch = (await client.get(f"/launches/{lid}", headers=h)).json()
        assert launch["status"] == "live" and launch["mint_address"].lower() == TOKEN
        params = node.launch_calls[-1][1][0]
        assert params[2].endswith(f"/public/launch-images/{lid}")
        img = await client.get(f"/public/launch-images/{lid}")
        assert img.status_code == 200 and img.content == PNG and img.headers["content-type"] == "image/png"

        await client.post(f"/launches/{lid}/wallets", json={"wallet_ids": [holder["id"]]}, headers=h)
        node.tokens[holder["address"].lower()] = 10**21
        body = {"side": "sell", "wallet_ids": [holder["id"]], "amount": 100, "mode": "stagger", "stagger_ms": 0}
        pf = (await client.post(f"/launches/{lid}/trade", json=body, headers=h)).json()
        r = await client.post(f"/launches/{lid}/trade", json={**body, "confirm": True, "preflight_hash": pf["preflight_hash"],
                                                              "idempotency_key": "p2"}, headers=h)
        job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
        assert job["status"] == "confirmed" and [t["kind"] for t in job["transactions"]] == ["sell_approve", "sell"], job

        node.pending_fees = 10**15
        pf = (await client.post(f"/launches/{lid}/claim-fees", json={"wallet_id": dev["id"]}, headers=h)).json()
        r = await client.post(f"/launches/{lid}/claim-fees", json={"wallet_id": dev["id"], "confirm": True,
                                                                  "preflight_hash": pf["preflight_hash"], "idempotency_key": "p3"}, headers=h)
        job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
        assert job["status"] == "confirmed", job
    finally:
        registry._cache.pop("pons", None)
