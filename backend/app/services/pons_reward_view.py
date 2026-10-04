"""Read-only stages; gross upstream fees are not creator income.

Source: https://docs.ponsfamily.com/v2, reviewed 2026-10-04.
Only tokens recognized by the pinned factory qualify.
"""
import asyncio
from datetime import datetime, timezone

from eth_abi import decode, encode
from eth_utils import keccak

from ..chains.pons import FACTORY, FEE_ESCROW, ZERO, selector

HOOK = "0xE5e702641Ea86F4ae6cC3cDaeD2B886f976Be044"


async def snapshot(adapter, token: str, rewards: dict | None) -> dict:
    info = await adapter.launched(token)
    if not info["exists"] or info["token"].lower() != token.lower():
        raise ValueError("Token does not belong to the reviewed Pons V2 factory")
    recipient = info["recipient"]
    routing_matches = bool(rewards and recipient.lower() == rewards["splitter"].lower())
    rows = []

    async def read(label, contract, signature, types=(), values=(), *, asset=None, scope="launch"):
        row = {"label": label, "contract": contract, "asset": asset or info["pair"],
               "amount_raw": None, "scope": scope}
        try:
            async with asyncio.timeout(5):
                raw = await adapter._call(contract, selector(signature) + encode(list(types), list(values)))
                payload = bytes.fromhex(raw.removeprefix("0x"))
                if len(payload) != 32:
                    raise ValueError("Unusable uint256")
                row["amount_raw"] = str(decode(["uint256"], payload)[0])
        except Exception:
            row["unavailable"] = "Provider could not verify this balance"
        rows.append(row)

    if info["phase"] == 0:
        await read("Unswept gross trading fees", info["curve"], "quoteFeeBalance()")
        await read("Unswept creator tax", info["curve"], "creatorTaxBalance()")
    else:
        currencies = sorted([token, info["pair"]], key=str.lower)
        pool_id = keccak(encode(["address", "address", "uint24", "int24", "address"],
                              [*currencies, info["pool_fee"], info["tick_spacing"], HOOK]))
        for asset in currencies:
            await read("Unswept gross trading fees", HOOK, "pendingFees(bytes32,address)",
                       ["bytes32", "address"], [pool_id, asset], asset=asset)
            await read("Unswept creator tax", HOOK, "pendingCreatorTax(bytes32,address)",
                       ["bytes32", "address"], [pool_id, asset], asset=asset)
    # Old splitter funds remain payable when the protocol redirects future rewards.
    escrow_recipient = rewards["splitter"] if rewards else recipient
    scope = "launch reward address" if rewards else "recipient across launches"
    if info["pair"].lower() == ZERO:
        await read("Escrow ready to claim", FEE_ESCROW, "balanceOf(address)",
                   ["address"], [escrow_recipient], scope=scope)
    else:
        await read("Escrow ready to claim", FEE_ESCROW, "balanceOfToken(address,address)",
                   ["address", "address"], [escrow_recipient, info["pair"]], scope=scope)
    if rewards:
        row = {"label": "Splitter-held native funds", "asset": ZERO, "contract": rewards["splitter"],
               "amount_raw": None, "scope": "launch reward address; may include donations"}
        try:
            async with asyncio.timeout(5):
                row["amount_raw"] = str(await adapter.rpc.get_balance(rewards["splitter"]))
        except Exception:
            row["unavailable"] = "Provider could not verify this balance"
        rows.append(row)
    return {"source": "Pons V2 on-chain reads", "factory": FACTORY, "recipient": recipient,
            "checked_at": datetime.now(timezone.utc).isoformat(), "rows": rows,
            "routing_matches_splitter": routing_matches if rewards else None,
            "payouts": "Completed payouts appear in transaction history; these balances are not received income.",
            "sweep": "Pons creator or sweep operator must move upstream fees to escrow. Conversion or buyback requires the protocol operator. Helm does not submit sweeps.",
            "note": "Gross trading fees include protocol allocations. Do not add these rows as creator income. Readings may come from different blocks."}
