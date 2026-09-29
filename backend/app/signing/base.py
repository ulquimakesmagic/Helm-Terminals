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


def family(network: str) -> str:
    """solana | evm | tron | near. Arc networks are EVM; TRON and NEAR have their own key and address formats."""
    if network == "solana":
        return "solana"
    if network in EVM_NETWORKS:
        return "evm"
    from ..chains.networks import EXTRA_NETWORKS

    n = EXTRA_NETWORKS.get(network)
    if n is None:
        raise SigningError(f"unknown network {network}")
    return n.family


def parse_private_key(network: str, secret: str) -> tuple[bytes, str]:
    """(32-byte private key, address) from a key as wallets export it. Errors describe the format, never the input.

    Solana: the base58 key wallet apps export (64-byte keypair), a 32-byte base58 seed, or a solana-keygen JSON array.
    EVM (Base, BNB Chain, Ethereum, Robinhood Chain, Arc): 64 hex characters, with or without 0x.
    TRON: the same 64 hex characters (what TronLink exports); the address is the key's TRON address.
    NEAR: "ed25519:<base58>" as NEAR wallets export it; the address is the key's own implicit account (64 hex).
    TON: never; Helm doesn't hold TON keys.
    """
    import json

    secret = secret.strip()
    fam = family(network)
    if fam == "ton":
        raise SigningError("Helm doesn't hold TON keys; connect the wallet with TON Connect instead")
    if fam in ("evm", "tron"):
        from eth_keys import keys as eth_keys

        try:
            pk = eth_keys.PrivateKey(bytes.fromhex(secret.removeprefix("0x").removeprefix("0X")))
        except Exception:
            what = "a TRON" if fam == "tron" else "an EVM"
            raise SigningError(f"that isn't {what} private key (expected 64 hex characters"
                               f"{'' if fam == 'tron' else ', optionally with 0x'})") from None
        address = pk.public_key.to_checksum_address()
        if fam == "tron":
            from ..chains.tron import address_from_evm

            address = address_from_evm(address)
        return pk.to_bytes(), address
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey

    if fam == "near":
        from ..chains.near import _b58d

        try:
            body = secret.split(":", 1)[1] if secret.lower().startswith("ed25519:") else secret
            raw = _b58d(body)
            if len(raw) not in (32, 64):
                raise ValueError
            kp = Keypair.from_seed(raw[:32])
            if len(raw) == 64 and bytes(kp.pubkey()) != raw[32:]:
                raise ValueError
        except Exception:
            raise SigningError("that isn't a NEAR private key (expected ed25519:… as your NEAR wallet exports it)") from None
        return raw[:32], bytes(kp.pubkey()).hex()
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
