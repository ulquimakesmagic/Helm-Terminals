from dataclasses import dataclass
from typing import Protocol


class SigningError(Exception):
    pass


@dataclass
class WalletRef:
    signer_ref: str  # opaque handle stored in wallets.signer_ref
    address: str


class Signer(Protocol):
    """Creates wallets and signs messages. Private keys never leave the signer backend in production."""

    async def create_wallet(self, user_id: str, network: str, label: str) -> WalletRef: ...

    async def import_wallet(self, user_id: str, network: str, secret: str) -> WalletRef:
        """Take custody of an existing private key. Raises NotImplementedError where the backend can't."""
        ...

    async def sign_message(self, signer_ref: str, message: bytes) -> bytes:
        """Return a raw 64-byte Ed25519 signature over `message` (Solana wallets)."""
        ...

    async def sign_digest(self, signer_ref: str, digest: bytes) -> tuple[int, int, int]:
        """Sign a 32-byte keccak digest with secp256k1 (EVM wallets). Returns (y_parity 0/1, r, s)."""
        ...


EVM_NETWORKS = frozenset({"base", "bsc", "ethereum", "robinhood"})


def parse_private_key(network: str, secret: str) -> tuple[bytes, str]:
    """(32-byte private key, address) from a key as wallets export it. Errors describe the format, never the input.

    Solana: the base58 key wallet apps export (64-byte keypair), a 32-byte base58 seed, or a solana-keygen JSON array.
    EVM: 64 hex characters, with or without 0x.
    """
    import json

    secret = secret.strip()
    if network in EVM_NETWORKS:
        from eth_keys import keys as eth_keys

        try:
            pk = eth_keys.PrivateKey(bytes.fromhex(secret.removeprefix("0x").removeprefix("0X")))
        except Exception:
            raise SigningError("that isn't an EVM private key (expected 64 hex characters, optionally with 0x)") from None
        return pk.to_bytes(), pk.public_key.to_checksum_address()
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey

    try:
        if secret.startswith("["):
            kp = Keypair.from_bytes(bytes(json.loads(secret)))
        elif len(secret) > 50:
            kp = Keypair.from_base58_string(secret)
        else:
            kp = Keypair.from_seed(bytes(Pubkey.from_string(secret)))
    except Exception:
        raise SigningError("that isn't a Solana private key (expected the base58 key your wallet exports, "
                           "or a solana-keygen JSON array)") from None
    return bytes(kp)[:32], str(kp.pubkey())
