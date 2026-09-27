from functools import lru_cache

from pydantic import Field, field_validator
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
    # Licensed CoinGecko Onchain API (GeckoTerminal data). Leave blank to disable third-party market data.
    # Market data is informational only and never feeds trade quotes or execution.
    coingecko_api_key: str = ""
    coingecko_onchain_api_url: str = "https://pro-api.coingecko.com/api/v3/onchain"
    pumpportal_trade_url: str = "https://pumpportal.fun/api/trade-local"
    pumpfun_ipfs_url: str = "https://pump.fun/api/ipfs"
    # EVM RPC URLs per chain, each a comma-separated list rotated like SOLANA_RPC_URL. Blank = public endpoint
    # (rate-limited; fine for testing). Every URL's chain ID is checked before use.
    evm_rpc_base: str = ""
    evm_rpc_bsc: str = ""
    evm_rpc_ethereum: str = ""
    evm_rpc_robinhood: str = ""
    letsbonk_storage_url: str = "https://nft-storage.letsbonk22.workers.dev"
    flap_upload_url: str = "https://funcs.flap.sh/api/upload"
    # Jupiter API key(s) (free at https://developers.jup.ag/portal), comma-separated to rotate. Enables Meteora DBC
    # launches through Jupiter Studio. Without one, Meteora DBC shows as "needs setup".
    jupiter_api_key: str = ""
    jupiter_api_url: str = "https://api.jup.ag"
    # DEX trading on EVM chains (graduated launchpad tokens, Uniswap V2 launches) goes through KyberSwap's aggregator.
    kyber_api_url: str = "https://aggregator-api.kyberswap.com"
    kyber_client_id: str = "HelmTerminals"
    # Cross-chain routes (USDC to gas on any chain) go through Relay (relay.link). A key is optional but raises
    # rate limits. ROUTE_FEE_BPS is Helm's fee on each route, paid by Relay to PLATFORM_FEE_WALLET_EVM as USDC
    # (claim it at relay.link). Always shown in the route preview; 0 or no EVM fee wallet turns it off.
    relay_api_url: str = "https://api.relay.link"
    relay_api_key: str = ""
    route_fee_bps: int = Field(default=30, ge=0, le=300)
    route_poll_seconds: float = 3.0
    route_wait_seconds: float = 180.0
    # Public base URL of this API. Launchpads that store an image URL on-chain (Pons) get the token image from here.
    public_api_url: str = "https://api.helmterminals.xyz"
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
    # Helm's 0x address on EVM chains. Used as the commission receiver on Flap tax tokens (Flap pays launchpad
    # commission to it). Optional.
    platform_fee_wallet_evm: str = ""
    # Share of a Clanker token's LP rewards paid to PLATFORM_FEE_WALLET_EVM, in bps (1000 = 10%, max 2000).
    # 0 = all rewards to the creator. Always shown in the deploy preview.
    clanker_platform_reward_bps: int = 0
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

    # Finite scheduled buys. The worker uses row locks so multiple API instances do not claim one run twice.
    automation_worker_enabled: bool = True
    automation_poll_seconds: float = Field(default=5.0, gt=0, le=60)
    automation_max_budget_sol: float = Field(default=10.0, gt=0, le=100)
    # Per-chain caps for EVM scheduled buys, in each chain's native coin ("network=amount,...").
    automation_max_budgets: str = "base=2,ethereum=2,robinhood=2,bsc=10"

    run_jobs_inline: bool = False  # tests: await jobs in the request instead of a background task
    cors_origins: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
