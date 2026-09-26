"""Build, sign, simulate, send and confirm EVM transactions for managed wallets.

The flow mirrors the Solana executor: nothing is sent unless the node's gas estimate (which executes the call
against current state) succeeds, and a transaction only counts as confirmed when its receipt says status 1.
Keys stay in the signer: Helm computes the transaction hash and asks the signer for a secp256k1 signature.
"""

import asyncio
from dataclasses import dataclass

from eth_account._utils.legacy_transactions import encode_transaction, serializable_unsigned_transaction_from_dict
from eth_account.typed_transactions import TypedTransaction
from eth_utils import to_checksum_address

from ..chains.evm import EvmRPC, EvmRPCError
from ..signing.base import Signer, SigningError

GAS_HEADROOM = 1.2
# Kept back when sending a wallet's whole balance: covers gas, plus the L1 data fee that rollups like Base add on top.
NATIVE_RESERVE_WEI = {"base": 30_000_000_000_000, "ethereum": 0, "bsc": 0, "robinhood": 30_000_000_000_000}

_locks: dict[tuple[int, str], asyncio.Lock] = {}
_next_nonce: dict[tuple[int, str], int] = {}


@dataclass
class EvmCall:
    to: str | None  # None deploys a contract
    data: bytes = b""
    value: int = 0  # wei
    gas: int | None = None  # skip estimation (rarely needed)


@dataclass
class EvmResult:
    signature: str | None  # the transaction hash
    status: str  # confirmed | failed | timeout
    error: str | None = None
    gas_cost_wei: int | None = None
    logs: list | None = None  # receipt logs, for reading events such as a new token's address
    contract_address: str | None = None  # set when the transaction deployed a contract


class EvmSender:
    def __init__(self, rpc: EvmRPC, signer: Signer, confirm_timeout_s: float = 90, poll_s: float = 1.5) -> None:
        self.rpc = rpc
        self.signer = signer
        self.confirm_timeout_s = confirm_timeout_s
        self.poll_s = poll_s

    async def fees(self) -> dict:
        """EIP-1559 fee fields, or a legacy gasPrice where the chain has no base fee."""
        base = await self.rpc.base_fee()
        if base is None:
            return {"gasPrice": await self.rpc.gas_price()}
        tip = await self.rpc.max_priority_fee()
        return {"maxFeePerGas": base * 2 + tip, "maxPriorityFeePerGas": tip}

    async def max_gas_cost(self, gas: int = 21_000) -> int:
        f = await self.fees()
        return gas * f.get("maxFeePerGas", f.get("gasPrice", 0))

    async def spendable(self, address: str) -> int:
        """What a plain transfer can send when emptying this wallet."""
        bal = await self.rpc.get_balance(address)
        reserve = 2 * await self.max_gas_cost() + NATIVE_RESERVE_WEI.get(self.rpc.network, 0)
        return max(bal - reserve, 0)

    async def estimate(self, sender: str, call: EvmCall) -> int:
        """Gas the call needs. Raises EvmRPCError if it would revert."""
        tx = {"from": sender, "value": hex(call.value), "data": "0x" + call.data.hex()}
        if call.to:
            tx["to"] = call.to
        return await self.rpc.estimate_gas(tx)

    def _unsigned(self, sender_nonce: int, gas: int, fees: dict, call: EvmCall) -> dict:
        tx = {"chainId": self.rpc.chain_id, "nonce": sender_nonce, "gas": gas, "value": call.value, "data": call.data,
              **fees}
        if call.to:
            tx["to"] = to_checksum_address(call.to)  # the signer rejects addresses that aren't checksummed
        if "maxFeePerGas" in tx:
            tx["type"] = 2
        return tx

    async def sign(self, tx: dict, signer_ref: str) -> bytes:
        if tx.get("type") == 2:
            unsigned = TypedTransaction.from_dict(tx)
            y, r, s = await self.signer.sign_digest(signer_ref, unsigned.hash())
            return TypedTransaction.from_dict({**tx, "v": y, "r": r, "s": s}).encode()
        legacy = {k: v for k, v in tx.items() if k != "type"}
        unsigned = serializable_unsigned_transaction_from_dict(legacy)
        y, r, s = await self.signer.sign_digest(signer_ref, unsigned.hash())
        return encode_transaction(unsigned, (y + 35 + 2 * self.rpc.chain_id, r, s))

    async def send(self, address: str, signer_ref: str, call: EvmCall) -> EvmResult:
        key = (self.rpc.chain_id, address.lower())
        lock = _locks.setdefault(key, asyncio.Lock())
        async with lock:  # one transaction at a time per wallet, so nonces never collide
            try:
                gas = call.gas or int(await self.estimate(address, call) * GAS_HEADROOM)
            except EvmRPCError as e:
                return EvmResult(None, "failed", f"simulation failed: {e}")
            fees = await self.fees()
            nonce = max(await self.rpc.nonce(address), _next_nonce.get(key, 0))
            tx = self._unsigned(nonce, gas, fees, call)
            try:
                raw = await self.sign(tx, signer_ref)
            except SigningError as e:
                return EvmResult(None, "failed", f"signing: {e}")
            try:
                tx_hash = await self.rpc.send_raw("0x" + raw.hex())
            except EvmRPCError as e:
                return EvmResult(None, "failed", f"rejected by node: {e}")
            _next_nonce[key] = nonce + 1
        return await self.wait(tx_hash)

    async def wait(self, tx_hash: str) -> EvmResult:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.confirm_timeout_s
        while loop.time() < deadline:
            rec = await self.rpc.receipt(tx_hash)
            if rec:
                cost = int(rec.get("gasUsed", "0x0"), 16) * int(rec.get("effectiveGasPrice", "0x0"), 16)
                if rec.get("status") == "0x1":
                    return EvmResult(tx_hash, "confirmed", None, cost, rec.get("logs") or [], rec.get("contractAddress"))
                return EvmResult(tx_hash, "failed", "transaction reverted on-chain", cost)
            await asyncio.sleep(self.poll_s)
        return EvmResult(tx_hash, "timeout", "not confirmed in time; check the explorer before retrying")


def eip191_digest(message: str) -> bytes:
    from eth_utils import keccak

    data = message.encode()
    return keccak(b"\x19Ethereum Signed Message:\n" + str(len(data)).encode() + data)


async def sign_text(signer: Signer, signer_ref: str, message: str) -> str:
    """personal_sign-style signature (r || s || v with v = 27/28), as wallets and launchpad APIs expect."""
    y, r, s = await signer.sign_digest(signer_ref, eip191_digest(message))
    return "0x" + r.to_bytes(32, "big").hex() + s.to_bytes(32, "big").hex() + bytes([27 + y]).hex()
