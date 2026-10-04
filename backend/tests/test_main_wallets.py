"""Main wallets made at sign-up, importing a wallet by private key, and launches routing USDC from the main wallet."""

import json

import httpx
from solders.keypair import Keypair
from sqlalchemy import select

from app.chains.relay import Relay
from app.db import get_sessionmaker
from app.deps import get_executor
from app.main import app
from app.models import Activity, Funder
from app.services.executor import Executor
from app.signing import get_signer

from .conftest import SOL, signup
from .test_auto_fund import relay_mock
from .test_bridge import TokenNode
from .test_evm_wallets import ETH

EVM = ["base", "bsc", "ethereum", "robinhood"]
PW = "correct-horse-battery"


async def test_signup_gives_every_account_a_main_wallet(client):
    h = await signup(client)
    ws = (await client.get("/wallets", headers=h)).json()
    mains = [w for w in ws if w["is_main"]]
    assert sorted(w["network"] for w in mains) == sorted(["solana", *EVM])
    assert all(w["kind"] == "managed" and w["label"] == "Main" for w in mains)
    assert len({w["address"] for w in mains if w["network"] != "solana"}) == 1  # one EVM key on every EVM chain
    assert ws[: len(mains)] == mains  # listed first
    funders = (await client.get("/funders", headers=h)).json()
    assert {f["network"] for f in funders if f["is_default"]} == {"solana", *EVM}
    # asking again creates nothing
    r = await client.post("/wallets/main", headers=h)
    assert r.status_code == 201 and r.json() == []


class BrokenSigner:
    async def create_wallet(self, *a):
        raise RuntimeError("signer down")


async def test_signup_still_works_when_the_signer_is_down_and_main_can_be_finished_later(client):
    app.dependency_overrides[get_signer] = lambda: BrokenSigner()
    try:
        r = await client.post("/auth/register", json={"email": "late@example.com", "password": "correct-horse-battery"})
        assert r.status_code == 201 and "message" in r.json()
        tok = (await client.post("/auth/login", json={"email": "late@example.com", "password": "correct-horse-battery"})).json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        assert (await client.get("/wallets", headers=h)).json() == []
        r = await client.post("/wallets/main", headers=h)
        assert r.status_code == 502 and "try again" in r.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_signer, None)
    r = await client.post("/wallets/main", headers=h)
    assert r.status_code == 201 and len(r.json()) == 5 and all(w["is_main"] for w in r.json())


async def test_import_a_wallet_by_private_key_never_echoes_the_key(client):
    h = await signup(client, main=False)
    kp = Keypair()
    secret = str(kp)
    r = await client.post("/wallets/import", json={"password": PW, "network": "solana", "private_key": secret, "label": "Old"}, headers=h)
    assert r.status_code == 201, r.text
    [w] = r.json()
    assert w["address"] == str(kp.pubkey()) and w["kind"] == "managed" and w["label"] == "Old"
    assert secret not in r.text
    # the same wallet again
    r = await client.post("/wallets/import", json={"password": PW, "network": "solana", "private_key": secret}, headers=h)
    assert r.status_code == 409
    # a bad key: explains the format, repeats nothing of what was sent
    bad = "not-a-key-" + secret[:20]
    r = await client.post("/wallets/import", json={"password": PW, "network": "solana", "private_key": bad}, headers=h)
    assert r.status_code == 400 and "base58" in r.json()["detail"] and bad not in r.text
    # a malformed request body doesn't echo the key in the validation error either
    r = await client.post("/wallets/import", json={"password": PW, "network": "solana", "private_key": secret, "label": "x" * 200}, headers=h)
    assert r.status_code == 422 and secret not in r.text
    r = await client.post("/wallets/import", json={"password": PW, "network": "solana", "private_key": secret, "all_evm": "maybe"}, headers=h)
    assert r.status_code == 422 and secret not in r.text
    # an EVM key on every EVM chain; a watched address becomes managed rather than duplicated
    evm_key = "0x" + "22" * 32
    from eth_keys import keys
    addr = keys.PrivateKey(bytes.fromhex("22" * 32)).public_key.to_checksum_address()
    assert (await client.post("/wallets/watch", json={"network": "bsc", "address": addr}, headers=h)).status_code == 201
    r = await client.post("/wallets/import", json={"password": PW, "network": "base", "private_key": evm_key, "all_evm": True}, headers=h)
    assert r.status_code == 201 and sorted(w["network"] for w in r.json()) == sorted(EVM)
    ws = (await client.get("/wallets", headers=h)).json()
    assert [w["kind"] for w in ws if w["network"] == "bsc"] == ["managed"]
    async with get_sessionmaker()() as db:
        logged = json.dumps([a.payload for a in (await db.execute(select(Activity))).scalars()])
    assert secret not in logged and "22" * 32 not in logged


class NoImportSigner:
    async def import_wallet(self, *a):
        raise NotImplementedError("importing a private key isn't available with this server's signer yet")


async def test_import_is_refused_plainly_where_the_signer_cant(client):
    h = await signup(client, main=False)
    app.dependency_overrides[get_signer] = lambda: NoImportSigner()
    try:
        r = await client.post("/wallets/import", json={"password": PW, "network": "solana", "private_key": str(Keypair())}, headers=h)
        assert r.status_code == 501 and "isn't available" in r.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_signer, None)


async def test_launch_routes_usdc_from_the_main_wallet_first(client, fake_rpc, monkeypatch):
    base = TokenNode("base")
    relay = Relay("https://relay.test", fee_bps=30, fee_recipient="0x" + "11" * 20,
                  client=httpx.AsyncClient(transport=relay_mock([], lambda: None)))
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"base": base}, relay=relay)
    try:
        h = await signup(client)
        ws = (await client.get("/wallets", headers=h)).json()
        main_base = next(w for w in ws if w["is_main"] and w["network"] == "base")
        other = (await client.post("/wallets", json={"network": "base", "count": 1}, headers=h)).json()[0]
        dev = (await client.post("/wallets", json={"count": 1}, headers=h)).json()[0]
        fake_rpc.balances[dev["address"]] = 1_000
        for w, usdc in ((main_base, 200), (other, 900)):  # the other wallet holds more, the main wallet still wins
            base.balances[w["address"].lower()] = ETH // 100
            base.tokens[w["address"].lower()] = usdc * 10**6
        lid = (await client.post("/launches", json={"name": "T", "symbol": "T", "dev_wallet_id": dev["id"],
                                                    "dev_buy_sol": 0.5}, headers=h)).json()["id"]
        pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
        assert pf["preflight"]["funding"]["from_address"] == main_base["address"]
    finally:
        app.dependency_overrides.pop(get_executor, None)
