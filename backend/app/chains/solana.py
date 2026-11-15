"""Solana RPC client and transaction helpers."""

import asyncio
import base64
from typing import Sequence

import httpx
from solders.address_lookup_table_account import AddressLookupTableAccount
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.instruction import CompiledInstruction
from solders.message import Message, MessageHeader, MessageV0, to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from ..keyring import KeyRing, parse_list, retry_after
from ..signing.base import Signer, SigningError

TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
TOKEN_2022_PROGRAM = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SYSTEM_PROGRAM = Pubkey.from_string("11111111111111111111111111111111")
LAMPORTS_PER_SOL = 1_000_000_000


class RPCError(Exception):
    """The node answered with a JSON-RPC error (e.g. not a token mint). Rotating won't help."""


class RPCUnavailable(Exception):
    """No configured endpoint gave a usable answer (rate limits, outages, bad keys). Never contains a URL."""


class SolanaRPC:
    """JSON-RPC client over one or more endpoints (SOLANA_RPC_URL may be a comma-separated list).

    Requests rotate across endpoints; a rate-limited or failing one is rested and the next is tried. A JSON-RPC
    error is the node's real answer and is returned as-is, not retried.
    """

    def __init__(self, url: str | list[str], client: httpx.AsyncClient | None = None) -> None:
        urls = parse_list(url)
        if not urls:
            raise ValueError("no Solana RPC URL configured")
        self.ring = KeyRing(urls, cooldown_s=30)
        self.ring.kind = "RPC endpoint"
        self._client = client or httpx.AsyncClient(timeout=30)

    @property
    def url(self) -> str:
        return self.ring.items[0]

    async def _call(self, method: str, params: list) -> dict | list | int | str | None:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        problems: list[str] = []
        for url in self.ring.order():
            name = self.ring.label(url)
            try:
                r = await self._client.post(url, json=payload)
            except httpx.HTTPError as e:
                self.ring.fail(url)
                problems.append(f"{name}: {type(e).__name__}")
                continue
            if r.status_code == 429 or r.status_code >= 500:
                self.ring.fail(url, retry_after(r.headers, 30 if r.status_code == 429 else 15))
                problems.append(f"{name}: HTTP {r.status_code}")
                continue
            if r.status_code in (401, 403):
                self.ring.fail(url, 600)  # bad or exhausted key: rest it longer
                problems.append(f"{name}: HTTP {r.status_code} (key rejected)")
                continue
            if r.status_code != 200:
                self.ring.fail(url)
                problems.append(f"{name}: HTTP {r.status_code}")
                continue
            try:
                data = r.json()
            except ValueError:
                self.ring.fail(url)
                problems.append(f"{name}: invalid response")
                continue
            self.ring.ok(url)
            if "error" in data:
                raise RPCError(f"{method}: {data['error']}")
            return data["result"]
        raise RPCUnavailable(f"{method} failed on every RPC endpoint ({'; '.join(problems)})")

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

    async def get_lookup_table(self, address: str) -> AddressLookupTableAccount | None:
        """An address lookup table's addresses (they start after the table's 56-byte header)."""
        res = await self._call("getAccountInfo", [address, {"encoding": "base64", "commitment": "confirmed"}])
        value = (res or {}).get("value")  # type: ignore[union-attr]
        if not value:
            return None
        raw = base64.b64decode(value["data"][0])
        keys = [Pubkey.from_bytes(raw[i:i + 32]) for i in range(56, len(raw) - 31, 32)]
        return AddressLookupTableAccount(Pubkey.from_string(address), keys)

    async def get_mint_program(self, mint: str) -> Pubkey:
        """The token program that owns a mint: classic SPL Token or Token-2022."""
        res = await self._call("getAccountInfo", [mint, {"encoding": "base64", "commitment": "confirmed"}])
        owner = ((res or {}).get("value") or {}).get("owner")  # type: ignore[union-attr]
        if owner == str(TOKEN_2022_PROGRAM):
            return TOKEN_2022_PROGRAM
        if owner == str(TOKEN_PROGRAM):
            return TOKEN_PROGRAM
        raise ValueError("Mint is missing or belongs to an unsupported token program")

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

    async def simulate(self, tx_bytes: bytes, sig_verify: bool = True) -> str | None:
        b64 = base64.b64encode(tx_bytes).decode()
        res = await self._call(
            "simulateTransaction",
            [b64, {"encoding": "base64", "sigVerify": sig_verify, "commitment": "confirmed"}],
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

    async def confirm(self, signature: str, timeout_s: float = 60, poll_s: float = 1.0,
                      fast_poll_s: float = 0.25, fast_for_s: float = 8.0) -> tuple[str, str | None]:
        """Return (status, error) where status is confirmed | failed | timeout.

        Polls every 0.25 s (under one slot) for the first seconds, when a landed transaction is most likely to show
        up, then every `poll_s`. The deadline is wall-clock time, including the RPC calls themselves. Recent
        signatures are in every node's status cache, so the full history is only searched once, at the end."""
        loop = asyncio.get_running_loop()
        start = loop.time()
        deadline = start + timeout_s
        misses = 0
        while True:
            last = loop.time() >= deadline
            try:
                res = await self._call("getSignatureStatuses", [[signature], {"searchTransactionHistory": last}])
                misses = 0
            except (RPCUnavailable, httpx.HTTPError):
                misses += 1  # a blip: keep asking; several in a row means the node is really down
                if misses >= 5 or last:
                    raise
                await asyncio.sleep(fast_poll_s)
                continue
            st = res["value"][0]  # type: ignore[index]
            if st:
                if st.get("err"):
                    return "failed", str(st["err"])
                if st.get("confirmationStatus") in ("confirmed", "finalized"):
                    return "confirmed", None
            if last:
                return "timeout", "not confirmed in time"
            elapsed = loop.time() - start
            await asyncio.sleep(min(fast_poll_s if elapsed < fast_for_s else poll_s, max(0.0, deadline - loop.time())))


# --- Transaction building -------------------------------------------------------------------------


def build_unsigned(payer: Pubkey, instructions: list[Instruction], blockhash: str,
                   lookup_tables: list[AddressLookupTableAccount] | None = None) -> bytes:
    msg = MessageV0.try_compile(payer, instructions, lookup_tables or [], Hash.from_string(blockhash))
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


def create_ata_idempotent_ix(payer: Pubkey, owner: Pubkey, mint: Pubkey,
                             token_program: Pubkey = TOKEN_PROGRAM) -> Instruction:
    ata = find_ata(owner, mint, token_program)
    return Instruction(
        ATA_PROGRAM,
        bytes([1]),
        [
            AccountMeta(payer, True, True),
            AccountMeta(ata, False, True),
            AccountMeta(owner, False, False),
            AccountMeta(mint, False, False),
            AccountMeta(SYSTEM_PROGRAM, False, False),
            AccountMeta(token_program, False, False),
        ],
    )


def token_transfer_checked_ix(owner: Pubkey, dest_owner: Pubkey, mint: Pubkey, amount: int, decimals: int,
                              token_program: Pubkey = TOKEN_PROGRAM) -> Instruction:
    src = find_ata(owner, mint, token_program)
    dst = find_ata(dest_owner, mint, token_program)
    data = bytes([12]) + amount.to_bytes(8, "little") + bytes([decimals])
    return Instruction(
        token_program,
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
    allow_missing: bool = False,
) -> bytes:
    """Fill every required signature slot. With `allow_missing`, slots nobody here can sign stay empty (for
    launchpads that add their own signature when they submit).

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
        elif allow_missing and i > 0:  # the fee payer (slot 0) must always be ours
            sigs.append(Signature.default())
        else:
            raise SigningError(f"no signer available for required signer {k}")
    return bytes(VersionedTransaction.populate(msg, sigs))


_shared: dict[tuple[str, ...], SolanaRPC] = {}


def shared_rpc(url: str) -> SolanaRPC:
    """One client per configured endpoint list, so rotation and rest periods persist across requests."""
    key = tuple(parse_list(url))
    if key not in _shared:
        _shared[key] = SolanaRPC(list(key))
    return _shared[key]
