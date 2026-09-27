"""Turnkey signer (production).

Private keys are created and held inside Turnkey. Helm only stores the private key ID and public address.
Requests are authenticated with a P-256 API key: the request body is signed and sent in the X-Stamp header.

NOTE: The activity type names and result shapes below follow Turnkey's public API as of writing. Verify them
against https://docs.turnkey.com before enabling this in production, and configure Turnkey policies
(per-key spend caps, destination allowlists, 2FA) on the organization.
"""

import base64
import json
import secrets
import time

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from .base import EVM_NETWORKS, SigningError, WalletRef


class TurnkeySigner:
    def __init__(
        self,
        base_url: str,
        org_id: str,
        api_public_key: str,
        api_private_key: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not (org_id and api_public_key and api_private_key):
            raise RuntimeError("Turnkey signer needs TURNKEY_ORG_ID, TURNKEY_API_PUBLIC_KEY, TURNKEY_API_PRIVATE_KEY")
        # Values pasted into a dashboard often carry spaces or a 0x prefix; either would break every request.
        org_id, api_public_key, api_private_key = (v.strip() for v in (org_id, api_public_key, api_private_key))
        api_public_key = api_public_key.lower().removeprefix("0x")
        self.base_url = base_url.strip().rstrip("/")
        self.org_id = org_id
        self.api_public_key = api_public_key
        self._priv = ec.derive_private_key(int(api_private_key.lower().removeprefix("0x"), 16), ec.SECP256R1())
        self._client = client or httpx.AsyncClient(timeout=30)

    def stamp(self, body: bytes) -> str:
        sig = self._priv.sign(body, ec.ECDSA(hashes.SHA256()))  # DER-encoded
        stamp = {
            "publicKey": self.api_public_key,
            "scheme": "SIGNATURE_SCHEME_TK_API_P256",
            "signature": sig.hex(),
        }
        raw = json.dumps(stamp, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    async def _submit(self, path: str, activity_type: str, parameters: dict) -> dict:
        body_obj = {
            "type": activity_type,
            "timestampMs": str(int(time.time() * 1000)),
            "organizationId": self.org_id,
            "parameters": parameters,
        }
        body = json.dumps(body_obj, separators=(",", ":")).encode()
        r = await self._client.post(
            f"{self.base_url}{path}",
            content=body,
            headers={"Content-Type": "application/json", "X-Stamp": self.stamp(body)},
        )
        if r.status_code != 200:
            raise SigningError(f"Turnkey {r.status_code}: {r.text[:300]}")
        activity = r.json().get("activity", {})
        status = activity.get("status")
        if status == "ACTIVITY_STATUS_FAILED":
            why = ((activity.get("failure") or {}).get("message") or "no reason given")[:300]
            raise SigningError(f"Turnkey refused the request: {why}")
        if status != "ACTIVITY_STATUS_COMPLETED":
            # Consensus or pending states mean a policy needs a human approval. Surface it, do not bypass it.
            raise SigningError(f"Turnkey activity not completed: {status}")
        return activity.get("result") or {}

    async def create_wallet(self, user_id: str, network: str, label: str) -> WalletRef:
        evm = network in EVM_NETWORKS
        if network != "solana" and not evm:
            raise SigningError(f"Turnkey signer doesn't support {network}")
        result = await self._submit(
            "/public/v1/submit/create_private_keys",
            "ACTIVITY_TYPE_CREATE_PRIVATE_KEYS_V2",
            {
                "privateKeys": [
                    {
                        # Turnkey requires unique names across the organization. The network and a random suffix
                        # keep a user's Solana and EVM keys (and any retry after a partial failure) distinct.
                        "privateKeyName": f"helm:{user_id}:{network}:{label}:{secrets.token_hex(3)}"[:100],
                        "curve": "CURVE_SECP256K1" if evm else "CURVE_ED25519",
                        "privateKeyTags": [],
                        "addressFormats": ["ADDRESS_FORMAT_ETHEREUM" if evm else "ADDRESS_FORMAT_SOLANA"],
                    }
                ]
            },
        )
        found = _find(result, "createPrivateKeysResultV2")
        if not found or not found.get("privateKeys"):
            raise SigningError("Turnkey created no key (unexpected response shape)")
        pk = found["privateKeys"][0]
        return WalletRef(signer_ref=pk["privateKeyId"], address=pk["addresses"][0]["address"])

    async def import_wallet(self, user_id: str, network: str, secret: str) -> WalletRef:
        # Turnkey imports keys through its own encrypted import flow (the key is encrypted to Turnkey's enclave on
        # the user's device), which Helm doesn't implement yet. Refuse rather than handle the raw key here.
        raise NotImplementedError("importing a private key isn't available with this server's signer yet")

    async def sign_message(self, signer_ref: str, message: bytes) -> bytes:
        result = await self._submit(
            "/public/v1/submit/sign_raw_payload",
            "ACTIVITY_TYPE_SIGN_RAW_PAYLOAD_V2",
            {
                "signWith": signer_ref,
                "payload": message.hex(),
                "encoding": "PAYLOAD_ENCODING_HEXADECIMAL",
                "hashFunction": "HASH_FUNCTION_NOT_APPLICABLE",
            },
        )
        sig = result["signRawPayloadResult"]
        return bytes.fromhex(sig["r"]) + bytes.fromhex(sig["s"])

    async def sign_digest(self, signer_ref: str, digest: bytes) -> tuple[int, int, int]:
        """secp256k1 signature over an already-hashed EVM payload (Helm computes the keccak digest itself)."""
        if len(digest) != 32:
            raise SigningError("EVM digest must be 32 bytes")
        result = await self._submit(
            "/public/v1/submit/sign_raw_payload",
            "ACTIVITY_TYPE_SIGN_RAW_PAYLOAD_V2",
            {
                "signWith": signer_ref,
                "payload": digest.hex(),
                "encoding": "PAYLOAD_ENCODING_HEXADECIMAL",
                "hashFunction": "HASH_FUNCTION_NO_OP",
            },
        )
        sig = result["signRawPayloadResult"]
        v = int(sig["v"], 16)
        return (v - 27 if v >= 27 else v), int(sig["r"], 16), int(sig["s"], 16)


def _find(obj, key: str):
    """The first value stored under `key` anywhere in a nested response (Turnkey nests results differently by API
    version)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            hit = _find(v, key)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find(v, key)
            if hit is not None:
                return hit
    return None
