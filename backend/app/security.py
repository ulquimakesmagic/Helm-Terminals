import base64
import hashlib
import secrets

import pyotp
from cryptography.fernet import Fernet
from passlib.hash import argon2

from .config import get_settings


def hash_password(password: str) -> str:
    return argon2.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return argon2.verify(password, hashed)
    except Exception:
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def derive_fernet_key(secret: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def _fernet() -> Fernet:
    return Fernet(derive_fernet_key(get_settings().secret_key))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="Helm Terminals")


def check_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)
