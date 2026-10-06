"""Launchpads on Arc: Tolly.

Each adapter turns a launch or a trade into a plan: facts to preview (the hashed summary, plus volatile fee
estimates) and the steps to run, in order, with the wallet's own key through Helm's signer. Every step re-reads what
it needs at send time and is checked before signing.

Tolly (Arc). No bonding curve: createToken mints the whole supply single-sided into a Uniswap V3 token/USDC pool
  (1% fee tier) with the LP locked; trading is Uniswap V3 swaps through Arc's SwapRouter02. Verified source:
  github.com/TollyLabs/v3-contracts (bytecode-matched deployment for chain 5042).
"""

import os
from dataclasses import dataclass, field
from typing import Awaitable, Callable

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
    evm: object = None  # an EvmSender for Arc


def _sel(sig: str) -> bytes:
    return keccak(text=sig)[:4]


def _slip_min(amount: int, slippage_pct: float) -> int:
    return amount * max(0, 10_000 - int(round(slippage_pct * 100))) // 10_000


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


EXTRA_PADS = {"tolly": Tolly}
