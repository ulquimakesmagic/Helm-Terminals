"""TON: addresses, cells/BoC, Jetton transfer bodies, TON Connect proofs, and the toncenter v3 index API.

Helm does not hold TON keys. Users connect their own wallet with TON Connect; Helm builds the messages, the wallet
shows them for approval and signs and broadcasts them, and Helm follows the result on the index. A connected wallet
is bound to a Helm account only after a ton_proof signed for Helm's domain is verified here.

- Addresses: raw "wc:hex64", or user-friendly 36 bytes (flag, workchain, hash, CRC16/XMODEM) in base64 or
  base64url. 0x11 bounceable, 0x51 non-bounceable, +0x80 testnet-only. Wallet recipients get the non-bounceable
  form so funds still arrive when the wallet isn't deployed yet.
- Amounts: nanoTON (9 decimals). Jettons are TEP-74: a transfer message goes to the sender's own jetton wallet
  (op 0x0f8a7ea5) with TON attached to pay for execution; decimals come from the jetton metadata (default 9).
- Tracking: the wallet returns the signed external message; its TEP-467 normalized hash finds the transaction
  on the index. Execution succeeds when the compute and action phases succeed and nothing aborted.
Sources: networks.SOURCES["ton"].
"""

import asyncio
import base64
import hashlib
import struct
import time

import httpx

from .base import AdapterError

NANO = 10**9
JETTON_TRANSFER = 0x0F8A7EA5
JETTON_ATTACH = 50_000_000  # 0.05 TON attached to a jetton transfer; the excess returns to the sender
JETTON_FORWARD = 1  # nanoTON forwarded so the recipient's wallet sends a transfer notification


# --- addresses --------------------------------------------------------------------------------------------


def crc16(data: bytes) -> int:
    """CRC16/XMODEM (poly 0x1021, init 0), as used by TON user-friendly addresses."""
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


class TonAddress:
    __slots__ = ("wc", "hash", "bounceable", "testnet")

    def __init__(self, wc: int, hash_: bytes, bounceable: bool = True, testnet: bool = False) -> None:
        if len(hash_) != 32 or not -128 <= wc <= 127:
            raise ValueError("bad TON address")
        self.wc, self.hash, self.bounceable, self.testnet = wc, hash_, bounceable, testnet

    def raw(self) -> str:
        return f"{self.wc}:{self.hash.hex()}"

    def friendly(self, bounceable: bool, testnet: bool) -> str:
        tag = (0x11 if bounceable else 0x51) | (0x80 if testnet else 0)
        body = bytes([tag, self.wc & 0xFF]) + self.hash
        return base64.urlsafe_b64encode(body + crc16(body).to_bytes(2, "big")).decode()

    def same(self, other: "TonAddress") -> bool:
        return self.wc == other.wc and self.hash == other.hash


def parse_address(s: str) -> TonAddress:
    s = (s or "").strip()
    if ":" in s:
        wc_s, _, h = s.partition(":")
        try:
            wc = int(wc_s)
            raw = bytes.fromhex(h)
        except ValueError:
            raise ValueError("not a TON address")
        if len(h) != 64:
            raise ValueError("not a TON address")
        return TonAddress(wc, raw)
    if len(s) != 48:
        raise ValueError("not a TON address")
    try:
        b = base64.b64decode(s.replace("-", "+").replace("_", "/"), validate=True)
    except (ValueError, TypeError):
        raise ValueError("not a TON address")
    if len(b) != 36 or crc16(b[:34]) != int.from_bytes(b[34:], "big"):
        raise ValueError("TON address checksum mismatch")
    tag = b[0]
    testnet = bool(tag & 0x80)
    tag &= 0x7F
    if tag not in (0x11, 0x51):
        raise ValueError("unknown TON address flag")
    wc = b[1] - 256 if b[1] > 127 else b[1]
    return TonAddress(wc, b[2:34], bounceable=tag == 0x11, testnet=testnet)


def valid_address(s: str) -> bool:
    try:
        a = parse_address(s)
    except ValueError:
        return False
    return a.wc in (0, -1) and not a.testnet  # Helm is mainnet-only: a testnet-only address is refused


# --- cells ------------------------------------------------------------------------------------------------


class Cell:
    """An ordinary (level 0) cell: up to 1023 bits and 4 references."""

    __slots__ = ("bits", "nbits", "refs", "_hash", "_depth")

    def __init__(self, bits: int = 0, nbits: int = 0, refs: list["Cell"] | None = None) -> None:
        if nbits > 1023 or len(refs or []) > 4:
            raise ValueError("cell overflow")
        self.bits, self.nbits, self.refs = bits, nbits, refs or []
        self._hash = self._depth = None

    def data(self) -> bytes:
        """Data bytes with the completion tag when the bit length isn't a whole number of bytes."""
        n = (self.nbits + 7) // 8
        if self.nbits % 8 == 0:
            return self.bits.to_bytes(n, "big") if n else b""
        pad = 8 * n - self.nbits
        return ((self.bits << pad) | (1 << (pad - 1))).to_bytes(n, "big")

    def descriptors(self) -> bytes:
        return bytes([len(self.refs), (self.nbits // 8) + ((self.nbits + 7) // 8)])

    def depth(self) -> int:
        if self._depth is None:
            self._depth = 0 if not self.refs else 1 + max(r.depth() for r in self.refs)
        return self._depth

    def hash(self) -> bytes:
        if self._hash is None:
            rep = self.descriptors() + self.data()
            rep += b"".join(r.depth().to_bytes(2, "big") for r in self.refs)
            rep += b"".join(r.hash() for r in self.refs)
            self._hash = hashlib.sha256(rep).digest()
        return self._hash

    def slice(self) -> "Slice":
        return Slice(self)

    def boc(self) -> bytes:
        return serialize_boc(self)

    def boc_b64(self) -> str:
        return base64.b64encode(self.boc()).decode()


class Builder:
    def __init__(self) -> None:
        self.bits, self.n, self.refs = 0, 0, []

    def uint(self, v: int, n: int) -> "Builder":
        if v < 0 or v >= 1 << n:
            raise ValueError("value out of range")
        self.bits, self.n = (self.bits << n) | v, self.n + n
        return self

    def sint(self, v: int, n: int) -> "Builder":
        return self.uint(v & ((1 << n) - 1), n)

    def bit(self, b: bool | int) -> "Builder":
        return self.uint(1 if b else 0, 1)

    def coins(self, v: int) -> "Builder":
        if v < 0:
            raise ValueError("negative amount")
        ln = (v.bit_length() + 7) // 8
        if ln > 15:
            raise ValueError("amount too large")
        self.uint(ln, 4)
        return self.uint(v, 8 * ln) if ln else self

    def address(self, a: TonAddress | None) -> "Builder":
        if a is None:
            return self.uint(0, 2)  # addr_none
        return self.uint(0b100, 3).sint(a.wc, 8).uint(int.from_bytes(a.hash, "big"), 256)  # addr_std, no anycast

    def ref(self, c: Cell) -> "Builder":
        self.refs.append(c)
        return self

    def slice_rest(self, s: "Slice") -> "Builder":
        n = s.remaining_bits()
        if n:
            self.uint(s.load_uint(n), n)
        while s.remaining_refs():
            self.ref(s.load_ref())
        return self

    def cell(self) -> Cell:
        return Cell(self.bits, self.n, list(self.refs))


class Slice:
    def __init__(self, c: Cell) -> None:
        self.c, self.pos, self.rpos = c, 0, 0

    def remaining_bits(self) -> int:
        return self.c.nbits - self.pos

    def remaining_refs(self) -> int:
        return len(self.c.refs) - self.rpos

    def load_uint(self, n: int) -> int:
        if n > self.remaining_bits():
            raise ValueError("cell underflow")
        shift = self.c.nbits - self.pos - n
        self.pos += n
        return (self.c.bits >> shift) & ((1 << n) - 1)

    def load_int(self, n: int) -> int:
        v = self.load_uint(n)
        return v - (1 << n) if v >> (n - 1) else v

    def load_bit(self) -> int:
        return self.load_uint(1)

    def load_coins(self) -> int:
        ln = self.load_uint(4)
        return self.load_uint(8 * ln) if ln else 0

    def load_ref(self) -> Cell:
        if not self.remaining_refs():
            raise ValueError("no more references")
        self.rpos += 1
        return self.c.refs[self.rpos - 1]

    def load_address(self) -> TonAddress | None:
        tag = self.load_uint(2)
        if tag == 0:
            return None
        if tag == 2:
            if self.load_bit():
                raise ValueError("anycast addresses aren't supported")
            wc = self.load_int(8)
            return TonAddress(wc, self.load_uint(256).to_bytes(32, "big"))
        raise ValueError("unsupported address kind")

    def skip_address_ext(self) -> None:
        tag = self.load_uint(2)
        if tag == 1:  # addr_extern
            self.load_uint(self.load_uint(9))
        elif tag != 0:
            raise ValueError("expected an external source address")


# --- BoC --------------------------------------------------------------------------------------------------


def _crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def serialize_boc(root: Cell, crc: bool = True) -> bytes:
    order: list[Cell] = []
    index: dict[int, int] = {}

    def visit(c: Cell) -> None:  # topological: a cell before the cells it references
        if id(c) in index:
            return
        index[id(c)] = len(order)
        order.append(c)
        for r in c.refs:
            visit(r)

    visit(root)
    # re-order so every reference points forward (DFS pre-order already guarantees it for trees)
    n = len(order)
    size = max(1, (n.bit_length() + 7) // 8)
    body = b""
    for c in order:
        body += c.descriptors() + c.data() + b"".join(index[id(r)].to_bytes(size, "big") for r in c.refs)
    off = max(1, (len(body).bit_length() + 7) // 8)
    head = bytes.fromhex("b5ee9c72") + bytes([(0x40 if crc else 0) | size, off])
    head += n.to_bytes(size, "big") + (1).to_bytes(size, "big") + (0).to_bytes(size, "big")
    head += len(body).to_bytes(off, "big") + (0).to_bytes(size, "big")
    out = head + body
    return out + struct.pack("<I", _crc32c(out)) if crc else out


MAX_CELLS = 4096
MAX_DEPTH = 1024


def parse_boc(data: bytes) -> Cell:
    """The first root cell of a bag of cells (ordinary cells only). Bounded for untrusted input: sizes, counts and
    depth are checked before anything is allocated, and hashes are computed bottom-up without recursion."""
    if len(data) < 6 or data[:4] != bytes.fromhex("b5ee9c72"):
        raise ValueError("not a BoC")
    flags = data[4]
    has_idx, has_crc, size = bool(flags & 0x80), bool(flags & 0x40), flags & 0x07
    off = data[5]
    if not 1 <= size <= 4 or not 1 <= off <= 8:
        raise ValueError("bad BoC header")
    p = 6

    def rd(n: int) -> int:
        nonlocal p
        if p + n > len(data):
            raise ValueError("truncated BoC")
        v = int.from_bytes(data[p:p + n], "big")
        p += n
        return v

    cells_n, roots_n, _absent, tot = rd(size), rd(size), rd(size), rd(off)
    if not 1 <= cells_n <= MAX_CELLS or not 1 <= roots_n <= cells_n or tot > len(data):
        raise ValueError("bad BoC counts")
    roots = [rd(size) for _ in range(roots_n)]
    if has_idx:
        if p + cells_n * off > len(data):
            raise ValueError("truncated BoC")
        p += cells_n * off
    if has_crc and (len(data) < 4 or struct.pack("<I", _crc32c(data[:-4])) != data[-4:]):
        raise ValueError("BoC checksum mismatch")
    raw: list[tuple[int, int, list[int]]] = []
    for _ in range(cells_n):
        d1, d2 = rd(1), rd(1)
        if d1 & 8 or d1 & 16 or d1 >> 5 or (d1 & 7) > 4:
            raise ValueError("only ordinary cells are supported")
        nbytes = (d2 + 1) // 2
        if p + nbytes > len(data):
            raise ValueError("truncated BoC")
        v = int.from_bytes(data[p:p + nbytes], "big")
        p += nbytes
        nbits = 8 * nbytes
        if d2 % 2:  # strip the completion tag
            if v == 0:
                raise ValueError("bad cell padding")
            tz = (v & -v).bit_length() - 1
            v >>= tz + 1
            nbits -= tz + 1
        refs = [rd(size) for _ in range(d1 & 7)]
        raw.append((v, nbits, refs))
    cells: list[Cell | None] = [None] * cells_n
    for i in range(cells_n - 1, -1, -1):
        v, nbits, refs = raw[i]
        if any(r <= i or r >= cells_n for r in refs):
            raise ValueError("BoC references must point forward")
        c = Cell(v, nbits, [cells[r] for r in refs])
        if c.depth() > MAX_DEPTH:
            raise ValueError("cell tree too deep")
        c.hash()  # children are already hashed, so this never recurses deeply
        cells[i] = c
    if roots[0] >= cells_n:
        raise ValueError("bad BoC root")
    return cells[roots[0]]


def cell_from_b64(s: str) -> Cell:
    try:
        return parse_boc(base64.b64decode(s))
    except (ValueError, IndexError, TypeError, OverflowError, RecursionError) as e:
        raise ValueError(f"couldn't read the cell: {str(e)[:80]}")


# --- messages ---------------------------------------------------------------------------------------------


def jetton_transfer_body(query_id: int, amount: int, destination: TonAddress, response: TonAddress,
                         forward_ton: int = JETTON_FORWARD) -> Cell:
    return (Builder().uint(JETTON_TRANSFER, 32).uint(query_id, 64).coins(amount).address(destination)
            .address(response).bit(0).coins(forward_ton).bit(0).cell())


def read_jetton_transfer(c: Cell) -> dict:
    s = c.slice()
    if s.load_uint(32) != JETTON_TRANSFER:
        raise ValueError("not a jetton transfer")
    return {"query_id": s.load_uint(64), "amount": s.load_coins(), "destination": s.load_address(),
            "response": s.load_address()}


def _skip_state_init(s: Slice) -> None:
    if s.load_bit():
        s.load_uint(5)
    if s.load_bit():
        s.load_uint(2)
    for _ in range(3):  # code, data, library
        if s.load_bit():
            s.load_ref()


def normalized_external_hash(boc_b64: str) -> tuple[TonAddress, str]:
    """(destination wallet, TEP-467 normalized hash hex) of a signed external-in message: source cleared, import
    fee zero, no init, body always as a reference."""
    root = cell_from_b64(boc_b64)
    s = root.slice()
    if s.load_uint(2) != 0b10:
        raise ValueError("not an external-in message")
    s.skip_address_ext()
    dest = s.load_address()
    if dest is None:
        raise ValueError("the message has no destination")
    s.load_coins()
    if s.load_bit():  # init present
        if s.load_bit():
            s.load_ref()
        else:
            _skip_state_init(s)
    body = s.load_ref() if s.load_bit() else Builder().slice_rest(s).cell()
    norm = Builder().uint(0b10, 2).uint(0, 2).address(dest).coins(0).bit(0).bit(1).ref(body).cell()
    return dest, norm.hash().hex()


# --- wallets and TON Connect proofs --------------------------------------------------------------------------

# Code hashes of the standard wallet contracts (computed from @ton/ton's WalletContractV3R2/V4/V5R1 code cells);
# a TON Connect proof is accepted only from these, whose data layout (and so public key position) is known.
WALLET_CODES = {
    "84dafa449f98a6987789ba232358072bc0f76dc4524002a5d0918b9a75d2d599": "v3r2",
    "feb5ff6820e2ff0d9483e7e0d62c817d846789fb4ae580c878866d959dabd5c0": "v4r2",
    "20834b7b72b112147e1b2fb457b84e74d1a30f04f737d4f62a668e9552d2b72f": "v5r1",
}


def state_init_parts(si: Cell) -> tuple[Cell | None, Cell | None]:
    s = si.slice()
    if s.load_bit():
        s.load_uint(5)
    if s.load_bit():
        s.load_uint(2)
    code = s.load_ref() if s.load_bit() else None
    data = s.load_ref() if s.load_bit() else None
    return code, data


def wallet_public_key(code: Cell | None, data: Cell | None) -> bytes:
    if code is None or data is None:
        raise ValueError("the wallet's code or data is missing")
    kind = WALLET_CODES.get(code.hash().hex())
    if kind is None:
        raise ValueError("unsupported wallet contract (Helm verifies v3R2, v4R2 and v5R1 wallets)")
    s = data.slice()
    if kind == "v5r1":
        s.load_bit()  # is_signature_allowed
    s.load_uint(32)  # seqno
    s.load_uint(32)  # subwallet / wallet id
    return s.load_uint(256).to_bytes(32, "big")


def proof_digest(address: TonAddress, domain: str, timestamp: int, payload: str) -> bytes:
    d = domain.encode()
    msg = (b"ton-proof-item-v2/" + struct.pack(">i", address.wc) + address.hash + struct.pack("<I", len(d)) + d
           + struct.pack("<Q", timestamp) + payload.encode())
    return hashlib.sha256(b"\xff\xff" + b"ton-connect" + hashlib.sha256(msg).digest()).digest()


def verify_proof(address: TonAddress, proof: dict, public_key: bytes, domain: str, payload: str,
                 max_age_s: int = 900, now: float | None = None) -> None:
    """Raises ValueError unless `proof` is a fresh ton_proof for `domain` and `payload`, signed by `public_key`."""
    from solders.pubkey import Pubkey
    from solders.signature import Signature

    dom = proof.get("domain") if isinstance(proof.get("domain"), dict) else {}
    if dom.get("value") != domain or str(dom.get("lengthBytes")) != str(len(domain.encode())):
        raise ValueError("the proof was made for a different site")
    if proof.get("payload") != payload:
        raise ValueError("the proof doesn't answer this sign-in request")
    try:
        ts = int(str(proof.get("timestamp")))
        sig = base64.b64decode(str(proof.get("signature") or ""))
    except (TypeError, ValueError):
        raise ValueError("the proof is malformed")
    if abs((now or time.time()) - ts) > max_age_s:
        raise ValueError("the proof has expired; connect again")
    if len(sig) != 64 or not Signature(sig).verify(Pubkey(public_key), proof_digest(address, domain, ts, payload)):
        raise ValueError("the wallet's signature doesn't verify")


# --- toncenter v3 --------------------------------------------------------------------------------------------


class Toncenter:
    """toncenter API v3 with the key in X-API-Key, bounded retries on 429/5xx/network errors, URL-free errors."""

    def __init__(self, base_url: str, api_key: str = "", timeout_s: float = 15.0, retries: int = 2,
                 client: httpx.AsyncClient | None = None) -> None:
        self.base = base_url.rstrip("/")
        self.headers = {"X-API-Key": api_key} if api_key else {}
        self.retries = retries
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    async def get(self, path: str, params: dict) -> dict:
        last = "no answer"
        for attempt in range(self.retries + 1):
            try:
                r = await self._client.get(self.base + path, params=params, headers=self.headers)
            except httpx.HTTPError as e:
                last = type(e).__name__
            else:
                if r.status_code in (429, 500, 502, 503, 504):
                    last = f"HTTP {r.status_code}" + (" (rate limited: set TONCENTER_API_KEY)" if r.status_code == 429 else "")
                elif r.status_code in (401, 403):
                    raise AdapterError("toncenter refused the request: check TONCENTER_API_KEY")
                elif r.status_code >= 400:
                    raise AdapterError(f"toncenter returned HTTP {r.status_code}")
                else:
                    try:
                        return r.json()
                    except ValueError:
                        raise AdapterError("toncenter returned a non-JSON answer")
            if attempt < self.retries:
                await asyncio.sleep(0.6 * (attempt + 1))
        raise AdapterError(f"toncenter is unreachable right now ({last})")

    async def account(self, address: str, include_boc: bool = False) -> dict:
        """{status: nonexist|uninit|active|frozen, balance (nanoTON), code_boc?, data_boc?}."""
        r = await self.get("/api/v3/accountStates", {"address": address, "include_boc": str(include_boc).lower()})
        accts = r.get("accounts") or []
        if not accts:
            return {"status": "nonexist", "balance": 0}
        a = accts[0]
        return {"status": a.get("status") or a.get("account_status") or "nonexist", "balance": int(a.get("balance") or 0),
                "code_boc": a.get("code_boc"), "data_boc": a.get("data_boc")}

    async def jetton_master(self, master: str) -> dict:
        r = await self.get("/api/v3/jetton/masters", {"address": master, "limit": 1})
        masters = r.get("jetton_masters") or []
        if not masters:
            raise AdapterError("that address isn't a jetton master on this network")
        m = masters[0]
        content = m.get("jetton_content") or {}
        info = next(iter(((r.get("metadata") or {}).get(m.get("address") or "", {}) or {}).get("token_info") or []), {})
        decimals = content.get("decimals")
        if decimals in (None, ""):
            decimals = (info.get("extra") or {}).get("decimals")
        try:
            decimals = 9 if decimals in (None, "") else int(str(decimals))
        except ValueError:
            raise AdapterError("the Jetton's metadata has unreadable decimals")
        if not 0 <= decimals <= 30:
            raise AdapterError("the Jetton's metadata has out-of-range decimals")
        return {"address": m.get("address"), "name": content.get("name") or info.get("name"),
                "symbol": content.get("symbol") or info.get("symbol"), "decimals": decimals,
                "total_supply": m.get("total_supply")}

    async def jetton_wallet(self, owner: str, master: str) -> dict | None:
        r = await self.get("/api/v3/jetton/wallets", {"owner_address": owner, "jetton_address": master, "limit": 1})
        w = (r.get("jetton_wallets") or [None])[0]
        return None if w is None else {"address": w.get("address"), "balance": int(w.get("balance") or 0),
                                       "owner": w.get("owner"), "jetton": w.get("jetton")}

    async def transactions(self, account: str, limit: int = 20) -> list[dict]:
        r = await self.get("/api/v3/transactions", {"account": account, "limit": limit, "sort": "desc"})
        return r.get("transactions") or []

    async def by_message(self, msg_hash_hex: str) -> list[dict]:
        r = await self.get("/api/v3/transactionsByMessage", {"msg_hash": msg_hash_hex, "direction": "in"})
        return r.get("transactions") or []


def tx_outcome(tx: dict) -> tuple[str, str | None]:
    """('confirmed' | 'failed', error) for an indexed transaction."""
    d = tx.get("description") or {}
    comp, act = d.get("compute_ph") or {}, d.get("action") or {}
    if d.get("aborted"):
        return "failed", f"the wallet rejected the message (exit code {comp.get('exit_code')})"
    if comp.get("skipped") or comp.get("success") is False:
        return "failed", f"execution failed (exit code {comp.get('exit_code')})"
    if act and act.get("success") is False:
        return "failed", f"the wallet couldn't send (action result {act.get('result_code')})"
    return "confirmed", None
