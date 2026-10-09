"""deBridge DLN (dln.debridge.finance): cross-chain routes, used next to Relay.

Flow: GET /v1.0/dln/order/create-tx returns the order's estimate and one transaction for the source wallet to sign
(EVM: to/data/value, after an ERC-20 approve of `tx.to`; Solana: a hex-serialized VersionedTransaction). A solver fills
the order on the destination chain for exactly the order's take amount, or the order can be cancelled and the input
returned. GET /v1.0/dln/order/{orderId}/status tracks it. Docs: https://docs.debridge.com (consulted 2026-10-09).

The quote is normalised to the shape Relay's quotes have (steps/items, details.currencyOut, requestId), so Helm's
existing route checks (output asset and chain, approved minimum, recipient, Solana spend guard, EVM call bounds)
apply unchanged. On top of that, this adapter refuses a transaction that doesn't carry the recipient's address and
reports DLN's flat native-coin fee (`fixFee`) so the preview shows it and confirmation bounds it.

DLN is cross-chain only: same-chain conversions stay with Relay (and Jupiter on Solana).
"""

import httpx
from eth_abi import encode as abi_encode

from .base import AdapterError
from .evm import EVM_CHAINS
from .relay import EVM_NATIVE, RELAY_SOLANA_CHAIN_ID, SOL_NATIVE, relay_chain_id

DLN_SOLANA_CHAIN_ID = 7565164
DONE_STATES = {"Fulfilled", "SentUnlock", "ClaimedUnlock"}
CANCELLED_STATES = {"OrderCancelled", "SentOrderCancel", "SentOrderCancelled", "ClaimedOrderCancel"}
PREFIX = "debridge:"


def dln_chain_id(network: str) -> int:
    return DLN_SOLANA_CHAIN_ID if network == "solana" else EVM_CHAINS[network]["chain_id"]


def _as_relay_chain(dln_id) -> int | None:
    try:
        n = int(dln_id)
    except (TypeError, ValueError):
        return None
    return RELAY_SOLANA_CHAIN_ID if n == DLN_SOLANA_CHAIN_ID else n


def _addr_bytes(network: str, address: str) -> bytes:
    if network == "solana":
        from .solana import b58decode

        return b58decode(address)
    return bytes.fromhex(address[2:] if address.startswith("0x") else address)


def _hex(s: str) -> bytes:
    s = s or ""
    return bytes.fromhex(s[2:] if s.startswith("0x") else s)


def _digits(v) -> int | None:
    return int(v) if isinstance(v, (int, str)) and str(v).isdigit() else None


class DeBridge:
    id = "debridge"
    label = "deBridge (debridge.finance)"

    def __init__(self, api_url: str = "https://dln.debridge.finance", access_token: str = "", fee_bps: int = 0,
                 fee_recipients: dict[str, str] | None = None, client: httpx.AsyncClient | None = None) -> None:
        self.api = api_url.rstrip("/")
        self.access_token = access_token
        self.fee_bps = fee_bps
        # DLN pays an affiliate fee on the source chain, to an address of that chain's family ("evm" / "solana").
        self.fee_recipients = {k: v for k, v in (fee_recipients or {}).items() if v}
        self._client = client or httpx.AsyncClient(timeout=30)

    def _fee_for(self, network: str) -> tuple[int, str | None]:
        recipient = self.fee_recipients.get("solana" if network == "solana" else "evm")
        return (self.fee_bps, recipient) if self.fee_bps and recipient else (0, None)

    async def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            r = await self._client.get(f"{self.api}{path}", params=params)
        except httpx.HTTPError as e:
            raise AdapterError(f"deBridge is unreachable ({type(e).__name__})")
        try:
            data = r.json()
        except ValueError:
            raise AdapterError(f"deBridge {r.status_code}: {r.text[:160]}")
        if r.status_code >= 400:
            msg = data.get("errorMessage") or data.get("message") if isinstance(data, dict) else None
            raise AdapterError(f"deBridge couldn't route this: {msg or r.status_code}")
        return data

    async def quote(self, *, user: str, recipient: str, from_network: str, to_network: str, from_currency: str,
                    to_currency: str, amount: int, slippage_pct: float, provider: str | None = None) -> dict:
        if provider not in (None, self.id):
            raise AdapterError(f"deBridge can't serve a {provider} quote")
        if from_network == to_network:
            raise AdapterError("deBridge only routes between chains")
        fee_bps, fee_to = self._fee_for(from_network)
        params = {
            "srcChainId": dln_chain_id(from_network), "srcChainTokenIn": from_currency,
            "srcChainTokenInAmount": str(amount),
            "dstChainId": dln_chain_id(to_network), "dstChainTokenOut": to_currency, "dstChainTokenOutAmount": "auto",
            "dstChainTokenOutRecipient": recipient,
            # Both authorities are the user's own wallets, so only they can cancel; a cancelled order refunds `user`.
            "srcChainOrderAuthorityAddress": user, "dstChainOrderAuthorityAddress": recipient,
            "srcAllowedCancelBeneficiary": user, "senderAddress": user,
            # The input stays exactly the amount the user chose; DLN's costs come out of the output instead.
            "prependOperatingExpenses": "false",
        }
        if fee_to:
            params.update({"affiliateFeePercent": f"{fee_bps / 100:g}", "affiliateFeeRecipient": fee_to})
        if self.access_token:
            params["accesstoken"] = self.access_token
        r = await self._get("/v1.0/dln/order/create-tx", params)
        q = self.normalize(r, user=user, recipient=recipient, from_network=from_network, to_network=to_network,
                           from_currency=from_currency, amount=amount)
        # Helm's route fee on this route, as a DLN affiliate fee (claimed from deBridge, not recorded in Helm's
        # revenue ledger). 0 when the source chain has no treasury address.
        q["helmFeeBps"] = fee_bps if fee_to else 0
        return q

    @staticmethod
    def normalize(r: dict, *, user: str, recipient: str, from_network: str, to_network: str, from_currency: str,
                  amount: int) -> dict:
        est = r.get("estimation") or {}
        inn, out = est.get("srcChainTokenIn") or {}, est.get("dstChainTokenOut") or {}
        tx = r.get("tx") or {}
        order_id = r.get("orderId")
        if not order_id or not tx.get("data"):
            raise AdapterError("deBridge returned no transaction for this route")
        if _digits(inn.get("amount")) != amount:
            raise AdapterError("deBridge changed the amount to send; nothing was signed")
        fix_fee = _digits(r.get("fixFee")) or 0
        native_in = from_currency in (SOL_NATIVE, EVM_NATIVE)
        data = _hex(tx["data"]) if from_network != "solana" else None
        try:
            want = _addr_bytes(to_network, recipient)
            if want not in (data if data is not None else _hex(tx["data"])):
                raise AdapterError("deBridge's transaction doesn't pay the selected wallet; nothing was signed")
        except ValueError:
            raise AdapterError("deBridge returned a malformed transaction; nothing was signed")
        steps = []
        if from_network == "solana":
            steps.append({"id": "deposit", "kind": "transaction", "items": [{"status": "incomplete", "data": {
                "serializedTx": tx["data"], "from": user}}]})
        else:
            value = _digits(tx.get("value")) or 0
            if value > (amount if native_in else 0) + fix_fee:
                raise AdapterError("deBridge's transaction sends more native coin than quoted; nothing was signed")
            chain = relay_chain_id(from_network)
            if not native_in:
                approve = b"\x09\x5e\xa7\xb3" + abi_encode(["address", "uint256"], [tx["to"], amount])
                steps.append({"id": "approve", "kind": "transaction", "items": [{"status": "incomplete", "data": {
                    "to": from_currency, "data": "0x" + approve.hex(), "value": "0", "chainId": chain, "from": user}}]})
            steps.append({"id": "deposit", "kind": "transaction", "items": [{"status": "incomplete", "data": {
                "to": tx["to"], "data": tx["data"], "value": str(value), "chainId": chain, "from": user}}]})
        take = [v for v in (_digits(out.get("amount")), _digits(out.get("recommendedAmount"))) if v is not None]
        usd_in, usd_out = inn.get("approximateUsdValue"), out.get("recommendedApproximateUsdValue") or out.get("approximateUsdValue")
        try:
            cost_usd = max(float(usd_in) - float(usd_out), 0.0) if usd_in is not None and usd_out is not None else 0.0
        except (TypeError, ValueError):
            cost_usd = 0.0
        return {
            "provider": "debridge",
            "requestId": PREFIX + str(order_id),
            "steps": steps,
            "nativeFeeRaw": str(fix_fee),
            "nativeFee": {"decimals": 9 if from_network == "solana" else 18,
                          "symbol": "SOL" if from_network == "solana" else EVM_CHAINS[from_network]["native"]},
            "fees": {"relayer": {"amountUsd": str(round(cost_usd, 4))}, "gas": {"amountUsd": "0"}},
            "details": {
                "recipient": recipient,
                "timeEstimate": (r.get("order") or {}).get("approximateFulfillmentDelay"),
                "currencyOut": {
                    "currency": {"chainId": _as_relay_chain(out.get("chainId")), "address": out.get("address") or "",
                                 "decimals": out.get("decimals"), "symbol": out.get("symbol")},
                    "amount": str(max(take)) if take else None,
                    # DLN fills exactly the order's take amount or not at all, so the smaller figure is the floor.
                    "minimumAmount": str(min(take)) if take else None,
                    "amountUsd": usd_out,
                },
            },
        }

    async def status(self, request_id: str) -> dict:
        order_id = request_id[len(PREFIX):] if request_id.startswith(PREFIX) else request_id
        data = await self._get(f"/v1.0/dln/order/{order_id}/status")
        state = data.get("status") or ""
        mapped = "success" if state in DONE_STATES else "refund" if state in CANCELLED_STATES else "pending"
        return {"status": mapped, "provider_status": state, "txHashes": []}

    @staticmethod
    def summarize(q: dict) -> dict:
        from .relay import Relay

        out = Relay.summarize(q)
        fee = _digits(q.get("nativeFeeRaw")) or 0
        out["provider"] = "debridge"
        out["native_fee_raw"] = str(fee)
        out["helm_route_fee_bps"] = int(q.get("helmFeeBps") or 0)
        meta = q.get("nativeFee") or {}
        if fee and meta.get("decimals") is not None:
            out["native_fee"] = f"{fee / 10 ** int(meta['decimals']):.6g} {meta.get('symbol') or ''}".strip()
        return out
