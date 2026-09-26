"""Four.meme adapter (BNB Chain). Follows Four.meme's own integration kit (@four-meme/four-meme-ai).

Launch needs Four.meme's API before the contract call:
  1. POST /private/user/nonce/generate, then sign "You are sign in Meme {nonce}" (EIP-191) with the dev wallet
     and POST /private/user/login/dex -> access token. Helm signs this sign-in message through its signer; it
     authorises nothing on-chain.
  2. POST /private/token/upload (image) -> imgUrl.
  3. POST /private/token/create with the published raisedToken preset from /public/config -> createArg, signature.
  4. TokenManager2.createToken(createArg, signature) with msg.value = _launchFee() + dev buy + the trading fee on
     the dev buy (_tradingFeeRate, in bps). The token address comes from the TokenCreate event.

Trading (V2 tokens paired with BNB, before they list on PancakeSwap): quotes from TokenManagerHelper3
(tryBuy / trySell), buys with buyTokenAMAP(token, funds, minAmount), sells with an approve then
sellToken(origin, token, amount, minFunds). Amounts must be multiples of 1 gwei. Once a token has liquidity on
PancakeSwap it trades through the DEX route.
"""

import time

import httpx
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak, to_checksum_address

from .base import AdapterError, ClaimRequest, CreateRequest, EvmPlan, EvmStep, EvmTradeRequest
from .evm import EvmRPC, EvmRPCError
from .kyber import Graduated

API = "https://four.meme/meme-api/v1"
TM2 = "0x5c952063c7fc8610FFDB798152D69F0B9550762b"
HELPER3 = "0xF251F83e40a78868FcfA3FA4599Dad6494E46034"
ZERO = "0x0000000000000000000000000000000000000000"
GWEI = 10**9
LABELS = ["Meme", "AI", "Defi", "Games", "Infra", "De-Sci", "Social", "Depin", "Charity", "Others"]
TOKEN_CREATE_TOPIC = "0x" + keccak(text="TokenCreate(address,address,uint256,string,string,uint256,uint256,uint256)").hex()
TOKEN_INFO_T = ["uint256", "address", "address", "uint256", "uint256", "uint256", "uint256", "uint256", "uint256",
                "uint256", "uint256", "bool"]
# Revert strings Four.meme documents, translated
KNOWN_ERRORS = {"GW": "amount isn't a whole number of gwei", "SO": "order is too small for Four.meme",
                "Slippage": "price moved beyond your slippage", "More BNB": "not enough BNB sent",
                "A": "this is an X Mode token, which Helm can't trade yet"}


def selector(sig: str) -> bytes:
    return keccak(text=sig)[:4]


def explain(e: EvmRPCError) -> str:
    text = str(e)
    for code, msg in KNOWN_ERRORS.items():
        if text.rstrip().endswith(code) or f"reverted: {code}" in text:
            return f"{msg} ({code})"
    return text


def _hex(v: str) -> bytes:
    """Four.meme returns hex, sometimes without 0x, sometimes base64."""
    import base64
    import re

    if v.startswith("0x"):
        return bytes.fromhex(v[2:])
    if re.fullmatch(r"[0-9a-fA-F]+", v) and len(v) % 2 == 0:
        return bytes.fromhex(v)
    return base64.b64decode(v)


def _ether(wei: int) -> str:
    """Exact decimal BNB string for an amount in wei (no float rounding): 10**17 -> "0.1"."""
    from decimal import Decimal

    text = format(Decimal(wei) / Decimal(10**18), "f")
    return (text.rstrip("0").rstrip(".") if "." in text else text) or "0"


class FourMemeAdapter:
    id = "four-meme"
    network = "bsc"
    kind = "evm"
    quote_assets = ["native"]

    def __init__(self, rpc: EvmRPC, api_url: str = API, client: httpx.AsyncClient | None = None) -> None:
        self.rpc = rpc
        self.api = api_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=30)
        self._tokens: dict[str, tuple[str, float]] = {}  # dev address -> (access token, expiry)

    def options_schema(self) -> dict:
        return {
            "fields": [
                {"key": "dev_buy_sol", "type": "number", "min": 0, "label": "Dev buy (BNB)", "default": 0},
                {"key": "label", "type": "select", "options": LABELS, "default": "Meme", "label": "Category",
                 "hint": "Four.meme's category for the token."},
                {"key": "slippage_pct", "type": "number", "min": 0, "max": 50, "default": 10},
            ],
            "socials": ["twitter", "telegram", "website"],
            "requires_image": True,
        }

    def deploy_notes(self, params: dict) -> dict:
        return {"sign_in": "Helm signs Four.meme's sign-in message with your dev wallet (it can't move funds)",
                "graduates_to": "PancakeSwap when Four.meme's raise completes"}

    async def _read(self, to: str, sig: str, types: list[str] | None = None, args: list | None = None) -> str:
        data = selector(sig) + (abi_encode(types, args) if types else b"")
        return await self.rpc.call("eth_call", [{"to": to, "data": "0x" + data.hex()}, "latest"])

    async def launch_fee(self) -> int:
        return int(await self._read(TM2, "_launchFee()"), 16)

    # --- API ----------------------------------------------------------------------------------

    async def _api(self, method: str, path: str, token: str | None = None, **kw) -> object:
        headers = {"meme-web-access": token} if token else {}
        try:
            r = await self._client.request(method, f"{self.api}{path}", headers=headers, **kw)
        except httpx.HTTPError as e:
            raise AdapterError(f"Four.meme API unreachable: {type(e).__name__}")
        try:
            data = r.json()
        except ValueError:
            raise AdapterError(f"Four.meme API {r.status_code}: {r.text[:200]}")
        if str(data.get("code")) != "0":
            raise AdapterError(f"Four.meme API: {data.get('msg') or data.get('message') or data}")
        return data.get("data")

    async def _login(self, req: CreateRequest) -> str:
        cached = self._tokens.get(req.dev_pubkey.lower())
        if cached and cached[1] > time.time():
            return cached[0]
        if not req.sign_text:
            raise AdapterError("Four.meme needs a signed sign-in from the dev wallet, and no signer was provided")
        nonce = await self._api("POST", "/private/user/nonce/generate", json={
            "accountAddress": req.dev_pubkey, "verifyType": "LOGIN", "networkCode": "BSC"})
        signature = await req.sign_text(f"You are sign in Meme {nonce}")
        token = await self._api("POST", "/private/user/login/dex", json={
            "region": "WEB", "langType": "EN", "loginIp": "", "inviteCode": "", "walletName": "MetaMask",
            "verifyInfo": {"address": req.dev_pubkey, "networkCode": "BSC", "signature": signature,
                           "verifyType": "LOGIN"}})
        self._tokens[req.dev_pubkey.lower()] = (token, time.time() + 20 * 60)
        return token

    async def _raised_token(self) -> dict:
        presets = await self._api("GET", "/public/config")
        if not isinstance(presets, list) or not presets:
            raise AdapterError("Four.meme returned no raise presets")
        published = [p for p in presets if p.get("status") == "PUBLISH"] or presets
        bnb = next((p for p in published if p.get("symbol") == "BNB"), None)
        if not bnb:
            raise AdapterError("Four.meme's BNB raise preset isn't published right now")
        return bnb

    # --- launch -------------------------------------------------------------------------------

    async def prepare_create(self, req: CreateRequest) -> EvmPlan:
        if not req.image_bytes:
            raise AdapterError("Four.meme needs a token image. Add one to the launch and try again.")
        label = (req.options or {}).get("label") or "Meme"
        if label not in LABELS:
            raise AdapterError(f"Four.meme category must be one of {', '.join(LABELS)}")
        dev_buy = int(round(req.dev_buy_sol * 10**18)) // GWEI * GWEI
        token = await self._login(req)
        img = await self._api("POST", "/private/token/upload", token,
                              files={"file": ("logo.png", req.image_bytes, "image/png")})
        raised = await self._raised_token()
        s = req.socials or {}
        body = {
            "name": req.name, "shortName": req.symbol, "desc": req.description or req.name,
            "totalSupply": int(float(raised.get("totalAmount", 1_000_000_000))),
            "raisedAmount": float(raised.get("totalBAmount", 24)), "saleRate": float(raised.get("saleRate", 0.8)),
            "reserveRate": 0, "imgUrl": img, "raisedToken": raised, "launchTime": int(time.time() * 1000),
            "funGroup": False, "label": label, "lpTradingFee": 0.0025, "preSale": _ether(dev_buy),
            "clickFun": False, "symbol": raised["symbol"], "dexType": "PANCAKE_SWAP", "rushMode": False,
            "onlyMPC": False, "feePlan": False,
            **({"webUrl": s["website"]} if s.get("website") else {}),
            **({"twitterUrl": s["twitter"]} if s.get("twitter") else {}),
            **({"telegramUrl": s["telegram"]} if s.get("telegram") else {}),
        }
        created = await self._api("POST", "/private/token/create", token, json=body)
        args, sig = _hex(created["createArg"]), _hex(created["signature"])
        try:
            fee = await self.launch_fee()
            rate = int(await self._read(TM2, "_tradingFeeRate()"), 16) if dev_buy else 0
        except EvmRPCError as e:
            raise AdapterError(f"couldn't read Four.meme's fees: {e}")
        value = fee + dev_buy + dev_buy * rate // 10_000
        data = selector("createToken(bytes,bytes)") + abi_encode(["bytes", "bytes"], [args, sig])
        try:
            await self.rpc.call("eth_call", [{"from": req.dev_pubkey, "to": TM2, "value": hex(value),
                                              "data": "0x" + data.hex()}, "latest"])
        except EvmRPCError as e:
            raise AdapterError(f"Four.meme launch dry run failed: {explain(e)}")

        async def resolve(logs: list[dict]) -> str | None:
            for log in logs:
                topics = log.get("topics") or []
                if log.get("address", "").lower() == TM2.lower() and topics and topics[0].lower() == TOKEN_CREATE_TOPIC:
                    raw = bytes.fromhex(log["data"].removeprefix("0x"))
                    return to_checksum_address(abi_decode(["address", "address"], raw[:64])[1])
            return None

        return EvmPlan([EvmStep("launch", TM2, data, value)], token=None, resolve_token=resolve,
                       notes={"launch_fee_wei": fee, **self.deploy_notes({})})

    # --- trading ------------------------------------------------------------------------------

    async def _info(self, token: str) -> dict:
        try:
            ret = await self._read(HELPER3, "getTokenInfo(address)", ["address"], [token])
        except EvmRPCError as e:
            raise AdapterError(f"couldn't read this Four.meme token: {explain(e)}")
        v = abi_decode(TOKEN_INFO_T, bytes.fromhex(ret[2:]))
        return {"version": v[0], "manager": to_checksum_address(v[1]), "quote": v[2].lower(),
                "liquidity_added": v[11]}

    async def prepare_trade(self, req: EvmTradeRequest) -> EvmPlan:
        info = await self._info(req.token)
        if info["liquidity_added"]:
            raise Graduated("this token has listed on PancakeSwap")
        if info["version"] != 2:
            raise AdapterError("Helm only trades Four.meme V2 tokens on the curve")
        if info["quote"] != ZERO:
            raise AdapterError("Helm only trades BNB-paired Four.meme tokens for now")
        manager = info["manager"]
        slip = max(0.0, min(req.slippage_pct, 50.0)) / 100
        amount = req.amount_wei // GWEI * GWEI
        if amount <= 0:
            raise AdapterError("amount is too small")

        if req.side == "buy":
            try:
                ret = await self._read(HELPER3, "tryBuy(address,uint256,uint256)", ["address", "uint256", "uint256"],
                                       [req.token, 0, amount])
            except EvmRPCError as e:
                raise AdapterError(f"Four.meme buy quote failed: {explain(e)}")
            q = abi_decode(["address", "address", "uint256", "uint256", "uint256", "uint256", "uint256", "uint256"],
                           bytes.fromhex(ret[2:]))
            est, msg_value, funds = q[2], q[5], q[7] or amount
            data = selector("buyTokenAMAP(address,uint256,uint256)") + abi_encode(
                ["address", "uint256", "uint256"], [req.token, funds, int(est * (1 - slip))])
            return EvmPlan([EvmStep("buy", manager, data, msg_value)], token=req.token,
                           notes={"expected_tokens_raw": est})

        try:
            ret = await self._read(HELPER3, "trySell(address,uint256)", ["address", "uint256"], [req.token, amount])
        except EvmRPCError as e:
            raise AdapterError(f"Four.meme sell quote failed: {explain(e)}")
        funds = abi_decode(["address", "address", "uint256", "uint256"], bytes.fromhex(ret[2:]))[2]
        steps = []
        if await self.rpc.erc20_allowance(req.token, req.wallet, manager) < amount:
            steps.append(EvmStep("approve", req.token, selector("approve(address,uint256)")
                                 + abi_encode(["address", "uint256"], [manager, amount])))
        steps.append(EvmStep("sell", manager, selector("sellToken(uint256,address,uint256,uint256)") + abi_encode(
            ["uint256", "address", "uint256", "uint256"], [0, req.token, amount, int(funds * (1 - slip))])))
        return EvmPlan(steps, token=req.token, notes={"expected_native_wei": funds})

    async def prepare_claim(self, req: ClaimRequest) -> EvmPlan:
        raise AdapterError("Four.meme has no creator fees to claim on standard tokens.")
