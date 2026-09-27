"""Encrypting a private key to Turnkey's enclave for ACTIVITY_TYPE_IMPORT_PRIVATE_KEY.

A port of Turnkey's own `encryptPrivateKeyToBundle` (@turnkey/crypto): the import bundle from
ACTIVITY_TYPE_INIT_IMPORT_PRIVATE_KEY carries a target public key signed by Turnkey's signer enclave. Helm checks
that signature against Turnkey's published production signer key, checks the signed organization and user, then
HPKE-encrypts the key to the target (DHKEM(P-256, HKDF-SHA256), HKDF-SHA256, AES-256-GCM, with the sender and
receiver public keys as associated data). Only the enclave can decrypt the result. The plaintext key is never
logged or stored and is dropped as soon as the bundle is built.
"""

import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .base import SigningError

# Turnkey's production signer enclave key (@turnkey/crypto constants.ts, PRODUCTION_SIGNER_SIGN_PUBLIC_KEY).
PRODUCTION_SIGNER_SIGN_PUBLIC_KEY = (
    "04cf288fe433cc4e1aa0ce1632feac4ea26bf2f5a09dcfe5a42c398e06898710330f0572882f4dbdf0f5304b8fc8703acd69adca9a4bbf7f"
    "5d00d20a5e364b2569"
)

HPKE_VERSION = b"HPKE-v1"
SUITE_ID_1 = bytes([75, 69, 77, 0, 16])  # "KEM" || DHKEM(P-256, HKDF-SHA256)
SUITE_ID_2 = bytes([72, 80, 75, 69, 0, 16, 0, 1, 0, 2])  # "HPKE" || kem || HKDF-SHA256 || AES-256-GCM
# Turnkey's precomputed LabeledExpand infos for the key and base nonce (key_schedule_context included).
_CTX = bytes([
    0, 143, 195, 174, 184, 50, 73, 10, 75, 90, 179, 228, 32, 35, 40, 125, 178, 154, 31, 75, 199, 194, 34, 192, 223,
    34, 135, 39, 183, 10, 64, 33, 18, 47, 63, 4, 233, 32, 108, 209, 36, 19, 80, 53, 41, 180, 122, 198, 166, 48, 185,
    46, 196, 207, 125, 35, 69, 8, 208, 175, 151, 113, 201, 158, 80,
])
AES_KEY_INFO = bytes([0, 32]) + HPKE_VERSION + SUITE_ID_2 + b"key" + _CTX
IV_INFO = bytes([0, 12]) + HPKE_VERSION + SUITE_ID_2 + b"base_nonce" + _CTX


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    h = hmac.HMAC(salt or b"\x00" * 32, hashes.SHA256())
    h.update(ikm)
    return h.finalize()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    out, block, i = b"", b"", 1
    while len(out) < length:
        h = hmac.HMAC(prk, hashes.SHA256())
        h.update(block + info + bytes([i]))
        block = h.finalize()
        out += block
        i += 1
    return out[:length]


def _uncompressed(pub: ec.EllipticCurvePublicKey) -> bytes:
    return pub.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)


def hpke_encrypt(plaintext: bytes, target_public: bytes, ephemeral: ec.EllipticCurvePrivateKey | None = None) -> dict:
    """Encrypt to a P-256 public key the way Turnkey's enclave expects. Returns {encappedPublic, ciphertext} (hex)."""
    target = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), target_public)
    target_raw = _uncompressed(target)
    eph = ephemeral or ec.generate_private_key(ec.SECP256R1())
    eph_pub = _uncompressed(eph.public_key())
    dh = eph.exchange(ec.ECDH(), target)  # x coordinate, 32 bytes
    kem_context = eph_pub + target_raw
    ikm = HPKE_VERSION + SUITE_ID_1 + b"eae_prk" + dh
    info = bytes([0, 32]) + HPKE_VERSION + SUITE_ID_1 + b"shared_secret" + kem_context
    shared_secret = _hkdf_expand(_hkdf_extract(b"", ikm), info, 32)
    prk = _hkdf_extract(shared_secret, HPKE_VERSION + SUITE_ID_2 + b"secret")
    key, iv = _hkdf_expand(prk, AES_KEY_INFO, 32), _hkdf_expand(prk, IV_INFO, 12)
    ct = AESGCM(key).encrypt(iv, plaintext, eph_pub + target_raw)
    return {"encappedPublic": eph_pub.hex(), "ciphertext": ct.hex()}


def encrypt_private_key_to_bundle(key: bytes, import_bundle: str, organization_id: str, user_id: str,
                                  signer_public_key: str = PRODUCTION_SIGNER_SIGN_PUBLIC_KEY) -> str:
    """The `encryptedBundle` for ACTIVITY_TYPE_IMPORT_PRIVATE_KEY, after checking the enclave's signature."""
    try:
        bundle = json.loads(import_bundle)
        quorum, data_hex, sig_hex = bundle["enclaveQuorumPublic"], bundle["data"], bundle["dataSignature"]
    except (ValueError, KeyError, TypeError):
        raise SigningError("Turnkey's import bundle wasn't in the expected format")
    if quorum != signer_public_key:
        raise SigningError("Turnkey's import bundle isn't signed by Turnkey's signer enclave; refusing to import")
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes.fromhex(quorum)).verify(
            bytes.fromhex(sig_hex), bytes.fromhex(data_hex), ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError):
        raise SigningError("Turnkey's import bundle signature didn't verify; refusing to import")
    signed = json.loads(bytes.fromhex(data_hex))
    if signed.get("organizationId") != organization_id or signed.get("userId") != user_id:
        raise SigningError("Turnkey's import bundle is for a different organization or user; refusing to import")
    if not signed.get("targetPublic"):
        raise SigningError("Turnkey's import bundle has no target key")
    enc = hpke_encrypt(key, bytes.fromhex(signed["targetPublic"]))
    return json.dumps(enc, separators=(",", ":"))
