"""Importing a private key into Turnkey: the enclave bundle is verified and the key is HPKE-encrypted exactly as
Turnkey's own @turnkey/crypto does it, so only Turnkey's enclave can read it."""

import json

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from solders.keypair import Keypair

from app.signing.base import SigningError
from app.signing.turnkey import TurnkeySigner
from app.signing import turnkey_import as ti


def hpke_decrypt(receiver: ec.EllipticCurvePrivateKey, encapped_hex: str, ct_hex: str) -> bytes:
    """The receiving side (what Turnkey's enclave does), written from RFC 9180 with Turnkey's parameters."""
    enc = bytes.fromhex(encapped_hex)
    recv_pub = receiver.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    dh = receiver.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), enc))
    ikm = ti.HPKE_VERSION + ti.SUITE_ID_1 + b"eae_prk" + dh
    info = bytes([0, 32]) + ti.HPKE_VERSION + ti.SUITE_ID_1 + b"shared_secret" + enc + recv_pub
    ss = ti._hkdf_expand(ti._hkdf_extract(b"", ikm), info, 32)
    prk = ti._hkdf_extract(ss, ti.HPKE_VERSION + ti.SUITE_ID_2 + b"secret")
    key, iv = ti._hkdf_expand(prk, ti.AES_KEY_INFO, 32), ti._hkdf_expand(prk, ti.IV_INFO, 12)
    return AESGCM(key).decrypt(iv, bytes.fromhex(ct_hex), enc + recv_pub)


def test_matches_a_vector_made_by_turnkeys_own_library():
    # Produced with @turnkey/crypto: hpkeEncrypt("helm turnkey vector") to a fresh P-256 key.
    receiver = ec.derive_private_key(int("06617bdc6cc25e8d80695c005c4456fed6f5094549858e2295e382d85f986936", 16),
                                     ec.SECP256R1())
    pt = hpke_decrypt(
        receiver,
        "042aa2e0620a5cef53b0ea29d8eee8fcc3856a55a98d20d3f7c4047e144b56d1c7ea87e8914a8ea1296e8f45411020ed9cbb525e133036a9"
        "4197d1e305928d6ee5",
        "30f6934f03b8f8b9e61714c2bfc8167fe1955417f9f1e5fc59c4a8f6116bfe307b483b")
    assert pt == b"helm turnkey vector"


class FakeTurnkey:
    """whoami, init import and import, with a test signer enclave and a real P-256 target key."""

    def __init__(self, org="org1", user="tkuser1"):
        self.org, self.user = org, user
        self.enclave = ec.generate_private_key(ec.SECP256R1())
        self.enclave_pub = self.enclave.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint).hex()
        self.target = ec.generate_private_key(ec.SECP256R1())
        self.received: bytes | None = None
        self.bundle_user = user
        self.address_override = None

    def bundle(self):
        target = self.target.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint).hex()
        data = json.dumps({"organizationId": self.org, "userId": self.bundle_user, "targetPublic": target}).encode()
        sig = self.enclave.sign(data, ec.ECDSA(hashes.SHA256())).hex()
        return json.dumps({"version": "v1.0.0", "data": data.hex(), "dataSignature": sig,
                           "enclaveQuorumPublic": self.enclave_pub})

    def handler(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if req.url.path.endswith("/whoami"):
            return httpx.Response(200, json={"userId": self.user, "organizationId": self.org})
        if req.url.path.endswith("/init_import_private_key"):
            assert body["parameters"] == {"userId": self.user}
            return httpx.Response(200, json={"activity": {"status": "ACTIVITY_STATUS_COMPLETED", "result": {
                "initImportPrivateKeyResult": {"importBundle": self.bundle()}}}})
        if req.url.path.endswith("/import_private_key"):
            p = body["parameters"]
            enc = json.loads(p["encryptedBundle"])
            self.received = hpke_decrypt(self.target, enc["encappedPublic"], enc["ciphertext"])
            self.formats = p["addressFormats"]
            if p["curve"] == "CURVE_ED25519":
                address = str(Keypair.from_seed(self.received).pubkey())
            else:
                from eth_keys import keys
                address = keys.PrivateKey(self.received).public_key.to_checksum_address()
                if p["addressFormats"] == ["ADDRESS_FORMAT_TRON"]:
                    from app.chains.tron import address_from_evm
                    address = address_from_evm(address)
            return httpx.Response(200, json={"activity": {"status": "ACTIVITY_STATUS_COMPLETED", "result": {
                "importPrivateKeyResult": {"privateKeyId": "pk-imported",
                                           "addresses": [{"address": self.address_override or address}]}}}})
        raise AssertionError(req.url.path)

    def signer(self):
        api = ec.generate_private_key(ec.SECP256R1())
        return TurnkeySigner("https://tk.test", self.org, "02ab", format(api.private_numbers().private_value, "064x"),
                             client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
                             enclave_signer_public_key=self.enclave_pub)


async def test_solana_and_evm_keys_reach_the_enclave_intact():
    tk = FakeTurnkey()
    kp = Keypair()
    ref = await tk.signer().import_wallet("u1", "solana", str(kp))
    assert ref.address == str(kp.pubkey()) and ref.signer_ref == "pk-imported"
    assert tk.received == bytes(kp)[:32]  # the 32-byte seed, as Turnkey expects for ed25519
    ref = await tk.signer().import_wallet("u1", "base", "0x" + "44" * 32)
    assert tk.received == bytes.fromhex("44" * 32) and ref.address.startswith("0x")


async def test_arc_keys_import_as_evm_and_removed_networks_are_refused():
    """Arc takes the EVM key and address (ethers vector in tests/data/wallet_vectors.json). TRON, NEAR and TON were
    removed, so their keys are refused before anything reaches Turnkey."""
    tk = FakeTurnkey()
    ref = await tk.signer().import_wallet("u1", "arc", "0x" + "11" * 32)
    assert ref.address == "0x19E7E376E7C213B7E7e7e46cc70A5dD086DAff2A" and tk.formats == ["ADDRESS_FORMAT_ETHEREUM"]
    for net in ("tron", "near", "ton"):
        with pytest.raises(SigningError, match="unknown network"):
            await tk.signer().import_wallet("u1", net, "11" * 32)


async def test_import_refuses_an_unverified_or_mismatched_bundle():
    tk = FakeTurnkey()
    impostor = FakeTurnkey()
    s = tk.signer()
    s._enclave_key = impostor.enclave_pub  # bundle signed by a key other than the expected enclave
    with pytest.raises(SigningError, match="signer enclave"):
        await s.import_wallet("u1", "solana", str(Keypair()))
    tk.bundle_user = "someone-else"
    with pytest.raises(SigningError, match="different organization or user"):
        await tk.signer().import_wallet("u1", "solana", str(Keypair()))
    tk.bundle_user = tk.user
    tk.address_override = "So1anaAddressThatDoesNotMatch11111111111111"
    with pytest.raises(SigningError, match="doesn't match"):
        await tk.signer().import_wallet("u1", "solana", str(Keypair()))
    assert ti.PRODUCTION_SIGNER_SIGN_PUBLIC_KEY.startswith("04cf288fe4")
