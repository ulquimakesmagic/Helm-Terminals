from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"  # development | production
    database_url: str = "sqlite+aiosqlite:///./helm.db"  # use postgresql+asyncpg://... in prod
    secret_key: str = "dev-secret-change-me"
    session_ttl_hours: int = 12
    build_revision: str = "unknown"
    # Only trust forwarded client IP headers when the immediate peer belongs to an explicitly verified proxy CIDR.
    # Empty by default: use the socket peer address until the hosting provider's ranges are confirmed.
    trusted_proxy_cidrs: str = ""
    public_app_url: str = "https://helmterminals.xyz"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_implicit_tls: bool = False  # otherwise STARTTLS is required

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

    @field_validator("signer")
    @classmethod
    def _signer_kind(cls, v: str) -> str:
        if v not in {"local", "turnkey"}:
            raise ValueError("signer must be local or turnkey")
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
    # Dedicated fee payer, never inferred from a user's wallet or treasury recipient.
    solana_cash_sponsor_address: str = ""
    solana_cash_sponsor_ref: str = Field(default="", repr=False)
    solana_cash_sponsor_usdc_raw: int = Field(default=0, ge=0, le=10_000_000)
    solana_cash_sponsor_max_lamports: int = Field(default=3_000_000, ge=1, le=10_000_000)
    # GMGN display-only OpenAPI. No private key or GMGN trading access required.
    gmgn_api_key: str = Field(default="", repr=False)
    gmgn_requests_per_minute: int = Field(default=30, ge=1, le=300)
    # Codex (codex.io) display-only GraphQL data API. When set it is Helm's primary market source —
    # its coverage includes tokens DexScreener does not index; DexScreener stays the keyless fallback.
    codex_api_key: str = Field(default="", repr=False)
    codex_requests_per_minute: int = Field(default=120, ge=1, le=600)
    pumpportal_trade_url: str = "https://pumpportal.fun/api/trade-local"
    pumpfun_ipfs_url: str = "https://pump.fun/api/ipfs"
    # Official pump.fun transaction builder for creator-fee sharing and distribution. It returns unsigned
    # transactions; Helm's signer still signs locally, so no wallet secret is sent to pump.fun.
    pumpfun_agent_api_url: str = "https://fun-block.pump.fun"
    # EVM RPC URLs per chain, each a comma-separated list rotated like SOLANA_RPC_URL. Blank = public endpoint
    # (rate-limited; fine for testing). Every URL's chain ID is checked before use.
    evm_rpc_base: str = ""
    evm_rpc_bsc: str = ""
    evm_rpc_ethereum: str = ""
    evm_rpc_robinhood: str = ""
    # Enable only after independent contract review and a verified deployment on chain 4663.
    pons_rewards_enabled: bool = False
    pons_rewards_factory: str = ""
    # TRON, NEAR and Arc (see app/chains/networks.py). Off unless listed; mainnets also need the opt-in below.
    extra_networks: str = "tron,near,arc,ton"  # mainnets only; "" = off
    allow_mainnet_extra_networks: bool = True  # false keeps every mainnet off whatever EXTRA_NETWORKS lists
    tron_rpc_url: str = ""  # TronGrid full-node HTTP API (default https://api.trongrid.io)
    trongrid_api_key: str = ""  # sent as TRON-PRO-API-KEY; TronGrid expects one for production
    near_rpc_url: str = ""  # comma-separated JSON-RPC endpoints; default FastNEAR's free endpoint
    arc_rpc_url: str = ""  # default https://rpc.mainnet.arc.io
    ton_rpc_url: str = ""  # toncenter-compatible index API base (default https://toncenter.com)
    toncenter_api_key: str = ""  # X-API-Key; without it toncenter allows 1 request a second
    ton_proof_domains: str = ""  # hosts a TON Connect proof may name; default: helmterminals.xyz + CORS_ORIGINS hosts
    pinata_jwt: str = ""  # Pinata API JWT: pins Meme.Cooking token images and metadata to IPFS
    extra_rpc_timeout_s: float = Field(default=15.0, gt=0, le=60)
    letsbonk_storage_url: str = "https://nft-storage.letsbonk22.workers.dev"
    flap_upload_url: str = "https://funcs.flap.sh/api/upload"
    # Jupiter API key(s) (free at https://developers.jup.ag/portal), comma-separated to rotate. Enables Meteora DBC
    # launches through Jupiter Studio. Without one, Meteora DBC shows as "needs setup".
    jupiter_api_key: str = ""
    # Blank: https://api.jup.ag when a key is set (Meteora DBC always needs one), otherwise Jupiter's keyless lite
    # endpoint for swaps (rate-limited). Declared once: an earlier duplicate silently overrode this (audit HT-08).
    jupiter_api_url: str = ""
    # DEX trading on EVM chains (graduated launchpad tokens, Uniswap V2 launches) goes through KyberSwap's aggregator.
    kyber_api_url: str = "https://aggregator-api.kyberswap.com"
    kyber_client_id: str = "HelmTerminals"
    # Cross-chain routes (USDC to gas on any chain) go through Relay (relay.link). A key is optional but raises
    # rate limits. ROUTE_FEE_BPS is Helm's fee on each route, paid by Relay to PLATFORM_FEE_WALLET_EVM as USDC
    # (claim it at relay.link). Always shown in the route preview; 0 or no EVM fee wallet turns it off.
    relay_api_url: str = "https://api.relay.link"
    relay_api_key: str = ""
    # Cross-chain route providers, in the order they are tried: "relay", "debridge". The first that can quote a
    # route is used and pinned for confirmation. deBridge is off by default: Helm can't yet cancel an unfilled DLN
    # order or decode the order it signs (see docs/releases/2026-10-09-usdc-withdraw-and-route-providers.md).
    route_providers: str = "relay"
    debridge_api_url: str = "https://dln.debridge.finance"
    # Printr launches (Solana). The public preview API needs no key; PRINTR_API_KEY is an optional partner key.
    printr_api_url: str = "https://api-preview.printr.money"
    printr_api_key: str = ""
    debridge_access_token: str = ""  # optional
    route_fee_bps: int = Field(default=0, ge=0, le=300)
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

    # Trading fee on buys and sells placed through Helm, in basis points (100 = 1%). Taken inside the trade
    # transaction and shown in every preview. 0 turns fees off.
    platform_fee_bps: int = 100
    platform_fee_wallet: str = ""
    # Helm's 0x address on EVM chains. Used as the commission receiver on Flap tax tokens (Flap pays launchpad
    # commission to it). Optional.
    platform_fee_wallet_evm: str = ""
    # Share of a Clanker token's LP rewards paid to PLATFORM_FEE_WALLET_EVM, in bps (1000 = 10%, max 2000).
    # 0 = all rewards to the creator. Always shown in the deploy preview.
    clanker_platform_reward_bps: int = 0  # legacy; superseded by CREATOR_SHARE_BPS
    # Helm's share of a new launch's creator rewards where the launchpad supports a native split (Pump.fun, Bags,
    # Clanker), in bps (300 = 3%). An admin setting in the dashboard overrides this.
    creator_share_bps: int = 0
    # Flat fee per launch in US dollars, charged in the launch chain's coin at the live price after the token is
    # created (a failed launch pays nothing). 0 turns it off. An admin setting in the dashboard overrides this.
    launch_fee_usd: float = 20.0
    # Required for superadmin access: the owner account's user ID. Email alone never grants this role.
    superadmin_user_id: str = ""
    # The owner account's email. Superadmin binds only when BOTH this email and the user ID above match
    # the signing-in account; either left empty or mismatched leaves the deployment without a superadmin.
    superadmin_email: str = ""
    # Share of the trading fee paid on-chain to whoever referred the trader (2500 = 25% of the fee).
    referral_share_bps: int = 3000
    referral_code_max_uses: int = 1000
    referral_max_codes_per_user: int = Field(default=10, ge=1, le=100)

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
    telegram_allowed_user_ids: str = ""  # comma-separated Telegram numeric IDs; empty disables all bot sessions
    bot_daily_buy_limit_sol: float = 5.0  # cap on buys placed through the bot in any rolling 24 hours

    # Finite scheduled buys. The worker uses row locks so multiple API instances do not claim one run twice.
    automation_worker_enabled: bool = True
    automation_poll_seconds: float = Field(default=5.0, gt=0, le=60)
    automation_max_budget_sol: float = Field(default=10.0, gt=0, le=100)
    # Per-chain caps for EVM scheduled buys, in each chain's native coin ("network=amount,...").
    automation_max_budgets: str = "base=2,ethereum=2,robinhood=2,bsc=10"
    # Smallest order a position plan may place, per chain (native coin). Stops plans made of many tiny trades.
    automation_min_orders: str = "solana=0.01,base=0.0005,ethereum=0.001,robinhood=0.0005,bsc=0.001"
    automation_max_days: int = Field(default=30, ge=1, le=365)  # longest a plan may run before it expires

    run_jobs_inline: bool = False  # tests: await jobs in the request instead of a background task
    cors_origins: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
