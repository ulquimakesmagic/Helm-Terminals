"""Development-only signer. Keys are Fernet-encrypted and embedded in the signer_ref.

This keeps private keys in the application database, which is exactly what production must not do.
The constructor refuses to run when ENV=production; use the Turnkey signer there.
"""

import os

from cryptography.fernet import Fernet
from solders.keypair import Keypair

from eth_keys import keys as eth_keys

from .base import EVM_NETWORKS, SigningError, WalletRef, parse_private_key

PREFIX = "local:"
EVM_PREFIX = "local-evm:"


class LocalDevSigner:
    def __init__(self, fernet_key: bytes, env: str) -> None:
        if env == "production":
            raise RuntimeError("LocalDevSigner must not be used with ENV=production. Set SIGNER=turnkey.")
        self._f = Fernet(fernet_key)

    async def create_wallet(self, user_id: str, network: str, label: str) -> WalletRef:
        from .base import family

        fam = family(network)
        if fam == "ton":  # never derive a TON address from another chain's key format
            raise SigningError("Helm doesn't create TON keys; TON wallets connect through TON Connect")
        if fam in ("evm", "tron"):  # a new secp256k1 key; TRON shows it in its own address format
            pk = eth_keys.PrivateKey(os.urandom(32))
            token = self._f.encrypt(pk.to_bytes()).decode()
            address = pk.public_key.to_checksum_address()
            if fam == "tron":
                from ..chains.tron import address_from_evm

                address = address_from_evm(address)
            return WalletRef(signer_ref=EVM_PREFIX + token, address=address)
        kp = Keypair()  # a new ed25519 key: Solana address, or a NEAR implicit account (hex public key)
        token = self._f.encrypt(bytes(kp)).decode()
        return WalletRef(signer_ref=PREFIX + token, address=bytes(kp.pubkey()).hex() if fam == "near" else str(kp.pubkey()))

    async def import_wallet(self, user_id: str, network: str, secret: str) -> WalletRef:
        """Take custody of an existing key the user pasted. The key is only ever held encrypted, like created ones."""
        if network not in EVM_NETWORKS and network != "solana":
            raise SigningError(f"LocalDevSigner doesn't support {network}")
        raw, address = parse_private_key(network, secret)
        if network in EVM_NETWORKS:
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
