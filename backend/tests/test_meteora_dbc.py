"""Meteora DBC via Jupiter Studio: create-tx, presigned uploads, submit through Studio, swaps, fee claims."""

import base64
import json
import urllib.parse

import httpx
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from app.chains import registry
from app.chains.base import ClaimRequest, CreateRequest, TradeRequest
from app.chains.meteora_dbc import WSOL, MeteoraDbcAdapter
from app.chains.solana import SYSTEM_PROGRAM, build_unsigned, sol_transfer_ix
from app.config import get_settings

from .conftest import signup

PNG = b"\x89PNG\r\n\x1a\n" + b"3" * 20
MINT = Keypair()


def create_tx_for(dev: str) -> str:
    """An unsigned transaction needing the dev (fee payer) and the mint Jupiter generated."""
    d = Pubkey.from_string(dev)
    ix = Instruction(SYSTEM_PROGRAM, b"", [AccountMeta(MINT.pubkey(), True, False)])
    return base64.b64encode(build_unsigned(d, [sol_transfer_ix(d, d, 1), ix], str(Hash.default()))).decode()


def swap_tx_for(wallet: str) -> str:
    w = Pubkey.from_string(wallet)
    return base64.b64encode(build_unsigned(w, [sol_transfer_ix(w, w, 1)], str(Hash.default()))).decode()


def jupiter(log: list):
    def handler(req: httpx.Request):
        log.append(req)
        path = req.url.path
        if req.url.host == "upload.test":
            return httpx.Response(200)
        assert req.headers.get("x-api-key") == "JUPKEY"
        if path.endswith("/dbc-pool/create-tx"):
            body = json.loads(req.content)
            return httpx.Response(200, json={
                "transaction": create_tx_for(body["creator"]), "mint": str(MINT.pubkey()),
                "imagePresignedUrl": "https://upload.test/img", "metadataPresignedUrl": "https://upload.test/meta",
                "imageUrl": "https://static.jup.ag/img.png"})
        if path.endswith("/dbc-pool/submit"):
            return httpx.Response(200, json={"success": True, "data": {"mint": str(MINT.pubkey()), "configKey": "cfg"}})
        if path.endswith("/swap/v1/quote"):
            return httpx.Response(200, json={"inAmount": req.url.params["amount"], "outAmount": "999"})
        if path.endswith("/swap/v1/swap"):
            return httpx.Response(200, json={"swapTransaction": swap_tx_for(json.loads(req.content)["userPublicKey"])})
        if "/dbc-pool/addresses/" in path:
            return httpx.Response(200, json={"success": True, "data": {"dbcPoolAddress": "POOL111", "configKey": "cfg"}})
        if path.endswith("/dbc/fee/create-tx"):
            return httpx.Response(200, json={"transaction": swap_tx_for(json.loads(req.content)["ownerWallet"])})
        return httpx.Response(404, json={"error": "nope"})
    return httpx.MockTransport(handler)


async def test_new_studio_launch_requires_reward_collection(client, fake_rpc, monkeypatch):
    registry._cache["meteora-dbc"] = MeteoraDbcAdapter("JUPKEY", fake_rpc, client=httpx.AsyncClient(transport=jupiter([])))
    monkeypatch.setattr(get_settings(), "jupiter_api_key", "JUPKEY")
    try:
        h = await signup(client)
        listed = {p["id"]: p for p in (await client.get("/launchpads")).json()}
        assert listed["meteora-dbc"]["status"] == "setup_required"
        dev = (await client.post("/wallets", json={"count": 1}, headers=h)).json()[0]
        created = await client.post("/launches", json={"launchpad": "meteora-dbc", "name": "Pending", "symbol": "PEND",
            "dev_wallet_id": dev["id"], "image_b64": base64.b64encode(PNG).decode()}, headers=h)
        assert created.status_code == 201, created.text
        path = f"/launches/{created.json()['id']}/deploy"
        pf = (await client.post(path, json={}, headers=h)).json()
        assert pf["preflight"]["launch_fee_payment"]["usd"] == 0
        share = pf["preflight"]["helm_creator_share"]
        assert share["bps"] == 250 and not share["supported"]
        sent = await client.post(path, json={"confirm": True, "preflight_hash": pf["preflight_hash"],
            "idempotency_key": "pending-collection"}, headers=h)
        assert sent.status_code == 400 and "collection" in sent.json()["detail"]
        assert fake_rpc.sent == []
    finally:
        registry._cache.pop("meteora-dbc", None)


async def test_needs_key_and_rejects_dev_buy(client):
    a = MeteoraDbcAdapter("")
    try:
        await a._call("GET", "/x")
        raise AssertionError
    except Exception as e:
        assert "JUPITER_API_KEY" in str(e)
    assert a.deploy_warnings(0.5, {}) and not a.deploy_warnings(0, {})
    pads = {p["id"]: p for p in (await client.get("/launchpads")).json()}
    assert pads["meteora-dbc"]["status"] == "setup_required" and "JUPITER_API_KEY" in pads["meteora-dbc"]["reason"]


async def test_swaps_and_claims(fake_rpc):
    log = []
    a = MeteoraDbcAdapter("JUPKEY", fake_rpc, client=httpx.AsyncClient(transport=jupiter(log)))
    w = str(Keypair().pubkey())
    buy = await a.build_buy_tx(TradeRequest(mint=str(MINT.pubkey()), wallet_pubkey=w, side="buy", amount=0.5,
                                            denominated_in_sol=True, slippage_pct=5))
    q = next(r for r in log if r.url.path.endswith("/quote")).url.params
    assert q["inputMint"] == WSOL and q["amount"] == str(500_000_000) and q["slippageBps"] == "500"
    assert buy.mint == str(MINT.pubkey())
    fake_rpc.tokens[w] = 1000
    await a.build_sell_tx(TradeRequest(mint=str(MINT.pubkey()), wallet_pubkey=w, side="sell", amount="50%",
                                       denominated_in_sol=False, slippage_pct=5))
    assert [r for r in log if r.url.path.endswith("/quote")][-1].url.params["amount"] == "500"
    claim = await a.build_claim_fees_tx(ClaimRequest(wallet_pubkey=w, mint=str(MINT.pubkey())))
    assert claim is not None
    assert json.loads(log[-1].content)["poolAddress"] == "POOL111"
