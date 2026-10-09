"""TON: addresses, cells and BoC (byte-for-byte against @ton/core), TON Connect proofs, Jetton payloads, the
normalized message hash, and the connect / preview / confirm / follow flow. No network access and no real keys:
a fake stands in for toncenter, and the wallet key is a fixed test seed."""

import asyncio
import base64
import json
import time
from pathlib import Path

import pytest
from solders.keypair import Keypair

from app.chains import ton
from app.chains.base import AdapterError
from app.config import get_settings
from app.services import extra_chains as xc

from .conftest import signup

V = json.loads((Path(__file__).parent / "data" / "ton_vectors.json").read_text())
W4 = V["wallet"]["v4_addr"]
KP = Keypair.from_seed(bytes.fromhex(V["wallet"]["seed"]))
OTHER = "0:" + "11" * 32
JETTON = "0:" + "22" * 32
JW = "0:" + "33" * 32


@pytest.fixture
def tonnet(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "extra_networks", "ton")
    monkeypatch.setattr(s, "allow_mainnet_extra_networks", True)
    monkeypatch.setattr(s, "ton_proof_domains", "helmterminals.xyz")
    xc._clients.clear()
    t = FakeToncenter()
    xc._clients["ton"] = t
    yield t
    xc._clients.clear()


class FakeToncenter(ton.Toncenter):
    def __init__(self):
        super().__init__("https://ton.test")
        self.accounts = {}  # raw -> {status, balance, code_boc?, data_boc?}
        self.jetton_wallets = {}  # (owner raw, master raw) -> wallet dict
        self.masters = {JETTON: {"address": JETTON.upper(), "jetton_content": {"symbol": "TUSD", "name": "Test USD",
                                                                             "decimals": "6"}}}
        self.by_msg, self.fail, self.calls = {}, None, []

    async def get(self, path, params):
        self.calls.append((path, params))
        if self.fail:
            raise AdapterError(self.fail)
        if path == "/api/v3/accountStates":
            raw = ton.parse_address(params["address"]).raw()
            a = self.accounts.get(raw)
            return {"accounts": [{"address": raw.upper(), **a}] if a else []}
        if path == "/api/v3/jetton/masters":
            m = self.masters.get(ton.parse_address(params["address"]).raw())
            return {"jetton_masters": [m] if m else [], "metadata": {}}
        if path == "/api/v3/jetton/wallets":
            key = (ton.parse_address(params["owner_address"]).raw(), ton.parse_address(params["jetton_address"]).raw())
            w = self.jetton_wallets.get(key)
            return {"jetton_wallets": [w] if w else []}
        if path == "/api/v3/transactionsByMessage":
            return {"transactions": self.by_msg.get(params["msg_hash"], [])}
        if path == "/api/v3/transactions":
            return {"transactions": [
                {"hash": base64.b64encode(bytes(range(32))).decode(), "now": 1790000000,
                 "description": {"aborted": False, "compute_ph": {"success": True, "exit_code": 0},
                                 "action": {"success": True, "result_code": 0}},
                 "in_msg": {"source": OTHER.upper(), "value": "2500000000"}, "out_msgs": []}]}
        if path == "/api/v3/masterchainInfo":
            return {"last": {"seqno": 1}}
        raise AssertionError(path)


async def _nosleep(*_):
    return None


# --- primitives against @ton/core --------------------------------------------------------------------------


def test_addresses_match_ton_core_in_every_form():
    a = ton.parse_address(V["addr"]["raw"])
    assert a.friendly(True, False) == V["addr"]["b"] and a.friendly(False, False) == V["addr"]["nb"]
    assert a.friendly(True, True) == V["addr"]["tb"] and a.friendly(False, True) == V["addr"]["tnb"]
    for k in ("b", "nb", "tb", "tnb", "b_std"):
        assert ton.parse_address(V["addr"][k]).same(a)
    assert ton.parse_address(V["addr"]["tnb"]).testnet and not ton.parse_address(V["addr"]["nb"]).bounceable
    bad = V["addr"]["b"][:-1] + ("A" if V["addr"]["b"][-1] != "A" else "B")
    for s in (bad, "0:12", "EQ" + "A" * 46, "TJRabPrwbZy45sbavfcjinPJC18kjpRTv8", "0x" + "ab" * 20, "alice.near"):
        assert not ton.valid_address(s)


def test_cells_boc_and_hashes_match_ton_core():
    si = ton.cell_from_b64(V["wallet"]["v4_state_init"])
    assert si.hash().hex() == V["wallet"]["v4_si_hash"] and "0:" + si.hash().hex() == W4
    assert base64.b64encode(ton.serialize_boc(si)).decode() == V["wallet"]["v4_state_init"]
    si5 = ton.cell_from_b64(V["wallet"]["v5_state_init"])
    for cell in (si, si5):
        assert ton.wallet_public_key(*ton.state_init_parts(cell)).hex() == V["wallet"]["pub"]
    j = V["jetton"]
    body = ton.jetton_transfer_body(j["query_id"], int(j["amount"]), ton.parse_address(j["dest"]),
                                    ton.parse_address(j["resp"]), int(j["forward"]))
    assert body.boc_b64() == j["boc"] and body.hash().hex() == j["hash"]
    back = ton.read_jetton_transfer(ton.cell_from_b64(j["boc"]))
    assert back["amount"] == 1500000 and back["destination"].raw() == j["dest"]


def test_normalized_external_message_hash_matches_ton_core():
    dest, h = ton.normalized_external_hash(V["ext"]["boc"])  # with state init, body as reference
    assert dest.raw() == W4 and h == V["ext"]["norm_hash"] and h != V["ext"]["hash"]
    dest, h = ton.normalized_external_hash(V["ext_inline"]["boc"])  # body stored inline
    assert h == V["ext_inline"]["norm_hash"]
    with pytest.raises(ValueError):
        ton.normalized_external_hash(V["jetton"]["boc"])  # not an external message


def test_ton_proof_checks_domain_payload_age_and_signature():
    a = ton.parse_address(V["proof"]["address"])
    pk = bytes.fromhex(V["wallet"]["pub"])
    p = {"timestamp": V["proof"]["timestamp"], "domain": {"value": "helmterminals.xyz", "lengthBytes": 17},
         "payload": V["proof"]["payload"], "signature": V["proof"]["signature"]}
    ton.verify_proof(a, p, pk, "helmterminals.xyz", V["proof"]["payload"], now=p["timestamp"])  # signed by tweetnacl
    for proof, domain, payload, now, msg in [
        (p, "evil.example", p["payload"], p["timestamp"], "different site"),
        (p, "helmterminals.xyz", "other", p["timestamp"], "sign-in request"),
        (p, "helmterminals.xyz", p["payload"], p["timestamp"] + 3600, "expired"),
        ({**p, "signature": base64.b64encode(bytes(64)).decode()}, "helmterminals.xyz", p["payload"], p["timestamp"],
         "signature"),
    ]:
        with pytest.raises(ValueError, match=msg):
            ton.verify_proof(a, proof, pk, domain, payload, now=now)
    with pytest.raises(ValueError, match="signature"):
        ton.verify_proof(ton.parse_address(OTHER), p, pk, "helmterminals.xyz", p["payload"], now=p["timestamp"])


def test_outcomes():
    ok = {"description": {"aborted": False, "compute_ph": {"success": True}, "action": {"success": True}}}
    assert ton.tx_outcome(ok) == ("confirmed", None)
    assert ton.tx_outcome({"description": {"aborted": True, "compute_ph": {"exit_code": 33}}})[0] == "failed"
    assert ton.tx_outcome({"description": {"compute_ph": {"success": True},
                                           "action": {"success": False, "result_code": 37}}})[0] == "failed"


# --- connect -------------------------------------------------------------------------------------------------


def _proof(payload: str, domain="helmterminals.xyz", ts=None, address=W4):
    ts = int(ts or time.time())
    digest = ton.proof_digest(ton.parse_address(address), domain, ts, payload)
    return {"timestamp": ts, "domain": {"value": domain, "lengthBytes": len(domain.encode())}, "payload": payload,
            "signature": base64.b64encode(bytes(KP.sign_message(digest))).decode()}


async def _connect(client, h, **over):
    payload = (await client.post("/chains/ton/ton/proof-payload", headers=h)).json()["payload"]
    body = {"address": W4, "chain": "-239", "state_init": V["wallet"]["v4_state_init"],
            "public_key": V["wallet"]["pub"], "proof": _proof(payload)}
    body.update(over.pop("body", {}))
    if "proof" in over:
        body["proof"] = over["proof"](payload)
    return await client.post("/chains/ton/ton/connect", json=body, headers=h)


async def test_ton_is_off_unless_listed_and_helm_never_creates_ton_keys(client, monkeypatch):
    h = await signup(client, main=False)
    listed = {n["id"]: n for n in (await client.get("/chains")).json()["extra"]}
    assert listed["ton"]["enabled"] and listed["ton"]["network_id"] == "-239"  # TON mainnet on by default
    monkeypatch.setattr(get_settings(), "extra_networks", "")
    listed = {n["id"]: n for n in (await client.get("/chains")).json()["extra"]}
    assert not listed["ton"]["enabled"]
    assert listed["ton"]["capabilities"]["launches"].startswith("no")
    assert (await client.post("/chains/ton/ton/proof-payload", headers=h)).status_code == 409
    monkeypatch.setattr(get_settings(), "extra_networks", "ton")
    r = await client.post("/wallets", json={"network": "ton", "count": 1}, headers=h)
    assert r.status_code == 400 and "TON Connect" in r.text


async def test_connect_binds_the_wallet_only_with_a_valid_proof(client, tonnet):
    h = await signup(client, main=False)
    assert (await _connect(client, h, body={"chain": "-3"})).status_code == 400  # a testnet wallet isn't accepted
    assert "other than Helm" in (await _connect(client, h, proof=lambda p: _proof(p, domain="evil.example"))).text
    assert "expired" in (await _connect(client, h, proof=lambda p: _proof(p, ts=time.time() - 3600))).text
    r = await _connect(client, h, body={"public_key": "ab" * 32})
    assert r.status_code == 400 and "key" in r.text
    r = await _connect(client, h, body={"state_init": V["wallet"]["v5_state_init"]})  # someone else's contract
    assert r.status_code == 400 and "doesn't belong" in r.text
    r = await _connect(client, h, proof=lambda p: {**_proof(p), "payload": "00" * 32})  # forged payload
    assert r.status_code == 400 and "wasn't issued" in r.text
    r = await _connect(client, h)
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["kind"] == "connected" and w["address"] == ton.parse_address(W4).friendly(False, False)
    again = await _connect(client, h)
    assert again.json()["id"] == w["id"]  # connecting again doesn't duplicate it
    wallets = (await client.get("/wallets", headers=h)).json()
    assert [x["kind"] for x in wallets if x["network"] == "ton"] == ["connected"]
    # a payload issued to one Helm user can't be used by another (sessions are per user)
    h2 = await signup(client, email="b@example.com", main=False)
    p1 = (await client.post("/chains/ton/ton/proof-payload", headers=h)).json()["payload"]
    r = await client.post("/chains/ton/ton/connect", headers=h2, json={
        "address": W4, "chain": "-239", "state_init": V["wallet"]["v4_state_init"], "proof": _proof(p1)})
    assert r.status_code == 400 and "wasn't issued" in r.text


async def test_deployed_wallet_proof_uses_the_indexed_contract(client, tonnet):
    si = ton.cell_from_b64(V["wallet"]["v4_state_init"])
    code, data = ton.state_init_parts(si)
    h = await signup(client, main=False)
    r = await _connect(client, h, body={"state_init": None})
    assert r.status_code == 400 and "isn't deployed" in r.text
    tonnet.accounts[W4] = {"status": "active", "balance": "1", "code_boc": code.boc_b64(), "data_boc": data.boc_b64()}
    assert (await _connect(client, h, body={"state_init": None})).status_code == 201


async def test_watching_a_ton_address_stores_one_canonical_form(client, tonnet):
    h = await signup(client, main=False)
    r = await client.post("/wallets/watch", json={"network": "ton", "address": V["addr"]["b"]}, headers=h)
    assert r.status_code == 201 and r.json()["address"] == V["addr"]["nb"]
    r = await client.post("/wallets/watch", json={"network": "ton", "address": V["addr"]["raw"]}, headers=h)
    assert r.status_code == 409
    for testnet_form in (V["addr"]["tb"], V["addr"]["tnb"]):  # testnet-only addresses are refused
        r = await client.post("/wallets/watch", json={"network": "ton", "address": testnet_form}, headers=h)
        assert r.status_code == 400


# --- balances, history, health -------------------------------------------------------------------------------


async def test_balances_jettons_history_and_health(client, tonnet):
    h = await signup(client, main=False)
    w = (await _connect(client, h)).json()
    tonnet.accounts[W4] = {"status": "active", "balance": "3500000000"}
    tonnet.jetton_wallets[(W4, JETTON)] = {"address": JW.upper(), "balance": "2500000", "owner": W4.upper(),
                                           "jetton": JETTON.upper()}
    b = (await client.get(f"/chains/ton/wallets/{w['id']}/balances?tokens={JETTON}", headers=h)).json()
    assert b["native"]["amount"] == "3.5" and b["native"]["asset"] == "TON"
    assert b["tokens"][0]["amount"] == "2.5" and b["tokens"][0]["symbol"] == "TUSD" and not b["errors"]
    hist = (await client.get(f"/chains/ton/wallets/{w['id']}/history", headers=h)).json()
    row = hist["chain"][0]
    assert row["direction"] == "in" and row["raw"] == "2500000000" and row["reference"] == bytes(range(32)).hex()
    assert row["explorer"].startswith("https://tonviewer.com/transaction/")
    assert (await client.get("/chains/ton/health", headers=h)).json()["ok"] is True
    tonnet.fail = "toncenter is unreachable right now (HTTP 503)"
    b = (await client.get(f"/chains/ton/wallets/{w['id']}/balances", headers=h)).json()
    assert b["native"] is None and "unreachable" in b["errors"][0]


# --- transfers -----------------------------------------------------------------------------------------------


async def _confirm(client, h, body, key="k1"):
    pf = (await client.post("/chains/ton/ton/transfer", json=body, headers=h)).json()
    r = await client.post("/chains/ton/ton/transfer", headers=h, json={
        **body, "confirm": True, "preflight_hash": pf.get("preflight_hash"), "idempotency_key": key})
    return pf, r


def _wallet_tx(msgs, h=9, **desc):
    return {"hash": base64.b64encode(bytes([h]) * 32).decode(),
            "description": {"aborted": False, "compute_ph": {"success": True}, "action": {"success": True}, **desc},
            "out_msgs": [{"destination": m["address"], "value": m["amount"], "hash": f"out{h}{i}",
                          "message_content": {"body": m.get("payload")}} for i, m in enumerate(msgs)]}


async def test_native_transfer_job_first_then_wallet_signed_message_is_followed(client, tonnet, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    h = await signup(client, main=False)
    w = (await _connect(client, h)).json()
    tonnet.accounts[W4] = {"status": "active", "balance": "3000000000"}
    body = {"wallet_id": w["id"], "to": V["addr"]["b"], "amount": "1.25"}
    pf, r = await _confirm(client, h, body)
    p = pf["preflight"]
    req = p["wallet_request"]
    assert pf["requires_2fa"] is False and p["kind"] == "ton_transfer"
    assert req["network"] == "-239" and req["from"] == W4
    # the recipient isn't deployed: sent non-bounceable so it arrives
    assert req["messages"] == [{"address": ton.parse_address(V["addr"]["raw"]).friendly(False, False),
                                "amount": "1250000000"}]
    assert "non-bounceable" in p["recipient_note"]
    # confirming creates the job before the wallet is asked, and hands back exactly the reviewed request
    assert r.status_code == 200 and r.json()["status"] == "pending" and r.json()["wallet_request"] == req
    jid = r.json()["job_id"]
    # the wallet spends the balance before Helm hears back: the job is already there, nothing is re-checked
    tonnet.accounts[W4] = {"status": "active", "balance": "1"}
    again = (await client.post("/chains/ton/ton/transfer", headers=h, json={
        **body, "confirm": True, "preflight_hash": pf["preflight_hash"], "idempotency_key": "k1"})).json()
    assert again["job_id"] == jid and again["wallet_request"] == req
    bad = await client.post(f"/chains/ton/ton/jobs/{jid}/signed", headers=h, json={"signed_boc": V["jetton"]["boc"]})
    assert bad.status_code == 400 and "signed message" in bad.text
    tonnet.by_msg[V["ext_inline"]["norm_hash"]] = [_wallet_tx(req["messages"])]
    r = await client.post(f"/chains/ton/ton/jobs/{jid}/signed", headers=h, json={"signed_boc": V["ext_inline"]["boc"]})
    assert r.status_code == 200 and r.json()["message_hash"] == V["ext_inline"]["norm_hash"]
    job = (await client.get(f"/jobs/{jid}", headers=h)).json()
    assert job["status"] == "confirmed" and job["transactions"][0]["signature"] == (bytes([9]) * 32).hex()
    # the same signed message can't confirm a second transfer
    _, r2 = await _confirm(client, h, {**body, "amount": "1.25"}, key="k9")
    tonnet.accounts[W4] = {"status": "active", "balance": "3000000000"}
    _, r2 = await _confirm(client, h, body, key="k10")
    j2 = r2.json()["job_id"]
    await client.post(f"/chains/ton/ton/jobs/{j2}/signed", headers=h, json={"signed_boc": V["ext_inline"]["boc"]})
    job2 = (await client.get(f"/jobs/{j2}", headers=h)).json()
    assert job2["status"] == "failed" and "already recorded" in job2["transactions"][0]["error"]


async def test_declined_unseen_failed_and_different_messages(client, tonnet, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    from app.routes import ton as ton_routes

    monkeypatch.setattr(ton_routes, "CONFIRM_TIMEOUT_S", 0.05)
    h = await signup(client, main=False)
    w = (await _connect(client, h)).json()
    tonnet.accounts[W4] = {"status": "active", "balance": "3000000000"}
    tonnet.accounts[OTHER] = {"status": "active", "balance": "1"}
    body = {"wallet_id": w["id"], "to": OTHER, "amount": "1"}
    pf, r = await _confirm(client, h, body, key="d1")
    msgs = pf["preflight"]["wallet_request"]["messages"]
    assert msgs[0]["address"].startswith("E")  # active recipient: bounceable, mainnet form
    jid = r.json()["job_id"]
    assert (await client.post(f"/chains/ton/ton/jobs/{jid}/declined", headers=h)).json()["status"] == "failed"
    job = (await client.get(f"/jobs/{jid}", headers=h)).json()
    assert "Nothing was sent" in job["error"]
    r = await client.post(f"/chains/ton/ton/jobs/{jid}/signed", headers=h, json={"signed_boc": V["ext"]["boc"]})
    assert r.json()["status"] == "failed"  # a declined job doesn't come back to life
    # never seen on the index: unconfirmed, not failed
    _, r = await _confirm(client, h, body, key="d2")
    jid = r.json()["job_id"]
    await client.post(f"/chains/ton/ton/jobs/{jid}/signed", headers=h, json={"signed_boc": V["ext"]["boc"]})
    job = (await client.get(f"/jobs/{jid}", headers=h)).json()
    assert job["status"] == "unconfirmed" and "check your wallet" in job["transactions"][0]["error"]
    # the wallet rejected it
    tonnet.by_msg[V["ext_inline"]["norm_hash"]] = [{"hash": "ab" * 32, "description": {
        "aborted": True, "compute_ph": {"success": False, "exit_code": 33}}}]
    _, r = await _confirm(client, h, body, key="d3")
    jid = r.json()["job_id"]
    await client.post(f"/chains/ton/ton/jobs/{jid}/signed", headers=h, json={"signed_boc": V["ext_inline"]["boc"]})
    job = (await client.get(f"/jobs/{jid}", headers=h)).json()
    assert job["status"] == "failed" and "33" in job["transactions"][0]["error"]
    # the wallet sent something other than what was reviewed
    tonnet.by_msg[V["ext_inline"]["norm_hash"]] = [_wallet_tx([{**msgs[0], "amount": "999"}], h=7)]
    _, r = await _confirm(client, h, body, key="d4")
    jid = r.json()["job_id"]
    await client.post(f"/chains/ton/ton/jobs/{jid}/signed", headers=h, json={"signed_boc": V["ext_inline"]["boc"]})
    job = (await client.get(f"/jobs/{jid}", headers=h)).json()
    assert job["status"] == "failed" and "isn't the transfer you reviewed" in job["transactions"][0]["error"]
    # not enough TON is refused before any job exists
    r = (await client.post("/chains/ton/ton/transfer", headers=h, json={**body, "amount": "3"})).json()
    assert any("less than 3 TON" in x for x in r["preflight"]["warnings"])


async def test_jetton_transfer_is_followed_through_both_jetton_wallets(client, tonnet, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    h = await signup(client, main=False)
    w = (await _connect(client, h)).json()
    tonnet.accounts[W4] = {"status": "active", "balance": "1000000000"}
    body = {"wallet_id": w["id"], "to": OTHER, "amount": "1.5", "token": JETTON}
    r = await client.post("/chains/ton/ton/transfer", json=body, headers=h)
    assert r.status_code == 400 and "holds no" in r.text
    tonnet.jetton_wallets[(W4, JETTON)] = {"address": JW, "balance": "5000000", "owner": OTHER, "jetton": JETTON}
    r = await client.post("/chains/ton/ton/transfer", json=body, headers=h)
    assert r.status_code == 502 and "someone else" in r.text  # an index answer for another owner is refused
    tonnet.jetton_wallets[(W4, JETTON)] = {"address": JW, "balance": "5000000", "owner": W4, "jetton": JETTON}
    pf, r = await _confirm(client, h, body, key="j1")
    p = pf["preflight"]
    assert p["amount_raw"] == "1500000" and p["decimals"] == 6 and p["asset"] == "TUSD"
    assert p["token"] == ton.parse_address(JETTON).friendly(True, False)
    (m,) = p["wallet_request"]["messages"]
    assert ton.parse_address(m["address"]).raw() == JW and ton.parse_address(m["address"]).bounceable
    assert m["amount"] == "50000000"
    t = ton.read_jetton_transfer(ton.cell_from_b64(m["payload"]))
    assert t["amount"] == 1500000 and t["destination"].raw() == OTHER and t["response"].raw() == W4
    wallet_tx = _wallet_tx([m])
    tonnet.by_msg[V["ext_inline"]["norm_hash"]] = [wallet_tx]
    ok = {"hash": "cd" * 32, "description": {"aborted": False, "compute_ph": {"success": True}, "action": {"success": True}},
          "out_msgs": [{"hash": "hop2"}]}
    tonnet.by_msg["out90"] = [{**ok, "description": {"aborted": True, "compute_ph": {"success": False, "exit_code": 47}}}]
    jid = r.json()["job_id"]
    await client.post(f"/chains/ton/ton/jobs/{jid}/signed", headers=h, json={"signed_boc": V["ext_inline"]["boc"]})
    job = (await client.get(f"/jobs/{jid}", headers=h)).json()
    assert job["status"] == "failed" and "tokens didn't move" in job["transactions"][0]["error"]
    # both jetton wallets succeed: confirmed
    tonnet.by_msg[V["ext"]["norm_hash"]] = [_wallet_tx([m], h=5)]
    tonnet.by_msg["out50"] = [ok]
    tonnet.by_msg["hop2"] = [{**ok, "out_msgs": []}]
    _, r = await _confirm(client, h, body, key="j2")
    jid = r.json()["job_id"]
    await client.post(f"/chains/ton/ton/jobs/{jid}/signed", headers=h, json={"signed_boc": V["ext"]["boc"]})
    assert (await client.get(f"/jobs/{jid}", headers=h)).json()["status"] == "confirmed"
    tonnet.accounts[W4] = {"status": "active", "balance": "1000"}
    r = (await client.post("/chains/ton/ton/transfer", json=body, headers=h)).json()
    assert any("fees" in x for x in r["preflight"]["warnings"])
    r = await client.post("/chains/ton/ton/transfer", headers=h, json={**body, "amount": "1e40"})
    assert r.status_code == 400


def test_hostile_bocs_and_proofs_are_rejected_cleanly():
    import struct as st

    huge = bytes.fromhex("b5ee9c72") + bytes([0x04, 1]) + st.pack(">I", 1) + st.pack(">I", 2**32 - 1) + bytes(6)
    deep = ton.Cell(1, 1)
    for _ in range(1100):
        deep = ton.Cell(1, 1, [deep])
    import sys

    old = sys.getrecursionlimit()
    sys.setrecursionlimit(20000)
    try:
        deep_boc = ton.serialize_boc(deep)
    finally:
        sys.setrecursionlimit(old)
    for bad in (huge, b"\xb5\xee\x9c\x72", deep_boc, b"not a boc", base64.b64decode(V["ext"]["boc"])[:-10]):
        with pytest.raises(ValueError):
            ton.cell_from_b64(base64.b64encode(bad).decode())
    a = ton.parse_address(W4)
    for proof in ({"domain": "helmterminals.xyz"}, {"domain": {"value": "helmterminals.xyz", "lengthBytes": [17]}},
                  {"domain": {"value": "helmterminals.xyz", "lengthBytes": 17}, "payload": "p", "timestamp": {"x": 1}}):
        with pytest.raises(ValueError):
            ton.verify_proof(a, proof, bytes(32), "helmterminals.xyz", "p")


async def test_only_real_workchains(client, tonnet):
    h = await signup(client, main=False)
    w = (await _connect(client, h)).json()
    tonnet.accounts[W4] = {"status": "active", "balance": "3000000000"}
    r = await client.post("/chains/ton/ton/transfer", headers=h,
                          json={"wallet_id": w["id"], "to": "5:" + "11" * 32, "amount": "1"})
    assert r.status_code == 400 and "basechain" in r.text


async def test_watch_only_and_other_routes_cannot_move_ton(client, tonnet):
    h = await signup(client, main=False)
    wa = (await client.post("/wallets/watch", json={"network": "ton", "address": OTHER}, headers=h)).json()
    r = await client.post("/chains/ton/ton/transfer", headers=h,
                          json={"wallet_id": wa["id"], "to": W4, "amount": "1"})
    assert r.status_code == 400 and "TON Connect" in r.text
    r = await client.post("/chains/ton/transfer", headers=h, json={"wallet_id": wa["id"], "to": W4, "amount": "1"})
    assert r.status_code == 400 and "own wallet" in r.text
    r = await client.post("/wallets/withdraw", headers=h, json={"wallet_ids": [wa["id"]], "destination": W4})
    assert r.status_code == 400
