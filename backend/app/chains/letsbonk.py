"""LetsBonk (bonk.fun) adapter.

LetsBonk runs on Raydium LaunchLab. PumpPortal's "trade-local" API builds unsigned LetsBonk transactions
(pool "bonk"), the same way it does for Pump.fun, so Helm still signs everything itself. Metadata goes to
LetsBonk's own storage service rather than pump.fun's.
"""

import json

import httpx

from .base import AdapterError, BuiltTx, ClaimRequest, CreateRequest
from .pumpfun import PumpFunAdapter


class LetsBonkAdapter(PumpFunAdapter):
    id = "letsbonk"
    network = "solana"
    quote_assets = ["SOL"]
    create_pool = "bonk"

    def __init__(
        self,
        trade_url: str = "https://pumpportal.fun/api/trade-local",
        storage_url: str = "https://nft-storage.letsbonk22.workers.dev",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(trade_url=trade_url, client=client)
        self.storage_url = storage_url.rstrip("/")

    def options_schema(self) -> dict:
        return {**super().options_schema(), "requires_image": True}

    async def upload_metadata(self, req: CreateRequest) -> str:
        if not req.image_bytes:
            raise AdapterError("LetsBonk needs a token image. Add one to the launch and try again.")
        r = await self._client.post(f"{self.storage_url}/upload/img",
                                    files={"image": ("image.png", req.image_bytes, "image/png")})
        if r.status_code != 200 or not r.text.startswith("http"):
            raise AdapterError(f"LetsBonk image upload failed: {r.status_code} {r.text[:200]}")
        meta = {
            "createdOn": "https://bonk.fun",
            "name": req.name,
            "symbol": req.symbol,
            "description": req.description,
            "image": r.text.strip(),
        }
        for k in ("website", "twitter", "telegram"):
            if req.socials.get(k):
                meta[k] = req.socials[k]
        m = await self._client.post(f"{self.storage_url}/upload/meta", content=json.dumps(meta),
                                    headers={"Content-Type": "application/json"})
        if m.status_code != 200 or not m.text.startswith("http"):
            raise AdapterError(f"LetsBonk metadata upload failed: {m.status_code} {m.text[:200]}")
        return m.text.strip()

    async def build_claim_fees_tx(self, req: ClaimRequest) -> BuiltTx | None:
        raise AdapterError("Claiming LetsBonk creator fees isn't supported through Helm yet. Claim them on bonk.fun.")
