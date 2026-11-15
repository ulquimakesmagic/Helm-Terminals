"""Jupiter Aggregator swaps on Solana.

Third-party service, treated with the same distrust as PumpPortal: Helm asks Jupiter for a quote,
then for an *unsigned* transaction, and only signs it through the wallet's own key. The tx_guard
verifies the transaction needs no signature Helm doesn't hold, and the executor simulates before
anything is broadcast. The lite endpoint (lite-api.jup.ag) serves quotes and swaps without a key at
low rate limits; production volume should set JUPITER_API_URL=https://api.jup.ag plus an API key.

Sources: dev.jup.ag docs (swap v1), verified against the live lite API on 2026-10-02.
"""

import base64

import httpx

from ..config import get_settings
from .base import AdapterError

SWAP_KINDS = ("ExactIn",)


class Jupiter:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._http = client
        self._own = client is None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15)
        return self._http

    async def _close_if_own(self) -> None:
        if self._own and self._http is not None:
            await self._http.aclose()
            self._http = None

    def _url(self, path: str) -> str:
        s = get_settings()
        base = (s.jupiter_api_url or ("https://api.jup.ag" if s.jupiter_api_key else "https://lite-api.jup.ag")).rstrip("/")
        return f"{base}{path}"

    def _headers(self) -> dict:
        key = get_settings().jupiter_api_key
        return {"x-api-key": key} if key else {}

    @staticmethod
    def _fail(r: httpx.Response, what: str) -> AdapterError:
        detail = r.text[:140] if r.text else r.reason_phrase
        return AdapterError(f"Jupiter {what} returned {r.status_code}: {detail}")

    async def quote(self, input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int) -> dict:
        c = self._client()
        try:
            r = await c.get(self._url("/swap/v1/quote"), params={
                "inputMint": input_mint, "outputMint": output_mint, "amount": amount_raw,
                "slippageBps": slippage_bps, "restrictIntermediateTokens": "true",
            }, headers=self._headers())
        finally:
            await self._close_if_own()
        if r.status_code != 200:
            raise self._fail(r, "quote")
        return r.json()

    async def swap_tx(self, quote: dict, user_public_key: str) -> bytes:
        """The unsigned versioned transaction (base64), decoded for Helm's signer."""
        c = self._client()
        try:
            r = await c.post(self._url("/swap/v1/swap"), headers=self._headers(), json={
                "quoteResponse": quote, "userPublicKey": user_public_key, "wrapAndUnwrapSol": True,
            })
        finally:
            await self._close_if_own()
        if r.status_code != 200:
            raise self._fail(r, "swap")
        try:
            return base64.b64decode(r.json()["swapTransaction"])
        except (KeyError, ValueError) as e:
            raise AdapterError(f"Jupiter swap response had no usable transaction ({type(e).__name__})") from e

    async def token(self, mint: str) -> dict | None:
        """Symbol/decimals for a mint from Jupiter's token list; None when it isn't listed."""
        c = self._client()
        try:
            r = await c.get(self._url("/tokens/v2/search"), params={"query": mint}, headers=self._headers())
        finally:
            await self._close_if_own()
        if r.status_code != 200:
            return None
        for t in r.json() or []:
            if t.get("id") == mint or t.get("address") == mint:
                return {"symbol": t.get("symbol"), "decimals": t.get("decimals")}
        return None
