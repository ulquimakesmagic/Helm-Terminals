from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"  # development | production
    database_url: str = "sqlite+aiosqlite:///./helm.db"  # use postgresql+asyncpg://... in prod
    secret_key: str = "dev-secret-change-me"
    session_ttl_hours: int = 24 * 7

    # Signing: "local" (dev only) or "turnkey"
    signer: str = "local"
    local_key_secret: str = ""  # optional Fernet key; derived from secret_key when empty
    turnkey_base_url: str = "https://api.turnkey.com"
    turnkey_org_id: str = ""
    turnkey_api_public_key: str = ""  # compressed P-256 public key, hex
    turnkey_api_private_key: str = ""  # P-256 private scalar, hex

    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    pumpportal_trade_url: str = "https://pumpportal.fun/api/trade-local"
    pumpfun_ipfs_url: str = "https://pump.fun/api/ipfs"

    # Optional platform fee on sells. 0 disables it. Always shown in the preflight.
    platform_fee_bps: int = 0
    platform_fee_wallet: str = ""

    # Transparency panel: warn when user-controlled wallets hold more than this share of supply.
    controlled_supply_warn_pct: float = 20.0

    run_jobs_inline: bool = False  # tests: await jobs in the request instead of a background task
    cors_origins: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
