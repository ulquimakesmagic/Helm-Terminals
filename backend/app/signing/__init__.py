from functools import lru_cache

from ..config import get_settings
from ..security import derive_fernet_key
from .base import Signer, SigningError, WalletRef


@lru_cache
def get_signer() -> Signer:
    s = get_settings()
    if s.signer == "turnkey":
        from .turnkey import TurnkeySigner

        return TurnkeySigner(
            base_url=s.turnkey_base_url,
            org_id=s.turnkey_org_id,
            api_public_key=s.turnkey_api_public_key,
            api_private_key=s.turnkey_api_private_key,
        )
    if s.env in {"production", "test"}:
        raise RuntimeError(f"SIGNER=local is disabled when ENV={s.env}; configure SIGNER=turnkey")
    from .local import LocalDevSigner

    key = s.local_key_secret.encode() if s.local_key_secret else derive_fernet_key(s.secret_key)
    return LocalDevSigner(fernet_key=key, env=s.env)


__all__ = ["Signer", "SigningError", "WalletRef", "get_signer"]
