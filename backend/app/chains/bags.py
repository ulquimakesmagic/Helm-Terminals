"""Bags (bags.fm) adapter, using the official Bags API v2.

A Bags launch is three calls:
  1. create-token-info  -> Bags stores the metadata and returns the token mint (its own mint key signs later)
  2. fee-share/config   -> a config transaction the creator signs first; 100% of creator fees go to the creator.
                           If Helm has a Bags partner key, Bags pays Helm its partner share (25% by default) of
                           the token's fees on top, out of Bags' side — this is disclosed in the deploy preview.
  3. create-launch-transaction -> the launch itself, with the dev buy

Pair-token launches (e.g. tokenized stocks like xStocks): instead of a SOL bonding curve, Bags opens a Meteora
DAMM v2 pool paired with the chosen token at launch. There is no fee-share config step; Bags returns up to five
ordered transactions (lookup table setup, token creation, launch, lookup table clean-up). Pool fees accrue in
the pair token.

Trades go through Bags' quote + swap endpoints. Every transaction comes back unsigned (apart from keys Bags
holds, like the mint), and Helm signs it through the Signer as usual.
"""

import time

import httpx

from .base import AdapterError, BuiltTx, ClaimRequest, CreateRequest, TradeRequest
from ..keyring import KeyRing, parse_list, retry_after
from .solana import LAMPORTS_PER_SOL, SolanaRPC, decode_tx

WSOL = "So11111111111111111111111111111111111111112"


class BagsAdapter:
    id = "bags"
    network = "solana"
    quote_assets = ["SOL"]

    def __init__(
        self,
        api_key: str,
        api_url: str = "https://public-api-v2.bags.fm/api/v1",
        rpc: SolanaRPC | None = None,
        partner_wallet: str = "",
        partner_config: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.keys = KeyRing(parse_list(api_key), cooldown_s=60)  # BAGS_API_KEY may list several keys
        self.rpc = rpc
        self.partner_wallet = partner_wallet
        self.partner_config = partner_config
        self._client = client or httpx.AsyncClient(timeout=30)
        self._quotes: tuple[float, list[dict]] | None = None

    def options_schema(self) -> dict:
        return {
            "fields": [
                {"key": "dev_buy_sol", "type": "number", "min": 0, "label": "Dev buy (SOL)", "default": 0},
                {"key": "slippage_pct", "type": "number", "min": 0, "max": 50, "default": 10},
            ],
            "socials": ["twitter", "telegram", "website"],
            "requires_image": True,
        }

    # --- HTTP -----------------------------------------------------------------------------------

    async def _call(self, method: str, path: str, **kw) -> object:
        """Call the Bags API, rotating keys: a rate-limited or rejected key is rested and the next one is tried."""
        if not self.keys:
            raise AdapterError("Bags API key is not configured")
        extra_headers = kw.pop("headers", {})
        problems: list[str] = []
        for key in self.keys.order():
            name = self.keys.label(key)
            try:
                r = await self._client.request(method, f"{self.api_url}{path}",
                                               headers={"x-api-key": key, **extra_headers}, **kw)
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
                raise AdapterError(f"Bags API {r.status_code}: {r.text[:200]}")
            if r.status_code != 200 or not data.get("success"):
                # A real answer about this request (bad input etc.): another key won't change it.
                msg = data.get("error") or data.get("response") or r.text[:200]
                raise AdapterError(f"Bags API {r.status_code}: {msg}")
            self.keys.ok(key)
            return data["response"]
        raise AdapterError("Bags API unavailable on every key (" + "; ".join(problems) + ")")

    # --- pair tokens --------------------------------------------------------------------------

    async def supported_quotes(self) -> list[dict]:
        """Tokens Bags allows as a launch pair (besides SOL), e.g. tokenized stocks. Cached for 10 minutes."""
        if self._quotes and time.monotonic() - self._quotes[0] < 600:
            return self._quotes[1]
        res = await self._call("GET", "/token-launch/damm-v2/supported-quote-tokens")
        tokens = [{"mint": t["mint"], "symbol": t.get("symbol") or t["mint"][:4], "name": t.get("name") or "",
                   "decimals": t.get("decimals", 0), "image": t.get("image"), "token_program": t.get("tokenProgram")}
                  for t in res.get("tokens", []) if t.get("mint") and t["mint"] != WSOL]
        self._quotes = (time.monotonic(), tokens)
        return tokens

    # --- launch ---------------------------------------------------------------------------------

    async def _token_info(self, req: CreateRequest) -> tuple[str, str]:
        form = {"name": req.name, "symbol": req.symbol, "description": req.description or req.name}
        for k in ("twitter", "telegram", "website"):
            if req.socials.get(k):
                form[k] = req.socials[k]
        info = await self._call("POST", "/token-launch/create-token-info", data=form,
                                files={"image": ("image.png", req.image_bytes, "image/png")})
        return info["tokenMint"], info["tokenMetadata"]

    async def _build_pair_launch(self, req: CreateRequest, quote_mint: str) -> BuiltTx:
        mint, metadata = await self._token_info(req)
        body = {"ipfs": metadata, "tokenMint": mint, "wallet": req.dev_pubkey, "quoteMint": quote_mint}
        if self.partner_wallet:
            body["partner"] = self.partner_wallet
        res = await self._call("POST", "/token-launch/damm-v2/create-transaction", json=body)
        by_type: dict[str, list[bytes]] = {}
        for t in res.get("transactions") or []:
            by_type.setdefault(t.get("type", ""), []).append(decode_tx(t["transaction"]))
        if len(by_type.get("LAUNCH", [])) != 1:
            raise AdapterError("Bags returned an unexpected launch plan (no single LAUNCH transaction)")
        # A lookup table can only be closed some minutes after it is deactivated, so the close step is skipped;
        # the table's small rent deposit stays with it and can be reclaimed later.
        return BuiltTx(raw=by_type["LAUNCH"][0], mint=mint,
                       pre=by_type.get("LUT_SETUP", []) + by_type.get("CREATE_TOKEN", []),
                       post=by_type.get("LUT_DEACTIVATE", []))

    async def build_create_tx(self, req: CreateRequest) -> BuiltTx:
        if not req.image_bytes:
            raise AdapterError("Bags needs a token image. Add one to the launch and try again.")
        quote = (req.options or {}).get("quote") or {}
        if quote.get("mint") and quote["mint"] != WSOL:
            return await self._build_pair_launch(req, quote["mint"])
        mint, metadata = await self._token_info(req)

        config_body = {
            "payer": req.dev_pubkey,
            "baseMint": mint,
            "claimersArray": [req.dev_pubkey],
            "basisPointsArray": [10_000],
        }
        if self.partner_wallet and self.partner_config:
            config_body["partner"] = self.partner_wallet
            config_body["partnerConfig"] = self.partner_config
        cfg = await self._call("POST", "/fee-share/config", json=config_body)
        if cfg.get("bundles"):
            raise AdapterError("Bags returned a Jito bundle for this config, which Helm doesn't send yet.")
        pre = [decode_tx(t["transaction"]) for t in cfg.get("transactions") or []]

        launch = await self._call("POST", "/token-launch/create-launch-transaction", json={
            "ipfs": metadata,
            "tokenMint": mint,
            "wallet": req.dev_pubkey,
            "initialBuyLamports": int(req.dev_buy_sol * LAMPORTS_PER_SOL),
            "configKey": cfg["meteoraConfigKey"],
        })
        return BuiltTx(raw=decode_tx(launch), mint=mint, pre=pre)

    # --- trading --------------------------------------------------------------------------------

    async def _swap(self, wallet: str, input_mint: str, output_mint: str, amount: int, slippage_pct: float) -> BuiltTx:
        if amount <= 0:
            raise AdapterError("nothing to trade")
        quote = await self._call("GET", "/trade/quote", params={
            "inputMint": input_mint, "outputMint": output_mint, "amount": str(amount),
            "slippageMode": "manual", "slippageBps": str(int(slippage_pct * 100)),
        })
        swap = await self._call("POST", "/trade/swap", json={"quoteResponse": quote, "userPublicKey": wallet})
        return BuiltTx(raw=decode_tx(swap["swapTransaction"]))

    async def build_buy_tx(self, req: TradeRequest) -> BuiltTx:
        if not req.denominated_in_sol:
            raise AdapterError("Bags buys are sized in SOL")
        built = await self._swap(req.wallet_pubkey, WSOL, req.mint, int(float(req.amount) * LAMPORTS_PER_SOL),
                                 req.slippage_pct)
        built.mint = req.mint
        return built

    async def build_sell_tx(self, req: TradeRequest) -> BuiltTx:
        if self.rpc is None:
            raise AdapterError("Bags sells need an RPC connection to read your token balance")
        if isinstance(req.amount, str) and req.amount.endswith("%"):
            balance = await self.rpc.get_token_balance(req.wallet_pubkey, req.mint)
            raw = balance * float(req.amount[:-1]) // 100
        else:
            raise AdapterError("Bags sells are sized as a percentage of the wallet's tokens")
        built = await self._swap(req.wallet_pubkey, req.mint, WSOL, int(raw), req.slippage_pct)
        built.mint = req.mint
        return built

    # --- fees -----------------------------------------------------------------------------------

    async def build_claim_fees_tx(self, req: ClaimRequest) -> BuiltTx | None:
        quote = (req.options or {}).get("quote") or {}
        if quote.get("mint") and quote["mint"] != WSOL:
            raise AdapterError(f"Fees on {quote.get('symbol', 'pair-token')}-paired Bags launches accrue in "
                               f"{quote.get('symbol', 'the pair token')}. Claim them on bags.fm for now.")
        if not req.mint:
            raise AdapterError("Bags claims are per token; this launch has no mint yet")
        txs = await self._call("POST", "/token-launch/claim-txs/v3",
                               json={"feeClaimer": req.wallet_pubkey, "tokenMint": req.mint})
        if not txs:
            return None
        raws = [decode_tx(t["tx"]) for t in txs]
        return BuiltTx(raw=raws[-1], pre=raws[:-1])
