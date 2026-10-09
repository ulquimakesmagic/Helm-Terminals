"""Linking your own Arc wallet (ownership proofs), managing wallets in settings, and sending from a linked wallet with
the wallet's own signature. Signature checks are proven against ethers output; the chain is a fake, keys are fixed
test keys. (TRON, NEAR and TON linking were removed on 6 October 2026.)"""

import json
import time
from pathlib import Path

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from app.config import get_settings
from app.services import extra_chains as xc

from .conftest import signup

V = json.loads((Path(__file__).parent / "data" / "wallet_vectors.json").read_text())
PK = bytes.fromhex("11" * 32)
EVM_ADDR = V["evm"]["address"]
DOMAIN = "helmterminals.xyz"


@pytest.fixture
def nets(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "extra_networks", "arc")
    monkeypatch.setattr(s, "allow_mainnet_extra_networks", True)
    monkeypatch.setattr(s, "ton_proof_domains", DOMAIN)
    xc._clients.clear()
    yield
    xc._clients.clear()


def test_wallet_signatures_match_ethers():
    assert Account.recover_message(encode_defunct(text=V["message"]), signature=V["evm"]["signature"]) == EVM_ADDR


def evm_sign(message: str, key: bytes = PK) -> str:
    return Account.sign_message(encode_defunct(text=message), key).signature.hex()


async def _link(client, h, network, address, **over):
    c = (await client.post(f"/chains/{network}/wallet-proof/challenge", json={"address": address, "domain": DOMAIN},
                           headers=h)).json()
    body = {"address": address, "domain": DOMAIN, "nonce": c["nonce"], "chain_id": over.pop("chain_id", c["chain_id"]),
            "signature": over.pop("sig", None) or evm_sign(c["message"])}
    body.update(over)
    return c, await client.post(f"/chains/{network}/wallet-proof/verify", json=body, headers=h)


@pytest.fixture
def evm(nets):
    from app.deps import get_executor
    from app.main import app

    fake = EvmFake()
    app.dependency_overrides[get_executor] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_executor, None)


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




# --- linking --------------------------------------------------------------------------------------------------


async def test_linking_proves_ownership_bound_to_user_network_site_and_time(client, nets, monkeypatch):
    h = await signup(client, main=False)
    c, r = await _link(client, h, "arc", EVM_ADDR)
    assert "Arc (mainnet, chain ID 5042)" in c["message"] and EVM_ADDR in c["message"]
    assert "doesn't authorize any transaction" in c["message"]
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["kind"] == "connected" and "signer_ref" not in w
    # a challenge links once
    body = {"address": EVM_ADDR, "domain": DOMAIN, "nonce": c["nonce"], "chain_id": 5042, "signature": evm_sign(c["message"])}
    r = await client.post("/chains/arc/wallet-proof/verify", json=body, headers=h)
    assert r.status_code == 400 and "already used" in r.text
    # someone else's key
    _, r = await _link(client, h, "arc", EVM_ADDR, sig=None or evm_sign("x", bytes([2]) * 32))
    assert r.status_code == 400 and "different account" in r.text
    # another Helm user can't use this user's challenge
    h2 = await signup(client, email="b@example.com", main=False)
    c3 = (await client.post("/chains/arc/wallet-proof/challenge", json={"address": EVM_ADDR, "domain": DOMAIN},
                            headers=h)).json()
    r = await client.post("/chains/arc/wallet-proof/verify", headers=h2, json={
        "address": EVM_ADDR, "domain": DOMAIN, "nonce": c3["nonce"], "chain_id": 5042, "signature": evm_sign(c3["message"])})
    assert r.status_code == 400 and "wasn't issued" in r.text
    # only Helm's own site, and challenges expire
    r = await client.post("/chains/arc/wallet-proof/challenge", json={"address": EVM_ADDR, "domain": "evil.example"},
                          headers=h)
    assert r.status_code == 400
    from app.routes import external_wallets

    real = time.time
    monkeypatch.setattr(external_wallets.time, "time", lambda: real() + 3600)
    r = await client.post("/chains/arc/wallet-proof/verify", headers=h, json={
        "address": EVM_ADDR, "domain": DOMAIN, "nonce": c3["nonce"], "chain_id": 5042, "signature": evm_sign(c3["message"])})
    assert r.status_code == 400 and "expired" in r.text


async def test_arc_link_needs_the_right_chain_and_signer(client, nets):
    h = await signup(client, main=False)
    _, r = await _link(client, h, "arc", EVM_ADDR, chain_id=5042002)  # the old Arc testnet id
    assert r.status_code == 400 and "switch networks" in r.text
    _, r = await _link(client, h, "arc", EVM_ADDR.lower())
    assert r.status_code == 201 and r.json()["address"] == EVM_ADDR  # stored checksummed
    other = Account.sign_message(encode_defunct(text="x"), bytes([3]) * 32).signature.hex()
    _, r = await _link(client, h, "arc", "0x" + "44" * 20, sig=other)
    assert r.status_code == 400


async def test_removed_networks_cant_be_linked(client, nets):
    h = await signup(client, main=False)
    for net in ("tron", "near", "ton"):
        r = await client.post(f"/chains/{net}/wallet-proof/challenge", json={"address": "x", "domain": DOMAIN},
                              headers=h)
        assert r.status_code == 404, net


async def test_overview_rename_disconnect_remove_and_isolation(client, evm):
    evm.balance = 7 * 10**18
    h = await signup(client, main=False)
    w = (await _link(client, h, "arc", EVM_ADDR))[1].json()
    managed = (await client.post("/wallets", json={"network": "arc", "count": 1}, headers=h)).json()[0]
    ov = (await client.get("/chains/wallets/overview", headers=h)).json()
    rows = {x["id"]: x for x in ov["wallets"]}
    mine = rows[w["id"]]
    assert mine["kind"] == "connected" and mine["signer"] == "Your wallet" and "testnet" not in mine
    assert mine["native"] == {"asset": "USDC", "amount": "7"} and mine["fees"]["kind"] == "gas"
    assert "USDC" in mine["fees"]["transfer"] and mine["explorer"].startswith("https://explorer.arc.io/")
    assert rows[managed["id"]]["signer"] == "Helm signer"
    text = json.dumps(ov)
    assert "signer_ref" not in text and "local:" not in text  # no key material in wallet APIs
    assert [n["id"] for n in ov["networks"]] == ["arc"]
    assert ov["networks"][0]["external"] == "Browser EVM wallet" and ov["networks"][0]["managed"] is True
    assert (await client.patch(f"/wallets/{w['id']}", json={"label": "Cold Arc"}, headers=h)).json()["label"] == "Cold Arc"
    # another user sees none of it and can't touch it
    h2 = await signup(client, email="b@example.com", main=False)
    assert (await client.get("/chains/wallets/overview", headers=h2)).json()["wallets"] == []
    assert (await client.patch(f"/wallets/{w['id']}", json={"label": "x"}, headers=h2)).status_code == 404
    assert (await client.post(f"/wallets/{w['id']}/disconnect", headers=h2)).status_code == 404
    r = await client.post("/chains/arc/external/transfer", headers=h2,
                          json={"wallet_id": w["id"], "to": managed["address"], "amount": "1"})
    assert r.status_code == 404
    # disconnect keeps it watch-only; a managed wallet can't be disconnected or removed
    assert (await client.post(f"/wallets/{managed['id']}/disconnect", headers=h)).status_code == 400
    assert (await client.delete(f"/wallets/{managed['id']}", headers=h)).status_code == 400
    assert (await client.post(f"/wallets/{w['id']}/disconnect", headers=h)).json()["kind"] == "watch"
    r = await client.post("/chains/arc/external/transfer", headers=h,
                          json={"wallet_id": w["id"], "to": managed["address"], "amount": "1"})
    assert r.status_code == 400 and "link this wallet" in r.text
    assert (await client.delete(f"/wallets/{w['id']}", headers=h)).json() == {"removed": w["id"]}


# --- sending from a linked wallet ------------------------------------------------------------------------------


async def _pending(client, h, network, body, key):
    pf = (await client.post(f"/chains/{network}/external/transfer", json=body, headers=h)).json()
    r = await client.post(f"/chains/{network}/external/transfer", headers=h, json={
        **body, "confirm": True, "preflight_hash": pf["preflight_hash"], "idempotency_key": key})
    return pf, r


async def test_arc_linked_wallet_sends_and_helm_checks_what_it_sent(client, nets, monkeypatch):
    from app.deps import get_executor
    from app.main import app

    fake = EvmFake()
    app.dependency_overrides[get_executor] = lambda: fake
    try:
        h = await signup(client, main=False)
        w = (await _link(client, h, "arc", EVM_ADDR))[1].json()
        to = "0x" + "44" * 20
        _, r = await _pending(client, h, "arc", {"wallet_id": w["id"], "to": to, "amount": "1.5"}, "a1")
        req = r.json()["wallet_request"]
        assert req["chain_id"] == 5042 and req["from"] == EVM_ADDR and int(req["value"], 16) == 15 * 10**17
        jid = r.json()["job_id"]
        good = "0x" + "aa" * 32
        fake.txs[good] = {"from": EVM_ADDR.lower(), "to": req["to"].lower(), "input": req["data"], "value": req["value"],
                          "chainId": hex(5042)}
        await client.post(f"/chains/arc/external/jobs/{jid}/signed", headers=h, json={"tx_hash": good})
        assert (await client.get(f"/jobs/{jid}", headers=h)).json()["status"] == "confirmed"
        # a hash for something else is recorded as not the reviewed transfer
        _, r = await _pending(client, h, "arc", {"wallet_id": w["id"], "to": to, "amount": "1"}, "a2")
        jid = r.json()["job_id"]
        wrong = "0x" + "bb" * 32
        fake.txs[wrong] = {**fake.txs[good], "value": hex(1)}
        await client.post(f"/chains/arc/external/jobs/{jid}/signed", headers=h, json={"tx_hash": wrong})
        job = (await client.get(f"/jobs/{jid}", headers=h)).json()
        # money may have moved, so it's never "failed" (which would invite a resend): it waits for a look on the explorer
        assert job["status"] == "unconfirmed" and "different transaction" in job["transactions"][0]["error"]
        # a hash Helm already recorded can't be claimed for another transfer
        _, r = await _pending(client, h, "arc", {"wallet_id": w["id"], "to": to, "amount": "1.5"}, "a4")
        r = await client.post(f"/chains/arc/external/jobs/{r.json()['job_id']}/signed", headers=h,
                              json={"tx_hash": good.upper().replace("0X", "0x")})
        assert r.status_code == 400 and "already recorded" in r.text
        # the wallet errored after approval: unknown, not failed, and it can't be sent again as new
        _, r = await _pending(client, h, "arc", {"wallet_id": w["id"], "to": to, "amount": "1"}, "a5")
        jid5 = r.json()["job_id"]
        assert (await client.post(f"/chains/arc/external/jobs/{jid5}/unknown", headers=h)).json()["status"] == "unconfirmed"
        assert (await client.post(f"/chains/arc/external/jobs/{jid5}/declined", headers=h)).json()["status"] == "unconfirmed"
        # another user can't touch the job
        other = await signup(client, email="b@example.com", main=False)
        r = await client.post(f"/chains/arc/external/jobs/{jid5}/unknown", headers=other)
        assert r.status_code == 404
        # declined in the wallet
        _, r = await _pending(client, h, "arc", {"wallet_id": w["id"], "to": to, "amount": "1"}, "a3")
        jid = r.json()["job_id"]
        assert (await client.post(f"/chains/arc/external/jobs/{jid}/declined", headers=h)).json()["status"] == "failed"
        r = await client.post(f"/chains/arc/external/jobs/{jid}/signed", headers=h, json={"tx_hash": good})
        assert r.json()["status"] == "failed"  # a declined transfer doesn't come back
        # not enough USDC for the amount plus gas is refused before any job exists
        fake.balance = 10**15
        r = (await client.post("/chains/arc/external/transfer", headers=h,
                               json={"wallet_id": w["id"], "to": to, "amount": "1"})).json()
        assert r["preflight"]["warnings"]
    finally:
        app.dependency_overrides.pop(get_executor, None)


