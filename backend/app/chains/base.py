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


@dataclass
class BuiltTx:
    raw: bytes  # unsigned serialized VersionedTransaction
    extra_signers: list[Keypair] = field(default_factory=list)
    mint: str | None = None


class LaunchpadAdapter(Protocol):
    id: str
    network: str
    quote_assets: list[str]

    def options_schema(self) -> dict: ...
    async def build_create_tx(self, req: CreateRequest) -> BuiltTx: ...
    async def build_buy_tx(self, req: TradeRequest) -> BuiltTx: ...
    async def build_sell_tx(self, req: TradeRequest) -> BuiltTx: ...
    async def build_claim_fees_tx(self, req: ClaimRequest) -> BuiltTx | None: ...
