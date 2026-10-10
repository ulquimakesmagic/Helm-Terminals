"""Printr launches on Solana: Printr builds the instructions, Helm compiles and signs them with the dev wallet."""

import base64
import json

import httpx
import pytest
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from app.chains import registry
from app.chains.base import AdapterError, CreateRequest
from app.chains.printr import SOLANA_CAIP2, PrintrAdapter

from .conftest import signup
from .test_api import confirm, make_wallets

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
PROGRAM = str(Keypair().pubkey())
MINT = str(Keypair().pubkey())


def printr_mock(seen: list, *, extra_signer=None, total_lamports=None):
    def handler(req: httpx.Request):
        assert req.url.path == "/v0/print", req.url.path
        body = json.loads(req.read())
        seen.append(body)
        dev = body["creator_accounts"][0].split(":")[-1]
        accounts = [{"pubkey": dev, "is_signer": True, "is_writable": True},
                    {"pubkey": MINT, "is_signer": False, "is_writable": True}]
        if extra_signer:
            accounts.append({"pubkey": extra_signer, "is_signer": True, "is_writable": False})
        quote = {"id": "q", "router": "r", "assets": [], "costs": [],
                 "total": {"asset_id": f"{SOLANA_CAIP2}:11111111111111111111111111111111", "cost_usd": 1.0,
                           "cost_asset_atomic": str(total_lamports if total_lamports is not None
                                                    else int(body["initial_buy"]["spend_native"]) + 20_000_000)}}
        return httpx.Response(200, json={"token_id": "0xabc", "quote": quote, "payload": {
            "mint_address": f"{SOLANA_CAIP2}:{MINT}",
            "ixs": [{"program_id": PROGRAM, "accounts": accounts, "data": base64.b64encode(b"\x01create").decode()}]}})
    return httpx.MockTransport(handler)


def adapter(fake_rpc, seen, **kw):
    return PrintrAdapter("https://printr.test", rpc=fake_rpc, client=httpx.AsyncClient(transport=printr_mock(seen, **kw)))


def req(dev: str, **kw):
    return CreateRequest(name="Helm Printr", symbol="HPRT", description="d", image_bytes=PNG,
                         socials={"twitter": "https://x.com/helm"}, dev_pubkey=dev, dev_buy_sol=0.5, **kw)


async def test_printr_instructions_are_compiled_into_a_dev_paid_transaction(fake_rpc):
    seen: list = []
    dev = str(Keypair().pubkey())
    built = await adapter(fake_rpc, seen).build_create_tx(req(dev))
    body = seen[-1]
    assert body["chains"] == [SOLANA_CAIP2] and body["creator_accounts"] == [f"{SOLANA_CAIP2}:{dev}"]
    assert body["initial_buy"] == {"spend_native": "500000000"} and body["fee_sink"] == "dev"
    assert base64.b64decode(body["image"]) == PNG and not body["image"].startswith("data:")
    assert body["external_links"] == {"x": "https://x.com/helm"}
    tx = VersionedTransaction.from_bytes(built.raw)
    assert str(tx.message.account_keys[0]) == dev and tx.message.header.num_required_signatures == 1
    assert built.mint == MINT


async def test_printr_refuses_foreign_signers_overpriced_quotes_and_bad_images(fake_rpc):
    dev = str(Keypair().pubkey())
    with pytest.raises(AdapterError, match="signature Helm doesn't hold"):
        await adapter(fake_rpc, [], extra_signer=str(Keypair().pubkey())).build_create_tx(req(dev))
    with pytest.raises(AdapterError, match="more than the dev buy"):
        await adapter(fake_rpc, [], total_lamports=10 * 10**9).build_create_tx(req(dev))
    a = adapter(fake_rpc, [])
    with pytest.raises(AdapterError, match="image"):
        await a.build_create_tx(CreateRequest(name="x", symbol="x", description="", image_bytes=None, socials={},
                                              dev_pubkey=dev))
    with pytest.raises(AdapterError, match="PNG or JPEG"):
        await a.build_create_tx(CreateRequest(name="x", symbol="x", description="", image_bytes=b"GIF89a....",
                                              socials={}, dev_pubkey=dev))
    with pytest.raises(AdapterError, match="Jupiter"):
        await a.build_buy_tx(None)


async def test_printr_launch_through_helm_goes_live(client, fake_rpc):
    seen: list = []
    registry._cache["printr"] = adapter(fake_rpc, seen)
    try:
        listed = {p["id"]: p for p in (await client.get("/launchpads")).json()}
        assert listed["printr"]["status"] == "available"
        assert "gaspump" not in listed
        h = await signup(client)
        dev = (await make_wallets(client, h, 1))[0]
        r = await client.post("/launches", json={"launchpad": "printr", "name": "Helm Printr", "symbol": "HPRT",
                                                 "dev_wallet_id": dev["id"], "dev_buy_sol": 0.5,
                                                 "image_b64": base64.b64encode(PNG).decode()}, headers=h)
        assert r.status_code == 201, r.text
        lid = r.json()["id"]
        pf, r = await confirm(client, h, f"/launches/{lid}/deploy", {})
        assert r.status_code == 200, r.text
        assert "Printr bonding curve" in pf["preflight"]["curve"]
        job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
        assert job["status"] == "confirmed", job
        launch = (await client.get(f"/launches/{lid}", headers=h)).json()
        assert launch["status"] == "live" and launch["mint_address"] == MINT
        tx = VersionedTransaction.from_bytes(fake_rpc.sent[-1])
        assert str(tx.message.account_keys[0]) == dev["address"]
    finally:
        registry._cache.pop("printr", None)
