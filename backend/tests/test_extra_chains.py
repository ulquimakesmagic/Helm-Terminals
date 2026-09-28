"""TRON, NEAR and Arc: address formats, transaction encoding (checked against TronWeb and near-api-js output),
signing, network gating, balances, previews with resource/storage costs, confirmation, failures and unsupported
features. No network access, no real keys: fakes stand in for TronGrid, NEAR RPC and Arc RPC."""

import asyncio
import hashlib
import json
import time

import httpx
import pytest
from eth_keys import keys

from app.chains import near, tron
from app.chains.base import AdapterError
from app.config import get_settings
from app.services import extra_chains as xc

from .conftest import enable_2fa, signup, totp_code

# --- vectors produced by TronWeb 6 (utils.transaction.txJsonToPb / txPbToTxID) and @near-js/transactions 1 --
TW = {"owner": "TJRabPrwbZy45sbavfcjinPJC18kjpRTv8", "to": "TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj",
      "token": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
      "trx": {"raw_data_hex": "0a021a2b220801020304050607084080b08fe6b2775a67080112630a2d747970652e676f6f676c65617069732e636f6d2f70726f746f636f6c2e5472616e73666572436f6e747261637412320a15415cbdd86a2fa8dc4bddd8a8f69dba48572eec07fb121541ea51342dabbb928ae1e576bd39eff8aaf070a8c61887ad4b70a0db8be6b277",
              "txID": "ea1407496c807277ecb22e190a32b347e0fb7121b8f7db8c1bef03e2c25e7846"},
      "trc20": {"raw_data_hex": "0a021a2b220801020304050607084080b08fe6b2775aae01081f12a9010a31747970652e676f6f676c65617069732e636f6d2f70726f746f636f6c2e54726967676572536d617274436f6e747261637412740a15415cbdd86a2fa8dc4bddd8a8f69dba48572eec07fb121541a614f803b6fd780986a42c78ec9c7f77e6ded13c2244a9059cbb000000000000000000000000ea51342dabbb928ae1e576bd39eff8aaf070a8c600000000000000000000000000000000000000000000000000000000004c4b4070a0db8be6b27790018087a70e",
                "txID": "9ba328398dfb44b31109d88df9df8804f6b9816fd5e8363398431dc222f88431"}}
NEAR_JS = ("0d000000616c6963652e746573746e657400725da2eda4b2ee1b9e3221f046e23f92035a7f2ae6758c51184ce8ba6eb1b70a"
           "07000000000000000b000000626f622e746573746e6574000102030405060708090a0b0c0d0e0f101112131415161718191a1b"
           "1c1d1e1f0200000003000000a1edccce1bc2d3000000000000020b00000066745f7472616e736665722a0000007b2272656365"
           "697665725f6964223a22626f622e746573746e6574222c22616d6f756e74223a2235227d00e057eb481b000001000000000000"
           "000000000000000000")
NEAR_PK = bytes.fromhex("725da2eda4b2ee1b9e3221f046e23f92035a7f2ae6758c51184ce8ba6eb1b70a")


@pytest.fixture
def extra(monkeypatch):
    """Enable the three testnets for a test, with fake clients (no network)."""
    s = get_settings()
    monkeypatch.setattr(s, "extra_networks", "tron-nile,near-testnet,arc-testnet")
    monkeypatch.setattr(s, "allow_mainnet_extra_networks", False)
    xc._clients.clear()
    yield
    xc._clients.clear()


# --- TRON: addresses, protobuf and signatures --------------------------------------------------------------


def test_tron_addresses_are_validated_with_tron_rules():
    assert tron.valid_address(TW["owner"]) and tron.valid_address(TW["token"])
    assert not tron.valid_address(TW["owner"][:-1] + ("9" if TW["owner"][-1] != "9" else "8"))  # checksum
    assert not tron.valid_address("0x5cbdd86a2fa8dc4bddd8a8f69dba48572eec07fb")  # an EVM address is not TRON
    assert not tron.valid_address("41" + "5c" * 20)
    assert tron.decode_address(TW["owner"]).hex() == "415cbdd86a2fa8dc4bddd8a8f69dba48572eec07fb"  # TronWeb toHex
    pk = keys.PrivateKey(bytes(range(1, 33)))
    # TronWeb's ecRecover of our signature gave this address for this key (see the module docstring)
    assert tron.address_from_evm(pk.public_key.to_checksum_address()) == "TK31Bs4p7BZionueRbSrG5ZTfxPyHCLy3a"


def _pb_varint(n: int) -> bytes:
    out = b""
    while True:
        b, n = n & 0x7F, n >> 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _pb(num: int, v) -> bytes:
    if isinstance(v, int):
        return _pb_varint(num << 3) + _pb_varint(v)
    return _pb_varint(num << 3 | 2) + _pb_varint(len(v)) + v


def build_tron_tx(owner: str, *, to: str | None = None, amount: int = 0, token: str | None = None,
                  token_to: str | None = None, token_amount: int = 0, fee_limit: int = 0,
                  expiration: int = 4102444800000) -> dict:
    """What TronGrid would return, encoded like TronWeb (the test below proves the encoding matches)."""
    from eth_abi import encode as abi_encode

    if token is None:
        inner = _pb(1, tron.decode_address(owner)) + _pb(2, tron.decode_address(to)) + _pb(3, amount)
        ctype, url = 1, b"type.googleapis.com/protocol.TransferContract"
    else:
        data = tron.TRC20_TRANSFER + abi_encode(["address", "uint256"], ["0x" + tron.decode_address(token_to)[1:].hex(), token_amount])
        inner = _pb(1, tron.decode_address(owner)) + _pb(2, tron.decode_address(token)) + _pb(4, data)
        ctype, url = 31, b"type.googleapis.com/protocol.TriggerSmartContract"
    contract = _pb(1, ctype) + _pb(2, _pb(1, url) + _pb(2, inner))
    raw = (_pb(1, bytes.fromhex("1a2b")) + _pb(4, bytes.fromhex("0102030405060708")) + _pb(8, expiration)
           + _pb(11, contract) + _pb(14, 4102444740000) + (_pb(18, fee_limit) if fee_limit else b""))
    return {"txID": hashlib.sha256(raw).hexdigest(), "raw_data_hex": raw.hex(), "raw_data": {}, "visible": True}


def test_our_tron_encoding_matches_tronweb():
    a = build_tron_tx(TW["owner"], to=TW["to"], amount=1234567)
    assert a["raw_data_hex"] == TW["trx"]["raw_data_hex"] and a["txID"] == TW["trx"]["txID"]
    b = build_tron_tx(TW["owner"], token=TW["token"], token_to=TW["to"], token_amount=5_000_000, fee_limit=30_000_000)
    assert b["raw_data_hex"] == TW["trc20"]["raw_data_hex"] and b["txID"] == TW["trc20"]["txID"]


def test_node_built_tron_transactions_are_checked_before_signing():
    good = dict(TW["trx"])
    tron.check_transaction(good, owner=TW["owner"], to=TW["to"], amount=1234567)
    for bad in ({"amount": 1234568}, {"to": TW["token"]}, {"owner": TW["to"]}):
        with pytest.raises(AdapterError):
            tron.check_transaction(good, **{"owner": TW["owner"], "to": TW["to"], "amount": 1234567, **bad})
    with pytest.raises(AdapterError, match="doesn't match its contents"):
        tron.check_transaction({**good, "txID": "00" * 32}, owner=TW["owner"], to=TW["to"], amount=1234567)
    tok = dict(TW["trc20"])
    kw = dict(owner=TW["owner"], token=TW["token"], token_to=TW["to"], token_amount=5_000_000)
    tron.check_transaction(tok, **kw, max_fee_limit=30_000_000)
    with pytest.raises(AdapterError, match="higher energy fee"):
        tron.check_transaction(tok, **kw, max_fee_limit=29_999_999)
    with pytest.raises(AdapterError):
        tron.check_transaction(tok, **{**kw, "token_amount": 5_000_001}, max_fee_limit=30_000_000)
    with pytest.raises(AdapterError):  # a TRX transfer presented as a token transfer
        tron.check_transaction(good, **kw, max_fee_limit=10**9)
    expired = build_tron_tx(TW["owner"], to=TW["to"], amount=1, expiration=int(time.time() * 1000) - 1000)
    with pytest.raises(AdapterError, match="expired"):
        tron.check_transaction(expired, owner=TW["owner"], to=TW["to"], amount=1)


# --- NEAR: accounts and borsh ------------------------------------------------------------------------------


def test_near_account_formats():
    for ok in ("alice.near", "bob.testnet", "a1", "app-1_x.sub.testnet", "ab" * 32, "0x" + "ab" * 20):
        assert near.valid_account(ok), ok
    for bad in ("A.near", "a", "-bob.near", "bob..near", "bob.near.", "a" * 65, "0x" + "AB" * 20, "TXLAQ63X"):
        assert not near.valid_account(bad), bad


def test_our_near_borsh_matches_near_api_js():
    tx = near.serialize_transaction(
        "alice.testnet", NEAR_PK, 7, "bob.testnet", bytes(range(32)),
        [near.transfer_action(10**24),
         near.function_call_action("ft_transfer", {"receiver_id": "bob.testnet", "amount": "5"}, 30 * near.TGAS, 1)])
    assert tx.hex() == NEAR_JS


def test_near_outcomes():
    assert near.outcome_status({"status": {"SuccessValue": ""}, "receipts_outcome": []}) == ("confirmed", None)
    st, err = near.outcome_status({"status": {"SuccessValue": ""}, "receipts_outcome": [
        {"outcome": {"status": {"Failure": {"ActionError": {}}}}}]})
    assert st == "failed" and "receipt failed" in err
    assert near.available_balance({"amount": str(5 * 10**24), "storage_usage": 1000}) == 5 * 10**24 - 1000 * 10**19


# --- fakes ---------------------------------------------------------------------------------------------------


class FakeTron(tron.TronClient):
    def __init__(self):
        super().__init__("https://tron.test")
        self.accounts = {}  # address -> sun (present = activated)
        self.tokens = {}  # (token, holder) -> raw
        self.resources_ = {"free_bandwidth": 600, "staked_bandwidth": 0, "energy": 0}
        self.broadcasts, self.fail = [], None
        self.solid_after = 0

    async def _req(self, method, path, body=None, params=None, retries=None):
        if self.fail:
            raise AdapterError(self.fail)
        if path == "/wallet/getaccount":
            a = body["address"]
            return {"balance": self.accounts[a]} if a in self.accounts else {}
        if path == "/wallet/getaccountresource":
            r = self.resources_
            return {"freeNetLimit": 600, "freeNetUsed": 600 - r["free_bandwidth"], "EnergyLimit": r["energy"]}
        if path == "/wallet/getchainparameters":
            return {"chainParameter": [{"key": "getEnergyFee", "value": 100}, {"key": "getTransactionFee", "value": 1000},
                                       {"key": "getCreateAccountFee", "value": 100000},
                                       {"key": "getCreateNewAccountFeeInSystemContract", "value": 1000000},
                                       {"key": "getMaxFeeLimit", "value": 15_000_000_000}]}
        if path == "/wallet/triggerconstantcontract":
            sel, tok = body["function_selector"], body["contract_address"]
            from eth_abi import encode as e

            if sel == "balanceOf(address)":
                holder = tron.encode_address(b"\x41" + bytes.fromhex(body["parameter"][-40:]))
                val = e(["uint256"], [self.tokens.get((tok, holder), 0)]).hex()
            elif sel == "decimals()":
                val = e(["uint8"], [6]).hex()
            elif sel in ("name()", "symbol()"):
                val = e(["string"], ["Tether USD" if sel == "name()" else "USDT"]).hex()
            else:
                return {"result": {"result": True}, "energy_used": 64_285, "constant_result": [""]}
            return {"result": {"result": True}, "constant_result": [val], "energy_used": 400}
        if path == "/wallet/createtransaction":
            return build_tron_tx(body["owner_address"], to=body["to_address"], amount=body["amount"])
        if path == "/wallet/triggersmartcontract":
            from eth_abi import decode as d

            to_raw, amt = d(["address", "uint256"], bytes.fromhex(body["parameter"]))
            tx = build_tron_tx(body["owner_address"], token=body["contract_address"],
                               token_to=tron.encode_address(b"\x41" + bytes.fromhex(to_raw[2:])),
                               token_amount=amt, fee_limit=body["fee_limit"])
            return {"result": {"result": True}, "transaction": tx}
        if path == "/wallet/broadcasttransaction":
            self.broadcasts.append(body)
            return {"result": True, "txid": body["txID"]}
        if path == "/walletsolidity/gettransactioninfobyid":
            self.solid_after -= 1
            return {} if self.solid_after >= 0 else {"id": body["value"], "receipt": {"result": "SUCCESS"}}
        if path.startswith("/v1/accounts/"):
            if path.endswith("/trc20"):
                return {"data": [{"transaction_id": "cd" * 32, "block_timestamp": 2, "value": "1500000", "to": TW["to"],
                                  "token_info": {"address": TW["token"], "symbol": "USDT", "decimals": 6}}]}
            return {"data": [{"txID": "ab" * 32, "block_timestamp": 1, "ret": [{"contractRet": "SUCCESS"}],
                              "raw_data": {"contract": [{"type": "TransferContract", "parameter": {"value": {
                                  "amount": 2_500_000, "to_address": tron.decode_address(TW["to"]).hex()}}}]}}]}
        raise AssertionError(path)


class FakeNear(near.NearClient):
    def __init__(self):
        super().__init__(["https://near.test"])
        self.accounts, self.ft, self.registered, self.sent, self.fail = {}, {}, set(), [], None

    async def call(self, method, params, resend=True):
        if self.fail:
            raise AdapterError(self.fail)
        if method == "query":
            rt = params["request_type"]
            if rt == "view_account":
                a = params["account_id"]
                if a not in self.accounts:
                    raise AdapterError("NEAR RPC error UNKNOWN_ACCOUNT")
                return {"amount": str(self.accounts[a]), "storage_usage": 182}
            if rt == "view_access_key":
                return {"nonce": 41, "permission": "FullAccess"}
            if rt == "call_function":
                import base64

                args = json.loads(base64.b64decode(params["args_base64"]))
                m = params["method_name"]
                value = {"ft_metadata": {"name": "Test USD", "symbol": "TUSD", "decimals": 6},
                         "ft_balance_of": str(self.ft.get(args.get("account_id"), 0)),
                         "storage_balance_of": ({"total": "1"} if args.get("account_id") in self.registered else None),
                         "storage_balance_bounds": {"min": "1250000000000000000000"}}[m]
                return {"result": list(json.dumps(value).encode())}
        if method == "block":
            return {"header": {"hash": near._b58(bytes(range(32)))}}
        if method == "gas_price":
            return {"gas_price": "100000000"}
        if method == "EXPERIMENTAL_protocol_config":
            return {"runtime_config": {"storage_amount_per_byte": str(10**19)}}
        if method == "send_tx":
            self.sent.append(params)
            return {"status": {"SuccessValue": ""}, "receipts_outcome": []}
        raise AssertionError(method)


# --- gating and wallets --------------------------------------------------------------------------------------


async def test_new_networks_are_off_unless_enabled_and_mainnets_need_an_opt_in(client, monkeypatch):
    h = await signup(client, main=False)
    r = await client.post("/wallets", json={"network": "tron-nile", "count": 1}, headers=h)
    assert r.status_code == 400
    s = get_settings()
    monkeypatch.setattr(s, "extra_networks", "tron,near,arc,tron-nile")
    listed = {n["id"]: n for n in (await client.get("/chains")).json()["extra"]}
    assert listed["tron-nile"]["enabled"] and not listed["tron"]["enabled"] and not listed["arc"]["enabled"]
    assert listed["arc-testnet"]["chain_id"] == 5042002 and listed["arc"]["chain_id"] == 5042
    assert "launches" in listed["tron"]["capabilities"] and listed["tron"]["capabilities"]["launches"].startswith("no")
    assert (await client.post("/wallets", json={"network": "tron", "count": 1}, headers=h)).status_code == 400
    monkeypatch.setattr(s, "allow_mainnet_extra_networks", True)
    assert (await client.get("/chains")).json()["extra"][0]["enabled"] is True  # tron mainnet, now opted in


async def test_managed_wallets_use_each_networks_own_address_format(client, extra):
    h = await signup(client, main=False)
    made = {}
    for net in ("tron-nile", "near-testnet", "arc-testnet"):
        r = await client.post("/wallets", json={"network": net, "count": 1}, headers=h)
        assert r.status_code == 201, r.text
        made[net] = r.json()[0]["address"]
    assert tron.valid_address(made["tron-nile"])
    assert near.IMPLICIT.match(made["near-testnet"])
    assert made["arc-testnet"].startswith("0x") and len(made["arc-testnet"]) == 42
    # watch-only: each network's rules, not EVM's
    for net, addr, ok in (("tron-nile", TW["to"], 201), ("tron-nile", "0x" + "ab" * 20, 400),
                          ("near-testnet", "alice.testnet", 201), ("near-testnet", "Alice", 400),
                          ("arc-testnet", "0x" + "cd" * 20, 201), ("arc-testnet", TW["to"], 400)):
        r = await client.post("/wallets/watch", json={"network": net, "address": addr}, headers=h)
        assert r.status_code == ok, (net, addr, r.text)
    # core fund-moving routes refuse these wallets; they use the network's own transfer
    wid = [w for w in (await client.get("/wallets", headers=h)).json() if w["network"] == "tron-nile"][0]["id"]
    r = await client.post("/wallets/withdraw", json={"wallet_ids": [wid], "destination": TW["to"], "all": True},
                          headers=h)
    assert r.status_code == 400 and "own transfer" in r.json()["detail"]


async def test_importing_keys_on_new_networks_is_refused(client, extra):
    h = await signup(client, main=False)
    secret = await enable_2fa(client, h)
    r = await client.post("/wallets/import", json={"network": "tron-nile", "private_key": "11" * 32,
                                                   "password": "correct-horse-battery",
                                                   "totp": await totp_code(secret)}, headers=h)
    assert r.status_code in (400, 501)


# --- balances, metadata, history ----------------------------------------------------------------------------


async def _wallet(client, h, net):
    return (await client.post("/wallets", json={"network": net, "count": 1}, headers=h)).json()[0]


async def test_tron_balances_metadata_and_history(client, extra):
    t = FakeTron()
    xc._clients["tron-nile"] = t
    h = await signup(client, main=False)
    w = await _wallet(client, h, "tron-nile")
    t.accounts[w["address"]] = 12_500_000
    t.tokens[(TW["token"], w["address"])] = 7_000_000
    r = (await client.get(f"/chains/tron-nile/wallets/{w['id']}/balances?tokens={TW['token']}", headers=h)).json()
    assert r["native"]["amount"] == "12.5" and r["native"]["asset"] == "TRX"
    assert r["tokens"][0]["symbol"] == "USDT" and r["tokens"][0]["amount"] == "7" and "nile.tronscan.org" in r["explorer"]
    m = (await client.get(f"/chains/tron-nile/tokens/{TW['token']}", headers=h)).json()
    assert m["decimals"] == 6 and m["explorer"].endswith(TW["token"])
    assert (await client.get("/chains/tron-nile/tokens/0xdeadbeef", headers=h)).status_code == 400
    hist = (await client.get(f"/chains/tron-nile/wallets/{w['id']}/history", headers=h)).json()
    trc20, trx = hist["chain"]  # newest first
    assert trx["status"] == "SUCCESS" and trx["raw"] == "2500000" and trx["decimals"] == 6 and trx["to"] == TW["to"]
    assert trc20["token"] == TW["token"] and trc20["raw"] == "1500000" and trc20["explorer"].endswith("cd" * 32)
    wallets = (await client.get("/wallets?with_balances=true", headers=h)).json()
    assert wallets[0]["balance_native"] == "12.5"
    t.fail = "TronGrid is unreachable right now (HTTP 503)"
    r = (await client.get(f"/chains/tron-nile/wallets/{w['id']}/balances", headers=h)).json()
    assert r["native"] is None and "unreachable" in r["errors"][0]


async def test_near_balances_are_after_storage_and_tokens_by_contract(client, extra):
    n = FakeNear()
    xc._clients["near-testnet"] = n
    h = await signup(client, main=False)
    w = await _wallet(client, h, "near-testnet")
    n.accounts[w["address"]] = 2 * 10**24
    n.ft[w["address"]] = 1_500_000
    r = (await client.get(f"/chains/near-testnet/wallets/{w['id']}/balances?tokens=usdc.testnet", headers=h)).json()
    assert r["native"]["amount"] == str(2 - 182 * 10**19 / 10**24).rstrip("0")  # storage kept aside
    assert r["tokens"][0]["symbol"] == "TUSD" and r["tokens"][0]["amount"] == "1.5"
    hist = (await client.get(f"/chains/near-testnet/wallets/{w['id']}/history", headers=h)).json()
    assert hist["chain"] == [] and "no official account-history API" in hist["note"]


# --- transfers ----------------------------------------------------------------------------------------------


async def _confirm(client, h, path, body, secret, key):
    pf = await client.post(path, json=body, headers=h)
    assert pf.status_code == 200, pf.text
    data = pf.json()
    r = await client.post(path, json={**body, "confirm": True, "preflight_hash": data["preflight_hash"],
                                      "idempotency_key": key, "totp": await totp_code(secret)}, headers=h)
    return data, r


async def test_tron_trx_transfer_shows_activation_and_bandwidth_then_waits_for_solidity(client, extra, monkeypatch):
    t = FakeTron()
    xc._clients["tron-nile"] = t
    monkeypatch.setattr(tron.asyncio, "sleep", _nosleep)
    h = await signup(client, main=False)
    secret = await enable_2fa(client, h)
    w = await _wallet(client, h, "tron-nile")
    t.accounts[w["address"]] = 5 * tron.SUN
    body = {"wallet_id": w["id"], "to": TW["to"], "amount": "2"}
    pf, r = await _confirm(client, h, "/chains/tron-nile/transfer", body, secret, "tron-trx-1")
    p = pf["preflight"]
    assert p["kind"] == "withdraw" and p["amount_raw"] == "2000000" and p["activation_fee_trx"] == "1"
    assert "activates it" in p["recipient_note"] and pf["requires_2fa"] is True
    assert r.status_code == 200, r.text
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "confirmed" and len(t.broadcasts) == 1
    sig = bytes.fromhex(t.broadcasts[0]["signature"][0])
    assert len(sig) == 65 and sig[64] in (0, 1)
    # the signature recovers to the sending wallet (TRON = secp256k1 over the txID)
    pub = keys.Signature(sig[:64] + bytes([sig[64]])).recover_public_key_from_msg_hash(bytes.fromhex(t.broadcasts[0]["txID"]))
    assert tron.address_from_pubkey(pub.to_bytes()) == w["address"]
    # not enough TRX once the activation fee is counted
    t.accounts[w["address"]] = int(2.5 * tron.SUN)
    pf = (await client.post("/chains/tron-nile/transfer", json=body, headers=h)).json()
    assert any("plus about 1 TRX" in x for x in pf["preflight"]["warnings"])


async def _nosleep(*_):
    return None


async def test_trc20_transfer_counts_energy_and_refuses_without_trx(client, extra, monkeypatch):
    t = FakeTron()
    xc._clients["tron-nile"] = t
    monkeypatch.setattr(tron.asyncio, "sleep", _nosleep)
    h = await signup(client, main=False)
    secret = await enable_2fa(client, h)
    w = await _wallet(client, h, "tron-nile")
    t.accounts[w["address"]] = 1 * tron.SUN
    t.accounts[TW["to"]] = 0
    t.tokens[(TW["token"], w["address"])] = 10_000_000
    body = {"wallet_id": w["id"], "to": TW["to"], "amount": "3", "token": TW["token"]}
    pf = (await client.post("/chains/tron-nile/transfer", json=body, headers=h)).json()["preflight"]
    assert pf["kind"] == "send_tokens" and pf["energy_needed"] == 64_285 and pf["energy_fee_trx"] == "6.4285"
    assert any("energy and bandwidth" in x for x in pf["warnings"])  # 1 TRX isn't enough to burn for energy
    t.resources_["energy"] = 100_000  # staked energy covers it
    pf, r = await _confirm(client, h, "/chains/tron-nile/transfer", body, secret, "tron-trc20-1")
    assert not pf["preflight"]["warnings"] and r.status_code == 200, r.text
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "confirmed" and t.broadcasts[-1]["raw_data_hex"]


async def test_tron_rpc_failure_and_unsolidified_transfers(client, extra, monkeypatch):
    t = FakeTron()
    xc._clients["tron-nile"] = t
    monkeypatch.setattr(tron.asyncio, "sleep", _nosleep)
    h = await signup(client, main=False)
    secret = await enable_2fa(client, h)
    w = await _wallet(client, h, "tron-nile")
    t.accounts[w["address"]] = 5 * tron.SUN
    t.accounts[TW["to"]] = 1
    t.solid_after = 10**9  # never solidifies within the wait
    body = {"wallet_id": w["id"], "to": TW["to"], "amount": "1"}
    import app.chains.tron as tr

    orig = tr.sign_and_send

    async def quick(*a, **k):
        return await orig(*a, **{**k, "confirm_timeout_s": 0.01, "poll_s": 0})

    monkeypatch.setattr(tr, "sign_and_send", quick)
    _, r = await _confirm(client, h, "/chains/tron-nile/transfer", body, secret, "tron-slow-1")
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "unconfirmed" and "solidified" in job["transactions"][0]["error"]
    t.fail = "TronGrid is unreachable right now (HTTP 503)"
    r = await client.post("/chains/tron-nile/transfer", json=body, headers=h)
    assert r.status_code == 502 and "unreachable" in r.json()["detail"]


async def test_near_transfers_register_storage_and_await_final(client, extra):
    n = FakeNear()
    xc._clients["near-testnet"] = n
    h = await signup(client, main=False)
    secret = await enable_2fa(client, h)
    w = await _wallet(client, h, "near-testnet")
    n.accounts[w["address"]] = 3 * 10**24
    n.ft[w["address"]] = 5_000_000
    body = {"wallet_id": w["id"], "to": "bob.testnet", "amount": "2", "token": "usdc.testnet"}
    # tokens to a named account that doesn't exist would be stranded: flagged and refused
    pf = (await client.post("/chains/near-testnet/transfer", json=body, headers=h)).json()
    assert any("doesn't exist" in x for x in pf["preflight"]["warnings"])
    n.accounts["bob.testnet"] = 1
    pf, r = await _confirm(client, h, "/chains/near-testnet/transfer", body, secret, "near-ft-1")
    p = pf["preflight"]
    assert p["storage_deposit_near"] == "0.00125" and "registers it" in p["recipient_note"]
    assert p["storage_deposit_yocto"] == "1250000000000000000000"  # real NEAR attached: part of the confirmed hash
    assert r.status_code == 200, r.text
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "confirmed"
    import base64

    signed = base64.b64decode(n.sent[0]["signed_tx_base64"])
    assert n.sent[0]["wait_until"] == "FINAL"
    tx, sig = signed[:-65], signed[-64:]
    from solders.pubkey import Pubkey
    from solders.signature import Signature

    assert Signature(sig).verify(Pubkey(bytes.fromhex(w["address"])), hashlib.sha256(tx).digest())
    assert b"storage_deposit" in tx and b"ft_transfer" in tx and b'"amount":"2000000"' in tx
    # native: a transfer to a named account that doesn't exist is flagged; not enough NEAR is refused
    pf = (await client.post("/chains/near-testnet/transfer", json={"wallet_id": w["id"], "to": "nobody.testnet",
                                                                   "amount": "1"}, headers=h)).json()
    assert any("doesn't exist" in x for x in pf["preflight"]["warnings"])
    pf = (await client.post("/chains/near-testnet/transfer", json={"wallet_id": w["id"], "to": "bob.testnet",
                                                                   "amount": "5"}, headers=h)).json()
    assert any("available" in x for x in pf["preflight"]["warnings"])


async def test_arc_uses_usdc_gas_and_its_own_chain(client, extra, monkeypatch):
    from app.chains.evm import evm_config
    from app.deps import get_executor
    from app.main import app
    from app.services.executor import Executor
    from app.signing import get_signer

    assert evm_config("arc-testnet")["chain_id"] == 5042002 and evm_config("arc")["native"] == "USDC"

    class ArcRPC:
        network, chain_id = "arc-testnet", 5042002

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

    rpc = ArcRPC()
    app.dependency_overrides[get_executor] = lambda: Executor(None, get_signer(), evm_rpcs={"arc-testnet": rpc})
    try:
        h = await signup(client, main=False)
        w = await _wallet(client, h, "arc-testnet")
        rpc.balance = 10**18  # 1 USDC (native, 18 decimals)
        r = (await client.get(f"/chains/arc-testnet/wallets/{w['id']}/balances", headers=h)).json()
        assert r["native"]["asset"] == "USDC" and r["native"]["amount"] == "1" and r["tokens"][0]["symbol"] == "EURC"
        assert all(t["address"].lower() != "0x" + "36" + "0" * 38 for t in r["tokens"])  # no double-counted USDC
        body = {"wallet_id": w["id"], "to": "0x" + "cd" * 20, "amount": "0.5"}
        pf = (await client.post("/chains/arc-testnet/transfer", json=body, headers=h)).json()["preflight"]
        assert pf["asset"] == "USDC" and pf["amount_raw"] == str(5 * 10**17) and "one confirmation" in pf["finality"]
        assert pf["gas_fee_usdc_max"] == "0.000861"  # 21,000 gas x (2 x 20 gwei + 1 gwei), paid in USDC
        pf = (await client.post("/chains/arc-testnet/transfer", json={**body, "amount": "1"}, headers=h)).json()
        assert any("in gas" in x for x in pf["preflight"]["warnings"])
        r = await client.post("/chains/arc-testnet/transfer", json={**body, "to": "0x" + "00" * 20}, headers=h)
        assert r.status_code == 400 and "zero address" in r.json()["detail"]
        r = await client.get("/chains/arc-testnet/tokens/0x3600000000000000000000000000000000000000", headers=h)
        assert r.status_code == 502 and "same balance as native USDC" in r.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_executor, None)


async def test_amounts_and_recipients_are_validated(client, extra):
    xc._clients["tron-nile"] = FakeTron()
    h = await signup(client, main=False)
    w = await _wallet(client, h, "tron-nile")
    for body, msg in (({"to": TW["to"], "amount": "0"}, "above zero"), ({"to": TW["to"], "amount": "1.0000001"}, "decimal places"),
                      ({"to": "0x" + "ab" * 20, "amount": "1"}, "valid"), ({"to": w["address"], "amount": "1"}, "sending wallet")):
        r = await client.post("/chains/tron-nile/transfer", json={"wallet_id": w["id"], **body}, headers=h)
        assert r.status_code == 400 and msg in r.json()["detail"], (body, r.text)


async def test_launches_swaps_and_market_data_stay_unavailable(client, extra):
    pads = (await client.get("/launchpads")).json()
    assert not any(p["network"] in ("tron-nile", "near-testnet", "arc-testnet", "tron", "near", "arc") for p in pads)
    caps = {n["id"]: n["capabilities"] for n in (await client.get("/chains")).json()["extra"]}
    for net in caps:
        for k in ("launches", "swaps", "market_data", "cross_chain"):
            assert caps[net][k].startswith("no"), (net, k)


# --- review fixes: never report a possibly-landed transfer as failed, and never resend blindly -----------------


class _Signer:
    async def sign_digest(self, ref, digest):
        return 0, 1, 2

    async def sign_message(self, ref, msg):
        return bytes(64)


async def test_tron_broadcast_is_never_retried_and_duplicates_are_resolved_by_polling(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    t = FakeTron()
    tx = build_tron_tx(TW["owner"], to=TW["to"], amount=1)
    calls = []

    async def dup(method, path, body=None, params=None, retries=None):
        if path == "/wallet/broadcasttransaction":
            calls.append(retries)
            return {"result": False, "code": "DUP_TRANSACTION_ERROR", "message": ""}
        return await FakeTron._req(t, method, path, body, params)

    t._req = dup
    txid, status, err, _ = await tron.sign_and_send(t, _Signer(), "ref", tx, confirm_timeout_s=10, poll_s=0)
    assert calls == [0] and status == "confirmed" and txid == tx["txID"]
    # a definitive refusal is a failure with nothing sent; an unclear one stays "unknown" with the txID
    async def refuse(method, path, body=None, params=None, retries=None):
        return {"result": False, "code": "SIGERROR", "message": ""}

    t._req = refuse
    assert (await tron.sign_and_send(t, _Signer(), "ref", tx, confirm_timeout_s=0, poll_s=0))[:2] == (None, "failed")

    async def busy(method, path, body=None, params=None, retries=None):
        if path == "/wallet/broadcasttransaction":
            return {"result": False, "code": "SERVER_BUSY", "message": ""}
        return {}

    t._req = busy
    txid, status, err, _ = await tron.sign_and_send(t, _Signer(), "ref", tx, confirm_timeout_s=0.01, poll_s=0)
    assert txid == tx["txID"] and status == "timeout" and "SERVER_BUSY" in err


async def test_tron_client_does_not_resend_a_broadcast_after_a_lost_answer(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    posts = []

    def handler(req):
        posts.append(req.url.path)
        raise httpx.ReadTimeout("lost", request=req)

    c = tron.TronClient("https://tron.test", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(AdapterError):
        await c.broadcast({"txID": "00"})
    assert posts == ["/wallet/broadcasttransaction"]
    posts.clear()
    with pytest.raises(AdapterError):
        await c.account(TW["owner"])  # reads are still retried
    assert len(posts) == 3


def test_trc10_tokens_attached_to_a_trc20_call_are_refused():
    tx = build_tron_tx(TW["owner"], token=TW["token"], token_to=TW["to"], token_amount=5, fee_limit=10**6)
    tron.check_transaction(tx, owner=TW["owner"], token=TW["token"], token_to=TW["to"], token_amount=5, max_fee_limit=10**6)
    raw = bytes.fromhex(tx["raw_data_hex"])
    f = tron.pb_fields(raw)
    c = tron.pb_fields(f[11][0])
    param = tron.pb_fields(c[2][0])
    inner = param[2][0] + _pb(5, 7)  # call_token_value
    param_b = _pb(1, param[1][0]) + _pb(2, inner)
    contract = _pb(1, c[1][0]) + _pb(2, param_b)
    rest = b"".join(_pb(k, v) for k, vs in f.items() if k != 11 for v in vs)
    bad_raw = rest + _pb(11, contract)
    bad = {"raw_data_hex": bad_raw.hex(), "txID": hashlib.sha256(bad_raw).hexdigest()}
    with pytest.raises(AdapterError, match="doesn't match the token transfer"):
        tron.check_transaction(bad, owner=TW["owner"], token=TW["token"], token_to=TW["to"], token_amount=5,
                               max_fee_limit=10**6)


async def test_near_send_is_not_resent_after_an_unknown_outcome(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    posts = []

    def handler(req):
        body = json.loads(req.content)
        posts.append(body["method"])
        if body["method"] == "send_tx":
            raise httpx.ReadTimeout("lost", request=req)
        if body["method"] == "query":
            return httpx.Response(200, json={"result": {"nonce": 1}})
        return httpx.Response(200, json={"result": {"header": {"hash": near._b58(bytes(32))}}})

    c = near.NearClient(["https://a.test", "https://b.test"], client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    h, status, err = await near.sign_and_send(c, _Signer(), "ref", "a" * 64, bytes(32), "bob.testnet",
                                              [near.transfer_action(1)])
    assert posts.count("send_tx") == 1 and status == "timeout" and h and "check the explorer" in err
    assert near.outcome_status({"status": {}})[0] == "timeout"


async def test_long_amounts_are_rejected_not_rounded(client, extra):
    xc._clients["tron-nile"] = FakeTron()
    h = await signup(client, main=False)
    w = await _wallet(client, h, "tron-nile")
    r = await client.post("/chains/tron-nile/transfer", json={"wallet_id": w["id"], "to": TW["to"],
                                                              "amount": "1234567890123456789012.1234567"}, headers=h)
    assert r.status_code == 400 and "decimal places" in r.text
    from app.routes.chains import _raw_amount

    assert _raw_amount("1234567890.123456789012345678", 18) == 1234567890123456789012345678
    with pytest.raises(Exception):
        _raw_amount("1234567890.1234567890123456789", 18)


async def test_arc_rpc_outage_is_reported_as_unavailable(monkeypatch):
    from app.chains.evm import EvmRPC, EvmRPCUnavailable

    def handler(req):
        return httpx.Response(503)

    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    rpc = EvmRPC("arc-testnet", ["https://arc.test"], client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(EvmRPCUnavailable) as e:
        await rpc.get_balance("0x" + "11" * 20)
    assert "Arc testnet" in str(e.value)


async def test_health_needs_a_session(client, extra):
    assert (await client.get("/chains/tron-nile/health")).status_code == 401
