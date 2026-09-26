from dataclasses import dataclass, field
from typing import Protocol

from solders.keypair import Keypair


class AdapterError(Exception):
    pass


@dataclass
class CreateRequest:
    name: str
    symbol: str
    description: str
    image_bytes: bytes | None
    socials: dict
    dev_pubkey: str
    dev_buy_sol: float = 0.0
    slippage_pct: float = 10.0
    priority_fee_sol: float = 0.0005
    options: dict = field(default_factory=dict)


@dataclass
class TradeRequest:
    mint: str
    wallet_pubkey: str
    side: str  # buy | sell
    amount: float | str  # SOL for buys; token units, or a percentage string like "50%", for sells
    denominated_in_sol: bool
    slippage_pct: float = 10.0
    priority_fee_sol: float = 0.0005


@dataclass
class ClaimRequest:
    wallet_pubkey: str
    mint: str | None = None
    priority_fee_sol: float = 0.0005
    options: dict = field(default_factory=dict)  # the launch's launchpad_params (e.g. its quote asset)


@dataclass
class BuiltTx:
    raw: bytes  # unsigned serialized VersionedTransaction
    extra_signers: list[Keypair] = field(default_factory=list)
    mint: str | None = None
    # Transactions that must land first, in order (e.g. a launchpad's fee-share config before its launch).
    # Signed by the same wallet. If any fails, the main transaction is not sent.
    pre: list[bytes] = field(default_factory=list)
    # Non-essential clean-up sent after the main transaction confirms (e.g. deactivating a lookup table).
    # Failures here are recorded but never fail the action.
    post: list[bytes] = field(default_factory=list)


class LaunchpadAdapter(Protocol):
    id: str
    network: str
    quote_assets: list[str]

    def options_schema(self) -> dict: ...
    async def build_create_tx(self, req: CreateRequest) -> BuiltTx: ...
    async def build_buy_tx(self, req: TradeRequest) -> BuiltTx: ...
    async def build_sell_tx(self, req: TradeRequest) -> BuiltTx: ...
    async def build_claim_fees_tx(self, req: ClaimRequest) -> BuiltTx | None: ...


# --- EVM launchpads ------------------------------------------------------------------------------
# EVM adapters don't return serialized transactions: they return the contract calls to make, in order.
# Helm's EvmSender fills in nonce, gas and fees, simulates each call and signs it with the wallet's key.


@dataclass
class EvmStep:
    label: str  # shown in the job log, e.g. "approve", "swap"
    to: str
    data: bytes
    value: int = 0  # wei
    essential: bool = True  # if an essential step fails, later steps are not sent


@dataclass
class EvmPlan:
    steps: list[EvmStep]
    token: str | None = None  # the token address (known before sending, e.g. from CREATE2)
    notes: dict = field(default_factory=dict)  # extra facts for the preview (token type, commission, ...)


@dataclass
class EvmTradeRequest:
    token: str
    wallet: str
    side: str  # buy | sell
    amount_wei: int  # native coin in for buys; tokens in (raw units) for sells
    slippage_pct: float = 10.0


class EvmLaunchpadAdapter(Protocol):
    id: str
    network: str
    quote_assets: list[str]
    kind: str  # "evm"

    def options_schema(self) -> dict: ...
    async def prepare_create(self, req: CreateRequest) -> EvmPlan: ...
    async def prepare_trade(self, req: EvmTradeRequest) -> EvmPlan: ...
    async def build_claim_fees_tx(self, req: ClaimRequest) -> None: ...
