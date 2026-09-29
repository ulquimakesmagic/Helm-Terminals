"""Launchpads on TRON, NEAR and Arc: SunPump, Meme.Cooking and Tolly.

Each adapter turns a launch or a trade into a plan: facts to preview (the hashed summary, plus volatile fee
estimates) and the steps to run, in order, with the wallet's own key through Helm's signer. Every step re-reads what
it needs at send time and is checked before signing (TRON: the node-built call must be exactly the planned one).

SunPump (TRON). One launchpad contract for create, buy and sell; tokens trade on its bonding curve until they
  graduate to SunSwap V2 (not routed by Helm). Addresses and ABI from SUN.io's own SDK (@sun-protocol/sun-kit 1.2.0),
  cross-checked with Bitquery's SunPump docs and Pinax's ABI set. SunPump's contract source is not published.
  createAndInitPurchase(name, symbol) takes no image: SunPump shows images from its own site, so a token launched
  here has no image on sunpump.meme.
Meme.Cooking (NEAR). A timed auction, not a bonding curve: deposit wNEAR during the countdown, withdraw (2% fee)
  before it ends; on success the token launches with a Ref Finance pool and depositors claim their share, otherwise
  they claim a refund. Methods from the open-source Meme.Cooking frontend (Shitzu-Apes/app); the factory's source is
  not public. The image and metadata are pinned to IPFS with Helm's Pinata key (PINATA_JWT).
Tolly (Arc). No bonding curve: createToken mints the whole supply single-sided into a Uniswap V3 token/USDC pool
  (1% fee tier) with the LP locked; trading is Uniswap V3 swaps through Arc's SwapRouter02. Verified source:
  github.com/TollyLabs/v3-contracts (bytecode-matched deployment for chain 5042).
"""

import base64
import hashlib
import io
import json
import os
import time
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import httpx
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak, to_checksum_address

from .base import AdapterError


@dataclass
class StepResult:
    signature: str | None
    status: str  # confirmed | failed | timeout
    error: str | None = None
    extra: dict = field(default_factory=dict)  # e.g. a receipt, a NEAR return value


@dataclass
class PadStep:
    label: str
    run: Callable[[], Awaitable[StepResult]]
    essential: bool = True


@dataclass
class PadPlan:
    summary: dict  # facts the user confirms (hashed)
    display: dict = field(default_factory=dict)  # volatile estimates (shown, not hashed)
    warnings: list[str] = field(default_factory=list)
    steps: list[PadStep] = field(default_factory=list)
    # After the steps: the launched token's address and anything to remember on the launch.
    finish: Callable[[list[StepResult]], Awaitable[dict]] | None = None


@dataclass
class PadContext:
    """Who is acting and how to sign: the wallet, the signer, and the chain clients."""
    address: str
    signer_ref: str
    signer: object
    tron: object = None
    near: object = None
    evm: object = None  # an EvmSender for Arc


def _sel(sig: str) -> bytes:
    return keccak(text=sig)[:4]


def _slip_min(amount: int, slippage_pct: float) -> int:
    return amount * max(0, 10_000 - int(round(slippage_pct * 100))) // 10_000


# --- SunPump (TRON) ---------------------------------------------------------------------------------------

SUNPUMP = {"tron": "TTfvyrAz86hbZk5iDpKD78pqLGgi8C7AAw", "tron-nile": "TLtTyEwqacNKc5CHLunKvxmqLB336R4Lrm"}
SUNPUMP_TOKEN_CREATE = keccak(text="TokenCreate(address,uint256,address)").hex()
SUNPUMP_STATES = {0: "not a SunPump token", 1: "trading on the bonding curve", 2: "graduating", 3: "graduated"}
TRX = 1_000_000


class SunPump:
    id = "sunpump"
    network = "tron"
    kind = "extra"
    quote_assets = ["native"]
    unit = "TRX"

    def options_schema(self) -> dict:
        return {"fields": [{"key": "dev_buy_sol", "type": "number", "min": 0, "label": "Dev buy (TRX)", "default": 0}],
                "notes": ["SunPump stores only the name and symbol on-chain; the image shows in Helm, not on "
                          "sunpump.meme.", "Buys and sells use SunPump's bonding curve until the token graduates to "
                          "SunSwap, which Helm doesn't route yet."]}

    def _pad(self, network: str) -> str:
        return SUNPUMP[network]

    def token_from_info(self, network: str, info: dict) -> dict:
        """The new token from a solidified receipt: TokenCreate(tokenAddress, tokenIndex, creator), none indexed,
        emitted by the launchpad itself (Pinax's SunPump ABI)."""
        from . import tron

        pad_hex = tron.decode_address(self._pad(network))[1:].hex()
        for lg in tron.event_logs(info, SUNPUMP_TOKEN_CREATE):
            if (lg.get("address") or "").lower()[-40:] == pad_hex and len(lg.get("data") or "") >= 64:
                return {"token": tron.address_from_word(lg["data"][:64])}
        return {}

    async def resolve(self, ctx: PadContext, network: str, tx: str, symbol: str) -> dict:
        """For a launch that was sent but not confirmed in time: look for it again."""
        info = await ctx.tron.solid_info(tx)
        if not info:
            return {}
        if info.get("result") == "FAILED":
            return {"failed": True}
        return self.token_from_info(network, info)

    async def _word(self, c, owner: str, network: str, sig: str, types: list[str], values: list, n: int = 1,
                    call_value: int = 0) -> list[int]:
        r = await c.constant(owner, self._pad(network), sig, abi_encode(types, values).hex() if types else "",
                             call_value=call_value)
        raw = bytes.fromhex((r.get("constant_result") or [""])[0] or "")
        if len(raw) < 32 * n:
            raise AdapterError(f"SunPump didn't answer {sig.split('(')[0]}")
        return [int.from_bytes(raw[32 * i:32 * i + 32], "big") for i in range(n)]

    async def _energy_fee(self, c, owner: str, contract: str, sig: str, param_hex: str, call_value: int) -> tuple[int, int]:
        """(fee_limit, estimated burn) in sun, from a simulated call's energy and the chain's energy price."""
        import math

        r = await c.constant(owner, contract, sig, param_hex, call_value=call_value)
        energy = int(r.get("energy_used") or 0) + int(r.get("energy_penalty") or 0)
        params = await c.chain_parameters()
        price = params.get("getEnergyFee", 100)
        burn = energy * price
        limit = min(max(math.ceil(burn * 1.5), 10 * TRX), params.get("getMaxFeeLimit", 15_000 * TRX))
        return limit, burn

    async def _send_call(self, ctx: PadContext, contract: str, sig: str, param_hex: str, call_value: int,
                         fee_limit: int) -> StepResult:
        from . import tron

        try:
            tx = await ctx.tron.build_call(ctx.address, contract, sig, param_hex, call_value, fee_limit)
            tron.check_call(tx, owner=ctx.address, contract=contract, data=_sel(sig) + bytes.fromhex(param_hex),
                            call_value=call_value, max_fee_limit=fee_limit)
            txid, status, err, info = await tron.sign_and_send(ctx.tron, ctx.signer, ctx.signer_ref, tx)
        except AdapterError as e:
            return StepResult(None, "failed", str(e)[:200])
        return StepResult(txid, status, err, {"info": info})

    async def plan_create(self, ctx: PadContext, network: str, name: str, symbol: str, dev_buy: int, **_) -> PadPlan:
        c = ctx.tron
        pad = self._pad(network)
        fee = (await self._word(c, ctx.address, network, "LAUNCH_FEE()", [], []))[0]
        value = fee + dev_buy
        param = abi_encode(["string", "string"], [name, symbol]).hex()
        sig = "createAndInitPurchase(string,string)"
        warnings = []
        try:
            limit, burn = await self._energy_fee(c, ctx.address, pad, sig, param, value)
        except AdapterError as e:
            limit, burn = 0, 0
            warnings.append(f"SunPump would reject this launch right now: {e}")
        bal = await c.balance(ctx.address)
        if bal < value + burn:
            warnings.append(f"The dev wallet holds {bal / TRX:g} TRX; the launch needs about "
                            f"{(value + burn) / TRX:g} TRX (launch fee, dev buy and energy).")
        summary = {"launchpad": "sunpump", "contract": pad, "launch_fee_trx": fee / TRX, "dev_buy": dev_buy / TRX,
                   "value_sent_trx": value / TRX, "image": "not stored on-chain by SunPump"}

        async def run():
            return await self._send_call(ctx, pad, sig, param, value, limit)

        async def finish(results: list[StepResult]) -> dict:
            return self.token_from_info(network, results[0].extra.get("info") or {})

        return PadPlan(summary, {"energy_fee_trx_about": burn / TRX, "fee_limit_trx": limit / TRX}, warnings,
                       [PadStep("launch", run)], finish)

    async def _quote(self, ctx: PadContext, network: str, tok: str, side: str, amount: int) -> tuple[int, int]:
        if side == "buy":
            out, fee = await self._word(ctx.tron, ctx.address, network, "getTokenAmountByPurchaseWithFee(address,uint256)",
                                        ["address", "uint256"], [tok, amount], n=2)
        else:
            out, fee = await self._word(ctx.tron, ctx.address, network, "getTrxAmountBySaleWithFee(address,uint256)",
                                        ["address", "uint256"], [tok, amount], n=2)
        return out, fee

    def _call(self, side: str, tok: str, amount: int, min_out: int) -> tuple[str, str, int]:
        if side == "buy":
            return "purchaseToken(address,uint256)", abi_encode(["address", "uint256"], [tok, min_out]).hex(), amount
        return ("saleToken(address,uint256,uint256)",
                abi_encode(["address", "uint256", "uint256"], [tok, amount, min_out]).hex(), 0)

    async def plan_trade(self, ctx: PadContext, network: str, token: str, side: str, amount: int,
                         slippage_pct: float) -> PadPlan:
        """amount: sun to spend (buy) or raw tokens to sell. The quote and its minimum output are shown here and
        taken again when the trade is sent, so the confirmation only pins the amount and the slippage."""
        from . import tron

        c = ctx.tron
        pad = self._pad(network)
        tok = "0x" + tron.decode_address(token)[1:].hex()
        state = (await self._word(c, ctx.address, network, "getTokenState(address)", ["address"], [tok]))[0]
        if state != 1:
            raise AdapterError(f"this token is {SUNPUMP_STATES.get(state, 'not tradable here')}: SunPump's curve "
                               "trades only before graduation (after it, it trades on SunSwap, which Helm doesn't "
                               "route yet)")
        out, fee = await self._quote(ctx, network, tok, side, amount)
        warnings, steps = [], []
        pad_hex = "0x" + tron.decode_address(pad)[1:].hex()
        if side == "buy":
            summary = {"side": "buy", "spend_trx": amount / TRX, "slippage_pct": slippage_pct}
            display = {"expected_tokens_raw": str(out), "launchpad_fee_trx": fee / TRX}
        else:
            summary = {"side": "sell", "tokens_raw": str(amount), "slippage_pct": slippage_pct}
            display = {"expected_trx": out / TRX, "launchpad_fee_trx": fee / TRX}
            held = await c.trc20_balance(token, ctx.address)
            if held < amount:
                warnings.append("The wallet holds fewer tokens than this sell.")
            r = await c.constant(ctx.address, token, "allowance(address,address)",
                                 abi_encode(["address", "address"], ["0x" + tron.decode_address(ctx.address)[1:].hex(),
                                                                     pad_hex]).hex())
            if int((r.get("constant_result") or ["0"])[0] or "0", 16) < amount:
                ap = abi_encode(["address", "uint256"], [pad_hex, amount]).hex()

                async def approve():
                    try:
                        limit, _ = await self._energy_fee(c, ctx.address, token, "approve(address,uint256)", ap, 0)
                    except AdapterError as e:
                        return StepResult(None, "failed", f"the approval would fail: {e}")
                    return await self._send_call(ctx, token, "approve(address,uint256)", ap, 0, limit)

                steps.append(PadStep("approve", approve))
        sig, param, value = self._call(side, tok, amount, _slip_min(out, slippage_pct))
        try:
            _, burn = await self._energy_fee(c, ctx.address, pad, sig, param, value)
        except AdapterError as e:
            if side == "sell" and steps:
                burn = 0  # can only be simulated once SunPump may spend the tokens
            else:
                raise AdapterError(f"SunPump would reject this trade: {e}")
        bal = await c.balance(ctx.address)
        if bal < value + burn:
            warnings.append(f"The wallet holds {bal / TRX:g} TRX; this needs about {(value + burn) / TRX:g} TRX "
                            "including energy.")
        display["energy_fee_trx_about"] = burn / TRX
        confirmed_min = _slip_min(out, slippage_pct)
        cap = max(int(burn * 1.5 * 1.2), 30 * TRX)

        async def run():
            # The minimum output is the stricter of the quote the user confirmed and the live one, so a price move
            # after confirming can't make the trade worse than what was reviewed (it reverts instead).
            try:
                now_out, _ = await self._quote(ctx, network, tok, side, amount)
                s2, p2, v2 = self._call(side, tok, amount, max(confirmed_min, _slip_min(now_out, slippage_pct)))
                limit, _ = await self._energy_fee(c, ctx.address, pad, s2, p2, v2)
            except AdapterError as e:
                return StepResult(None, "failed", f"SunPump would reject this trade right now: {e}")
            return await self._send_call(ctx, pad, s2, p2, v2, min(limit, cap))

        steps.append(PadStep(side, run))
        return PadPlan(summary, display, warnings, steps)


# --- Meme.Cooking (NEAR) ----------------------------------------------------------------------------------

MEME_COOKING = {"near": "meme-cooking.near"}
WRAP_NEAR = {"near": "wrap.near"}
YOCTO = 10**24
TGAS = 10**12
FT_STORAGE = 1_250_000_000_000_000_000_000  # 0.00125 NEAR: NEP-145 registration used by wNEAR and meme tokens
DURATIONS_H = (1, 6, 12, 24)


def _meme_id(v) -> int | None:
    try:
        return int(v) if v is not None and not isinstance(v, bool) and str(v).strip().isdigit() else None
    except (TypeError, ValueError):
        return None


def meme_token_id(symbol: str, meme_id: int, network: str = "near") -> str:
    return f"{symbol.lower()}-{meme_id}.{MEME_COOKING[network]}"


def icon_data_uri(image: bytes) -> str:
    """The 96x96 PNG data URI Meme.Cooking stores on-chain as the token icon."""
    from PIL import Image

    im = Image.open(io.BytesIO(image)).convert("RGBA")
    im.thumbnail((96, 96))
    canvas = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    canvas.paste(im, ((96 - im.width) // 2, (96 - im.height) // 2))
    buf = io.BytesIO()
    canvas.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class Pinata:
    """Pins the token image and its metadata JSON to IPFS with Helm's own Pinata key."""

    def __init__(self, jwt: str, client: httpx.AsyncClient | None = None) -> None:
        self.jwt = jwt
        self._client = client or httpx.AsyncClient(timeout=30)

    async def _post(self, path: str, **kw) -> str:
        r = await self._client.post("https://api.pinata.cloud" + path, headers={"Authorization": f"Bearer {self.jwt}"}, **kw)
        if r.status_code in (401, 403):
            raise AdapterError("Pinata refused the upload: check PINATA_JWT")
        if r.status_code >= 400:
            raise AdapterError(f"Pinata upload failed (HTTP {r.status_code})")
        cid = (r.json() or {}).get("IpfsHash")
        if not cid:
            raise AdapterError("Pinata returned no IPFS hash")
        return cid

    async def pin_file(self, data: bytes, name: str) -> str:
        return await self._post("/pinning/pinFileToIPFS", files={"file": (name, data)})

    async def pin_bytes_json(self, raw: bytes, name: str) -> str:
        return await self._post("/pinning/pinFileToIPFS", files={"file": (name, raw, "application/json")})


class MemeCooking:
    id = "meme-cooking"
    network = "near"
    kind = "extra"
    quote_assets = ["native"]
    unit = "NEAR"

    def __init__(self, pinata: Pinata | None = None) -> None:
        self.pinata = pinata

    def options_schema(self) -> dict:
        return {"requires_image": True, "fields": [
            {"key": "dev_buy_sol", "type": "number", "min": 0, "label": "Your own deposit (NEAR)", "default": 0},
            {"key": "duration_h", "type": "select", "options": list(DURATIONS_H), "default": 24,
             "label": "Auction length (hours)"},
            {"key": "soft_cap_near", "type": "number", "min": 1, "default": 100,
             "label": "Soft cap (NEAR): below this the launch fails and deposits are refunded"},
            {"key": "hard_cap_near", "type": "number", "min": 0, "default": 0,
             "label": "Hard cap (NEAR, 0 = none): reaching it launches immediately"},
        ], "notes": ["Meme.Cooking is a timed auction, not a bonding curve: 'buy' deposits NEAR, 'sell' withdraws "
                     "before the end (2% fee), and after the end you claim tokens (or a refund).",
                     "On success the token trades on Ref Finance, which Helm doesn't route yet."]}

    async def _view(self, ctx: PadContext, contract: str, method: str, args: dict):
        return await ctx.near.view(contract, method, args)

    async def _send(self, ctx: PadContext, receiver: str, actions: list[bytes]) -> StepResult:
        from . import near

        try:
            h, status, err, result = await near.sign_and_send_result(
                ctx.near, ctx.signer, ctx.signer_ref, ctx.address, bytes.fromhex(ctx.address), receiver, actions)
        except AdapterError as e:
            return StepResult(None, "failed", str(e)[:200])
        return StepResult(h, status, err, {"value": near.success_value(result)})

    async def _registered(self, ctx: PadContext, contract: str) -> bool:
        return await self._view(ctx, contract, "storage_balance_of", {"account_id": ctx.address}) is not None

    async def plan_create(self, ctx: PadContext, network: str, name: str, symbol: str, dev_buy: int,
                          description: str = "", image: bytes | None = None, socials: dict | None = None,
                          options: dict | None = None, **_) -> PadPlan:
        from . import near

        if not image:
            raise AdapterError("Meme.Cooking needs a token image")
        if self.pinata is None:
            raise AdapterError("Meme.Cooking launches need Helm's IPFS key (PINATA_JWT) on the server")
        o = options or {}
        mc = MEME_COOKING[network]
        duration_h = int(o.get("duration_h", 24))
        if duration_h not in DURATIONS_H:
            raise AdapterError(f"auction length must be one of {DURATIONS_H} hours")
        try:
            soft = int(Decimal(str(o.get("soft_cap_near", 100))) * YOCTO)
            hard = int(Decimal(str(o.get("hard_cap_near", 0) or 0)) * YOCTO)
        except (InvalidOperation, ValueError):
            raise AdapterError("the soft and hard caps must be numbers")
        if soft <= 0:
            raise AdapterError("the soft cap must be above zero")
        if hard and hard <= soft:
            raise AdapterError("the hard cap must be above the soft cap")
        icon = icon_data_uri(image)
        s = socials or {}
        meta = {"description": description or "", "twitterLink": s.get("twitter") or "",
                "telegramLink": s.get("telegram") or "", "website": s.get("website") or "", "image": ""}
        args = {"duration_ms": str(duration_h * 3_600_000), "name": name, "symbol": symbol, "icon": icon,
                "decimals": 18, "total_supply": str(10**9 * 10**18), "deposit_token_id": WRAP_NEAR[network],
                "soft_cap": str(soft), **({"hard_cap": str(hard)} if hard else {})}
        costs = await self._view(ctx, mc, "storage_costs", {})
        registered = await self._registered(ctx, mc)
        placeholder = {**args, "reference": "Q" * 46, "reference_hash": base64.b64encode(bytes(32)).decode()}
        storage = int(await self._view(ctx, mc, "create_meme_storage_cost", {**placeholder, "sender_id": ctx.address}))
        deposit = storage * 105 // 100
        need = deposit + (0 if registered else int(costs["account"])) + dev_buy
        acct = await ctx.near.account(ctx.address)
        avail = near.available_balance(acct)
        warnings = []
        if avail < need + 5 * 10**22:  # plus about 0.05 NEAR of gas
            warnings.append(f"The dev wallet has {avail / YOCTO:g} NEAR available; the launch needs about "
                            f"{need / YOCTO:g} NEAR (storage{', your deposit' if dev_buy else ''} and gas).")
        summary = {"launchpad": "meme-cooking", "contract": mc, "auction_hours": duration_h,
                   "soft_cap_near": soft / YOCTO, "hard_cap_near": hard / YOCTO if hard else None,
                   "total_supply": "1,000,000,000 (18 decimals)", "own_deposit_near": dev_buy / YOCTO,
                   "storage_deposit_near": round(deposit / YOCTO, 6)}
        state: dict = {}

        async def create():
            # Pinned at send time, so nothing is uploaded for a preview that isn't confirmed.
            try:
                image_cid = await self.pinata.pin_file(image, f"{symbol}.png")
                ref_raw = json.dumps({**meta, "image": image_cid}, separators=(",", ":")).encode()
                ref_cid = await self.pinata.pin_bytes_json(ref_raw, f"{symbol}.json")
            except AdapterError as e:
                return StepResult(None, "failed", f"image upload failed, nothing was sent: {e}")
            final = {**args, "reference": ref_cid,
                     "reference_hash": base64.b64encode(hashlib.sha256(ref_raw).digest()).decode()}
            cost = int(await self._view(ctx, mc, "create_meme_storage_cost", {**final, "sender_id": ctx.address}))
            if cost * 105 // 100 > deposit * 12 // 10:
                return StepResult(None, "failed", "Meme.Cooking's storage cost rose above the preview; nothing was "
                                                  "sent. Review the launch again.")
            actions = []
            if not await self._registered(ctx, mc):
                actions.append(near.function_call_action("storage_deposit", {}, 20 * TGAS, int(costs["account"])))
            actions.append(near.function_call_action("create_meme", final, 250 * TGAS, cost * 105 // 100))
            res = await self._send(ctx, mc, actions)
            state["tx"] = res.signature
            if res.status == "confirmed":
                mid = _meme_id(res.extra.get("value"))
                if mid is not None:  # otherwise the launch stays "deploying" and is looked up again, never relaunched
                    state["meme_id"] = mid
            return res

        steps = [PadStep("launch", create)]
        if dev_buy:
            async def own_deposit():
                if "meme_id" not in state:
                    return StepResult(None, "failed", "the launch didn't confirm, so no deposit was made")
                return await self._deposit(ctx, network, state["meme_id"], dev_buy)

            steps.append(PadStep("deposit", own_deposit, essential=False))

        async def finish(results: list[StepResult]) -> dict:
            if "meme_id" not in state:
                return {}
            mid = state["meme_id"]
            return {"token": meme_token_id(symbol, mid, network),
                    "params": {"meme_id": mid, "auction_ends_ms": int(time.time() * 1000) + duration_h * 3_600_000}}

        return PadPlan(summary, {}, warnings, steps, finish)

    async def resolve(self, ctx: PadContext, network: str, tx: str, symbol: str) -> dict:
        from . import near

        try:
            res = await ctx.near.status(tx, ctx.address)
        except AdapterError:
            return {}
        status, _ = near.outcome_status(res)
        if status == "failed":
            return {"failed": True}
        mid = _meme_id(near.success_value(res))
        return {"token": meme_token_id(symbol, mid, network), "params": {"meme_id": mid}} if mid is not None else {}

    async def _deposit(self, ctx: PadContext, network: str, meme_id: int, amount: int) -> StepResult:
        from . import near

        mc, wrap = MEME_COOKING[network], WRAP_NEAR[network]
        if not await self._registered(ctx, mc):
            costs = await self._view(ctx, mc, "storage_costs", {})
            reg = await self._send(ctx, mc, [near.function_call_action("storage_deposit", {}, 30 * TGAS,
                                                                        int(costs["account"]))])
            if reg.status != "confirmed":
                return reg
        actions = []
        if not await self._registered(ctx, wrap):
            actions.append(near.function_call_action("storage_deposit", {}, 15 * TGAS, FT_STORAGE))
        actions.append(near.function_call_action("near_deposit", {}, 15 * TGAS, amount))
        actions.append(near.function_call_action("ft_transfer_call", {
            "receiver_id": mc, "amount": str(amount), "msg": json.dumps({"Deposit": {"meme_id": meme_id}}, separators=(",", ":"))},
            100 * TGAS, 1))
        return await self._send(ctx, wrap, actions)

    async def meme(self, ctx: PadContext, network: str, meme_id: int) -> dict:
        m = await self._view(ctx, MEME_COOKING[network], "get_meme", {"meme_id": meme_id})
        if not isinstance(m, dict):
            raise AdapterError("Meme.Cooking has no such meme")
        return m

    async def my_deposit(self, ctx: PadContext, network: str, meme_id: int) -> int:
        a = await self._view(ctx, MEME_COOKING[network], "get_account", {"account_id": ctx.address}) or {}
        return sum(int(v) for mid, v in (a.get("deposits") or []) if int(mid) == int(meme_id))

    async def plan_trade(self, ctx: PadContext, network: str, token: str, side: str, amount: int,
                         slippage_pct: float, meme_id: int | None = None) -> PadPlan:
        """buy = deposit `amount` yoctoNEAR; sell = withdraw `amount` yocto of this wallet's deposit (2% fee)."""
        from . import near

        if meme_id is None:
            raise AdapterError("this launch has no Meme.Cooking id")
        m = await self.meme(ctx, network, meme_id)
        ends = int(m.get("end_timestamp_ms") or 0)
        if time.time() * 1000 >= ends:
            raise AdapterError("the auction has ended: claim your tokens (or refund) instead")
        warnings = []
        if side == "buy":
            avail = near.available_balance(await ctx.near.account(ctx.address))
            if avail < amount + FT_STORAGE + 2 * 10**22:
                warnings.append(f"The wallet has {avail / YOCTO:g} NEAR available; this deposit needs about "
                                f"{(amount + FT_STORAGE) / YOCTO:g} NEAR plus gas.")

            async def run():
                return await self._deposit(ctx, network, meme_id, amount)

            return PadPlan({"side": "deposit", "meme_id": meme_id, "amount_near": amount / YOCTO},
                           {"auction_ends_ms": ends}, warnings, [PadStep("deposit", run)])
        mine = await self.my_deposit(ctx, network, meme_id)
        if amount > mine:
            warnings.append(f"This wallet has {mine / YOCTO:g} NEAR deposited in this auction.")
        mc, wrap = MEME_COOKING[network], WRAP_NEAR[network]

        async def withdraw():
            return await self._send(ctx, mc, [near.function_call_action(
                "withdraw", {"meme_id": meme_id, "amount": str(amount)}, 100 * TGAS, 1)])

        async def unwrap():
            bal = min(int(await self._view(ctx, wrap, "ft_balance_of", {"account_id": ctx.address}) or 0),
                      amount * 98 // 100)  # only what this withdrawal returned (less the 2% fee)
            if not bal:
                return StepResult(None, "confirmed", None)
            return await self._send(ctx, wrap, [near.function_call_action(
                "near_withdraw", {"amount": str(bal)}, 20 * TGAS, 1)])

        return PadPlan({"side": "withdraw", "meme_id": meme_id, "amount_near": amount / YOCTO,
                        "withdrawal_fee": "2% (paid to the depositors who stay)"},
                       {"auction_ends_ms": ends, "you_receive_about_near": amount * 98 // 100 / YOCTO}, warnings,
                       [PadStep("withdraw", withdraw), PadStep("unwrap", unwrap, essential=False)])

    async def plan_claim(self, ctx: PadContext, network: str, token: str, meme_id: int) -> PadPlan:
        from . import near

        m = await self.meme(ctx, network, meme_id)
        if time.time() * 1000 < int(m.get("end_timestamp_ms") or 0):
            raise AdapterError("the auction is still running")
        launched = await ctx.near.account(token) is not None
        mine = await self.my_deposit(ctx, network, meme_id)
        steps = []
        if launched and await self._view(ctx, token, "storage_balance_of", {"account_id": ctx.address}) is None:
            async def register():
                return await self._send(ctx, token, [near.function_call_action("storage_deposit", {}, 30 * TGAS,
                                                                               FT_STORAGE)])

            steps.append(PadStep("register", register))

        async def claim():
            return await self._send(ctx, MEME_COOKING[network], [near.function_call_action(
                "claim", {"meme_id": meme_id}, 50 * TGAS, 1)])

        steps.append(PadStep("claim", claim))
        warnings = [] if mine else ["This wallet has nothing to claim in this auction."]
        return PadPlan({"action": "claim tokens" if launched else "claim refund (the soft cap wasn't reached)",
                        "meme_id": meme_id, "deposit_near": mine / YOCTO}, {}, warnings, steps)


# --- Tolly (Arc) ------------------------------------------------------------------------------------------

TOLLY = {"arc": {"pad": "0xcad7ee36ac193bf2eddb7b3e2736c5bdb8269c8b",
                 "router": "0x53BF6B0684Ec7eF91e1387Da3D1a1769bC5A6F77",
                 "usdc": "0x3600000000000000000000000000000000000000"}}
TOLLY_FEE = 10_000  # the 1% Uniswap V3 fee tier every Tolly pool uses
TOLLY_CREATED = "0x" + keccak(text="TokenCreated(address,address,string,string,address,string,string,string,string)").hex()
USDC_SCALE = 10**12  # Arc's native USDC has 18 decimals; its ERC-20 view (which pools use) has 6
MAX_META = 512
DEV_BUY_ANTI_SNIPE_USDC = 150  # ~3% of supply at the ~$5,000 opening valuation: the anti-snipe cap


class Tolly:
    id = "tolly"
    network = "arc"
    kind = "extra"
    quote_assets = ["native"]
    unit = "USDC"

    def options_schema(self) -> dict:
        return {"requires_image": True, "fields": [
            {"key": "dev_buy_sol", "type": "number", "min": 0, "label": "Dev buy (USDC)", "default": 0}],
            "notes": ["Tolly has no bonding curve: the whole supply goes into a Uniswap V3 token/USDC pool with "
                      "locked liquidity, opening at about a $5,000 valuation.",
                      "For the first 5 minutes no wallet may hold more than 3% of supply, so keep the dev buy "
                      "under about 150 USDC."]}

    async def resolve(self, ctx: PadContext, network: str, tx: str, symbol: str) -> dict:
        try:
            r = await ctx.evm.rpc.receipt(tx)
        except Exception:
            return {}
        if not r:
            return {}
        if str(r.get("status")) in ("0x0", "0"):
            return {"failed": True}
        token = None
        for lg in r.get("logs") or []:
            topics = lg.get("topics") or []
            if (lg.get("address") or "").lower() == TOLLY[network]["pad"] and topics and topics[0].lower() == TOLLY_CREATED:
                token = to_checksum_address("0x" + topics[1][-40:])
        return {"token": token} if token else {}

    async def _send(self, ctx: PadContext, to: str, data: bytes) -> StepResult:
        from ..services.evm_exec import EvmCall

        r = await ctx.evm.send(ctx.address, ctx.signer_ref, EvmCall(to, data))
        return StepResult(r.signature, r.status, r.error)

    def _approve_step(self, ctx: PadContext, token: str, spender: str, amount: int) -> PadStep:
        async def run():
            have = await ctx.evm.rpc.erc20_allowance(token, ctx.address, spender)
            if have >= amount:
                return StepResult(None, "confirmed", None)
            return await self._send(ctx, token, _sel("approve(address,uint256)") +
                                    abi_encode(["address", "uint256"], [to_checksum_address(spender), amount]))

        return PadStep("approve", run)

    async def plan_create(self, ctx: PadContext, network: str, name: str, symbol: str, dev_buy: int,
                          image_url: str | None = None, socials: dict | None = None, **_) -> PadPlan:
        """dev_buy in native USDC wei (18 decimals); sent to Tolly as 6-decimal USDC."""
        t = TOLLY[network]
        if not image_url:
            raise AdapterError("Tolly needs a token image URL: set PUBLIC_API_URL so Helm can serve the image")
        s = socials or {}
        meta = (image_url, s.get("website") or "", s.get("twitter") or "", s.get("telegram") or "")
        if any(len(m.encode()) > MAX_META for m in meta):
            raise AdapterError("a link is longer than Tolly's 512-byte limit")
        quote_in = dev_buy // USDC_SCALE
        salt = os.urandom(32)
        data = _sel("createToken(string,string,(string,string,string,string),bytes32,uint256)") + abi_encode(
            ["string", "string", "(string,string,string,string)", "bytes32", "uint256"],
            [name, symbol, meta, salt, quote_in])
        warnings = []
        bal = await ctx.evm.rpc.get_balance(ctx.address)
        gas = await ctx.evm.max_gas_cost(6_000_000)
        if bal < dev_buy + gas:
            warnings.append(f"The dev wallet holds {bal / 10**18:g} USDC; the launch needs about "
                            f"{(dev_buy + gas) / 10**18:g} USDC (dev buy and gas).")
        if quote_in > DEV_BUY_ANTI_SNIPE_USDC * 10**6:
            warnings.append("A dev buy above about 150 USDC exceeds Tolly's 3% anti-snipe limit and would revert.")
        steps = []
        if quote_in:
            steps.append(self._approve_step(ctx, t["usdc"], t["pad"], quote_in))
        state: dict = {}

        async def create():
            return await self._send(ctx, t["pad"], data)

        steps.append(PadStep("launch", create))

        async def finish(results: list[StepResult]) -> dict:
            launch = results[-1] if results else None
            if not launch or launch.status != "confirmed" or not launch.signature:
                return {}
            return await self.resolve(ctx, network, launch.signature, symbol)

        return PadPlan({"launchpad": "tolly", "contract": t["pad"], "dev_buy_usdc": quote_in / 10**6,
                        "pool": "Uniswap V3 token/USDC, 1% fee, liquidity locked", "supply": "1,000,000,000"},
                       {"max_gas_usdc": gas / 10**18}, warnings, steps, finish)

    async def plan_trade(self, ctx: PadContext, network: str, token: str, side: str, amount: int,
                         slippage_pct: float) -> PadPlan:
        """amount: native USDC wei to spend (buy) or raw tokens to sell."""
        t = TOLLY[network]
        token = to_checksum_address(token)
        if side == "buy":
            amount_in = amount // USDC_SCALE
            tin, tout = t["usdc"], token
        else:
            amount_in = amount
            tin, tout = token, t["usdc"]
        if amount_in <= 0:
            raise AdapterError("the amount is too small")
        sig = "exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))"

        def call(min_out: int) -> bytes:
            return _sel(sig) + abi_encode(["(address,address,uint24,address,uint256,uint256,uint160)"],
                                          [(to_checksum_address(tin), to_checksum_address(tout), TOLLY_FEE,
                                            to_checksum_address(ctx.address), amount_in, min_out, 0)])

        warnings = []
        bal = await ctx.evm.rpc.get_balance(ctx.address)
        gas = await ctx.evm.max_gas_cost(400_000)
        if side == "buy" and bal < amount + gas:
            warnings.append(f"The wallet holds {bal / 10**18:g} USDC; this buy needs about {(amount + gas) / 10**18:g}.")
        if side == "sell":
            held = await ctx.evm.rpc.erc20_balance(token, ctx.address)
            if held < amount_in:
                warnings.append("The wallet holds fewer tokens than this sell.")
            if bal < gas:
                warnings.append("The wallet needs a little USDC for gas.")

        # The quote the user confirms: a swap simulated now (it can't be before an approval exists, so a first buy
        # or sell has no confirmed floor and relies on the send-time simulation alone).
        try:
            confirmed_min = _slip_min(int(await ctx.evm.rpc.eth_call(t["router"], "0x" + call(0).hex(), ctx.address), 16),
                                      slippage_pct)
        except Exception:
            confirmed_min = 0

        async def swap():
            # Simulated again after the approval has landed; the stricter minimum wins, so a price move after
            # confirming makes the swap revert rather than fill worse than reviewed.
            try:
                ret = await ctx.evm.rpc.eth_call(t["router"], "0x" + call(0).hex(), ctx.address)
                out = int(ret, 16)
            except Exception as e:
                return StepResult(None, "failed", f"the swap would fail right now: {str(e)[:160]}")
            return await self._send(ctx, t["router"], call(max(confirmed_min, _slip_min(out, slippage_pct))))

        return PadPlan({"side": side, "router": t["router"], "pool_fee": "1%",
                        ("spend_usdc" if side == "buy" else "tokens_raw"): (amount_in / 10**6 if side == "buy"
                                                                            else str(amount_in)),
                        "slippage_pct": slippage_pct},
                       {"max_gas_usdc": 2 * gas / 10**18}, warnings,
                       [self._approve_step(ctx, tin, t["router"], amount_in), PadStep("swap", swap)])


EXTRA_PADS = {"sunpump": SunPump, "meme-cooking": MemeCooking, "tolly": Tolly}
