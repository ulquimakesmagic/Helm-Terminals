"""Relay (relay.link): cross-chain and same-chain routes, e.g. USDC on Base -> ETH on Robinhood Chain.

Flow: POST /quote/v2 returns `steps`, each with `items` whose `data` is a transaction to send from the user's
wallet (EVM: to/data/value; Solana: raw instructions plus lookup tables, which we compile ourselves). After the
origin transaction lands, Relay's solver fills on the destination chain; GET /intents/status/v3 tracks it.

Helm's route fee is a Relay "app fee": bps of the input, paid to an EVM address as a USDC balance held by Relay,
claimable at relay.link. It shows up in the quote under `fees.app` and in every preview.
"""

import httpx

from .base import AdapterError
from .evm import EVM_CHAINS

RELAY_SOLANA_CHAIN_ID = 792703809
SOL_NATIVE = "11111111111111111111111111111111"
EVM_NATIVE = "0x0000000000000000000000000000000000000000"
DONE = {"success"}
FAILED = {"failure", "refund"}


def relay_chain_id(network: str) -> int:
    return RELAY_SOLANA_CHAIN_ID if network == "solana" else EVM_CHAINS[network]["chain_id"]


def native_currency(network: str) -> str:
    return SOL_NATIVE if network == "solana" else EVM_NATIVE


class Relay:
    def __init__(self, api_url: str = "https://api.relay.link", api_key: str = "", fee_bps: int = 0,
                 fee_recipient: str = "", client: httpx.AsyncClient | None = None) -> None:
        self.api = api_url.rstrip("/")
        self.api_key = api_key
        # Relay pays app fees to an EVM address, so there's no fee without one.
        self.fee_bps = fee_bps if fee_recipient else 0
        self.fee_recipient = fee_recipient
        self._client = client or httpx.AsyncClient(timeout=30)

    @property
    def fee_note(self) -> str:
        return f"{self.fee_bps / 100:g}% of the amount sent" if self.fee_bps else "none"

    def _headers(self) -> dict:
        return {"x-api-key": self.api_key} if self.api_key else {}

    async def _req(self, method: str, path: str, **kw) -> dict:
        try:
            r = await self._client.request(method, f"{self.api}{path}", headers=self._headers(), **kw)
        except httpx.HTTPError as e:
            raise AdapterError(f"Relay is unreachable ({type(e).__name__})")
        try:
            data = r.json()
        except ValueError:
            raise AdapterError(f"Relay {r.status_code}: {r.text[:160]}")
        if r.status_code >= 400:
            msg = data.get("message") if isinstance(data, dict) else None
            raise AdapterError(f"Relay couldn't route this: {msg or r.status_code}")
        return data

    async def quote(self, *, user: str, recipient: str, from_network: str, to_network: str, from_currency: str,
                    to_currency: str, amount: int, slippage_pct: float) -> dict:
        body = {
            "user": user, "recipient": recipient,
            "originChainId": relay_chain_id(from_network), "destinationChainId": relay_chain_id(to_network),
            "originCurrency": from_currency, "destinationCurrency": to_currency,
            "amount": str(amount), "tradeType": "EXACT_INPUT",
            "slippageTolerance": str(int(round(slippage_pct * 100))),
            "refundTo": user,
        }
        if self.fee_bps:
            body["appFees"] = [{"recipient": self.fee_recipient, "fee": str(self.fee_bps)}]
        q = await self._req("POST", "/quote/v2", json=body)
        if not q.get("steps"):
            raise AdapterError("Relay returned no route for this amount")
        for step in q["steps"]:
            if step.get("kind") != "transaction":
                raise AdapterError(f"Relay asked for a '{step.get('kind')}' step Helm can't sign; try another amount")
        return q

    async def status(self, request_id: str) -> dict:
        return await self._req("GET", "/intents/status/v3", params={"requestId": request_id})

    async def app_fee_balances(self, wallet: str) -> dict:
        """App fees Relay holds for `wallet`, claimable at relay.link/claim-app-fees (read-only)."""
        return await self._req("GET", f"/app-fees/{wallet}/balances")

    async def request(self, request_id: str) -> dict | None:
        """One request from GET /requests/v2 (status, quoted appFees, and paidAppFees actually collected)."""
        data = await self._req("GET", "/requests/v2", params={"id": request_id})
        reqs = data.get("requests") or []
        return reqs[0] if reqs else None

    @staticmethod
    def summarize(q: dict) -> dict:
        """The parts of a quote a person needs: what arrives, the minimum, fees, and time."""
        d = q.get("details") or {}
        out = d.get("currencyOut") or {}
        cur = out.get("currency") or {}
        dec = int(cur.get("decimals") or 0)

        def human(raw) -> float | None:
            try:
                return int(raw) / 10**dec
            except (TypeError, ValueError):
                return None

        fees = q.get("fees") or {}

        def usd(name: str) -> float:
            try:
                return round(float((fees.get(name) or {}).get("amountUsd") or 0), 4)
            except ValueError:
                return 0.0

        return {
            "receive_estimate": human(out.get("amount")), "receive_minimum": human(out.get("minimumAmount")),
            "receive_symbol": cur.get("symbol"), "receive_usd": out.get("amountUsd"),
            "fees_usd": {"network_gas": usd("gas"), "relayer": usd("relayer"), "helm_route_fee": usd("app")},
            "time_estimate_s": d.get("timeEstimate"),
            "request_id": q.get("requestId") or next((s.get("requestId") for s in q["steps"] if s.get("requestId")), None),
        }
