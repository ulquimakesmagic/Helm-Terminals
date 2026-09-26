"""Meteora Dynamic Bonding Curve launches through Jupiter Studio's API, trades through Jupiter's swap API.

Launch (developers.jup.ag, Studio "Create token"):
  1. POST /studio/v1/dbc-pool/create-tx -> unsigned transaction, mint, presigned upload URLs.
  2. PUT the image and the metadata JSON to those URLs.
  3. Sign with the dev wallet and POST the signed transaction to /studio/v1/dbc-pool/submit (multipart), which
     is how Studio registers the token. Helm then confirms it on-chain by the dev wallet's signature.
Trading: Jupiter's quote + swap API routes DBC pools while on the curve and the DAMM pool after migration.
Creator fees: /studio/v1/dbc-pool/addresses/{mint} -> pool, then /studio/v1/dbc/fee/create-tx.

Every call needs a Jupiter API key (free at developers.jup.ag/portal); JUPITER_API_KEY may list several.
"""

import base64

import httpx

from .base import AdapterError, BuiltTx, ClaimRequest, CreateRequest, TradeRequest
from ..keyring import KeyRing, parse_list, retry_after
from .solana import LAMPORTS_PER_SOL, SolanaRPC, decode_tx

WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
# Starting and graduation market caps, in the quote currency. SOL ones roughly match pump.fun's.
QUOTES = {"SOL": {"mint": WSOL, "decimals": 9, "initial": 100, "migration": 400},
          "USDC": {"mint": USDC, "decimals": 6, "initial": 16000, "migration": 69000}}


def _content_type(img: bytes) -> str:
    if img.startswith(b"\x89PNG"):
        return "image/png"
    if img[:4] == b"GIF8":
        return "image/gif"
    if img[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _find(obj, *keys: str):
    """First value under any of `keys`, anywhere in a nested JSON response."""
    if isinstance(obj, dict):
        for k in keys:
            if isinstance(obj.get(k), str) and obj[k]:
                return obj[k]
        for v in obj.values():
            found = _find(v, *keys)
            if found:
                return found
    if isinstance(obj, list):
        for v in obj:
            found = _find(v, *keys)
            if found:
                return found
    return None


class MeteoraDbcAdapter:
    id = "meteora-dbc"
    network = "solana"
    quote_assets = ["SOL", "USDC"]

    def __init__(self, api_key: str, rpc: SolanaRPC | None = None, api_url: str = "https://api.jup.ag",
                 client: httpx.AsyncClient | None = None) -> None:
        self.keys = KeyRing(parse_list(api_key), cooldown_s=60)
        self.keys.kind = "Jupiter API key"
        self.rpc = rpc
        self.api = api_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=30)

    def options_schema(self) -> dict:
        return {
            "fields": [
                {"key": "dev_buy_sol", "type": "number", "min": 0, "max": 0, "label": "Dev buy (SOL)", "default": 0,
                 "hint": "Studio launches have no dev buy; buy right after launch from the workspace."},
                {"key": "dbc_quote", "type": "select", "options": list(QUOTES), "default": "SOL", "label": "Curve currency",
                 "hint": "SOL: opens at 100 SOL market cap, graduates at 400 SOL. USDC: $16k to $69k."},
                {"key": "slippage_pct", "type": "number", "min": 0, "max": 50, "default": 10},
            ],
            "socials": ["twitter", "telegram", "website"],
            "requires_image": True,
        }

    def deploy_warnings(self, dev_buy: float, params: dict) -> list[str]:
        return ["Jupiter Studio launches have no dev buy. Set it to 0 and buy from the workspace after launch."] \
            if dev_buy > 0 else []

    def deploy_notes(self, params: dict) -> dict:
        q = QUOTES.get(params.get("dbc_quote") or "SOL", QUOTES["SOL"])
        unit = "SOL" if q["mint"] == WSOL else "USD"
        return {"curve": f"Meteora bonding curve from {q['initial']:,} to {q['migration']:,} {unit} market cap, "
                         "then a locked Meteora pool",
                "pool_fee": "1% per trade, with Jupiter's anti-sniping fee in the first moments",
                "steps": "Jupiter Studio creates the transaction; Helm signs it with your dev wallet"}

    # --- HTTP -----------------------------------------------------------------------------------

    async def _call(self, method: str, path: str, **kw) -> dict:
        if not self.keys:
            raise AdapterError("Jupiter API key is not configured (JUPITER_API_KEY)")
        problems = []
        for key in self.keys.order():
            name = self.keys.label(key)
            try:
                r = await self._client.request(method, f"{self.api}{path}", headers={"x-api-key": key}, **kw)
            except httpx.HTTPError as e:
                self.keys.fail(key, 15)
                problems.append(f"{name}: {type(e).__name__}")
                continue
            if r.status_code == 429 or r.status_code >= 500:
                self.keys.fail(key, retry_after(r.headers, 60 if r.status_code == 429 else 15))
                problems.append(f"{name}: HTTP {r.status_code}")
                continue
            if r.status_code in (401, 403):
                self.keys.fail(key, 600)
                problems.append(f"{name}: HTTP {r.status_code} (key rejected)")
                continue
            try:
                data = r.json()
            except ValueError:
                raise AdapterError(f"Jupiter {r.status_code}: {r.text[:200]}")
            if r.status_code >= 400 or (isinstance(data, dict) and data.get("success") is False):
                msg = data.get("error") or data.get("message") if isinstance(data, dict) else None
                raise AdapterError(f"Jupiter {r.status_code}: {msg or str(data)[:200]}")
            self.keys.ok(key)
            return data
        raise AdapterError("Jupiter API unavailable on every key (" + "; ".join(problems) + ")")

    # --- launch ---------------------------------------------------------------------------------

    async def build_create_tx(self, req: CreateRequest) -> BuiltTx:
        if not req.image_bytes:
            raise AdapterError("Jupiter Studio needs a token image. Add one to the launch and try again.")
        if req.dev_buy_sol > 0:
            raise AdapterError("Jupiter Studio launches have no dev buy; set it to 0")
        q = QUOTES.get((req.options or {}).get("dbc_quote") or "SOL")
        if not q:
            raise AdapterError("curve currency must be SOL or USDC")
        ctype = _content_type(req.image_bytes)
        res = await self._call("POST", "/studio/v1/dbc-pool/create-tx", json={
            "buildCurveByMarketCapParam": {
                "quoteMint": q["mint"], "initialMarketCap": q["initial"], "migrationMarketCap": q["migration"],
                "tokenQuoteDecimal": q["decimals"],
                "lockedVestingParam": {"totalLockedVestingAmount": 0, "cliffUnlockAmount": 0, "numberOfVestingPeriod": 0,
                                       "totalVestingDuration": 0, "cliffDurationFromMigrationTime": 0},
            },
            "antiSniping": True, "fee": {"feeBps": 100}, "isLpLocked": True,
            "tokenName": req.name, "tokenSymbol": req.symbol, "tokenImageContentType": ctype, "creator": req.dev_pubkey,
        })
        for need in ("transaction", "mint", "imagePresignedUrl", "metadataPresignedUrl"):
            if not res.get(need):
                raise AdapterError(f"Jupiter Studio response is missing {need}")
        s = req.socials or {}
        try:
            up = await self._client.put(res["imagePresignedUrl"], content=req.image_bytes, headers={"Content-Type": ctype})
            meta = {"name": req.name, "symbol": req.symbol, "description": req.description or "",
                    "image": res.get("imageUrl") or "", "website": s.get("website") or "",
                    "twitter": s.get("twitter") or "", "telegram": s.get("telegram") or ""}
            um = await self._client.put(res["metadataPresignedUrl"], json=meta, headers={"Content-Type": "application/json"})
        except httpx.HTTPError as e:
            raise AdapterError(f"Jupiter Studio upload failed: {type(e).__name__}")
        if up.status_code >= 300 or um.status_code >= 300:
            raise AdapterError(f"Jupiter Studio upload failed (image {up.status_code}, metadata {um.status_code})")

        async def submit(signed: bytes) -> None:
            await self._call("POST", "/studio/v1/dbc-pool/submit", data={
                "transaction": base64.b64encode(signed).decode(), "owner": req.dev_pubkey, "content": req.description or ""})

        return BuiltTx(raw=decode_tx(res["transaction"]), mint=res["mint"], submit_via=submit)

    # --- trading (Jupiter swap API) ---------------------------------------------------------------

    async def _swap(self, wallet: str, input_mint: str, output_mint: str, amount: int, slippage_pct: float) -> BuiltTx:
        if amount <= 0:
            raise AdapterError("nothing to trade")
        quote = await self._call("GET", "/swap/v1/quote", params={
            "inputMint": input_mint, "outputMint": output_mint, "amount": str(amount),
            "slippageBps": str(int(max(0.0, min(slippage_pct, 50.0)) * 100))})
        swap = await self._call("POST", "/swap/v1/swap", json={
            "quoteResponse": quote, "userPublicKey": wallet, "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True, "prioritizationFeeLamports": "auto"})
        if not swap.get("swapTransaction"):
            raise AdapterError("Jupiter returned no swap transaction")
        return BuiltTx(raw=decode_tx(swap["swapTransaction"]))

    async def build_buy_tx(self, req: TradeRequest) -> BuiltTx:
        built = await self._swap(req.wallet_pubkey, WSOL, req.mint, int(float(req.amount) * LAMPORTS_PER_SOL),
                                 req.slippage_pct)
        built.mint = req.mint
        return built

    async def build_sell_tx(self, req: TradeRequest) -> BuiltTx:
        if self.rpc is None:
            raise AdapterError("sells need an RPC connection to read your token balance")
        if not (isinstance(req.amount, str) and req.amount.endswith("%")):
            raise AdapterError("sells are sized as a percentage of the wallet's tokens")
        balance = await self.rpc.get_token_balance(req.wallet_pubkey, req.mint)
        built = await self._swap(req.wallet_pubkey, req.mint, WSOL, int(balance * float(req.amount[:-1]) // 100),
                                 req.slippage_pct)
        built.mint = req.mint
        return built

    # --- creator fees -------------------------------------------------------------------------

    async def build_claim_fees_tx(self, req: ClaimRequest) -> BuiltTx | None:
        if not req.mint:
            raise AdapterError("this launch has no token yet")
        addrs = await self._call("GET", f"/studio/v1/dbc-pool/addresses/{req.mint}")
        pool = _find(addrs, "dbcPoolAddress", "poolAddress", "pool")
        if not pool:
            raise AdapterError("Jupiter didn't return a pool for this token")
        res = await self._call("POST", "/studio/v1/dbc/fee/create-tx", json={
            "ownerWallet": req.wallet_pubkey, "poolAddress": pool, "maxQuoteAmount": 10**18})
        tx = _find(res, "transaction")
        if not tx:
            return None
        return BuiltTx(raw=decode_tx(tx))
