"""EVM managed wallets: creation, signing, funding and withdrawing, against a fake node that verifies signatures."""

import rlp
from hexbytes import HexBytes
from eth_account import Account
from eth_account.typed_transactions import TypedTransaction
from eth_keys import keys
from eth_utils import keccak

from app.chains.evm import EVM_CHAINS, EvmRPCError
from app.deps import get_executor
from app.main import app
from app.services.evm_exec import EvmCall, EvmSender
from app.services.executor import Executor
from app.signing import get_signer

from .conftest import enable_2fa, signup, totp_code

ETH = 10**18


def decode(raw: bytes) -> dict:
    """Decode a signed tx (type-2 or legacy) and recover its sender."""
    sender = Account.recover_transaction(raw)
    if raw[0] == 2:
        d = TypedTransaction.from_bytes(HexBytes(raw)).as_dict()
        return {"from": sender, "to": "0x" + bytes(d["to"]).hex(), "value": d["value"], "nonce": d["nonce"],
                "chainId": d["chainId"], "type": 2, "data": bytes(d.get("data") or b"")}
    nonce, gas_price, gas, to, value, data, v, r, s = rlp.decode(raw)
    v = int.from_bytes(v, "big")
    return {"from": sender, "to": "0x" + to.hex(), "value": int.from_bytes(value, "big"),
            "nonce": int.from_bytes(nonce, "big"), "chainId": (v - 35) // 2, "type": 0, "data": bytes(data)}


class FakeEvm:
    def __init__(self, network: str, eip1559: bool = True):
        self.network = network
        self.chain_id = EVM_CHAINS[network]["chain_id"]
        self.eip1559 = eip1559
        self.balances: dict[str, int] = {}
        self.nonces: dict[str, int] = {}
        self.sent: list[dict] = []
        self.revert = False

    async def get_balance(self, a):
        return self.balances.get(a.lower(), 0)

    async def base_fee(self):
        return 10**9 if self.eip1559 else None

    async def max_priority_fee(self):
        return 10**8

    async def gas_price(self):
        return 3 * 10**9

    async def estimate_gas(self, tx):
        if self.revert:
            raise EvmRPCError("eth_estimateGas: execution reverted")
        return 21_000

    async def nonce(self, a):
        return self.nonces.get(a.lower(), 0)

    async def send_raw(self, raw_hex):
        d = decode(bytes.fromhex(raw_hex[2:]))
        assert d["chainId"] == self.chain_id
        src = d["from"].lower()
        assert d["nonce"] == self.nonces.get(src, 0), "nonce reuse"
        self.nonces[src] = d["nonce"] + 1
        fee = 21_000 * 10**9
        assert self.balances.get(src, 0) >= d["value"] + fee, "insufficient funds"
        self.balances[src] -= d["value"] + fee
        self.balances[d["to"].lower()] = self.balances.get(d["to"].lower(), 0) + d["value"]
        self.sent.append(d)
        return "0x" + keccak(bytes.fromhex(raw_hex[2:])).hex()

    async def receipt(self, h):
        return {"status": "0x1", "gasUsed": hex(21_000), "effectiveGasPrice": hex(10**9)}


def use_fake(fake_rpc, *nodes):
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={n.network: n for n in nodes})


async def test_local_signer_evm_signature_recovers_to_wallet_address():
    signer = get_signer()
    ref = await signer.create_wallet("u1", "base", "W")
    assert ref.address.startswith("0x") and len(ref.address) == 42
    digest = keccak(b"helm")
    y, r, s = await signer.sign_digest(ref.signer_ref, digest)
    pub = keys.Signature(vrs=(y, r, s)).recover_public_key_from_msg_hash(digest)
    assert pub.to_checksum_address() == ref.address


async def test_sender_signs_type2_and_legacy_and_blocks_reverts():
    signer = get_signer()
    ref = await signer.create_wallet("u1", "base", "W")
    for net, eip in (("base", True), ("bsc", False)):
        node = FakeEvm(net, eip)
        node.balances[ref.address.lower()] = ETH
        res = await EvmSender(node, signer, poll_s=0).send(ref.address, ref.signer_ref, EvmCall(to="0x" + "11" * 20, value=10**15))
        assert res.status == "confirmed" and node.sent[-1]["type"] == (2 if eip else 0)
        node.revert = True
        res = await EvmSender(node, signer, poll_s=0).send(ref.address, ref.signer_ref, EvmCall(to="0x" + "11" * 20, value=1))
        assert res.status == "failed" and "simulation failed" in res.error and len(node.sent) == 1


async def test_create_fund_and_withdraw_evm_wallets(client, fake_rpc):
    base = FakeEvm("base")
    use_fake(fake_rpc, base)
    h = await signup(client)
    r = await client.post("/wallets", json={"network": "base", "count": 3, "label_prefix": "B"}, headers=h)
    assert r.status_code == 201, r.text
    ws = r.json()
    assert all(w["network"] == "base" and w["address"].startswith("0x") for w in ws)
    funder, a, b = ws
    base.balances[funder["address"].lower()] = ETH

    body = {"funder_wallet_id": funder["id"], "allocations": [{"wallet_id": a["id"], "sol": 0.1}, {"wallet_id": b["id"], "sol": 0.2}]}
    pf = (await client.post("/wallets/fund", json=body, headers=h)).json()
    assert pf["preflight"]["coin"] == "ETH" and pf["preflight"]["total"] == 0.3
    r = await client.post("/wallets/fund", json={**body, "confirm": True, "preflight_hash": pf["preflight_hash"], "idempotency_key": "f1"}, headers=h)
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "confirmed", job
    assert base.balances[a["address"].lower()] == ETH // 10 and [t["nonce"] for t in base.sent] == [0, 1]

    # withdrawing needs 2FA, then sends everything minus the gas reserve
    wbody = {"wallet_ids": [a["id"]], "destination": "0x" + "22" * 20, "all": True}
    pf = (await client.post("/wallets/withdraw", json=wbody, headers=h)).json()
    assert any("2FA" in w or "2fa" in w.lower() for w in pf["preflight"]["warnings"])
    secret = await enable_2fa(client, h)
    import pyotp
    pf = (await client.post("/wallets/withdraw", json=wbody, headers=h)).json()
    assert not pf["preflight"]["warnings"], pf["preflight"]["warnings"]
    r = await client.post("/wallets/withdraw", json={**wbody, "confirm": True, "preflight_hash": pf["preflight_hash"],
                                                     "idempotency_key": "w1", "totp": await totp_code(secret)}, headers=h)
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "confirmed", job
    assert base.balances["0x" + "22" * 20] > 0.09 * ETH


async def test_evm_rules(client, fake_rpc):
    use_fake(fake_rpc, FakeEvm("base"), FakeEvm("bsc", False))
    h = await signup(client)
    b = (await client.post("/wallets", json={"network": "base", "count": 1}, headers=h)).json()[0]
    s = (await client.post("/wallets", json={"network": "solana", "count": 1}, headers=h)).json()[0]
    r = await client.post("/wallets/fund", json={"funder_wallet_id": b["id"], "allocations": [{"wallet_id": s["id"], "sol": 0.1}]}, headers=h)
    assert r.status_code == 400 and "network" in r.json()["detail"]
    r = await client.post("/wallets/withdraw", json={"wallet_ids": [b["id"]], "destination": "So11111111111111111111111111111111111111112"}, headers=h)
    assert r.status_code == 400 and "destination" in r.json()["detail"]
    assert (await client.post("/wallets", json={"network": "dogechain", "count": 1}, headers=h)).status_code == 400
    listed = (await client.get("/wallets", headers=h)).json()
    assert all("signer_ref" not in w for w in listed)
