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
