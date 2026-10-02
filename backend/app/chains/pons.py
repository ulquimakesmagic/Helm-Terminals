"""Pons adapter (Robinhood Chain), talking to the Pons V2 contracts directly.

Launch:
  - With a dev buy: LaunchAndBuyRouter.launchAndBuy, msg.value = launchFee + dev buy. The router adds the dev's
    own wallet to the launch's snipe-tax exemptions, so the dev buy isn't hit by the 99% anti-snipe tax. Helm
    passes no other exemptions.
  - Without: Factory.launchToken, msg.value = launchFee exactly.
  expectedEconomics comes from Factory.previewLaunchEconomics and pins the launch terms. The token's logo is a URL
  stored on-chain; Helm serves the launch image from its own API for that.
  The new token's address is read from the TokenLaunched event in the receipt.

Trading happens on each token's own bonding-curve contract (found with Factory.getLaunchedToken):
buy(amountIn, minTokensOut, recipient) with ETH as msg.value; sell(tokensIn, minQuoteOut, recipient) after
approving the curve for exactly tokensIn. Minimum outputs come from simulating the same call with eth_call.
After graduation the token trades on a Uniswap V4 pool, which Helm doesn't route yet.

Creator fees accrue in ETH in Pons' fee escrow. Legacy launches use the dev wallet; enabled new launches
use an immutable per-launch reward splitter with a fixed 94/6 split. Both use the recipient's claim().

Sources: ponsfamily.com; the pons-mcp client's docs/PROTOCOL.md; verified ABIs on Sourcify (chain 4663).
"""

import os

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak, to_checksum_address

from .base import AdapterError, ClaimRequest, CreateRequest, EvmPlan, EvmStep, EvmTradeRequest
from .evm import EvmRPC, EvmRPCError
from .kyber import Graduated

ZERO = "0x0000000000000000000000000000000000000000"
FACTORY = "0x7eD598BcEf8bd9Edd8C97A195C6d13f40801EC7e"
ROUTER = "0xe33E9E479dF8802cb0866d5d05258bEc4cF62948"
FEE_ESCROW = "0xd3afeb2a57f70ef218aa82451c51b2fb0416ac9e"
CONFIG_ID = 0  # Pons' default launch config (1B supply, 1% curve fee, graduates at 4.2 ETH)
CREATOR_TAX_PCT = (0, 1, 2, 3, 5, 10)  # Pons allows up to 10%
MAX_LOGO_BYTES = 512

PARAMS_T = "(string,string,string,string,(string,string,string,string,string),address,uint16,bool,bytes32,bytes32)"
LAUNCH_TOKEN = f"launchToken({PARAMS_T},uint256,address)"
LAUNCH_AND_BUY = f"launchAndBuy({PARAMS_T},uint256,address,uint256,uint256,address,address[])"
LAUNCHED_TOKEN_T = "(address,address,address,address,address,uint256,uint24,int24,uint16,bool,uint8,uint256,uint256,uint256,bool)"
TOPIC_TOKEN_LAUNCHED = "0x" + keccak(text="TokenLaunched(address,address,address,address,uint256,uint256)").hex()

KNOWN_ERRORS = {sel: msg for sel, msg in (
    ("LaunchFeeNotPaid()", "Pons rejected the launch fee amount."),
    ("CreatorTaxTooHigh()", "Pons rejected the creator tax: it must be 10% or less."),
    ("InvalidLaunchConfigId()", "Pons' default launch config isn't available right now."),
    ("NotWhitelisted()", "Pons only allows whitelisted wallets or pair tokens for this."),
)}
KNOWN_ERRORS = {"0x" + keccak(text=k)[:4].hex(): v for k, v in KNOWN_ERRORS.items()}


def selector(sig: str) -> bytes:
    return keccak(text=sig)[:4]


def explain(e: EvmRPCError) -> str:
    return KNOWN_ERRORS.get((e.data or "")[:10].lower(), str(e))


def _word(ret: str | None) -> int:
    return int(ret, 16) if ret and ret != "0x" else 0


class PonsAdapter:
    id = "pons"
    network = "robinhood"
    kind = "evm"
    quote_assets = ["native"]

    def __init__(self, rpc: EvmRPC) -> None:
        self.rpc = rpc

    def options_schema(self) -> dict:
        return {
            "fields": [
                {"key": "dev_buy_sol", "type": "number", "min": 0, "label": "Dev buy (ETH)", "default": 0},
                {"key": "creator_tax_pct", "type": "select", "options": list(CREATOR_TAX_PCT), "default": 0,
                 "label": "Creator tax (%)",
                 "hint": "Added to Pons' curve fee. The launch preview shows the recipient and any Helm reward split. 0 for no extra tax."},
                {"key": "slippage_pct", "type": "number", "min": 0, "max": 50, "default": 10},
            ],
            "socials": ["twitter", "telegram", "website"],
            "requires_image": False,
        }

    def deploy_notes(self, params: dict) -> dict:
        ct = int(params.get("creator_tax_pct") or 0)
        split = (params.get("helm_creator_share") or {}).get("bps") == 600
        return {"creator_tax": (f"{ct}% on trades; included in the 94/6 creator-reward split" if split else
                                f"{ct}% on trades, paid to your dev wallet") if ct else "none",
                "graduates_to": "a Uniswap V4 pool at 4.2 ETH raised"}

    async def launch_fee(self) -> int:
        """Pons' flat launch fee (currently 0.0005 ETH), paid on top of any dev buy."""
        return _word(await self._call(FACTORY, selector("launchFee()")))

    async def _call(self, to: str, data: bytes, sender: str | None = None, value: int = 0) -> str:
        tx = {"to": to, "data": "0x" + data.hex(), **({"from": sender} if sender else {}),
              **({"value": hex(value)} if value else {})}
        return await self.rpc.call("eth_call", [tx, "latest"])

    # --- launch -------------------------------------------------------------------------------

    async def prepare_create(self, req: CreateRequest) -> EvmPlan:
        opts = req.options or {}
        rewards = opts.get("pons_rewards")
        recipient = req.dev_pubkey
        if rewards:
            from .pons_rewards import verify
            await verify(self.rpc, rewards)
            share = opts.get("helm_creator_share") or {}
            if (rewards["creator"].lower() != req.dev_pubkey.lower() or share.get("bps") != 600
                    or share.get("recipient", "").lower() != rewards["treasury"].lower()):
                raise AdapterError("Pons reward recipients do not match the approved launch terms")
            recipient = rewards["splitter"]
        elif (opts.get("helm_creator_share") or {}).get("bps"):
            raise AdapterError("Missing Pons per-launch reward recipient")
        tax_pct = int(opts.get("creator_tax_pct") or 0)
        if tax_pct not in CREATOR_TAX_PCT:
            raise AdapterError(f"Pons creator tax must be one of {', '.join(map(str, CREATOR_TAX_PCT))}%")
        logo = opts.get("logo_url") or ""
        if len(logo.encode()) > MAX_LOGO_BYTES:
            logo = ""
        try:
            fee = _word(await self._call(FACTORY, selector("launchFee()")))
            econ = await self._call(FACTORY, selector("previewLaunchEconomics(uint256,address)")
                                    + abi_encode(["uint256", "address"], [CONFIG_ID, ZERO]))
        except EvmRPCError as e:
            raise AdapterError(f"couldn't read Pons launch terms: {explain(e)}")
        economics = bytes.fromhex(econ[2:66]) if econ and len(econ) >= 66 else b"\x00" * 32
        if economics == b"\x00" * 32:
            raise AdapterError("Pons returned no launch terms for its default config")
        s = req.socials or {}
        salt = keccak(f"{req.name} {req.symbol} {os.urandom(8).hex()}".encode())
        params = (req.name, req.symbol, logo, (req.description or "")[:500],
                  (s.get("twitter") or "", s.get("telegram") or "", "", s.get("website") or "", ""),
                  recipient, tax_pct * 100, False, economics, salt)
        dev_buy = int(round(req.dev_buy_sol * 10**18))
        notes = {"launch_fee_wei": fee, "creator_tax": f"{tax_pct}% to your dev wallet" if tax_pct else "none",
                 "graduates_to": "a Uniswap V4 pool at 4.2 ETH raised"}
        if rewards:
            notes.update(pons_rewards=rewards, creator_tax=f"{tax_pct}%; creator rewards split 94% creator / 6% Helm")

        if dev_buy <= 0:
            data = selector(LAUNCH_TOKEN) + abi_encode([PARAMS_T, "uint256", "address"], [params, CONFIG_ID, ZERO])
            try:
                ret = await self._call(FACTORY, data, req.dev_pubkey, fee)
            except EvmRPCError as e:
                raise AdapterError(f"Pons launch dry run failed: {explain(e)}")
            candidates = [to_checksum_address(a) for a in abi_decode(["address", "address"], bytes.fromhex(ret[2:]))]
            return EvmPlan([EvmStep("launch", FACTORY, data, fee)], token=None, notes=notes,
                           resolve_token=self._resolver(candidates))

        # Simulate with no minimum to learn the expected tokens, then set the slippage floor from it.
        def encode(min_out: int) -> bytes:
            return selector(LAUNCH_AND_BUY) + abi_encode(
                [PARAMS_T, "uint256", "address", "uint256", "uint256", "address", "address[]"],
                [params, CONFIG_ID, ZERO, dev_buy, min_out, req.dev_pubkey, []])
        try:
            ret = await self._call(ROUTER, encode(0), req.dev_pubkey, fee + dev_buy)
        except EvmRPCError as e:
            raise AdapterError(f"Pons launch dry run failed: {explain(e)}")
        token, curve, tokens_out = abi_decode(["address", "address", "uint256"], bytes.fromhex(ret[2:]))
        min_out = int(tokens_out * (1 - max(0.0, min(req.slippage_pct, 50.0)) / 100))
        notes["dev_buy_tokens_expected"] = tokens_out
        return EvmPlan([EvmStep("launch", ROUTER, encode(min_out), fee + dev_buy)], token=to_checksum_address(token),
                       notes=notes, resolve_token=self._resolver([to_checksum_address(token)]))

    def _resolver(self, candidates: list[str]):
        async def resolve(logs: list[dict]) -> str | None:
            for log in logs:
                topics = log.get("topics") or []
                if (log.get("address", "").lower() == FACTORY.lower() and len(topics) >= 2
                        and topics[0].lower() == TOPIC_TOKEN_LAUNCHED):
                    return to_checksum_address("0x" + topics[1][-40:])
            for c in candidates:  # no log available: ask the factory which candidate is a Pons token
                try:
                    if (await self.launched(c))["exists"]:
                        return c
                except (AdapterError, EvmRPCError):
                    continue
            return None
        return resolve

    # --- token state --------------------------------------------------------------------------

    async def launched(self, token: str) -> dict:
        ret = await self._call(FACTORY, selector("getLaunchedToken(address)") + abi_encode(["address"], [token]))
        t = abi_decode([LAUNCHED_TOKEN_T], bytes.fromhex(ret[2:]))[0]
        return {"token": t[0], "curve": to_checksum_address(t[1]), "recipient": t[3], "pair": t[4], "creator_tax_bps": t[8],
                "phase": t[10], "exists": t[14]}

    async def _tradeable_curve(self, token: str) -> str:
        try:
            info = await self.launched(token)
        except EvmRPCError as e:
            raise AdapterError(f"couldn't read this Pons token: {explain(e)}")
        if not info["exists"]:
            raise AdapterError("this isn't a Pons V2 token")
        if info["pair"].lower() != ZERO:
            raise AdapterError("Helm only trades ETH-paired Pons tokens for now")
        curve = info["curve"]
        graduated = _word(await self._call(curve, selector("graduated()")))
        ready = _word(await self._call(curve, selector("readyToGraduate()")))
        if info["phase"] != 0 or graduated or ready:
            raise Graduated("this token has finished its Pons bonding curve and trades on Uniswap V4")
        return curve

    # --- trading ------------------------------------------------------------------------------

    async def prepare_trade(self, req: EvmTradeRequest) -> EvmPlan:
        if req.amount_wei <= 0:
            raise AdapterError("nothing to trade")
        curve = await self._tradeable_curve(req.token)
        slip = max(0.0, min(req.slippage_pct, 50.0)) / 100

        if req.side == "buy":
            snipe = _word(await self._call(curve, selector("currentSnipeTaxBps(address)")
                                           + abi_encode(["address"], [req.wallet])))
            if snipe:
                raise AdapterError(f"Pons' anti-snipe tax is {snipe / 100:g}% right now (it fades out in the first "
                                   "seconds after launch). Try again in a few seconds.")
            buy = lambda m: selector("buy(uint256,uint256,address)") + abi_encode(  # noqa: E731
                ["uint256", "uint256", "address"], [req.amount_wei, m, req.wallet])
            try:
                out = _word(await self._call(curve, buy(0), req.wallet, req.amount_wei))
            except EvmRPCError as e:
                raise AdapterError(f"Pons buy dry run failed: {explain(e)}")
            return EvmPlan([EvmStep("buy", curve, buy(int(out * (1 - slip))), req.amount_wei)], token=req.token,
                           notes={"expected_tokens_raw": out})

        sell = lambda m: selector("sell(uint256,uint256,address)") + abi_encode(  # noqa: E731
            ["uint256", "uint256", "address"], [req.amount_wei, m, req.wallet])

        async def build_sell() -> bytes:
            # Simulated only now, after the approve (if any) has landed.
            try:
                out = _word(await self._call(curve, sell(0), req.wallet))
            except EvmRPCError as e:
                raise AdapterError(f"Pons sell dry run failed: {explain(e)}")
            return sell(int(out * (1 - slip)))

        steps = []
        if await self.rpc.erc20_allowance(req.token, req.wallet, curve) < req.amount_wei:
            steps.append(EvmStep("approve", req.token, selector("approve(address,uint256)")
                                 + abi_encode(["address", "uint256"], [curve, req.amount_wei])))
        steps.append(EvmStep("sell", curve, b"", build=build_sell))
        return EvmPlan(steps, token=req.token)

    # --- creator fees -------------------------------------------------------------------------

    async def prepare_claim(self, req: ClaimRequest) -> EvmPlan:
        rewards = (req.options or {}).get("pons_rewards")
        if rewards:
            from .pons_rewards import distribution_data, verify
            await verify(self.rpc, rewards)
            pending = _word(await self._call(FEE_ESCROW, selector("balanceOf(address)")
                                             + abi_encode(["address"], [rewards["splitter"]])))
            if not pending and not await self.rpc.get_balance(rewards["splitter"]):
                raise AdapterError("no Pons rewards are waiting for this launch")
            data = distribution_data(rewards)
            # A successful dry run verifies counterfactual deployment, escrow claim and payout execution.
            try:
                await self._call(rewards["factory"], data, req.wallet_pubkey)
            except EvmRPCError as e:
                raise AdapterError(f"Pons reward claim simulation failed: {explain(e)}") from e
            return EvmPlan([EvmStep("claim", rewards["factory"], data)], notes={"pons_rewards": rewards})
        pending = _word(await self._call(FEE_ESCROW, selector("balanceOf(address)")
                                         + abi_encode(["address"], [req.wallet_pubkey])))
        if not pending:
            raise AdapterError("no Pons creator fees are waiting for this wallet")
        return EvmPlan([EvmStep("claim", FEE_ESCROW, selector("claim()"))], notes={"pending_wei": pending})
