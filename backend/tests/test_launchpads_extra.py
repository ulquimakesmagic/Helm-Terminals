"""SunPump (TRON), Meme.Cooking (NEAR) and Tolly (Arc) launches through Helm's launch flow, with fakes for each
chain. Checks what is signed (the exact contract call, value and arguments), what is shown, and how results land."""

import base64
import hashlib
import json
import time

import httpx
import pytest
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak

from app.chains import extra_pads as ep
from app.chains import near, tron
from app.chains.base import AdapterError
from app.config import get_settings
from app.services import extra_chains as xc
from app.services import extra_launch

from .conftest import signup
from .test_extra_chains import FakeNear, FakeTron, _pb

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
SP = ep.SUNPUMP["tron"]
TOKEN_T = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


@pytest.fixture
def pads(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "extra_networks", "tron,near,arc")
    monkeypatch.setattr(s, "allow_mainnet_extra_networks", True)
    monkeypatch.setattr(s, "public_api_url", "https://api.helm.test")
    from app.services import platform_config as pc
    original = pc.treasury_for
    defaults = {"tron": "TLa2f6VPqDgRE67v1736s7bJ8Ray5wYjU7", "near": "bc" * 32}
    monkeypatch.setattr(pc, "treasury_for", lambda network: original(network) or defaults.get(network, ""))
    xc._clients.clear()
    yield
    xc._clients.clear()


def tron_call_tx(owner: str, contract: str, data: bytes, call_value: int, fee_limit: int) -> dict:
    inner = _pb(1, tron.decode_address(owner)) + _pb(2, tron.decode_address(contract))
    if call_value:
        inner += _pb(3, call_value)
    inner += _pb(4, data)
    contract_pb = _pb(1, 31) + _pb(2, _pb(1, b"type.googleapis.com/protocol.TriggerSmartContract") + _pb(2, inner))
    raw = (_pb(1, bytes.fromhex("1a2b")) + _pb(4, bytes.fromhex("0102030405060708")) + _pb(8, 4102444800000)
           + _pb(11, contract_pb) + _pb(14, 4102444740000) + _pb(18, fee_limit))
    return {"txID": hashlib.sha256(raw).hexdigest(), "raw_data_hex": raw.hex(), "raw_data": {}, "visible": True}


class FakeSunPump(FakeTron):
    def __init__(self):
        super().__init__()
        self.accounts["TLa2f6VPqDgRE67v1736s7bJ8Ray5wYjU7"] = ep.TRX  # configured, activated fee treasury
        self.state, self.calls, self.sent = 1, [], []
        self.allowance = 0
        self.tamper = False

    async def _req(self, method, path, body=None, params=None, retries=None):
        if path == "/wallet/broadcasttransaction":
            contract = tron.pb_fields(bytes.fromhex(body["raw_data_hex"]))[11][0]
            fields = tron.pb_fields(contract)
            if fields[1][0] == 31:
                payload = tron.pb_fields(tron.pb_fields(fields[2][0])[2][0])
                data = payload.get(4, [b""])[0]
                if data[:4] == keccak(text="saleToken(address,uint256,uint256)")[:4]:
                    _, amount, _ = abi_decode(["address", "uint256", "uint256"], data[4:])
                    owner = tron.encode_address(payload[1][0])
                    self.accounts[owner] = self.accounts.get(owner, 0) + amount // 2000
        if path == "/wallet/triggerconstantcontract" and body["contract_address"] in (SP, TOKEN_T):
            sel, p = body["function_selector"], bytes.fromhex(body.get("parameter") or "")
            word = lambda *v: {"result": {"result": True}, "energy_used": 150_000,  # noqa: E731
                               "constant_result": [b"".join(x.to_bytes(32, "big") for x in v).hex()]}
            if sel == "LAUNCH_FEE()":
                return word(20 * ep.TRX)
            if sel == "getTokenState(address)":
                return word(self.state)
            if sel == "getTokenAmountByPurchaseWithFee(address,uint256)":
                return word(abi_decode(["address", "uint256"], p)[1] * 1000, 10_000)
            if sel == "getTrxAmountBySaleWithFee(address,uint256)":
                return word(abi_decode(["address", "uint256"], p)[1] // 2000, 5_000)
            if sel == "allowance(address,address)":
                return word(self.allowance)
            if sel == "balanceOf(address)":
                return word(10_000_000)
            if sel in ("name()", "symbol()", "decimals()"):
                return await super()._req(method, path, body, params)
            return word(0)  # a simulated write
        if path == "/wallet/triggersmartcontract" and body["contract_address"] in (SP, TOKEN_T):
            data = keccak(text=body["function_selector"])[:4] + bytes.fromhex(body["parameter"])
            if self.tamper:
                data += b"\x00"
            self.calls.append((body["function_selector"], body["call_value"]))
            return {"result": {"result": True}, "transaction": tron_call_tx(
                body["owner_address"], body["contract_address"], data, body["call_value"], body["fee_limit"])}
        if path == "/walletsolidity/gettransactioninfobyid":
            word = "0" * 24 + tron.decode_address(TOKEN_T)[1:].hex()
            creator = "0" * 24 + "11" * 20
            return {"id": body["value"], "receipt": {"result": "SUCCESS"},
                    "log": [  # TokenCreate(tokenAddress, tokenIndex, creator), nothing indexed, from the launchpad
                        {"address": "cc" * 20, "topics": [ep.SUNPUMP_TOKEN_CREATE], "data": creator + "0" * 64 + creator},
                        {"address": tron.decode_address(SP)[1:].hex(), "topics": [ep.SUNPUMP_TOKEN_CREATE],
                         "data": word + "0" * 63 + "5" + creator}]}
        return await super()._req(method, path, body, params)


async def _launch(client, h, pad, dev, **extra):
    body = {"name": "Helm Test", "symbol": "HLMT", "launchpad": pad, "dev_wallet_id": dev["id"],
            "image_b64": base64.b64encode(PNG).decode(), "socials": {"website": "https://helm.test"}, **extra}
    r = await client.post("/launches", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


async def _go(client, h, path, body, key):
    pf = await client.post(path, json=body, headers=h)
    assert pf.status_code == 200, pf.text
    r = await client.post(path, json={**body, "confirm": True, "preflight_hash": pf.json()["preflight_hash"],
                                      "idempotency_key": key}, headers=h)
    return pf.json(), r


async def test_new_launchpads_are_listed_with_their_setup_state(client, pads):
    listed = {p["id"]: p for p in (await client.get("/launchpads")).json()}
    assert listed["sunpump"]["network"] == "tron" and listed["sunpump"]["status"] == "available"
    assert listed["tolly"]["network"] == "arc" and listed["tolly"]["status"] == "available"
    assert listed["meme-cooking"]["status"] == "setup_required" and "PINATA_JWT" in listed["meme-cooking"]["reason"]


async def test_sunpump_launch_buy_and_sell(client, pads, monkeypatch):
    import asyncio

    async def nosleep(*_):
        return None

    monkeypatch.setattr(asyncio, "sleep", nosleep)
    t = FakeSunPump()
    t.accounts["x"] = 0
    xc._clients["tron"] = t
    h = await signup(client, main=False)
    dev = (await client.post("/wallets", json={"network": "tron", "count": 1}, headers=h)).json()[0]
    t.accounts[dev["address"]] = 500 * ep.TRX
    lid = (await _launch(client, h, "sunpump", dev, dev_buy_sol=50))["id"]
    pf, r = await _go(client, h, f"/launches/{lid}/deploy", {}, "d1")
    p = pf["preflight"]
    assert p["launch_fee_trx"] == 20 and p["value_sent_trx"] == 70 and p["contract"] == SP
    assert p["launch_fee_payment"]["usd"] == 0 and "energy_fee_trx_about" in p
    assert r.status_code == 200, r.text
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "confirmed", job
    assert t.calls[-1] == ("createAndInitPurchase(string,string)", 70 * ep.TRX)
    sig = t.broadcasts[-1]["signature"][0]
    assert len(bytes.fromhex(sig)) == 65
    launch = (await client.get(f"/launches/{lid}", headers=h)).json()
    assert launch["status"] == "live" and launch["mint_address"] == TOKEN_T
    # buy: minimum out is the live quote less slippage, taken again when sent
    pf, r = await _go(client, h, f"/launches/{lid}/trade",
                      {"side": "buy", "wallet_ids": [dev["id"]], "amount": 10, "slippage_pct": 5}, "b1")
    assert pf["preflight"]["per_wallet"][0] == {"side": "buy", "spend_trx": 10, "slippage_pct": 5}
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "confirmed", job
    assert t.calls[-1] == ("purchaseToken(address,uint256)", 10 * ep.TRX)
    sent = bytes.fromhex(tron.pb_fields(tron.pb_fields(tron.pb_fields(tron.pb_fields(bytes.fromhex(
        t.broadcasts[-2]["raw_data_hex"]))[11][0])[2][0])[2][0])[4][0].hex())
    tok, min_out = abi_decode(["address", "uint256"], sent[4:])
    assert min_out == 10 * ep.TRX * 1000 * 95 // 100
    # sell 50%: approve first, then saleToken
    pf, r = await _go(client, h, f"/launches/{lid}/trade",
                      {"side": "sell", "wallet_ids": [dev["id"]], "amount": 50, "slippage_pct": 5}, "s1")
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "confirmed", job
    assert [c[0] for c in t.calls[-2:]] == ["approve(address,uint256)", "saleToken(address,uint256,uint256)"]
    # a node that returns a different call than planned is refused before signing
    t.tamper = True
    n = len(t.broadcasts)
    _, r = await _go(client, h, f"/launches/{lid}/trade",
                     {"side": "buy", "wallet_ids": [dev["id"]], "amount": 1, "slippage_pct": 5}, "b2")
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "failed" and len(t.broadcasts) == n, job
    assert "not signing" in job["transactions"][0]["error"], job
    t.tamper = False
    # graduated tokens aren't traded on the curve
    t.state = 3
    r = await client.post(f"/launches/{lid}/trade", json={"side": "buy", "wallet_ids": [dev["id"]], "amount": 1}, headers=h)
    assert r.status_code == 400 and "graduated" in r.text
    ws = (await client.get(f"/launches/{lid}/workspace", headers=h)).json()
    assert ws["wallets"][0]["address"] == dev["address"] and "No chart" in ws["market_note"]


class FakeMemeCooking(FakeNear):
    def __init__(self):
        super().__init__()
        self.memes, self.deposits, self.registered_mc = {}, {}, set()

    async def call(self, method, params, resend=True):
        if method == "query" and params.get("request_type") == "call_function":
            args = json.loads(base64.b64decode(params["args_base64"]))
            m, acct = params["method_name"], params["account_id"]
            value = None
            if m == "storage_costs":
                value = {"account": str(10**22), "perMemeDeposit": str(10**23)}
            elif m == "storage_balance_of":
                value = {"total": "1"} if (acct, args["account_id"]) in self.registered_mc else None
            elif m == "create_meme_storage_cost":
                value = str(2 * 10**24)
            elif m == "get_meme":
                value = self.memes.get(args["meme_id"])
            elif m == "get_account":
                value = {"account_id": args["account_id"],
                         "deposits": [[k, str(v)] for k, v in self.deposits.items()], "income": [], "shitstar_claim": "0"}
            elif m == "ft_balance_of":
                value = str(10**24)
            else:
                return await super().call(method, params)
            return {"result": list(json.dumps(value).encode())}
        if method == "send_tx":
            self.sent.append(params)
            signed = base64.b64decode(params["signed_tx_base64"])
            if b"near_withdraw" in signed:
                import re
                match = re.search(rb'"amount"\s*:\s*"(\d+)"', signed)
                if match:
                    owner = next(iter(self.accounts))
                    self.accounts[owner] += int(match.group(1))
            if b"create_meme" in signed:
                self.memes[7] = {"id": 7, "end_timestamp_ms": str(int(time.time() * 1000) + 3_600_000)}
                return {"status": {"SuccessValue": base64.b64encode(b"7").decode()}, "receipts_outcome": []}
            return {"status": {"SuccessValue": ""}, "receipts_outcome": []}
        return await super().call(method, params)


def _pinata():
    pins = []

    def handler(req):
        pins.append(req.url.path)
        return httpx.Response(200, json={"IpfsHash": f"bafy{len(pins)}"})

    return ep.Pinata("jwt-test", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))), pins


async def test_meme_cooking_create_deposit_withdraw_and_claim(client, pads, monkeypatch):
    from app.chains import registry

    monkeypatch.setattr(get_settings(), "pinata_jwt", "jwt-test")
    pin, pins = _pinata()
    registry._cache["meme-cooking"] = ep.MemeCooking(pin)
    n = FakeMemeCooking()
    xc._clients["near"] = n
    h = await signup(client, main=False)
    dev = (await client.post("/wallets", json={"network": "near", "count": 1}, headers=h)).json()[0]
    n.accounts[dev["address"]] = 50 * 10**24
    lid = (await _launch(client, h, "meme-cooking", dev, dev_buy_sol=2,
                         options={"duration_h": 6, "soft_cap_near": 100, "hard_cap_near": 500}))["id"]
    pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
    assert "preflight" in pf, pf
    p = pf["preflight"]
    assert p["auction_hours"] == 6 and p["soft_cap_near"] == 100 and p["hard_cap_near"] == 500
    assert pins == []  # nothing is uploaded for a preview
    r = await client.post(f"/launches/{lid}/deploy", json={"confirm": True, "preflight_hash": pf["preflight_hash"],
                                                           "idempotency_key": "m1"}, headers=h)
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "confirmed", job
    assert len(pins) == 2
    created = base64.b64decode(n.sent[0]["signed_tx_base64"])
    assert b"storage_deposit" in created, created[:200]
    assert b"create_meme" in created
    assert b'"soft_cap":"' + str(100 * 10**24).encode() in created
    assert b"data:image/png;base64," in created and b'"reference":"bafy2"' in created and b"hard_cap" in created
    own = next(b for b in (base64.b64decode(x["signed_tx_base64"]) for x in n.sent) if b"near_deposit" in b)
    assert b"wrap.near" in own and b"near_deposit" in own and b'{\\"Deposit\\":{\\"meme_id\\":7}}' in own
    launch = (await client.get(f"/launches/{lid}", headers=h)).json()
    assert launch["status"] == "live" and launch["mint_address"] == "hlmt-7.meme-cooking.near"
    # withdraw half the deposit during the auction, then unwrap
    n.deposits[7] = 2 * 10**24
    pf, r = await _go(client, h, f"/launches/{lid}/trade",
                      {"side": "sell", "wallet_ids": [dev["id"]], "amount": 50}, "m2")
    assert pf["preflight"]["per_wallet"][0]["amount_near"] == 1
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "confirmed", job
    assert b"withdraw" in base64.b64decode(n.sent[-3]["signed_tx_base64"])
    assert b"near_withdraw" in base64.b64decode(n.sent[-2]["signed_tx_base64"])
    # claiming waits for the end
    r = await client.post(f"/launches/{lid}/claim-fees", json={"wallet_id": dev["id"]}, headers=h)
    assert r.status_code == 400 and "still running" in r.text
    n.memes[7]["end_timestamp_ms"] = str(int(time.time() * 1000) - 1000)
    r = await client.post(f"/launches/{lid}/trade", json={"side": "buy", "wallet_ids": [dev["id"]], "amount": 1}, headers=h)
    assert r.status_code == 400 and "ended" in r.text
    pf, r = await _go(client, h, f"/launches/{lid}/claim-fees", {"wallet_id": dev["id"]}, "m3")
    assert "refund" in pf["preflight"]["action"]  # the token account doesn't exist: the soft cap wasn't met
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "confirmed", job
    assert b"claim" in base64.b64decode(n.sent[-1]["signed_tx_base64"])


class FakeArc:
    """EvmSender stand-in: records calls, answers the few reads Tolly needs."""

    class Rpc:
        def __init__(self, outer):
            self.o = outer

        async def get_balance(self, a):
            return self.o.balance

        async def erc20_allowance(self, token, owner, spender):
            return self.o.allowance

        async def erc20_balance(self, token, owner):
            return 5 * 10**24

        async def erc20_meta(self, token):
            return {"name": "Helm Test", "symbol": "HLMT", "decimals": 18, "total_supply": 10**27}

        async def eth_call(self, to, data, sender=None):
            self.o.quotes.append((to, data))
            return "0x" + (10**21).to_bytes(32, "big").hex()

        async def receipt(self, h):
            return {"logs": [{"address": ep.TOLLY["arc"]["pad"], "topics": [
                ep.TOLLY_CREATED, "0x" + "00" * 12 + "ab" * 20, "0x" + "00" * 32]}]}

    def __init__(self):
        self.rpc = self.Rpc(self)
        self.balance, self.allowance = 1000 * 10**18, 0
        self.sent, self.quotes = [], []

    async def max_gas_cost(self, gas):
        return 10**16

    async def send(self, address, ref, call, *, on_signed=None, intent=None):
        from app.services.evm_exec import EvmResult

        self.sent.append(call)
        signature = "0x" + "%064x" % len(self.sent)
        if on_signed:
            await on_signed(signature)
        return EvmResult(signature, "confirmed")


async def test_tolly_launch_with_dev_buy_then_swaps(client, pads, monkeypatch):
    arc = FakeArc()
    real = extra_launch.ctx_for

    def ctx_for(ex, network, w):
        c = real(ex, network, w) if network != "arc" else ep.PadContext(w.address, w.signer_ref or "", ex.signer)
        if network == "arc":
            c.evm = arc
        return c

    monkeypatch.setattr(extra_launch, "ctx_for", ctx_for)
    from app.services.executor import Executor
    original_evm = Executor.evm
    monkeypatch.setattr(Executor, "evm", lambda self, network: arc if network == "arc" else original_evm(self, network))
    h = await signup(client, main=False)
    dev = (await client.post("/wallets", json={"network": "arc", "count": 1}, headers=h)).json()[0]
    lid = (await _launch(client, h, "tolly", dev, dev_buy_sol=100))["id"]
    pf, r = await _go(client, h, f"/launches/{lid}/deploy", {}, "t1")
    assert pf["preflight"]["dev_buy_usdc"] == 100 and "Uniswap V3" in pf["preflight"]["pool"]
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "confirmed", job
    approve, create = arc.sent[0], arc.sent[1]
    assert approve.to == ep.TOLLY["arc"]["usdc"] and abi_decode(["address", "uint256"], approve.data[4:])[1] == 100 * 10**6
    name, symbol, meta, salt, quote = abi_decode(
        ["string", "string", "(string,string,string,string)", "bytes32", "uint256"], create.data[4:])
    assert (name, symbol, quote) == ("Helm Test", "HLMT", 100 * 10**6)
    assert meta[0] == f"https://api.helm.test/public/launch-images/{lid}" and meta[1] == "https://helm.test"
    launch = (await client.get(f"/launches/{lid}", headers=h)).json()
    assert launch["status"] == "live" and launch["mint_address"].lower() == "0x" + "ab" * 20
    # a buy approves USDC for the router, then swaps with the simulated output less slippage
    arc.sent.clear()
    arc.allowance = 0
    pf, r = await _go(client, h, f"/launches/{lid}/trade",
                      {"side": "buy", "wallet_ids": [dev["id"]], "amount": 25, "slippage_pct": 10}, "t2")
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "confirmed", job
    swap = arc.sent[-2]
    assert arc.sent[-1].value == 25 * 10**18 * 250 // 10000
    assert swap.to == ep.TOLLY["arc"]["router"]
    (params,) = abi_decode(["(address,address,uint24,address,uint256,uint256,uint160)"], swap.data[4:])
    assert params[2] == 10_000 and params[4] == 25 * 10**6 and params[5] == 10**21 * 90 // 100
    # a dev buy over the anti-snipe limit is flagged before anything is sent
    ctx = ep.PadContext("0x" + "11" * 20, "ref", None, evm=arc)
    plan = await ep.Tolly().plan_create(ctx, "arc", "A", "A", 200 * 10**18, image_url="https://x/y.png")
    assert any("anti-snipe" in w for w in plan.warnings)


async def test_extra_launches_need_their_network_enabled(client, pads, monkeypatch):
    h = await signup(client, main=False)
    dev = (await client.post("/wallets", json={"network": "tron", "count": 1}, headers=h)).json()[0]
    lid = (await _launch(client, h, "sunpump", dev))["id"]
    monkeypatch.setattr(get_settings(), "extra_networks", "near")
    r = await client.post(f"/launches/{lid}/deploy", json={}, headers=h)
    assert r.status_code == 400 and "isn't enabled" in r.text


def test_icon_is_a_96px_png_data_uri():
    from PIL import Image
    import io

    uri = ep.icon_data_uri(PNG)
    im = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
    assert uri.startswith("data:image/png;base64,") and im.size == (96, 96)


async def test_a_confirmed_launch_whose_token_cant_be_read_is_never_offered_again(client, pads, monkeypatch):
    arc = FakeArc()
    real_receipt = arc.rpc.receipt
    fail = {"on": True}

    async def receipt(h):
        if fail["on"]:
            raise httpx.ReadTimeout("rpc down")
        return await real_receipt(h)

    arc.rpc.receipt = receipt
    real = extra_launch.ctx_for

    def ctx_for(ex, network, w):
        if network != "arc":
            return real(ex, network, w)
        c = ep.PadContext(w.address, w.signer_ref or "", ex.signer)
        c.evm = arc
        return c

    monkeypatch.setattr(extra_launch, "ctx_for", ctx_for)
    h = await signup(client, main=False)
    dev = (await client.post("/wallets", json={"network": "arc", "count": 1}, headers=h)).json()[0]
    lid = (await _launch(client, h, "tolly", dev))["id"]
    _, r = await _go(client, h, f"/launches/{lid}/deploy", {}, "x1")
    launch = (await client.get(f"/launches/{lid}", headers=h)).json()
    assert launch["status"] == "deploying"  # sent and confirmed, token unknown: not "failed"
    r = await client.post(f"/launches/{lid}/deploy", json={}, headers=h)
    assert r.status_code == 409  # so it can't be launched a second time
    fail["on"] = False
    launch = (await client.post(f"/launches/{lid}/recheck", headers=h)).json()
    assert launch["status"] == "live" and launch["mint_address"].lower() == "0x" + "ab" * 20


async def test_trades_only_from_wallets_on_the_tokens_network(client, pads):
    t = FakeSunPump()
    xc._clients["tron"] = t
    h = await signup(client, main=False)
    dev = (await client.post("/wallets", json={"network": "tron", "count": 1}, headers=h)).json()[0]
    lid = (await _launch(client, h, "sunpump", dev))["id"]
    from app import db as dbmod
    from app.models import Launch, LaunchWallet

    base = (await client.post("/wallets", json={"network": "base", "count": 1}, headers=h)).json()[0]
    async with dbmod.get_sessionmaker()() as db:
        l = await db.get(Launch, lid)
        l.status, l.mint_address = "live", TOKEN_T
        db.add(LaunchWallet(launch_id=lid, wallet_id=base["id"], role="trader"))
        await db.commit()
    r = await client.post(f"/launches/{lid}/trade", json={"side": "buy", "wallet_ids": [base["id"]], "amount": 1}, headers=h)
    assert r.status_code == 400
