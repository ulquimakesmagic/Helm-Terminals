"""Solana RPC client and transaction helpers."""

import asyncio
import base64
from typing import Sequence

import httpx
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0, to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from ..signing.base import Signer, SigningError

TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SYSTEM_PROGRAM = Pubkey.from_string("11111111111111111111111111111111")
LAMPORTS_PER_SOL = 1_000_000_000


class RPCError(Exception):
    pass


class SolanaRPC:
    def __init__(self, url: str, client: httpx.AsyncClient | None = None) -> None:
        self.url = url
        self._client = client or httpx.AsyncClient(timeout=30)

    async def _call(self, method: str, params: list) -> dict | list | int | str | None:
        r = await self._client.post(self.url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RPCError(f"{method}: {data['error']}")
        return data["result"]

    async def get_balance(self, address: str) -> int:
        res = await self._call("getBalance", [address, {"commitment": "confirmed"}])
        return int(res["value"])  # type: ignore[index]

    async def get_latest_blockhash(self) -> str:
        res = await self._call("getLatestBlockhash", [{"commitment": "confirmed"}])
        return res["value"]["blockhash"]  # type: ignore[index]

    async def get_token_balance(self, owner: str, mint: str) -> int:
        res = await self._call(
            "getTokenAccountsByOwner",
            [owner, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}],
        )
        total = 0
        for acc in res["value"]:  # type: ignore[index]
            total += int(acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"])
        return total

    async def get_token_supply(self, mint: str) -> tuple[int, int]:
        """Return (raw_supply, decimals)."""
        res = await self._call("getTokenSupply", [mint, {"commitment": "confirmed"}])
        v = res["value"]  # type: ignore[index]
        return int(v["amount"]), int(v["decimals"])

    async def get_largest_owners(self, mint: str) -> list[tuple[str, int]]:
        """Top token accounts resolved to (owner, raw_amount)."""
        res = await self._call("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])
        accounts = res["value"]  # type: ignore[index]
        if not accounts:
            return []
        addrs = [a["address"] for a in accounts]
        infos = await self._call("getMultipleAccounts", [addrs, {"encoding": "jsonParsed"}])
        out: list[tuple[str, int]] = []
        for acc, info in zip(accounts, infos["value"]):  # type: ignore[index]
            owner = info["data"]["parsed"]["info"]["owner"] if info else addrs[0]
            out.append((owner, int(acc["amount"])))
        return out

    async def get_asset(self, mint: str) -> dict:
        """DAS getAsset (Helius and compatible RPCs)."""
        return await self._call("getAsset", {"id": mint})  # type: ignore[return-value,arg-type]

    async def simulate(self, tx_bytes: bytes) -> str | None:
        b64 = base64.b64encode(tx_bytes).decode()
        res = await self._call(
            "simulateTransaction",
            [b64, {"encoding": "base64", "sigVerify": True, "commitment": "confirmed"}],
        )
        err = res["value"]["err"]  # type: ignore[index]
        return None if err is None else str(err)

    async def send(self, tx_bytes: bytes) -> str:
        b64 = base64.b64encode(tx_bytes).decode()
        return await self._call(  # type: ignore[return-value]
            "sendTransaction", [b64, {"encoding": "base64", "skipPreflight": True, "maxRetries": 3}]
        )

    async def confirm(self, signature: str, timeout_s: float = 60, poll_s: float = 1.0) -> tuple[str, str | None]:
        """Return (status, error) where status is confirmed | failed | timeout."""
        waited = 0.0
        while waited < timeout_s:
            res = await self._call("getSignatureStatuses", [[signature], {"searchTransactionHistory": True}])
            st = res["value"][0]  # type: ignore[index]
            if st:
                if st.get("err"):
                    return "failed", str(st["err"])
                if st.get("confirmationStatus") in ("confirmed", "finalized"):
                    return "confirmed", None
            await asyncio.sleep(poll_s)
            waited += poll_s
        return "timeout", "not confirmed in time"


# --- Transaction building -------------------------------------------------------------------------


def build_unsigned(payer: Pubkey, instructions: list[Instruction], blockhash: str) -> bytes:
    msg = MessageV0.try_compile(payer, instructions, [], Hash.from_string(blockhash))
    n = msg.header.num_required_signatures
    tx = VersionedTransaction.populate(msg, [Signature.default()] * n)
    return bytes(tx)


def sol_transfer_ix(src: Pubkey, dst: Pubkey, lamports: int) -> Instruction:
    return transfer(TransferParams(from_pubkey=src, to_pubkey=dst, lamports=lamports))


def find_ata(owner: Pubkey, mint: Pubkey, token_program: Pubkey = TOKEN_PROGRAM) -> Pubkey:
    return Pubkey.find_program_address([bytes(owner), bytes(token_program), bytes(mint)], ATA_PROGRAM)[0]


def create_ata_idempotent_ix(payer: Pubkey, owner: Pubkey, mint: Pubkey) -> Instruction:
    ata = find_ata(owner, mint)
    return Instruction(
        ATA_PROGRAM,
        bytes([1]),
        [
            AccountMeta(payer, True, True),
            AccountMeta(ata, False, True),
            AccountMeta(owner, False, False),
            AccountMeta(mint, False, False),
            AccountMeta(SYSTEM_PROGRAM, False, False),
            AccountMeta(TOKEN_PROGRAM, False, False),
        ],
    )


def token_transfer_checked_ix(owner: Pubkey, dest_owner: Pubkey, mint: Pubkey, amount: int, decimals: int) -> Instruction:
    src = find_ata(owner, mint)
    dst = find_ata(dest_owner, mint)
    data = bytes([12]) + amount.to_bytes(8, "little") + bytes([decimals])
    return Instruction(
        TOKEN_PROGRAM,
        data,
        [
            AccountMeta(src, False, True),
            AccountMeta(mint, False, False),
            AccountMeta(dst, False, True),
            AccountMeta(owner, True, False),
        ],
    )


def token_burn_checked_ix(owner: Pubkey, mint: Pubkey, amount: int, decimals: int) -> Instruction:
    acct = find_ata(owner, mint)
    data = bytes([15]) + amount.to_bytes(8, "little") + bytes([decimals])
    return Instruction(
        TOKEN_PROGRAM,
        data,
        [AccountMeta(acct, False, True), AccountMeta(mint, False, True), AccountMeta(owner, True, False)],
    )


# --- Signing ---------------------------------------------------------------------------------------


async def sign_versioned(
    unsigned: bytes,
    signer: Signer,
    refs: dict[str, str],
    extra: Sequence[Keypair] = (),
) -> bytes:
    """Fill every required signature slot.

    `refs` maps a base58 address to a signer_ref (a wallet the user controls through the Signer).
    `extra` are ephemeral keypairs held by the app for this transaction only (e.g. a new token mint).
    """
    tx = VersionedTransaction.from_bytes(unsigned)
    msg = tx.message
    n = msg.header.num_required_signatures
    msg_bytes = to_bytes_versioned(msg)
    extra_map = {str(k.pubkey()): k for k in extra}
    sigs: list[Signature] = []
    for key in list(msg.account_keys)[:n]:
        k = str(key)
        if k in extra_map:
            sigs.append(extra_map[k].sign_message(msg_bytes))
        elif k in refs:
            sigs.append(Signature.from_bytes(await signer.sign_message(refs[k], msg_bytes)))
        else:
            raise SigningError(f"no signer available for required signer {k}")
    return bytes(VersionedTransaction.populate(msg, sigs))
