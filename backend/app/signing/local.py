"""Development-only signer. Keys are Fernet-encrypted and embedded in the signer_ref.

This keeps private keys in the application database, which is exactly what production must not do.
The constructor refuses to run when ENV=production; use the Turnkey signer there.
"""

import os

from cryptography.fernet import Fernet
from solders.keypair import Keypair

from eth_keys import keys as eth_keys

from .base import EVM_NETWORKS, SigningError, WalletRef, parse_private_key  # noqa: F401

PREFIX = "local:"
EVM_PREFIX = "local-evm:"


class LocalDevSigner:
    def __init__(self, fernet_key: bytes, env: str) -> None:
        if env in {"production", "test"}:
            raise RuntimeError(f"LocalDevSigner must not be used with ENV={env}. Set SIGNER=turnkey.")
        self._f = Fernet(fernet_key)

    async def create_wallet(self, user_id: str, network: str, label: str) -> WalletRef:
        from .base import family

        fam = family(network)
        if fam == "evm":  # a new secp256k1 key
            pk = eth_keys.PrivateKey(os.urandom(32))
            token = self._f.encrypt(pk.to_bytes()).decode()
            return WalletRef(signer_ref=EVM_PREFIX + token, address=pk.public_key.to_checksum_address())
        kp = Keypair()  # a new ed25519 key: a Solana address
        token = self._f.encrypt(bytes(kp)).decode()
        return WalletRef(signer_ref=PREFIX + token, address=str(kp.pubkey()))

    async def import_wallet(self, user_id: str, network: str, secret: str) -> WalletRef:
        """Take custody of an existing key the user pasted. The key is only ever held encrypted, like created ones."""
        from .base import family

        fam = family(network)
        raw, address = parse_private_key(network, secret)
        if fam == "evm":
            return WalletRef(signer_ref=EVM_PREFIX + self._f.encrypt(raw).decode(), address=address)
        return WalletRef(signer_ref=PREFIX + self._f.encrypt(bytes(Keypair.from_seed(raw))).decode(), address=address)

    async def sign_digest(self, signer_ref: str, digest: bytes) -> tuple[int, int, int]:
        if not signer_ref.startswith(EVM_PREFIX):
            raise SigningError("not a local EVM signer ref")
        if len(digest) != 32:
            raise SigningError("EVM digest must be 32 bytes")
        pk = eth_keys.PrivateKey(self._f.decrypt(signer_ref[len(EVM_PREFIX):].encode()))
        sig = pk.sign_msg_hash(digest)
        return sig.v, sig.r, sig.s

    async def sign_message(self, signer_ref: str, message: bytes) -> bytes:
        if not signer_ref.startswith(PREFIX):
            raise SigningError("not a local signer ref")
        secret = self._f.decrypt(signer_ref[len(PREFIX) :].encode())
        kp = Keypair.from_bytes(secret)
        return bytes(kp.sign_message(message))

    async def reveal_secret(self, signer_ref: str) -> str:
        """Development export: decrypt the stored key in its ecosystem's standard form. Production
        keys live in Turnkey and are exported through Turnkey's own flow, never through Helm."""
        if signer_ref.startswith(EVM_PREFIX):
            return "0x" + self._f.decrypt(signer_ref[len(EVM_PREFIX):].encode()).hex()
        if signer_ref.startswith(PREFIX):
            secret = self._f.decrypt(signer_ref[len(PREFIX) :].encode())
            return str(Keypair.from_bytes(secret))  # base58 of the 64-byte Solana secret key
        raise SigningError("this wallet's keys are not exportable through Helm")
