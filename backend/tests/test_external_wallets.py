"""Linking your own TRON, Arc and NEAR wallets (ownership proofs), managing wallets in settings, and sending from a
linked wallet with the wallet's own signature. Signature checks are proven against TronWeb, ethers and near-js output;
chains are fakes, keys are fixed test keys."""

import asyncio
import base64
import json
import time
from pathlib import Path

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_keys import keys
from solders.keypair import Keypair

from app.chains import near, tron
from app.config import get_settings
from app.services import extra_chains as xc

from .conftest import signup
from .test_extra_chains import FakeNear, FakeTron, build_tron_tx

V = json.loads((Path(__file__).parent / "data" / "wallet_vectors.json").read_text())
PK = bytes.fromhex("11" * 32)
TRON_ADDR = V["tron"]["address"]
EVM_ADDR = V["evm"]["address"]
NEAR_KP = Keypair.from_seed(bytes([7]) * 32)
NEAR_PUB = "ed25519:" + near._b58(bytes(NEAR_KP.pubkey()))
DOMAIN = "helmterminals.xyz"


@pytest.fixture
def nets(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "extra_networks", "tron-nile,near-testnet,arc-testnet")
    monkeypatch.setattr(s, "allow_mainnet_extra_networks", False)
    monkeypatch.setattr(s, "ton_proof_domains", DOMAIN)
    xc._clients.clear()
    yield
    xc._clients.clear()


# --- signatures, against the libraries wallets use ------------------------------------------------------------


def test_wallet_signatures_match_tronweb_ethers_and_near_js():
    assert tron.recover_address(tron.message_hash_v2(V["message"]), V["tron"]["signature"]) == TRON_ADDR
    assert Account.recover_message(encode_defunct(text=V["message"]), signature=V["evm"]["signature"]) == EVM_ADDR
    n = V["near"]
    pk = near.verify_nep413(V["message"], bytes.fromhex(n["nonce"]), n["recipient"], n["public_key"], n["signature"])
    assert len(pk) == 32
    with pytest.raises(ValueError):
        near.verify_nep413(V["message"] + "x", bytes.fromhex(n["nonce"]), n["recipient"], n["public_key"], n["signature"])
    with pytest.raises(ValueError):  # a different recipient (site) doesn't verify
        near.verify_nep413(V["message"], bytes.fromhex(n["nonce"]), "evil.example", n["public_key"], n["signature"])
    t = V["tron_tx"]
    assert tron.signer_of({"txID": t["txID"], "signature": t["signature"]}) == t["signer"] == TRON_ADDR


# --- linking --------------------------------------------------------------------------------------------------


def tron_sign(message: str) -> str:
    sig = keys.PrivateKey(PK).sign_msg_hash(tron.message_hash_v2(message))
    return "0x" + sig.to_bytes()[:64].hex() + format(sig.v + 27, "02x")


def evm_sign(message: str) -> str:
    return Account.sign_message(encode_defunct(text=message), PK).signature.hex()


def near_sign(message: str, nonce_hex: str, recipient: str = DOMAIN) -> str:
    return base64.b64encode(bytes(NEAR_KP.sign_message(near.nep413_hash(message, bytes.fromhex(nonce_hex), recipient)))).decode()


async def _link(client, h, network, address, **over):
    c = (await client.post(f"/chains/{network}/wallet-proof/challenge", json={"address": address, "domain": DOMAIN},
                           headers=h)).json()
    body = {"address": address, "domain": DOMAIN, "nonce": c["nonce"], "chain_id": over.pop("chain_id", c["chain_id"])}
    if network.startswith("tron"):
        body["signature"] = over.pop("sig", None) or tron_sign(c["message"])
    elif network.startswith("arc"):
        body["signature"] = over.pop("sig", None) or evm_sign(c["message"])
    else:
        body["signature"] = over.pop("sig", None) or near_sign(c["message"], c["nonce"], over.pop("recipient", DOMAIN))
        body["public_key"] = NEAR_PUB
    body.update(over)
    return c, await client.post(f"/chains/{network}/wallet-proof/verify", json=body, headers=h)


class Near(FakeNear):
    def __init__(self, permission="FullAccess"):
        super().__init__()
        self.permission, self.txs = permission, {}

    async def call(self, method, params, resend=True):
        if method == "query" and params.get("request_type") == "view_access_key":
            if params["public_key"] != NEAR_PUB:
                from app.chains.base import AdapterError

                raise AdapterError("NEAR RPC error UNKNOWN_ACCESS_KEY")
            return {"nonce": 1, "permission": self.permission if self.permission == "FullAccess"
                    else {"FunctionCall": {"receiver_id": "x.near", "method_names": []}}}
        if method == "tx":
            return self.txs[params["tx_hash"]]
        return await super().call(method, params)


async def test_linking_proves_ownership_bound_to_user_network_site_and_time(client, nets, monkeypatch):
    xc._clients["tron-nile"] = FakeTron()
    h = await signup(client, main=False)
    c, r = await _link(client, h, "tron-nile", TRON_ADDR)
    assert "TRON Nile testnet (testnet)" in c["message"] and TRON_ADDR in c["message"]
    assert "doesn't authorize any transaction" in c["message"]
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["kind"] == "connected" and "signer_ref" not in w
    # a challenge links once
    body = {"address": TRON_ADDR, "domain": DOMAIN, "nonce": c["nonce"], "signature": tron_sign(c["message"])}
    r = await client.post("/chains/tron-nile/wallet-proof/verify", json=body, headers=h)
    assert r.status_code == 400 and "already used" in r.text
    # someone else's key
    other = "0x" + keys.PrivateKey(bytes([2]) * 32).sign_msg_hash(tron.message_hash_v2(c["message"])).to_bytes().hex()
    c2, r = await _link(client, h, "tron-nile", TRON_ADDR, sig=other)
    assert r.status_code == 400 and "different TRON account" in r.text
    # another Helm user can't use this user's challenge
    h2 = await signup(client, email="b@example.com", main=False)
    c3 = (await client.post("/chains/tron-nile/wallet-proof/challenge", json={"address": TRON_ADDR, "domain": DOMAIN},
                            headers=h)).json()
    r = await client.post("/chains/tron-nile/wallet-proof/verify", headers=h2, json={
        "address": TRON_ADDR, "domain": DOMAIN, "nonce": c3["nonce"], "signature": tron_sign(c3["message"])})
    assert r.status_code == 400 and "wasn't issued" in r.text
    # only Helm's own site, and challenges expire
    r = await client.post("/chains/tron-nile/wallet-proof/challenge", json={"address": TRON_ADDR, "domain": "evil.example"},
                          headers=h)
    assert r.status_code == 400
    from app.routes import ton as ton_routes

    real = time.time
    monkeypatch.setattr(ton_routes.time, "time", lambda: real() + 3600)
    c4 = {"nonce": c3["nonce"]}
    r = await client.post("/chains/tron-nile/wallet-proof/verify", headers=h, json={
        "address": TRON_ADDR, "domain": DOMAIN, "nonce": c4["nonce"], "signature": tron_sign(c3["message"])})
    assert r.status_code == 400 and "expired" in r.text


async def test_arc_link_needs_the_right_chain_and_signer(client, nets):
    h = await signup(client, main=False)
    _, r = await _link(client, h, "arc-testnet", EVM_ADDR, chain_id=5042)  # mainnet id while linking testnet
    assert r.status_code == 400 and "switch networks" in r.text
    _, r = await _link(client, h, "arc-testnet", EVM_ADDR.lower())
    assert r.status_code == 201 and r.json()["address"] == EVM_ADDR  # stored checksummed
    other = Account.sign_message(encode_defunct(text="x"), bytes([3]) * 32).signature.hex()
    _, r = await _link(client, h, "arc-testnet", "0x" + "44" * 20, sig=other)
    assert r.status_code == 400


async def test_near_link_needs_a_full_access_key_of_the_account(client, nets):
    n = Near()
    xc._clients["near-testnet"] = n
    h = await signup(client, main=False)
    _, r = await _link(client, h, "near-testnet", "alice.testnet", recipient="evil.example")
    assert r.status_code == 400 and "doesn't verify" in r.text
    n.permission = "FunctionCall"
    _, r = await _link(client, h, "near-testnet", "alice.testnet")
    assert r.status_code == 400 and "full-access" in r.text
    n.permission = "FullAccess"
    _, r = await _link(client, h, "near-testnet", "Alice.testnet")
    assert r.status_code == 201 and r.json()["address"] == "alice.testnet"


async def test_ton_links_only_through_ton_connect(client, nets, monkeypatch):
    monkeypatch.setattr(get_settings(), "extra_networks", "ton-testnet")
    h = await signup(client, main=False)
    r = await client.post("/chains/ton-testnet/wallet-proof/challenge", json={"address": "0:" + "11" * 32, "domain": DOMAIN},
                          headers=h)
    assert r.status_code == 400 and "TON Connect" in r.text


# --- settings -------------------------------------------------------------------------------------------------


async def test_overview_rename_disconnect_remove_and_isolation(client, nets):
    t = FakeTron()
    t.accounts[TRON_ADDR] = 7_000_000
    xc._clients["tron-nile"] = t
    h = await signup(client, main=False)
    w = (await _link(client, h, "tron-nile", TRON_ADDR))[1].json()
    managed = (await client.post("/wallets", json={"network": "tron-nile", "count": 1}, headers=h)).json()[0]
    ov = (await client.get("/chains/wallets/overview", headers=h)).json()
    rows = {x["id"]: x for x in ov["wallets"]}
    mine = rows[w["id"]]
    assert mine["kind"] == "connected" and mine["signer"] == "Your wallet" and mine["testnet"] is True
    assert mine["native"] == {"asset": "TRX", "amount": "7"} and mine["fees"]["kind"] == "resources"
    assert "energy" in mine["fees"]["token_transfer"] and mine["explorer"].startswith("https://nile.tronscan.org/")
    assert rows[managed["id"]]["signer"] == "Helm signer"
    text = json.dumps(ov)
    assert "signer_ref" not in text and "local:" not in text  # no key material in wallet APIs
    tron_net = next(n for n in ov["networks"] if n["id"] == "tron-nile")
    assert tron_net["external"] == "TronLink" and tron_net["managed"] is True
    ton_net = next(n for n in ov["networks"] if n["id"] == "ton")
    assert ton_net["managed"] is False and ton_net["enabled"] is False
    assert (await client.patch(f"/wallets/{w['id']}", json={"label": "Cold TRON"}, headers=h)).json()["label"] == "Cold TRON"
    # another user sees none of it and can't touch it
    h2 = await signup(client, email="b@example.com", main=False)
    assert (await client.get("/chains/wallets/overview", headers=h2)).json()["wallets"] == []
    assert (await client.patch(f"/wallets/{w['id']}", json={"label": "x"}, headers=h2)).status_code == 404
    assert (await client.post(f"/wallets/{w['id']}/disconnect", headers=h2)).status_code == 404
    r = await client.post("/chains/tron-nile/external/transfer", headers=h2,
                          json={"wallet_id": w["id"], "to": managed["address"], "amount": "1"})
    assert r.status_code == 404
    # disconnect keeps it watch-only; a managed wallet can't be disconnected or removed
    assert (await client.post(f"/wallets/{managed['id']}/disconnect", headers=h)).status_code == 400
    assert (await client.delete(f"/wallets/{managed['id']}", headers=h)).status_code == 400
    assert (await client.post(f"/wallets/{w['id']}/disconnect", headers=h)).json()["kind"] == "watch"
    r = await client.post("/chains/tron-nile/external/transfer", headers=h,
                          json={"wallet_id": w["id"], "to": managed["address"], "amount": "1"})
    assert r.status_code == 400 and "link this wallet" in r.text
    assert (await client.delete(f"/wallets/{w['id']}", headers=h)).json() == {"removed": w["id"]}


async def test_testnet_wallets_never_count_as_mainnet(client, nets, monkeypatch):
    t = FakeTron()
    t.accounts[TRON_ADDR] = 5_000_000
    xc._clients["tron-nile"] = t
    h = await signup(client, main=False)
    await _link(client, h, "tron-nile", TRON_ADDR)
    ov = (await client.get("/chains/wallets/overview", headers=h)).json()
    assert [(w["network"], w["testnet"]) for w in ov["wallets"]] == [("tron-nile", True)]
    # the same address can't be used on mainnet without linking it there
    monkeypatch.setattr(get_settings(), "extra_networks", "tron,tron-nile")
    monkeypatch.setattr(get_settings(), "allow_mainnet_extra_networks", True)
    w = ov["wallets"][0]
    r = await client.post("/chains/tron/external/transfer", headers=h, json={"wallet_id": w["id"], "to": TRON_ADDR, "amount": "1"})
    assert r.status_code == 400 and "tron-nile, not tron" in r.text


# --- sending from a linked wallet ------------------------------------------------------------------------------


async def _pending(client, h, network, body, key):
    pf = (await client.post(f"/chains/{network}/external/transfer", json=body, headers=h)).json()
    r = await client.post(f"/chains/{network}/external/transfer", headers=h, json={
        **body, "confirm": True, "preflight_hash": pf["preflight_hash"], "idempotency_key": key})
    return pf, r


async def _nosleep(*_):
    return None


async def test_tron_linked_wallet_signs_what_was_reviewed_then_helm_broadcasts(client, nets, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    t = FakeTron()
    to = "TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj"
    t.accounts[TRON_ADDR] = 50_000_000
    t.accounts[to] = 1
    xc._clients["tron-nile"] = t
    h = await signup(client, main=False)
    w = (await _link(client, h, "tron-nile", TRON_ADDR))[1].json()
    body = {"wallet_id": w["id"], "to": to, "amount": "2"}
    pf, r = await _pending(client, h, "tron-nile", body, "t1")
    assert pf["requires_2fa"] is False and pf["preflight"]["kind"] == "external_transfer"
    assert "bandwidth_bytes" in pf["preflight"]
    tx = r.json()["wallet_request"]["tx"]
    jid = r.json()["job_id"]
    # nothing is broadcast until the wallet signs; a different transaction or signer is refused
    assert t.broadcasts == []
    other = build_tron_tx(TRON_ADDR, to=to, amount=3)
    r = await client.post(f"/chains/tron-nile/external/jobs/{jid}/signed", headers=h,
                          json={"signed_tx": {**other, "signature": ["00" * 65]}})
    assert r.status_code == 400 and "different transaction" in r.text
    bad = keys.PrivateKey(bytes([5]) * 32).sign_msg_hash(bytes.fromhex(tx["txID"]))
    r = await client.post(f"/chains/tron-nile/external/jobs/{jid}/signed", headers=h,
                          json={"signed_tx": {**tx, "signature": [bad.to_bytes().hex()]}})
    assert r.status_code == 400 and "different account" in r.text
    good = keys.PrivateKey(PK).sign_msg_hash(bytes.fromhex(tx["txID"]))
    r = await client.post(f"/chains/tron-nile/external/jobs/{jid}/signed", headers=h,
                          json={"signed_tx": {**tx, "signature": [good.to_bytes().hex()]}})
    assert r.status_code == 200
    job = (await client.get(f"/jobs/{jid}", headers=h)).json()
    assert job["status"] == "confirmed" and len(t.broadcasts) == 1 and t.broadcasts[0]["txID"] == tx["txID"]


class EvmFake:
    def __init__(self):
        self.txs, self.balance = {}, 10**20

        class R:
            async def call(s, method, params):
                return self.txs.get(params[0])

            async def get_balance(s, a):
                return self.balance

            async def erc20_meta(s, t):
                return {"name": "E", "symbol": "EURC", "decimals": 6}

            async def erc20_balance(s, t, a):
                return 5_000_000

        self.rpc = R()

    def evm(self, network):
        return self

    async def max_gas_cost(self, gas):
        return 10**15

    async def fees(self):
        return {"max_fee": 10**10, "priority": 10**9}

    async def wait(self, h):
        from app.services.evm_exec import EvmResult

        return EvmResult(h, "confirmed")


async def test_arc_linked_wallet_sends_and_helm_checks_what_it_sent(client, nets, monkeypatch):
    from app.deps import get_executor
    from app.main import app

    fake = EvmFake()
    app.dependency_overrides[get_executor] = lambda: fake
    try:
        h = await signup(client, main=False)
        w = (await _link(client, h, "arc-testnet", EVM_ADDR))[1].json()
        to = "0x" + "44" * 20
        _, r = await _pending(client, h, "arc-testnet", {"wallet_id": w["id"], "to": to, "amount": "1.5"}, "a1")
        req = r.json()["wallet_request"]
        assert req["chain_id"] == 5042002 and req["from"] == EVM_ADDR and int(req["value"], 16) == 15 * 10**17
        jid = r.json()["job_id"]
        good = "0x" + "aa" * 32
        fake.txs[good] = {"from": EVM_ADDR.lower(), "to": req["to"].lower(), "input": req["data"], "value": req["value"],
                          "chainId": hex(5042002)}
        await client.post(f"/chains/arc-testnet/external/jobs/{jid}/signed", headers=h, json={"tx_hash": good})
        assert (await client.get(f"/jobs/{jid}", headers=h)).json()["status"] == "confirmed"
        # a hash for something else is recorded as not the reviewed transfer
        _, r = await _pending(client, h, "arc-testnet", {"wallet_id": w["id"], "to": to, "amount": "1"}, "a2")
        jid = r.json()["job_id"]
        wrong = "0x" + "bb" * 32
        fake.txs[wrong] = {**fake.txs[good], "value": hex(1)}
        await client.post(f"/chains/arc-testnet/external/jobs/{jid}/signed", headers=h, json={"tx_hash": wrong})
        job = (await client.get(f"/jobs/{jid}", headers=h)).json()
        # money may have moved, so it's never "failed" (which would invite a resend): it waits for a look on the explorer
        assert job["status"] == "unconfirmed" and "different transaction" in job["transactions"][0]["error"]
        # a hash Helm already recorded can't be claimed for another transfer
        _, r = await _pending(client, h, "arc-testnet", {"wallet_id": w["id"], "to": to, "amount": "1.5"}, "a4")
        r = await client.post(f"/chains/arc-testnet/external/jobs/{r.json()['job_id']}/signed", headers=h,
                              json={"tx_hash": good.upper().replace("0X", "0x")})
        assert r.status_code == 400 and "already recorded" in r.text
        # the wallet errored after approval: unknown, not failed, and it can't be sent again as new
        _, r = await _pending(client, h, "arc-testnet", {"wallet_id": w["id"], "to": to, "amount": "1"}, "a5")
        jid5 = r.json()["job_id"]
        assert (await client.post(f"/chains/arc-testnet/external/jobs/{jid5}/unknown", headers=h)).json()["status"] == "unconfirmed"
        assert (await client.post(f"/chains/arc-testnet/external/jobs/{jid5}/declined", headers=h)).json()["status"] == "unconfirmed"
        # another user can't touch the job
        other = await signup(client, email="b@example.com", main=False)
        r = await client.post(f"/chains/arc-testnet/external/jobs/{jid5}/unknown", headers=other)
        assert r.status_code == 404
        # declined in the wallet
        _, r = await _pending(client, h, "arc-testnet", {"wallet_id": w["id"], "to": to, "amount": "1"}, "a3")
        jid = r.json()["job_id"]
        assert (await client.post(f"/chains/arc-testnet/external/jobs/{jid}/declined", headers=h)).json()["status"] == "failed"
        r = await client.post(f"/chains/arc-testnet/external/jobs/{jid}/signed", headers=h, json={"tx_hash": good})
        assert r.json()["status"] == "failed"  # a declined transfer doesn't come back
        # not enough USDC for the amount plus gas is refused before any job exists
        fake.balance = 10**15
        r = (await client.post("/chains/arc-testnet/external/transfer", headers=h,
                               json={"wallet_id": w["id"], "to": to, "amount": "1"})).json()
        assert r["preflight"]["warnings"]
    finally:
        app.dependency_overrides.pop(get_executor, None)


async def test_near_linked_wallet_sends_the_reviewed_actions(client, nets):
    n = Near()
    n.accounts["alice.testnet"] = 5 * 10**24
    n.accounts["bob.testnet"] = 1
    xc._clients["near-testnet"] = n
    h = await signup(client, main=False)
    w = (await _link(client, h, "near-testnet", "alice.testnet"))[1].json()
    _, r = await _pending(client, h, "near-testnet", {"wallet_id": w["id"], "to": "bob.testnet", "amount": "1"}, "n1")
    req = r.json()["wallet_request"]
    assert req == {"receiver_id": "bob.testnet", "actions": [{"type": "Transfer", "params": {"deposit": str(10**24)}}]}
    jid = r.json()["job_id"]
    n.txs["H1"] = {"status": {"SuccessValue": ""}, "receipts_outcome": [], "transaction": {
        "signer_id": "alice.testnet", "receiver_id": "bob.testnet", "actions": [{"Transfer": {"deposit": str(10**24)}}]}}
    await client.post(f"/chains/near-testnet/external/jobs/{jid}/signed", headers=h, json={"tx_hash": "H1"})
    assert (await client.get(f"/jobs/{jid}", headers=h)).json()["status"] == "confirmed"
    _, r = await _pending(client, h, "near-testnet", {"wallet_id": w["id"], "to": "bob.testnet", "amount": "1"}, "n2")
    jid = r.json()["job_id"]
    n.txs["H2"] = {**n.txs["H1"], "transaction": {**n.txs["H1"]["transaction"], "receiver_id": "eve.testnet"}}
    await client.post(f"/chains/near-testnet/external/jobs/{jid}/signed", headers=h, json={"tx_hash": "H2"})
    assert (await client.get(f"/jobs/{jid}", headers=h)).json()["status"] == "unconfirmed"
    # two /signed calls racing for one job record one transaction
    _, r = await _pending(client, h, "near-testnet", {"wallet_id": w["id"], "to": "bob.testnet", "amount": "1"}, "n3")
    jid = r.json()["job_id"]
    n.txs["H3"] = n.txs["H1"]
    import asyncio

    await asyncio.gather(*[client.post(f"/chains/near-testnet/external/jobs/{jid}/signed", headers=h, json={"tx_hash": "H3"})
                           for _ in range(3)])
    job = (await client.get(f"/jobs/{jid}", headers=h)).json()
    assert job["status"] == "confirmed" and len(job["transactions"]) == 1
