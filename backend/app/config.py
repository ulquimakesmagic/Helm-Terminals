from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"  # development | production
    database_url: str = "sqlite+aiosqlite:///./helm.db"  # use postgresql+asyncpg://... in prod
    secret_key: str = "dev-secret-change-me"
    session_ttl_hours: int = 24 * 7

    @field_validator("platform_fee_bps")
    @classmethod
    def _fee_cap(cls, v: int) -> int:
        if not 0 <= v <= 500:
            raise ValueError("platform_fee_bps must be between 0 and 500 (5%)")
        return v

    @field_validator("referral_share_bps")
    @classmethod
    def _share_cap(cls, v: int) -> int:
        if not 0 <= v <= 10_000:
            raise ValueError("referral_share_bps must be between 0 and 10000")
        return v

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, v: str) -> str:
        # Railway/Heroku hand out postgres:// or postgresql://; SQLAlchemy async needs the asyncpg driver.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+asyncpg://" + v[len(prefix):]
        return v

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
    letsbonk_storage_url: str = "https://nft-storage.letsbonk22.workers.dev"
    # Bags (bags.fm). Get an API key at https://dev.bags.fm. Without one, Bags shows as "needs setup".
    bags_api_key: str = ""
    bags_api_url: str = "https://public-api-v2.bags.fm/api/v1"
    # Optional Bags partner key: Bags pays this wallet its partner share of fees on tokens launched via Helm.
    bags_partner_wallet: str = ""
    bags_partner_config: str = ""

    # Optional platform fee on sells. 0 disables it. Always shown in the preflight.
    # Trading fee on buys and sells placed through Helm, in basis points (100 = 1%). Taken inside the trade
    # transaction and shown in every preview. 0 turns fees off.
    platform_fee_bps: int = 0
    platform_fee_wallet: str = ""
    # Share of the trading fee paid on-chain to whoever referred the trader (2500 = 25% of the fee).
    referral_share_bps: int = 2500
    referral_code_max_uses: int = 1000

    # Transparency panel: warn when user-controlled wallets hold more than this share of supply.
    controlled_supply_warn_pct: float = 20.0

    # Actions that move assets out to an arbitrary address. These need a 2FA code, and 2FA must be set up first.
    # Trades and other actions inside the user's own wallets do not.
    tfa_required_kinds: str = "withdraw,send_tokens"

    # Telegram bot integration. The bot authenticates with a shared service key; empty disables the integration.
    bot_api_key: str = ""
    telegram_bot_username: str = ""
    bot_session_ttl_minutes: int = 30
    link_code_ttl_minutes: int = 10
    bot_daily_buy_limit_sol: float = 5.0  # cap on buys placed through the bot in any rolling 24 hours

    run_jobs_inline: bool = False  # tests: await jobs in the request instead of a background task
    cors_origins: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
