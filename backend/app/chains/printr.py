"""Printr (printr.money): omnichain bonding-curve launches. Helm launches on Solana; trades go through Jupiter.

Create: POST {api}/v0/print with the creator's CAIP-10 account, name, symbol, description, a raw-base64 image (max
500 KB), the chain (CAIP-2) and an initial buy. Printr returns `token_id` and an unsigned Solana payload: the
instructions to run and an optional address lookup table, plus the expected mint address. Printr never signs; Helm
compiles the instructions into one transaction paid by the dev wallet and signs it like any other launch, under the
same spend guard. Sources (consulted 2026-10-10): Printr's OpenAPI types in github.com/PrintrFi/printr-mcp
(packages/sdk/src/api.gen.d.ts, svm.ts) and docs.cdp.coinbase.com/.../plugins/printr.md.

Trading: Printr has no trade endpoint; buys and sells use Jupiter's swap API (JUPITER_API_KEY), which must be able
to route the token. Creator fees accrue to the dev wallet (fee_sink "dev") and are claimed on app.printr.money:
Printr's claim API is a separate protobuf service Helm doesn't integrate yet. Printr documents no partner fee split,
so Helm's creator-reward share doesn't apply here (its launch and trading fees do).
"""

import base64
from decimal import Decimal

import httpx
from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey

from .base import AdapterError, BuiltTx, ClaimRequest, CreateRequest, TradeRequest
from .solana import LAMPORTS_PER_SOL, SolanaRPC, build_unsigned

SOLANA_CAIP2 = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
SOL_NATIVE = "11111111111111111111111111111111"
MAX_IMAGE_BYTES = 500 * 1024
GRADUATION_USD = (15_000, 1_000_000)


def _strip_caip10(value: str) -> str:
    """'solana:<ref>:<address>' -> '<address>' (only for Solana mainnet accounts)."""
    if not isinstance(value, str) or not value.startswith(SOLANA_CAIP2 + ":"):
        raise AdapterError("Printr returned an account on an unexpected chain; nothing was signed")
    return value[len(SOLANA_CAIP2) + 1:]


class PrintrAdapter:
    id = "printr"
    network = "solana"
    quote_assets = ["SOL"]

    def __init__(self, api_url: str = "https://api-preview.printr.money", api_key: str = "",
                 rpc: SolanaRPC | None = None, swapper=None, client: httpx.AsyncClient | None = None) -> None:
        self.api = api_url.rstrip("/") + "/v0"
        self.api_key = api_key
        self.rpc = rpc
        self.swapper = swapper  # a Jupiter swap client (MeteoraDbcAdapter's swap path)
        self._client = client or httpx.AsyncClient(timeout=45)

    def options_schema(self) -> dict:
        return {
            "fields": [
                {"key": "dev_buy_sol", "type": "number", "min": 0, "default": 0.1, "label": "Dev buy (SOL)",
                 "hint": "Bought on Printr's curve in the same transaction as the launch."},
                {"key": "graduation_usd", "type": "select", "options": ["69000", "250000"], "default": "69000",
                 "label": "Graduation at (USD)", "hint": "The bonding curve's size before the token moves to its AMM pool."},
                {"key": "slippage_pct", "type": "number", "min": 0, "max": 50, "default": 10},
            ],
            "socials": ["twitter", "telegram", "website"],
            "requires_image": True,
        }

    def deploy_notes(self, params: dict) -> dict:
        grad = int((params or {}).get("graduation_usd") or 69_000)
        return {"curve": f"Printr bonding curve on Solana, graduating at ${grad:,} to its AMM pool",
                "creator_fees": "Printr's creator fees go to your dev wallet; claim them on app.printr.money",
                "steps": "Printr builds the launch instructions; Helm signs them with your dev wallet",
                "trading": "Buys and sells go through Jupiter once it routes the token"}

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def _post(self, path: str, body: dict) -> dict:
        try:
            r = await self._client.post(f"{self.api}{path}", json=body, headers=self._headers())
        except httpx.HTTPError as e:
            raise AdapterError(f"Printr is unreachable ({type(e).__name__})")
        if r.status_code >= 400:
            try:
                err = r.json().get("error") or {}
                msg = err.get("message") or err.get("code")
            except (ValueError, AttributeError):
                msg = r.text[:200]
            raise AdapterError(f"Printr couldn't create this: {msg or r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise AdapterError("Printr returned a malformed response; nothing was signed")

    # --- launch ---------------------------------------------------------------------------------

    def _request(self, req: CreateRequest) -> dict:
        if not req.image_bytes:
            raise AdapterError("Printr needs a token image. Add one to the launch and try again.")
        if len(req.image_bytes) > MAX_IMAGE_BYTES:
            raise AdapterError("Printr accepts images up to 500 KB; use a smaller image")
        if not (req.image_bytes.startswith(b"\x89PNG") or req.image_bytes[:3] == b"\xff\xd8\xff"):
            raise AdapterError("Printr accepts PNG or JPEG images only")
        grad = int((req.options or {}).get("graduation_usd") or 69_000)
        if not GRADUATION_USD[0] <= grad <= GRADUATION_USD[1]:
            raise AdapterError("Printr's graduation threshold must be between $15,000 and $1,000,000")
        lamports = int(Decimal(str(req.dev_buy_sol)) * LAMPORTS_PER_SOL)
        s = req.socials or {}
        links = {k: v for k, v in {"website": s.get("website"), "x": s.get("twitter"),
                                    "telegram": s.get("telegram")}.items() if v}
        body = {
            "creator_accounts": [f"{SOLANA_CAIP2}:{req.dev_pubkey}"],
            "name": req.name[:32], "symbol": req.symbol[:10], "description": (req.description or "")[:500],
            "image": base64.b64encode(req.image_bytes).decode(),  # raw base64, no data: prefix
            "chains": [SOLANA_CAIP2],
            "initial_buy": {"spend_native": str(lamports)},
            "graduation_threshold_per_chain_usd": grad,
            "fee_sink": "dev",
        }
        if links:
            body["external_links"] = links
        return body

    @staticmethod
    def total_lamports(quote: dict | None) -> int | None:
        """What Printr says the launch costs in SOL (dev buy plus its fees), when the quote is priced in SOL."""
        total = (quote or {}).get("total") or {}
        asset = str(total.get("asset_id") or "")
        raw = total.get("cost_asset_atomic")
        if asset.startswith(SOLANA_CAIP2 + ":") and asset.endswith(SOL_NATIVE) and str(raw).isdigit():
            return int(raw)
        return None

    async def build_create_tx(self, req: CreateRequest) -> BuiltTx:
        from ..services.solana_guard import CREATE_OVERHEAD_LAMPORTS

        res = await self._post("/print", self._request(req))
        payload = res.get("payload") or {}
        ixs_json = payload.get("ixs")
        if not res.get("token_id") or not isinstance(ixs_json, list) or not ixs_json:
            raise AdapterError("Printr returned no Solana instructions for this launch")
        dev_buy = int(Decimal(str(req.dev_buy_sol)) * LAMPORTS_PER_SOL)
        cost = self.total_lamports(res.get("quote"))
        if cost is not None and cost > dev_buy + CREATE_OVERHEAD_LAMPORTS:
            raise AdapterError(f"Printr quotes {cost / LAMPORTS_PER_SOL:.4f} SOL for this launch, more than the dev "
                               "buy plus Helm's 0.1 SOL creation allowance; nothing was signed")
        dev = Pubkey.from_string(req.dev_pubkey)
        ixs = []
        try:
            for ix in ixs_json:
                metas = []
                for a in ix.get("accounts") or []:
                    key = Pubkey.from_string(a["pubkey"])
                    if a.get("is_signer") and key != dev:
                        raise AdapterError("Printr's launch asks for a signature Helm doesn't hold; nothing was signed")
                    metas.append(AccountMeta(key, bool(a.get("is_signer")), bool(a.get("is_writable"))))
                ixs.append(Instruction(Pubkey.from_string(ix["program_id"]), base64.b64decode(ix.get("data") or ""),
                                       metas))
            mint = _strip_caip10(payload["mint_address"]) if payload.get("mint_address") else None
            if mint:
                Pubkey.from_string(mint)
        except AdapterError:
            raise
        except (KeyError, TypeError, ValueError) as e:
            raise AdapterError(f"Printr returned malformed instructions ({type(e).__name__}); nothing was signed")
        if self.rpc is None:
            raise AdapterError("Printr launches need a Solana RPC connection")
        tables = []
        if payload.get("lookup_table"):
            table = await self.rpc.get_lookup_table(payload["lookup_table"])
            if table is None:
                raise AdapterError("Printr's lookup table couldn't be read; nothing was signed")
            tables.append(table)
        raw = build_unsigned(dev, ixs, await self.rpc.get_latest_blockhash(), tables)
        return BuiltTx(raw=raw, mint=mint)

    # --- trading (Jupiter) ----------------------------------------------------------------------

    def _need_swapper(self):
        if self.swapper is None:
            raise AdapterError("Printr tokens trade through Jupiter, which needs a Jupiter API key (JUPITER_API_KEY)")
        return self.swapper

    async def build_buy_tx(self, req: TradeRequest) -> BuiltTx:
        return await self._need_swapper().build_buy_tx(req)

    async def build_sell_tx(self, req: TradeRequest) -> BuiltTx:
        return await self._need_swapper().build_sell_tx(req)

    async def build_claim_fees_tx(self, req: ClaimRequest) -> BuiltTx | None:
        raise AdapterError("Printr creator fees are claimed on app.printr.money for now")
