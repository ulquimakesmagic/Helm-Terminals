"""TRON over TronGrid's full-node HTTP API.

Addresses are base58check strings starting with T (21 bytes: 0x41 + the last 20 bytes of keccak(pubkey)). They are
validated and converted here, never with EVM rules. Transactions follow the documented flow: the node builds an
unsigned transaction (/wallet/createtransaction for TRX, /wallet/triggersmartcontract for TRC-20), Helm checks it
(txID = SHA-256 of raw_data, and the decoded contract matches exactly what the preview showed), signs the txID with
secp256k1 (65 bytes r||s||v, v 0/1), broadcasts, and treats it as final only once the solidity node returns its
receipt (/walletsolidity/gettransactioninfobyid).

Fees are resources, not gas: bandwidth (a daily free allowance, else TRX burned per byte), energy for contract calls
(else TRX burned per unit, bounded by fee_limit), and a fee to activate a new account when sending it TRX.
Sources: see networks.SOURCES["tron"].
"""

import asyncio
import hashlib
import time

import httpx
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak

from .base import AdapterError

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
SUN = 1_000_000  # sun per TRX
TRANSFER_TYPE, TRIGGER_TYPE = 1, 31  # ContractType: TransferContract, TriggerSmartContract
TRC20_TRANSFER = bytes.fromhex("a9059cbb")  # transfer(address,uint256)
SIG_AND_RESULT_BYTES = 65 + 64  # a signature plus the result field TRON counts toward bandwidth


# --- addresses -----------------------------------------------------------------------------------------


def _b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = ALPHABET[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


def _b58decode(s: str) -> bytes:
    n = 0
    for c in s:
        i = ALPHABET.find(c)
        if i < 0:
            raise ValueError("not base58")
        n = n * 58 + i
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip("1"))) + body


def _checksum(b: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()[:4]


def encode_address(raw21: bytes) -> str:
    return _b58encode(raw21 + _checksum(raw21))


def decode_address(addr: str) -> bytes:
    """21 raw bytes (0x41 + 20) of a base58check TRON address. Raises ValueError if it isn't one."""
    if not isinstance(addr, str) or len(addr) != 34 or not addr.startswith("T"):
        raise ValueError("a TRON address is 34 characters starting with T")
    raw = _b58decode(addr)
    if len(raw) != 25 or raw[0] != 0x41 or _checksum(raw[:21]) != raw[21:]:
        raise ValueError("not a valid TRON address (checksum)")
    return raw[:21]


def valid_address(addr: str) -> bool:
    try:
        decode_address(addr.strip())
        return True
    except (ValueError, AttributeError):
        return False


def address_from_evm(checksum_address: str) -> str:
    """The TRON address for a secp256k1 key whose Ethereum-style address is given (same key, TRON format)."""
    return encode_address(b"\x41" + bytes.fromhex(checksum_address.removeprefix("0x")))


def address_from_pubkey(pub64: bytes) -> str:
    return encode_address(b"\x41" + keccak(pub64)[12:])


# --- a minimal protobuf reader, to check a node-built transaction before signing it ----------------------


def _varint(b: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        if i >= len(b):
            raise ValueError("truncated varint")
        byte = b[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7
        if shift > 70:
            raise ValueError("varint too long")


def pb_fields(b: bytes) -> dict[int, list]:
    """Field number -> values (ints for varints, bytes for length-delimited). Enough to read TRON's raw_data."""
    out: dict[int, list] = {}
    i = 0
    while i < len(b):
        key, i = _varint(b, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            v, i = _varint(b, i)
        elif wire == 2:
            n, i = _varint(b, i)
            v, i = b[i:i + n], i + n
            if len(v) != n:
                raise ValueError("truncated field")
        elif wire == 1:
            v, i = int.from_bytes(b[i:i + 8], "little"), i + 8
        elif wire == 5:
            v, i = int.from_bytes(b[i:i + 4], "little"), i + 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
        out.setdefault(num, []).append(v)
    return out


def check_transaction(tx: dict, *, owner: str, to: str | None = None, amount: int | None = None,
                      token: str | None = None, token_to: str | None = None, token_amount: int | None = None,
                      max_fee_limit: int = 0) -> None:
    """Refuse (AdapterError) unless the node-built transaction is exactly the transfer Helm asked for."""
    raw_hex = tx.get("raw_data_hex") or ""
    try:
        raw = bytes.fromhex(raw_hex)
    except ValueError:
        raise AdapterError("the TRON node returned a malformed transaction")
    if hashlib.sha256(raw).hexdigest() != (tx.get("txID") or "").lower():
        raise AdapterError("the TRON node's transaction id doesn't match its contents; not signing it")
    try:
        f = pb_fields(raw)
        contracts = f.get(11, [])
        if len(contracts) != 1:
            raise ValueError("expected one contract")
        c = pb_fields(contracts[0])
        ctype = c.get(1, [0])[0]
        param = pb_fields(c[2][0])
        inner = pb_fields(param[2][0])
        expiration_ms = f.get(8, [0])[0]
        fee_limit = f.get(18, [0])[0]
    except (ValueError, KeyError, IndexError):
        raise AdapterError("the TRON node returned a transaction Helm couldn't read; not signing it")
    if inner.get(1, [b""])[0] != decode_address(owner):
        raise AdapterError("the transaction isn't from the selected wallet; not signing it")
    if expiration_ms and expiration_ms < time.time() * 1000:
        raise AdapterError("the TRON node returned an already expired transaction")
    if token is None:
        if ctype != TRANSFER_TYPE or inner.get(2, [b""])[0] != decode_address(to) or inner.get(3, [0])[0] != amount:
            raise AdapterError("the transaction doesn't match the TRX transfer in the preview; not signing it")
        return
    data = inner.get(4, [b""])[0]
    if (ctype != TRIGGER_TYPE or inner.get(2, [b""])[0] != decode_address(token) or inner.get(3, [0])[0]
            or inner.get(5, [0])[0] or inner.get(6, [0])[0]  # no TRC-10 tokens attached to the call
            or data[:4] != TRC20_TRANSFER or len(data) != 68):
        raise AdapterError("the transaction doesn't match the token transfer in the preview; not signing it")
    dest, value = abi_decode(["address", "uint256"], data[4:])
    if bytes.fromhex(dest[2:]) != decode_address(token_to)[1:] or value != token_amount:
        raise AdapterError("the transaction's recipient or amount differs from the preview; not signing it")
    if fee_limit > max_fee_limit:
        raise AdapterError("the transaction allows a higher energy fee than the preview; not signing it")


# --- client --------------------------------------------------------------------------------------------


class TronClient:
    """TronGrid HTTP API with timeouts, bounded retries on 429/5xx/network errors, and URL-free errors."""

    def __init__(self, base_url: str, api_key: str = "", timeout_s: float = 15.0, retries: int = 2,
                 client: httpx.AsyncClient | None = None) -> None:
        self.base = base_url.rstrip("/")
        self.headers = {"TRON-PRO-API-KEY": api_key} if api_key else {}
        self.retries = retries
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    async def _req(self, method: str, path: str, body: dict | None = None, params: dict | None = None,
                   retries: int | None = None) -> dict:
        last = "no answer"
        tries = self.retries if retries is None else retries
        for attempt in range(tries + 1):
            try:
                r = await self._client.request(method, self.base + path, json=body, params=params, headers=self.headers)
            except httpx.HTTPError as e:
                last = type(e).__name__
            else:
                if r.status_code in (429, 500, 502, 503, 504):
                    last = f"HTTP {r.status_code}" + (" (rate limited)" if r.status_code == 429 else "")
                elif r.status_code == 403:
                    raise AdapterError("TronGrid refused the request (403): check TRONGRID_API_KEY and its quota")
                elif r.status_code >= 400:
                    raise AdapterError(f"TronGrid returned HTTP {r.status_code}")
                else:
                    try:
                        return r.json()
                    except ValueError:
                        raise AdapterError("TronGrid returned a non-JSON answer")
            if attempt < tries:
                await asyncio.sleep(0.5 * (attempt + 1))
        raise AdapterError(f"TronGrid is unreachable right now ({last})")

    async def post(self, path: str, body: dict) -> dict:
        return await self._req("POST", path, body)

    async def account(self, address: str) -> dict:
        """{} for an address that was never activated."""
        return await self.post("/wallet/getaccount", {"address": address, "visible": True})

    async def balance(self, address: str) -> int:
        return int((await self.account(address)).get("balance", 0))

    async def resources(self, address: str) -> dict:
        r = await self.post("/wallet/getaccountresource", {"address": address, "visible": True})
        return {"free_bandwidth": max(int(r.get("freeNetLimit", 0)) - int(r.get("freeNetUsed", 0)), 0),
                "staked_bandwidth": max(int(r.get("NetLimit", 0)) - int(r.get("NetUsed", 0)), 0),
                "energy": max(int(r.get("EnergyLimit", 0)) - int(r.get("EnergyUsed", 0)), 0)}

    async def chain_parameters(self) -> dict:
        r = await self._req("GET", "/wallet/getchainparameters")
        return {p["key"]: int(p.get("value", 0)) for p in r.get("chainParameter", []) if "key" in p}

    async def constant(self, owner: str, contract: str, selector: str, parameter: str = "") -> dict:
        r = await self.post("/wallet/triggerconstantcontract", {
            "owner_address": owner, "contract_address": contract, "function_selector": selector,
            "parameter": parameter, "visible": True})
        if not (r.get("result") or {}).get("result"):
            msg = (r.get("result") or {}).get("message") or "call failed"
            raise AdapterError(f"TRON contract call failed: {_readable(msg)}")
        return r

    async def trc20_balance(self, token: str, holder: str) -> int:
        r = await self.constant(holder, token, "balanceOf(address)", _param_address(holder))
        return int((r.get("constant_result") or ["0"])[0] or "0", 16)

    async def trc20_metadata(self, token: str, reader: str) -> dict:
        out = {}
        for name, sel, typ in (("name", "name()", "string"), ("symbol", "symbol()", "string"),
                               ("decimals", "decimals()", "uint8")):
            r = await self.constant(reader, token, sel)
            raw = bytes.fromhex((r.get("constant_result") or [""])[0])
            out[name] = abi_decode([typ], raw)[0] if raw else None
        if out["decimals"] is None:
            raise AdapterError("that contract doesn't answer like a TRC-20 token")
        return out

    async def trc20_transfer_energy(self, token: str, owner: str, to: str, amount: int) -> int:
        r = await self.constant(owner, token, "transfer(address,uint256)", _param_transfer(to, amount))
        return int(r.get("energy_used", 0)) + int(r.get("energy_penalty", 0))

    async def build_trx_transfer(self, owner: str, to: str, amount: int) -> dict:
        tx = await self.post("/wallet/createtransaction", {"owner_address": owner, "to_address": to,
                                                           "amount": amount, "visible": True})
        if tx.get("Error") or not tx.get("raw_data_hex"):
            raise AdapterError(f"the TRON node couldn't build the transfer: {_readable(tx.get('Error', 'no transaction'))}")
        return tx

    async def build_trc20_transfer(self, owner: str, token: str, to: str, amount: int, fee_limit: int) -> dict:
        r = await self.post("/wallet/triggersmartcontract", {
            "owner_address": owner, "contract_address": token, "function_selector": "transfer(address,uint256)",
            "parameter": _param_transfer(to, amount), "fee_limit": fee_limit, "call_value": 0, "visible": True})
        tx = r.get("transaction") or {}
        if not (r.get("result") or {}).get("result") or not tx.get("raw_data_hex"):
            raise AdapterError(f"the TRON node couldn't build the token transfer: "
                               f"{_readable((r.get('result') or {}).get('message', 'no transaction'))}")
        return tx

    async def broadcast(self, signed: dict) -> dict:
        # Never retried: a lost answer is resolved by polling the (deterministic) txID, not by sending again.
        return await self._req("POST", "/wallet/broadcasttransaction", signed, retries=0)

    async def solid_info(self, txid: str) -> dict:
        """The solidified (final) receipt, or {} until the transaction is solidified."""
        return await self.post("/walletsolidity/gettransactioninfobyid", {"value": txid})

    async def history(self, address: str, limit: int = 20, trc20: bool = False) -> list[dict]:
        path = f"/v1/accounts/{address}/transactions" + ("/trc20" if trc20 else "")
        r = await self._req("GET", path, params={"limit": min(limit, 200), "order_by": "block_timestamp,desc"})
        return r.get("data") or []


def _param_address(addr: str) -> str:
    return decode_address(addr)[1:].rjust(32, b"\0").hex()


def _param_transfer(to: str, amount: int) -> str:
    return abi_encode(["address", "uint256"], ["0x" + decode_address(to)[1:].hex(), amount]).hex()


def _readable(msg: str) -> str:
    """TRON error messages are often hex-encoded text."""
    try:
        return bytes.fromhex(msg).decode("utf-8", "replace")[:200]
    except (ValueError, TypeError):
        return str(msg)[:200]


# Broadcast answers that mean the transaction was refused outright and can't land later.
REJECTED_CODES = {"SIGERROR", "CONTRACT_VALIDATE_ERROR", "CONTRACT_EXE_ERROR", "BANDWITH_ERROR",
                  "TAPOS_ERROR", "TRANSACTION_EXPIRATION_ERROR", "TOO_BIG_TRANSACTION_ERROR"}


def bandwidth_bytes(tx: dict) -> int:
    return len(bytes.fromhex(tx["raw_data_hex"])) + SIG_AND_RESULT_BYTES


async def sign_and_send(client: TronClient, signer, signer_ref: str, tx: dict, confirm_timeout_s: float = 120,
                        poll_s: float = 3.0) -> tuple[str | None, str, str | None, dict]:
    """(txid, status, error, receipt). status: confirmed | failed | timeout (sent, not final yet)."""
    txid = tx["txID"]
    y, r, s = await signer.sign_digest(signer_ref, bytes.fromhex(txid))
    signed = {**tx, "signature": [r.to_bytes(32, "big").hex() + s.to_bytes(32, "big").hex() + bytes([y % 27 if y >= 27 else y]).hex()]}
    try:
        res = await client.broadcast(signed)
    except AdapterError as e:
        return txid, "timeout", f"broadcast result unknown ({e}); check the explorer before retrying", {}
    if not res.get("result"):
        code = str(res.get("code", ""))
        why = f"{code} {_readable(res.get('message', ''))}".strip()
        if code in REJECTED_CODES:
            return None, "failed", f"rejected by the network: {why}", {}
        # DUP_TRANSACTION_ERROR means it is already in the pool; SERVER_BUSY and the like leave it unknown. Either
        # way the txID is fixed, so the solidity node decides.
        unsure = f"the node answered {why}; "
    else:
        unsure = ""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + confirm_timeout_s
    while loop.time() < deadline:
        await asyncio.sleep(poll_s)
        try:
            info = await client.solid_info(txid)
        except AdapterError:
            continue
        if info:
            receipt = info.get("receipt") or {}
            if info.get("result") == "FAILED" or receipt.get("result") not in (None, "SUCCESS"):
                return txid, "failed", f"failed on-chain: {receipt.get('result') or _readable(info.get('resMessage', ''))}", info
            return txid, "confirmed", None, info
    return txid, "timeout", f"{unsure}not solidified yet; check the explorer before retrying", {}
