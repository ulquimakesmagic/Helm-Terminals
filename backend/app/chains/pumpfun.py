"""Pump.fun adapter.

Uses PumpPortal's "trade-local" API, which returns an *unsigned* transaction. Helm signs it through the
Signer, so private keys are never sent to a third party. PumpPortal and pump.fun's IPFS endpoint are third
party services whose request shapes can change; keep the fixtures in tests/ current.
"""

import json

import httpx
from solders.keypair import Keypair

from .base import AdapterError, BuiltTx, ClaimRequest, CreateRequest, TradeRequest


class PumpFunAdapter:
    id = "pumpfun"
    network = "solana"
    quote_assets = ["SOL"]

    def __init__(
        self,
        trade_url: str = "https://pumpportal.fun/api/trade-local",
        ipfs_url: str = "https://pump.fun/api/ipfs",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.trade_url = trade_url
        self.ipfs_url = ipfs_url
        self._client = client or httpx.AsyncClient(timeout=30)

    def options_schema(self) -> dict:
        return {
            "fields": [
                {"key": "dev_buy_sol", "type": "number", "min": 0, "label": "Dev buy (SOL)", "default": 0},
                {"key": "slippage_pct", "type": "number", "min": 0, "max": 50, "default": 10},
                {"key": "priority_fee_sol", "type": "number", "min": 0, "default": 0.0005},
            ],
            "socials": ["twitter", "telegram", "website"],
        }

    async def upload_metadata(self, req: CreateRequest) -> str:
        files = {}
        if req.image_bytes:
            files["file"] = ("image.png", req.image_bytes, "image/png")
        data = {
            "name": req.name,
            "symbol": req.symbol,
            "description": req.description,
            "twitter": req.socials.get("twitter", ""),
            "telegram": req.socials.get("telegram", ""),
            "website": req.socials.get("website", ""),
            "showName": "true",
        }
        r = await self._client.post(self.ipfs_url, data=data, files=files or None)
        if r.status_code != 200:
            raise AdapterError(f"metadata upload failed: {r.status_code} {r.text[:200]}")
        uri = r.json().get("metadataUri")
        if not uri:
            raise AdapterError("metadata upload returned no metadataUri")
        return uri

    async def _trade_local(self, body: dict) -> bytes:
        r = await self._client.post(self.trade_url, json=body)
        if r.status_code != 200:
            raise AdapterError(f"pumpportal {r.status_code}: {r.text[:300]}")
        if not r.content or r.content[:1] in (b"{", b"["):
            # An error payload, not a serialized transaction
            raise AdapterError(f"pumpportal returned no transaction: {r.text[:300]}")
        return r.content

    async def build_create_tx(self, req: CreateRequest) -> BuiltTx:
        mint = Keypair()  # ephemeral: signs once for account creation, then is discarded
        uri = await self.upload_metadata(req)
        raw = await self._trade_local(
            {
                "publicKey": req.dev_pubkey,
                "action": "create",
                "tokenMetadata": {"name": req.name, "symbol": req.symbol, "uri": uri},
                "mint": str(mint.pubkey()),
                "denominatedInSol": "true",
                "amount": req.dev_buy_sol,
                "slippage": req.slippage_pct,
                "priorityFee": req.priority_fee_sol,
                "pool": "pump",
            }
        )
        return BuiltTx(raw=raw, extra_signers=[mint], mint=str(mint.pubkey()))

    async def build_buy_tx(self, req: TradeRequest) -> BuiltTx:
        raw = await self._trade_local(
            {
                "publicKey": req.wallet_pubkey,
                "action": "buy",
                "mint": req.mint,
                "denominatedInSol": "true" if req.denominated_in_sol else "false",
                "amount": req.amount,
                "slippage": req.slippage_pct,
                "priorityFee": req.priority_fee_sol,
                "pool": "auto",
            }
        )
        return BuiltTx(raw=raw, mint=req.mint)

    async def build_sell_tx(self, req: TradeRequest) -> BuiltTx:
        raw = await self._trade_local(
            {
                "publicKey": req.wallet_pubkey,
                "action": "sell",
                "mint": req.mint,
                "denominatedInSol": "true" if req.denominated_in_sol else "false",
                "amount": req.amount,
                "slippage": req.slippage_pct,
                "priorityFee": req.priority_fee_sol,
                "pool": "auto",
            }
        )
        return BuiltTx(raw=raw, mint=req.mint)

    async def build_claim_fees_tx(self, req: ClaimRequest) -> BuiltTx | None:
        raw = await self._trade_local(
            {
                "publicKey": req.wallet_pubkey,
                "action": "collectCreatorFee",
                "priorityFee": req.priority_fee_sol,
                "pool": "pump",
            }
        )
        return BuiltTx(raw=raw)
