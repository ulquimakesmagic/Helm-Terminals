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

    async def sign_message(self, signer_ref: str, message: bytes) -> bytes:
        """Return a raw 64-byte Ed25519 signature over `message`."""
        ...
