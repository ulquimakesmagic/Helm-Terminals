"""Receipt/trace proof of native treasury income; unavailable evidence is not zero.

Geth callTracer schema: https://geth.ethereum.org/docs/developers/evm-tracing/built-in-tracers
"""
import asyncio


def native_net_received(trace: dict, recipient: str) -> int:
    recipient = recipient.lower()
    pending, total, visited = [trace], 0, 0
    while pending:
        frame = pending.pop()
        visited += 1
        if visited > 10000 or not isinstance(frame, dict):
            raise ValueError("unusable trace")
        if frame.get("error"):
            continue  # a reverted ancestor discards all descendant payments
        kind = frame.get("type", "").upper()
        if kind not in {"CALL", "STATICCALL", "DELEGATECALL", "CALLCODE", "CREATE", "CREATE2", "SELFDESTRUCT"}:
            raise ValueError("unknown trace frame")
        if kind in {"CALL", "CREATE", "CREATE2", "SELFDESTRUCT"}:
            value = int(frame.get("value") or "0x0", 16)
            if value < 0:
                raise ValueError("negative payment")
            if (frame.get("to") or "").lower() == recipient:
                total += value
            if (frame.get("from") or "").lower() == recipient:
                total -= value
        calls = frame.get("calls") or []
        if not isinstance(calls, list):
            raise ValueError("unusable trace")
        pending.extend(calls)
    return total


async def native_fee_evidence(rpc, reference: str, recipient: str):
    """Only finalized, canonical successful transactions with a usable trace qualify."""
    try:
        async with asyncio.timeout(15):
            receipt = await rpc.receipt(reference)
            if not receipt or int(receipt.get("status", "0x0"), 16) != 1:
                return None
            if (receipt.get("transactionHash") or "").lower() != reference.lower():
                return None
            finalized = await rpc.call("eth_getBlockByNumber", ["finalized", False])
            if not finalized or int(finalized["number"], 16) < int(receipt["blockNumber"], 16):
                return None
            canonical = await rpc.call("eth_getBlockByNumber", [receipt["blockNumber"], False])
            if not canonical or canonical.get("hash") != receipt.get("blockHash"):
                return None
            trace = await rpc.call("debug_traceTransaction", [reference, {"tracer": "callTracer", "timeout": "5s"}])
            amount = native_net_received(trace, recipient)
            if amount <= 0:
                return None
            after = await rpc.receipt(reference)
            if (not after or after.get("blockHash") != receipt.get("blockHash")
                    or int(after.get("status", "0x0"), 16) != 1
                    or (after.get("transactionHash") or "").lower() != reference.lower()):
                return None
            return {"method": "finalized_receipt_and_call_trace", "reference": reference,
                    "block_hash": receipt["blockHash"], "block_number": receipt["blockNumber"],
                    "recipient": recipient, "amount_raw": str(amount), "network": rpc.network}
    except Exception:
        return None  # do not expose provider URLs/messages; operator can retry verification
