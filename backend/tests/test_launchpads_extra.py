"""Tolly (Arc) launches through Helm's launch flow, with a fake Arc chain. Checks what is signed (the exact contract
call, value and arguments), what is shown, and how results land. SunPump (TRON) and Meme.Cooking (NEAR) were removed
on 6 October 2026."""

import base64

import httpx
import pytest
from eth_abi import decode as abi_decode

from app.chains import extra_pads as ep
from app.config import get_settings
from app.services import extra_chains as xc
from app.services import extra_launch

from .conftest import signup

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@pytest.fixture
def pads(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "extra_networks", "arc")
    monkeypatch.setattr(s, "allow_mainnet_extra_networks", True)
    monkeypatch.setattr(s, "public_api_url", "https://api.helm.test")
    xc._clients.clear()
    yield
    xc._clients.clear()


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

    async def send(self, address, ref, call):
        from app.services.evm_exec import EvmResult

        self.sent.append(call)
        return EvmResult("0x" + "%064x" % len(self.sent), "confirmed")


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


async def test_tolly_is_listed_and_the_removed_launchpads_are_gone(client, pads):
    listed = {p["id"]: p for p in (await client.get("/launchpads")).json()}
    assert listed["tolly"]["network"] == "arc" and listed["tolly"]["status"] == "available"
    assert not set(listed) & {"sunpump", "meme-cooking", "gaspump"}
    h = await signup(client, main=False)
    for pad in ("sunpump", "meme-cooking"):
        r = await client.post("/launches", json={"name": "A", "symbol": "A", "launchpad": pad}, headers=h)
        assert r.status_code in (400, 404, 422), (pad, r.text)


async def test_tolly_launch_with_dev_buy_then_swaps(client, pads, monkeypatch):
    arc = FakeArc()
    real = extra_launch.ctx_for

    def ctx_for(ex, network, w):
        c = real(ex, network, w) if network != "arc" else ep.PadContext(w.address, w.signer_ref or "", ex.signer)
        if network == "arc":
            c.evm = arc
        return c

    monkeypatch.setattr(extra_launch, "ctx_for", ctx_for)
    h = await signup(client, main=False)
    dev = (await client.post("/wallets", json={"network": "arc", "count": 1}, headers=h)).json()[0]
    lid = (await _launch(client, h, "tolly", dev, dev_buy_sol=100))["id"]
    pf, r = await _go(client, h, f"/launches/{lid}/deploy", {}, "t1")
    assert pf["preflight"]["dev_buy_usdc"] == 100 and "Uniswap V3" in pf["preflight"]["pool"]
    assert (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()["status"] == "confirmed"
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
    assert (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()["status"] == "confirmed"
    swap = arc.sent[-1]
    assert swap.to == ep.TOLLY["arc"]["router"]
    (params,) = abi_decode(["(address,address,uint24,address,uint256,uint256,uint160)"], swap.data[4:])
    assert params[2] == 10_000 and params[4] == 25 * 10**6 and params[5] == 10**21 * 90 // 100
    # a dev buy over the anti-snipe limit is flagged before anything is sent
    ctx = ep.PadContext("0x" + "11" * 20, "ref", None, evm=arc)
    plan = await ep.Tolly().plan_create(ctx, "arc", "A", "A", 200 * 10**18, image_url="https://x/y.png")
    assert any("anti-snipe" in w for w in plan.warnings)


async def test_extra_launches_need_their_network_enabled(client, pads, monkeypatch):
    h = await signup(client, main=False)
    dev = (await client.post("/wallets", json={"network": "arc", "count": 1}, headers=h)).json()[0]
    lid = (await _launch(client, h, "tolly", dev))["id"]
    monkeypatch.setattr(get_settings(), "extra_networks", "")
    r = await client.post(f"/launches/{lid}/deploy", json={}, headers=h)
    assert r.status_code == 400 and "isn't enabled" in r.text


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
    h = await signup(client, main=False)
    dev = (await client.post("/wallets", json={"network": "arc", "count": 1}, headers=h)).json()[0]
    lid = (await _launch(client, h, "tolly", dev))["id"]
    from app import db as dbmod
    from app.models import Launch, LaunchWallet

    base = (await client.post("/wallets", json={"network": "base", "count": 1}, headers=h)).json()[0]
    async with dbmod.get_sessionmaker()() as db:
        l = await db.get(Launch, lid)
        l.status, l.mint_address = "live", "0x" + "ab" * 20
        db.add(LaunchWallet(launch_id=lid, wallet_id=base["id"], role="trader"))
        await db.commit()
    r = await client.post(f"/launches/{lid}/trade", json={"side": "buy", "wallet_ids": [base["id"]], "amount": 1}, headers=h)
    assert r.status_code == 400
