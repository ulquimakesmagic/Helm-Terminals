"""Development-only signer. Keys are Fernet-encrypted and embedded in the signer_ref.

This keeps private keys in the application database, which is exactly what production must not do.
The constructor refuses to run when ENV=production; use the Turnkey signer there.
"""

import json
import os

from cryptography.fernet import Fernet
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from eth_keys import keys as eth_keys

from .base import EVM_NETWORKS, SigningError, WalletRef

PREFIX = "local:"
EVM_PREFIX = "local-evm:"


class LocalDevSigner:
    def __init__(self, fernet_key: bytes, env: str) -> None:
        if env == "production":
            raise RuntimeError("LocalDevSigner must not be used with ENV=production. Set SIGNER=turnkey.")
        self._f = Fernet(fernet_key)

    async def create_wallet(self, user_id: str, network: str, label: str) -> WalletRef:
        if network in EVM_NETWORKS:
            pk = eth_keys.PrivateKey(os.urandom(32))
            token = self._f.encrypt(pk.to_bytes()).decode()
            return WalletRef(signer_ref=EVM_PREFIX + token, address=pk.public_key.to_checksum_address())
        if network != "solana":
            raise SigningError(f"LocalDevSigner doesn't support {network}")
        kp = Keypair()
        token = self._f.encrypt(bytes(kp)).decode()
        return WalletRef(signer_ref=PREFIX + token, address=str(kp.pubkey()))

    async def import_wallet(self, user_id: str, network: str, secret: str) -> WalletRef:
        """Take custody of an existing key the user pasted. The key is only ever held encrypted, like created ones."""
        secret = secret.strip()
        if network in EVM_NETWORKS:
            raw = secret.removeprefix("0x").removeprefix("0X")
            try:
                pk = eth_keys.PrivateKey(bytes.fromhex(raw))
            except Exception:
                raise SigningError("that isn't an EVM private key (expected 64 hex characters, optionally with 0x)")
            return WalletRef(signer_ref=EVM_PREFIX + self._f.encrypt(pk.to_bytes()).decode(),
                             address=pk.public_key.to_checksum_address())
        if network != "solana":
            raise SigningError(f"LocalDevSigner doesn't support {network}")
        try:
            if secret.startswith("["):  # solana-keygen JSON: an array of 64 numbers
                kp = Keypair.from_bytes(bytes(json.loads(secret)))
            else:  # base58, as wallet apps export it: 64-byte keypair, or a 32-byte seed
                kp = (Keypair.from_base58_string(secret) if len(secret) > 50
                      else Keypair.from_seed(bytes(Pubkey.from_string(secret))))
        except Exception:
            raise SigningError("that isn't a Solana private key (expected the base58 key your wallet exports, "
                               "or a solana-keygen JSON array)")
        return WalletRef(signer_ref=PREFIX + self._f.encrypt(bytes(kp)).decode(), address=str(kp.pubkey()))

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
