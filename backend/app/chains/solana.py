"""Solana RPC client and transaction helpers."""

import asyncio
import base64
from typing import Sequence

import httpx
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.instruction import CompiledInstruction
from solders.message import Message, MessageHeader, MessageV0, to_bytes_versioned
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

    async def simulate_balance_after(self, tx_bytes: bytes, address: str) -> int | None:
        """Lamports `address` would hold after this transaction, per simulation. None if it would fail.

        Used to price a fee on a sell before sending it: proceeds are only known once the swap runs.
        """
        b64 = base64.b64encode(tx_bytes).decode()
        res = await self._call("simulateTransaction", [b64, {
            "encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True, "commitment": "confirmed",
            "accounts": {"addresses": [address], "encoding": "base64"},
        }])
        value = res["value"]  # type: ignore[index]
        if value.get("err") is not None or not value.get("accounts") or value["accounts"][0] is None:
            return None
        return int(value["accounts"][0]["lamports"])

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


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * (len(s) - len(s.lstrip("1"))) + body


def decode_tx(s: str) -> bytes:
    """Launchpad APIs return serialized transactions as base58 or base64; accept either."""
    try:
        raw = b58decode(s)
        VersionedTransaction.from_bytes(raw)
        return raw
    except Exception:
        raw = base64.b64decode(s)
        VersionedTransaction.from_bytes(raw)
        return raw


MAX_TX_BYTES = 1232


class TxTooLarge(Exception):
    pass


def append_sol_transfers(raw: bytes, payer: Pubkey, transfers: list[tuple[Pubkey, int]]) -> bytes:
    """Add SOL transfers from `payer` into an existing unsigned transaction, so they succeed or fail with it.

    Works on legacy and v0 messages, including ones that load accounts from address lookup tables:
    new static keys are slotted into the right header section and every account index is remapped.
    Raises TxTooLarge if the result would not fit in one transaction.
    """
    transfers = [(d, n) for d, n in transfers if n > 0]
    if not transfers:
        return raw
    tx = VersionedTransaction.from_bytes(raw)
    msg = tx.message
    h = msg.header
    keys = list(msg.account_keys)
    n_sig, n_ro_sig, n_ro_unsig = h.num_required_signatures, h.num_readonly_signed_accounts, h.num_readonly_unsigned_accounts
    if payer not in keys[: n_sig - n_ro_sig]:
        raise ValueError("payer must be a writable signer of the transaction")

    ws, rs = keys[: n_sig - n_ro_sig], keys[n_sig - n_ro_sig : n_sig]
    wu, ru = keys[n_sig : len(keys) - n_ro_unsig], keys[len(keys) - n_ro_unsig :]
    new_wu = [d for d, _ in transfers if d not in keys and d != payer]
    new_wu = list(dict.fromkeys(new_wu))
    for d, _ in transfers:
        if d in rs or d in ru:
            raise ValueError(f"fee recipient {d} is read-only in this transaction")
    new_ru = [] if SYSTEM_PROGRAM in keys else [SYSTEM_PROGRAM]
    new_keys = ws + rs + wu + new_wu + ru + new_ru
    added = len(new_wu) + len(new_ru)
    pos = {k: i for i, k in enumerate(new_keys)}

    def remap(i: int) -> int:
        return pos[keys[i]] if i < len(keys) else i + added  # indices past the static keys are lookup-loaded

    ixs = [CompiledInstruction(remap(ix.program_id_index), ix.data, bytes(remap(a) for a in ix.accounts))
           for ix in msg.instructions]
    for dst, lamports in transfers:
        data = sol_transfer_ix(payer, dst, lamports).data
        ixs.append(CompiledInstruction(pos[SYSTEM_PROGRAM], data, bytes([pos[payer], pos[dst]])))  # (program, data, accounts)

    header = MessageHeader(n_sig, n_ro_sig, n_ro_unsig + len(new_ru))
    if isinstance(msg, MessageV0):
        new_msg = MessageV0(header, new_keys, msg.recent_blockhash, ixs, list(msg.address_table_lookups))
    else:
        new_msg = Message.new_with_compiled_instructions(n_sig, n_ro_sig, n_ro_unsig + len(new_ru), new_keys,
                                                         msg.recent_blockhash, ixs)
    out = bytes(VersionedTransaction.populate(new_msg, [Signature.default()] * n_sig))
    if len(out) > MAX_TX_BYTES:
        raise TxTooLarge(f"transaction would be {len(out)} bytes (max {MAX_TX_BYTES})")
    return out


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
    existing = list(tx.signatures)
    sigs: list[Signature] = []
    for i, key in enumerate(list(msg.account_keys)[:n]):
        k = str(key)
        if k in extra_map:
            sigs.append(extra_map[k].sign_message(msg_bytes))
        elif k in refs:
            sigs.append(Signature.from_bytes(await signer.sign_message(refs[k], msg_bytes)))
        elif i < len(existing) and existing[i] != Signature.default():
            sigs.append(existing[i])  # already signed by the launchpad (e.g. a mint key it generated)
        else:
            raise SigningError(f"no signer available for required signer {k}")
    return bytes(VersionedTransaction.populate(msg, sigs))
