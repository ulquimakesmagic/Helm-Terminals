"""Arc, the one remaining extra network: gating, wallets, imports, balances, transfers with USDC gas, validation
and unsupported features. Also that TRON, NEAR and TON (removed 6 October 2026) are refused everywhere and that
wallets and launches saved on them stay stored but hidden. No network access, no real keys: fakes stand in for
Arc RPC."""

import asyncio

import httpx
import pytest

from app.config import get_settings
from app.services import extra_chains as xc

from .conftest import enable_2fa, signup, totp_code

REMOVED = ("tron", "near", "ton")


@pytest.fixture
def extra(monkeypatch):
    """Enable Arc for a test (no network)."""
    s = get_settings()
    monkeypatch.setattr(s, "extra_networks", "arc")
    monkeypatch.setattr(s, "allow_mainnet_extra_networks", True)
    xc._clients.clear()
    yield
    xc._clients.clear()


class ArcRPC:
    network, chain_id = "arc", 5042

    def __init__(self):
        self.balance = 0

    async def get_balance(self, a):
        return self.balance

    async def base_fee(self):
        return 20 * 10**9

    async def max_priority_fee(self):
        return 10**9

    async def erc20_meta(self, t):
        return {"name": "Euro Coin", "symbol": "EURC", "decimals": 6}

    async def erc20_balance(self, t, a):
        return 2_000_000


@pytest.fixture
def arc(extra):
    from app.deps import get_executor
    from app.main import app
    from app.services.executor import Executor
    from app.signing import get_signer

    rpc = ArcRPC()
    app.dependency_overrides[get_executor] = lambda: Executor(None, get_signer(), evm_rpcs={"arc": rpc})
    yield rpc
    app.dependency_overrides.pop(get_executor, None)


async def _wallet(client, h, net):
    return (await client.post("/wallets", json={"network": net, "count": 1}, headers=h)).json()[0]


async def _nosleep(*_):
    return None


# --- gating and wallets --------------------------------------------------------------------------------------


async def test_only_arc_is_offered_and_the_switches_turn_it_off(client, monkeypatch):
    h = await signup(client, main=False)
    s = get_settings()
    listed = {n["id"]: n for n in (await client.get("/chains")).json()["extra"]}
    assert set(listed) == {"arc"} and listed["arc"]["enabled"] and listed["arc"]["chain_id"] == 5042
    assert listed["arc"]["capabilities"]["launches"].startswith("yes")
    for gone in (*REMOVED, "tron-nile", "near-testnet", "arc-testnet", "ton-testnet"):
        r = await client.post("/wallets", json={"network": gone, "count": 1}, headers=h)
        assert r.status_code == 400, gone
    monkeypatch.setattr(s, "extra_networks", "tron,near,arc,ton")  # an old setting: the removed ids are ignored
    assert {n["id"] for n in (await client.get("/chains")).json()["extra"]} == {"arc"}
    monkeypatch.setattr(s, "extra_networks", "")
    assert (await client.post("/wallets", json={"network": "arc", "count": 1}, headers=h)).status_code == 400
    monkeypatch.setattr(s, "extra_networks", "arc")
    monkeypatch.setattr(s, "allow_mainnet_extra_networks", False)  # the kill switch
    assert not any(n["enabled"] for n in (await client.get("/chains")).json()["extra"])
    assert (await client.post("/wallets", json={"network": "arc", "count": 1}, headers=h)).status_code == 400


async def test_removed_networks_are_refused_on_every_route(client, extra):
    h = await signup(client, main=False)
    secret = await enable_2fa(client, h)
    for net in REMOVED:
        assert (await client.post("/wallets/watch", json={"network": net, "address": "alice.near"},
                                  headers=h)).status_code == 400, net
        r = await client.post("/wallets/import", json={"network": net, "private_key": "11" * 32,
                                                       "password": "correct-horse-battery",
                                                       "totp": await totp_code(secret)}, headers=h)
        assert r.status_code == 400 and ("11" * 32) not in r.text, net
        assert (await client.get(f"/chains/{net}/health", headers=h)).status_code == 404, net
        assert (await client.post(f"/chains/{net}/transfer", json={"wallet_id": "x", "to": "y", "amount": "1"},
                                  headers=h)).status_code == 404, net
        assert (await client.post(f"/chains/{net}/wallet-proof/challenge",
                                  json={"address": "y", "domain": "helmterminals.xyz"}, headers=h)).status_code == 404
    for path in ("/chains/ton/ton/proof-payload", "/chains/ton/ton/connect"):
        assert (await client.post(path, json={}, headers=h)).status_code in (404, 405), path
    pads = {p["id"] for p in (await client.get("/launchpads")).json()}
    assert not pads & {"sunpump", "meme-cooking", "gaspump"}


async def test_wallets_and_launches_left_on_removed_networks_are_kept_but_hidden(client, extra):
    from sqlalchemy import select

    from app.db import get_sessionmaker
    from app.models import Launch, User, Wallet

    h = await signup(client, main=False)
    async with get_sessionmaker()() as db:
        u = (await db.execute(select(User))).scalars().first()
        for net, addr in (("tron", "TCLBgkbfVkJroVBJVqBEsxtPNQEQMTQCLQ"), ("near", "alice.near"),
                          ("ton", "UQ" + "A" * 46), ("tron-nile", "TCLBgkbfVkJroVBJVqBEsxtPNQEQMTQCLQ")):
            db.add(Wallet(user_id=u.id, network=net, address=addr, kind="managed", label="old"))
        launch = Launch(user_id=u.id, network="tron", launchpad="sunpump", name="Old", symbol="OLD", status="live",
                        mint_address="TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
        db.add(launch)
        await db.commit()
        lid = launch.id
    assert all(w["network"] in ("solana", "base", "bsc", "ethereum", "robinhood", "arc")
               for w in (await client.get("/wallets", headers=h)).json())
    assert (await client.get("/chains/wallets/overview", headers=h)).json()["wallets"] == []
    assert all(l["id"] != lid for l in (await client.get("/launches", headers=h)).json())
    assert (await client.get(f"/launches/{lid}", headers=h)).status_code == 404
    assert (await client.get(f"/launches/{lid}/market", headers=h)).status_code == 404
    async with get_sessionmaker()() as db:  # nothing was deleted
        assert len((await db.execute(select(Wallet).where(Wallet.network.in_(REMOVED)))).scalars().all()) == 3
        assert await db.get(Launch, lid) is not None


async def test_arc_managed_and_watch_wallets_use_evm_addresses(client, extra):
    h = await signup(client, main=False)
    made = await _wallet(client, h, "arc")
    assert made["address"].startswith("0x") and len(made["address"]) == 42
    for addr, ok in (("0x" + "cd" * 20, 201), ("TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj", 400), ("alice.near", 400)):
        r = await client.post("/wallets/watch", json={"network": "arc", "address": addr}, headers=h)
        assert r.status_code == ok, (addr, r.text)
    # core fund-moving routes refuse Arc wallets; they use the network's own transfer
    r = await client.post("/wallets/withdraw", json={"wallet_ids": [made["id"]], "destination": "0x" + "cd" * 20,
                                                     "all": True}, headers=h)
    assert r.status_code == 400 and "own transfer" in r.json()["detail"]


async def test_importing_an_arc_key(client, extra):
    """A key exported from an EVM wallet can be imported (password and 2FA, like any import) and lands on Arc with
    the key's own address."""
    h = await signup(client, main=False)
    secret = await enable_2fa(client, h)
    key = "0x" + "11" * 32
    r = await client.post("/wallets/import", json={"network": "arc", "private_key": key, "all_evm": True,
                                                   "password": "correct-horse-battery",
                                                   "totp": await totp_code(secret)}, headers=h)
    assert r.status_code == 201, r.text
    out = r.json()
    assert [(w["network"], w["address"], w["kind"]) for w in out] == [
        ("arc", "0x19E7E376E7C213B7E7e7e46cc70A5dD086DAff2A", "managed")]
    assert "signer_ref" not in out[0] and key not in r.text
    r = await client.post("/wallets/import", json={"network": "arc", "private_key": "zz" * 32,
                                                   "password": "correct-horse-battery",
                                                   "totp": await totp_code(secret)}, headers=h)
    assert r.status_code == 400 and "zz" * 32 not in r.text


# --- balances and transfers ---------------------------------------------------------------------------------


async def test_arc_uses_usdc_gas_and_its_own_chain(client, arc):
    from app.chains.evm import evm_config

    assert evm_config("arc")["chain_id"] == 5042 and evm_config("arc")["native"] == "USDC"
    h = await signup(client, main=False)
    w = await _wallet(client, h, "arc")
    arc.balance = 10**18  # 1 USDC (native, 18 decimals)
    r = (await client.get(f"/chains/arc/wallets/{w['id']}/balances", headers=h)).json()
    assert r["native"]["asset"] == "USDC" and r["native"]["amount"] == "1" and r["tokens"][0]["symbol"] == "EURC"
    assert all(t["address"].lower() != "0x" + "36" + "0" * 38 for t in r["tokens"])  # no double-counted USDC
    body = {"wallet_id": w["id"], "to": "0x" + "cd" * 20, "amount": "0.5"}
    pf = (await client.post("/chains/arc/transfer", json=body, headers=h)).json()["preflight"]
    assert pf["asset"] == "USDC" and pf["amount_raw"] == str(5 * 10**17) and "one confirmation" in pf["finality"]
    assert pf["gas_fee_usdc_max"] == "0.000861"  # 21,000 gas x (2 x 20 gwei + 1 gwei), paid in USDC
    pf = (await client.post("/chains/arc/transfer", json={**body, "amount": "1"}, headers=h)).json()
    assert any("in gas" in x for x in pf["preflight"]["warnings"])
    r = await client.post("/chains/arc/transfer", json={**body, "to": "0x" + "00" * 20}, headers=h)
    assert r.status_code == 400 and "zero address" in r.json()["detail"]
    r = await client.get("/chains/arc/tokens/0x3600000000000000000000000000000000000000", headers=h)
    assert r.status_code == 502 and "same balance as native USDC" in r.json()["detail"]
    hist = (await client.get(f"/chains/arc/wallets/{w['id']}/history", headers=h)).json()
    assert hist["chain"] == [] and "only transfers made through Helm" in hist["note"]


async def test_amounts_and_recipients_are_validated(client, arc):
    arc.balance = 10**20
    h = await signup(client, main=False)
    w = await _wallet(client, h, "arc")
    to = "0x" + "cd" * 20
    for body, msg in (({"to": to, "amount": "0"}, "above zero"),
                      ({"to": to, "amount": "1.0000000000000000001"}, "decimal places"),
                      ({"to": "TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj", "amount": "1"}, "valid"),
                      ({"to": w["address"], "amount": "1"}, "sending wallet"),
                      ({"to": to, "amount": "1234567890123456789012.1234567890123456789"}, "decimal places")):
        r = await client.post("/chains/arc/transfer", json={"wallet_id": w["id"], **body}, headers=h)
        assert r.status_code == 400 and msg in r.json()["detail"], (body, r.text)
    from app.routes.chains import _raw_amount

    assert _raw_amount("1234567890.123456789012345678", 18) == 1234567890123456789012345678
    with pytest.raises(Exception):
        _raw_amount("1234567890.1234567890123456789", 18)


async def test_market_data_and_routes_stay_unavailable_on_arc(client, extra):
    pads = {p["id"]: p["network"] for p in (await client.get("/launchpads")).json() if p["status"] != "planned"}
    assert pads["tolly"] == "arc"
    assert all(n in ("solana", "base", "bsc", "robinhood", "ethereum", "arc") for n in pads.values())
    caps = (await client.get("/chains")).json()["extra"][0]["capabilities"]
    assert caps["cross_chain"].startswith("no") and caps["launches"].startswith("yes")


async def test_arc_rpc_outage_is_reported_as_unavailable(monkeypatch):
    from app.chains.evm import EvmRPC, EvmRPCUnavailable

    def handler(req):
        return httpx.Response(503)

    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    rpc = EvmRPC("arc", ["https://arc.test"], client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(EvmRPCUnavailable) as e:
        await rpc.get_balance("0x" + "11" * 20)
    assert "Arc" in str(e.value)


async def test_health_needs_a_session(client, extra):
    assert (await client.get("/chains/arc/health")).status_code == 401
