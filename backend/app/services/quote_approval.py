"""Authenticated, short-lived preview bounds. Clients cannot lower a signed floor."""
import base64
import hashlib
import hmac
import json
import time

from fastapi import HTTPException

from ..config import get_settings
from ..chains.base import AdapterError

TTL_SECONDS = 120


def _mac(payload: bytes) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, b"helm-quote-v1\0" + payload, hashlib.sha256).hexdigest()


def issue(user_id: str, scope: dict, bounds: dict) -> str:
    raw = json.dumps({"user": user_id, "scope": scope, "bounds": bounds,
                      "expires": int(time.time()) + TTL_SECONDS}, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode() + "." + _mac(raw)


def verify(token: str | None, user_id: str, scope: dict) -> dict:
    try:
        if not token or len(token) > 16384:
            raise ValueError()
        encoded, signature = token.split(".")
        raw = base64.b64decode(encoded, altchars=b"-_", validate=True)
        if not hmac.compare_digest(signature, _mac(raw)):
            raise ValueError()
        value = json.loads(raw)
        if value["user"] != user_id or value["scope"] != scope or value["expires"] <= time.time():
            raise ValueError()
        return {**value["bounds"], "expires": value["expires"]}
    except (ValueError, KeyError, TypeError):
        raise HTTPException(409, "quote_expired_or_changed: request a new preview and confirm again")


def relay_bounds(quote: dict, *, destination_asset: str, destination_network: str) -> dict:
    out = (quote.get("details") or {}).get("currencyOut") or {}
    from ..chains.relay import relay_chain_id
    actual = out.get("currency") or {}
    expected_address = destination_asset if destination_network == "solana" else destination_asset.lower()
    actual_address = actual.get("address", "")
    if destination_network != "solana":
        actual_address = actual_address.lower()
    if actual.get("chainId") != relay_chain_id(destination_network) or actual_address != expected_address:
        raise AdapterError("The quote output asset or network does not match the requested destination")
    raw = out.get("minimumAmount")
    if not isinstance(raw, (str, int)) or not str(raw).isdigit() or int(raw) <= 0:
        raise AdapterError("The provider did not supply a usable minimum output; request a new quote")
    provider = quote.get("provider") or "relay"
    # Only deBridge has a flat native fee (its fixFee), and only Helm's adapter sets it; a Relay response can't.
    fee = (quote.get("nativeFeeRaw") or "0") if provider == "debridge" else "0"
    helm_fee = int(quote.get("helmFeeBps") or 0) if provider == "debridge" else None
    if not str(fee).isdigit():
        raise AdapterError("The provider's fee is malformed; request a new quote")
    # Which provider quoted it, and its flat native-coin fee (deBridge's fixFee; 0 for Relay), are part of what the
    # user approves: confirmation re-quotes from the same provider and refuses a higher fee.
    out = {"minimum_raw": str(raw), "asset": destination_asset, "network": destination_network,
           "provider": provider, "native_fee_raw": str(fee)}
    if helm_fee is not None:
        out["helm_fee_bps"] = helm_fee  # deBridge: Helm's affiliate fee actually on this order
    return out


def check_relay_bounds(quote: dict, bounds: dict, destination_asset: str, destination_network: str) -> None:
    if bounds.get("expires", 0) <= time.time():
        raise AdapterError("The approved quote expired; review a new preview")
    current = relay_bounds(quote, destination_asset=destination_asset, destination_network=destination_network)
    if (bounds.get("asset") != destination_asset or bounds.get("network") != destination_network
            or int(current["minimum_raw"]) < int(bounds["minimum_raw"])):
        raise AdapterError("The new quote is below the approved minimum; review a new preview")


def jupiter_bounds(quote: dict, input_mint: str, output_mint: str, amount: int) -> dict:
    if (quote.get("inputMint") != input_mint or quote.get("outputMint") != output_mint
            or str(quote.get("inAmount")) != str(amount)):
        raise AdapterError("The swap quote does not match the approved input and output")
    raw = quote.get("otherAmountThreshold")
    if not str(raw).isdigit() or int(raw) <= 0:
        raise AdapterError("The swap quote has no usable minimum output")
    return {"input": input_mint, "asset": output_mint, "amount_raw": str(amount), "minimum_raw": str(raw)}


def check_jupiter_bounds(quote: dict, bounds: dict, input_mint: str, output_mint: str, amount: int) -> None:
    current = jupiter_bounds(quote, input_mint, output_mint, amount)
    if (bounds.get("expires", 0) <= time.time() or bounds.get("input") != input_mint
            or bounds.get("asset") != output_mint or bounds.get("amount_raw") != str(amount)
            or int(current["minimum_raw"]) < int(bounds["minimum_raw"])):
        raise AdapterError("The approved swap expired or its minimum output fell; review a fresh preview")
