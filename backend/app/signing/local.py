"""Development-only signer. Keys are Fernet-encrypted and embedded in the signer_ref.

This keeps private keys in the application database, which is exactly what production must not do.
The constructor refuses to run when ENV=production; use the Turnkey signer there.
"""

from cryptography.fernet import Fernet
from solders.keypair import Keypair

from .base import SigningError, WalletRef

PREFIX = "local:"


class LocalDevSigner:
    def __init__(self, fernet_key: bytes, env: str) -> None:
        if env == "production":
            raise RuntimeError("LocalDevSigner must not be used with ENV=production. Set SIGNER=turnkey.")
        self._f = Fernet(fernet_key)

    async def create_wallet(self, user_id: str, network: str, label: str) -> WalletRef:
        if network != "solana":
            raise SigningError(f"LocalDevSigner only supports solana, got {network}")
        kp = Keypair()
        token = self._f.encrypt(bytes(kp)).decode()
        return WalletRef(signer_ref=PREFIX + token, address=str(kp.pubkey()))

    async def sign_message(self, signer_ref: str, message: bytes) -> bytes:
        if not signer_ref.startswith(PREFIX):
            raise SigningError("not a local signer ref")
        secret = self._f.decrypt(signer_ref[len(PREFIX) :].encode())
        kp = Keypair.from_bytes(secret)
        return bytes(kp.sign_message(message))
