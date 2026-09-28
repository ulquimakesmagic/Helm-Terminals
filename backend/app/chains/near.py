"""NEAR over JSON-RPC.

Accounts are named (alice.near, 2-64 characters of lowercase letters, digits and - _ separators, dot-separated) or
implicit: the 64 lowercase hex characters of an ed25519 public key, created by a first transfer to it. Helm-managed
NEAR wallets are implicit accounts of their own ed25519 key. ETH-implicit accounts (0x + 40 hex) are recognised for
watching only.

Transactions are built locally in borsh (signer_id, public_key, nonce, receiver_id, block_hash, actions), and the
SHA-256 of those bytes is signed with ed25519. The nonce is the access key's nonce + 1; the block hash is a recent
final block. Sent with send_tx and awaited to FINAL. Fungible tokens are NEP-141: ft_transfer needs exactly 1
yoctoNEAR attached, and the recipient must be registered for storage (storage_deposit) first.
Sources: see networks.SOURCES["near"].
"""

import asyncio
import base64
import hashlib
import json
import re
import struct

import httpx
from solders.pubkey import Pubkey

from .base import AdapterError

YOCTO = 10**24
TGAS = 10**12
NAMED = re.compile(r"^(([a-z\d]+[\-_])*[a-z\d]+\.)*([a-z\d]+[\-_])*[a-z\d]+$")
IMPLICIT = re.compile(r"^[0-9a-f]{64}$")
ETH_IMPLICIT = re.compile(r"^0x[0-9a-f]{40}$")
TRANSFER_GAS = 1 * TGAS  # generous for a plain transfer (docs: ~0.45 Tgas); unused gas is refunded
FT_TRANSFER_GAS = 30 * TGAS
STORAGE_DEPOSIT_GAS = 30 * TGAS


def valid_account(account: str) -> bool:
    if not isinstance(account, str):
        return False
    a = account.strip()
    if IMPLICIT.match(a) or ETH_IMPLICIT.match(a):
        return True
    return 2 <= len(a) <= 64 and bool(NAMED.match(a))


def implicit_account(pubkey32: bytes) -> str:
    return pubkey32.hex()


def implicit_from_solana_style(address_b58: str) -> str:
    """The implicit account of an ed25519 public key given in base58 (how the signers report ed25519 keys)."""
    return bytes(Pubkey.from_string(address_b58)).hex()


# --- borsh ---------------------------------------------------------------------------------------------


def _string(s: str) -> bytes:
    b = s.encode()
    return struct.pack("<I", len(b)) + b


def _u128(n: int) -> bytes:
    return n.to_bytes(16, "little")


def transfer_action(deposit: int) -> bytes:
    return bytes([3]) + _u128(deposit)


def function_call_action(method: str, args: dict, gas: int, deposit: int) -> bytes:
    raw = json.dumps(args, separators=(",", ":")).encode()
    return bytes([2]) + _string(method) + struct.pack("<I", len(raw)) + raw + struct.pack("<Q", gas) + _u128(deposit)


def serialize_transaction(signer_id: str, pubkey32: bytes, nonce: int, receiver_id: str, block_hash: bytes,
                          actions: list[bytes]) -> bytes:
    return (_string(signer_id) + bytes([0]) + pubkey32 + struct.pack("<Q", nonce) + _string(receiver_id)
            + block_hash + struct.pack("<I", len(actions)) + b"".join(actions))


def signed_transaction(tx: bytes, signature64: bytes) -> bytes:
    return tx + bytes([0]) + signature64


def tx_hash(tx: bytes) -> bytes:
    return hashlib.sha256(tx).digest()


def _b58(b: bytes) -> str:
    from .tron import _b58encode

    return _b58encode(b)


def _b58d(s: str) -> bytes:
    from .tron import _b58decode

    return _b58decode(s)


# --- client --------------------------------------------------------------------------------------------


class NearClient:
    """JSON-RPC across one or more endpoints (NEAR_RPC_URL may list several), bounded retries, URL-free errors."""

    def __init__(self, urls: list[str], timeout_s: float = 15.0, client: httpx.AsyncClient | None = None) -> None:
        self.urls = urls
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    async def call(self, method: str, params, resend: bool = True) -> dict:
        """resend=False (transactions): only retry when the request provably never reached a node (connect errors);
        after anything else the outcome is unknown and is reported as such rather than sent again."""
        last = "no answer"
        for attempt, url in enumerate(self.urls * 2):
            try:
                r = await self._client.post(url, json={"jsonrpc": "2.0", "id": "helm", "method": method, "params": params})
            except httpx.HTTPError as e:
                last = type(e).__name__
                if not resend and not isinstance(e, httpx.ConnectError):
                    raise AdapterError(f"NEAR RPC outcome unknown ({last})")
                await asyncio.sleep(0.3 * (attempt + 1))
                continue
            if not resend and r.status_code >= 500:
                raise AdapterError(f"NEAR RPC outcome unknown (HTTP {r.status_code})")
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                await asyncio.sleep(0.3 * (attempt + 1))
                continue
            try:
                data = r.json()
            except ValueError:
                last = "non-JSON answer"
                continue
            if "error" in data:
                err = data["error"]
                cause = (err.get("cause") or {}).get("name") or err.get("name") or ""
                info = json.dumps(err.get("data") or (err.get("cause") or {}).get("info") or "")[:200]
                raise AdapterError(f"NEAR RPC error {cause}: {info}".strip())
            return data.get("result") or {}
        raise AdapterError(f"NEAR RPC is unreachable right now ({last})")

    async def query(self, request: dict) -> dict:
        res = await self.call("query", {"finality": "final", **request})
        if res.get("error"):
            raise AdapterError(f"NEAR query failed: {str(res['error'])[:200]}")
        return res

    async def account(self, account_id: str) -> dict | None:
        """view_account, or None if the account doesn't exist (yet)."""
        try:
            return await self.query({"request_type": "view_account", "account_id": account_id})
        except AdapterError as e:
            if "UNKNOWN_ACCOUNT" in str(e) or "does not exist" in str(e):
                return None
            raise

    async def view(self, contract: str, method: str, args: dict) -> object:
        res = await self.query({"request_type": "call_function", "account_id": contract, "method_name": method,
                                "args_base64": base64.b64encode(json.dumps(args).encode()).decode()})
        return json.loads(bytes(res.get("result") or b"null"))

    async def access_key(self, account_id: str, pubkey32: bytes) -> dict:
        return await self.query({"request_type": "view_access_key", "account_id": account_id,
                                 "public_key": "ed25519:" + _b58(pubkey32)})

    async def final_block_hash(self) -> bytes:
        block = await self.call("block", {"finality": "final"})
        return _b58d(block["header"]["hash"])

    async def gas_price(self) -> int:
        return int((await self.call("gas_price", [None])).get("gas_price", 100_000_000))

    async def protocol_storage_cost(self) -> int:
        cfg = await self.call("EXPERIMENTAL_protocol_config", {"finality": "final"})
        return int(cfg["runtime_config"]["storage_amount_per_byte"])

    async def send(self, signed: bytes) -> dict:
        return await self.call("send_tx", {"signed_tx_base64": base64.b64encode(signed).decode(), "wait_until": "FINAL"},
                               resend=False)

    async def status(self, tx_hash_b58: str, sender: str) -> dict:
        return await self.call("tx", {"tx_hash": tx_hash_b58, "sender_account_id": sender, "wait_until": "FINAL"})


def available_balance(acct: dict | None, storage_cost_per_byte: int = 10**19) -> int:
    """yoctoNEAR a wallet can spend: its balance minus what its storage keeps staked."""
    if not acct:
        return 0
    return max(int(acct.get("amount", 0)) - int(acct.get("storage_usage", 0)) * storage_cost_per_byte, 0)


def outcome_status(result: dict) -> tuple[str, str | None]:
    """('confirmed' | 'failed', error) from a FINAL send_tx/tx result. A failure in any receipt counts."""
    st = (result.get("status") or {})
    if "Failure" in st:
        return "failed", f"failed on-chain: {json.dumps(st['Failure'])[:200]}"
    for r in result.get("receipts_outcome") or []:
        s = (r.get("outcome") or {}).get("status") or {}
        if "Failure" in s:
            return "failed", f"a receipt failed on-chain: {json.dumps(s['Failure'])[:200]}"
    if "SuccessValue" in st or "SuccessReceiptId" in st:
        return "confirmed", None
    return "timeout", "the network didn't report a final outcome yet; check the explorer before retrying"


async def sign_and_send(client: NearClient, signer, signer_ref: str, signer_id: str, pubkey32: bytes,
                        receiver_id: str, actions: list[bytes]) -> tuple[str | None, str, str | None]:
    """(tx hash base58, status, error). Nonce and a recent final block hash are read fresh for each send."""
    key = await client.access_key(signer_id, pubkey32)
    nonce = int(key["nonce"]) + 1
    block_hash = await client.final_block_hash()
    tx = serialize_transaction(signer_id, pubkey32, nonce, receiver_id, block_hash, actions)
    digest = tx_hash(tx)
    sig = await signer.sign_message(signer_ref, digest)
    signed = signed_transaction(tx, sig)
    h = _b58(digest)
    try:
        result = await client.send(signed)
    except AdapterError as e:
        text = str(e)
        if "TIMEOUT" in text.upper() or "outcome unknown" in text:
            return h, "timeout", "sent, not final yet; check the explorer before retrying"
        if any(k in text for k in ("INVALID_TRANSACTION", "InvalidNonce", "NotEnoughBalance", "LackBalanceForState",
                                   "INVALID_SIGNATURE", "EXPIRED_TRANSACTION")):
            return None, "failed", f"rejected by the network: {text[:200]}"
        return h, "timeout", f"the result is unknown ({text[:120]}); check the explorer before retrying"
    status, err = outcome_status(result)
    return h, status, err
