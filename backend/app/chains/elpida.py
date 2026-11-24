"""Elpida V2.3 launch and router plans for Helm's managed EVM wallets."""
import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from eth_abi import decode, encode
from eth_abi.exceptions import DecodingError
from eth_utils import keccak, to_checksum_address

from .base import AdapterError, ClaimRequest, CreateRequest, EvmPlan, EvmStep, EvmTradeRequest
from .evm import EVM_CHAINS, EvmRPC, EvmRPCError, EvmRPCUnavailable
from .extra_pads import Pinata

ZERO = "0x" + "0" * 40
SOCIALS = ("website", "twitter", "telegram", "discord")
IMAGE_MAX = 5_000_000
CID = re.compile(r"[A-Za-z0-9]{9,121}")
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
LAUNCHED = "0x" + keccak(text="Launched(address,address,address,uint256,uint256,uint256)").hex()
METADATA_PUBLISHED = "0x" + keccak(text="MetadataPublished(address,string)").hex()
SWAPPED = "0x" + keccak(text="Swapped(address,address,address,bool,uint256,uint256)").hex()


@dataclass(frozen=True)
class Deployment:
    factory: str
    hook: str
    router: str | None
    start_block: int


@dataclass(frozen=True)
class Network:
    slug: str
    stable: str
    stable_symbol: str
    pool_manager: str
    current: Deployment
    previous: tuple[Deployment, ...] = ()


NETWORKS = {
    "base": Network(
        "base-mainnet", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "USDC",
        "0x498581fF718922c3f8e6A244956aF099B2652b2b",
        Deployment("0xF8E6aF46a5c602F74B90f5977D7587Ce827e1B70", "0x31bdfd511F7284B7B5c24A13ddE07D2e241D28cc",
                   "0x1908CF65F0650ccf0A211986442f6FF9ed342119", 61314709),
        (Deployment("0x29f0D2A8F2DD5B2dF872222f10bE35D6B27aFD71", "0xf53DddE8e792D8645f256ddE71Bb91e16dc7a8cC",
                    "0x63Cb3BCbDB6F00302EaD64Bd8D17A614FfBBD4B9", 59162718),),
    ),
    "bsc": Network(
        "bnb-mainnet", "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", "USDC",
        "0x28e2Ea090877bF75740558f6BFB36A5ffeE9e9dF",
        Deployment("0x30c8C1f345f68C3b48efEe450FA916F6CE6340a7", "0xC477cda5ca95D1Db781AdFa3bF6cE44D861128cC",
                   "0xDD7620485af435D6f276c99011D03309a04EC95B", 135325711),
        (Deployment("0x060742Fba64Ed98f159d9F99bdd0Cb268ECCa8B1", "0xe843acBa3505cE2842b09F32aa5c9147615DE8Cc",
                    "0xcA3743B66245410886Abb2eE6DCC5781F544aA51", 134752955),),
    ),
    "robinhood": Network(
        "robinhood-mainnet", "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168", "USDG",
        "0x8366a39CC670B4001A1121B8F6A443A643e40951",
        Deployment("0x29f0D2A8F2DD5B2dF872222f10bE35D6B27aFD71", "0x87e7C9477ffe0347d729c7D574964e72Ea76A8cc",
                   "0xC3C730EFade65F72B677E877d2ACcb8C5a697960", 121746960),
    ),
    "ethereum": Network(
        "ethereum-mainnet", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "USDC",
        "0x000000000004444c5dc75cB358380D2e3dE08A90",
        Deployment("0x29f0D2A8F2DD5B2dF872222f10bE35D6B27aFD71", "0x90962CD29484CC34434ACb1E8f396F7a064f68cC",
                   "0xC3C730EFade65F72B677E877d2ACcb8C5a697960", 26484623),
    ),
}

ABI = json.loads(Path(__file__).with_name("contracts").joinpath("ElpidaV23.json").read_text(encoding="utf-8"))
ERROR_MESSAGES = {
    "metadata required": "Elpida requires token artwork and IPFS metadata.",
    "IPFS URI required": "Elpida requires an IPFS metadata URI.",
    "metadata URI length": "Elpida's IPFS metadata URI must be 16–128 bytes.",
    "quote not approved": "Elpida has paused launches with this quote asset.",
    "launches paused": "Elpida has paused launches on this chain.",
    "daily launch cap": "Elpida's daily launch cap has been reached. Try again after midnight UTC.",
    "token address": "Elpida could not generate a valid token address. Try another wallet or wait for the next launch.",
    "name length": "Elpida names must be 1–64 UTF-8 bytes.",
    "ticker": "Elpida symbols must be 1–10 ASCII letters or digits.",
    "FeeOutOfRange": "Choose a trading fee between Elpida's current minimum and 4%.",
    "FirstBuyAboveCap": "Elpida caps the first buy at 3% of token supply. Lower your first buy.",
    "FeeQuoteMoved": "Elpida's launch fee moved above the reviewed limit. Refresh the preview.",
    "StaleOraclePrice": "Elpida's price feed is stale. Try again when it updates.",
    "OracleUnavailable": "Elpida's fee oracle is unavailable. Try again later.",
    "UsdcUnavailable": "Stablecoin fee payment is unavailable on this Elpida deployment.",
    "FeeTransferFailed": "Elpida could not collect the launch fee. Check the fee balance and allowance.",
    "FeeRefundFailed": "Elpida could not refund the extra native launch fee.",
    "Slippage": "The output fell below your minimum. Review a fresh quote.",
    "Expired": "The trade quote expired. Review a fresh quote.",
    "NotLaunched": "This token has no pool on the selected Elpida deployment.",
}
ERROR_SELECTORS = {
    keccak(text=f"{entry['name']}({','.join(i['type'] for i in entry['inputs'])})")[:4]: entry["name"]
    for contract in ABI.values() for entry in contract["abi"] if entry["type"] == "error"
}


def explain(error: EvmRPCError) -> str:
    data = error.data
    if isinstance(data, str) and data.startswith("0x"):
        try:
            raw = bytes.fromhex(data[2:])
            if raw[:4] == bytes.fromhex("08c379a0"):
                reason = decode(["string"], raw[4:])[0]
            else:
                reason = ERROR_SELECTORS.get(raw[:4], "")
            if reason in ERROR_MESSAGES:
                return ERROR_MESSAGES[reason]
        except (ValueError, DecodingError):
            pass
    message = str(error)
    return next((help_text for reason, help_text in ERROR_MESSAGES.items() if reason in message),
                "Elpida rejected the simulation. Check the available balance and current launch or pool conditions.")


def calldata(signature: str, types=(), args=()) -> bytes:
    return keccak(text=signature)[:4] + encode(types, args)


def https_link(value) -> str:
    if not isinstance(value, str) or len(value) > 2000 or any(c.isspace() or ord(c) < 32 for c in value):
        return ""
    try:
        url = urlsplit(value)
        if url.scheme == "https" and url.hostname and not url.username and not url.password:
            return value
    except ValueError:
        pass
    return ""


def validate_identity(name: str, symbol: str, description: str) -> None:
    if not 1 <= len(name.encode("utf-8")) <= 64:
        raise AdapterError(ERROR_MESSAGES["name length"])
    if not re.fullmatch(r"[A-Za-z0-9]{1,10}", symbol):
        raise AdapterError(ERROR_MESSAGES["ticker"])
    if len(description) > 1000:
        raise AdapterError("Elpida descriptions can be at most 1,000 characters.")


def artwork_extension(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis"):
        return "avif"
    if data.startswith(b"BM") and len(data) >= 26:
        return "bmp"
    return None


def ipfs_uri(cid: str) -> str:
    if not isinstance(cid, str) or not CID.fullmatch(cid):
        raise AdapterError("IPFS returned an invalid content identifier.")
    return "ipfs://" + cid


def metadata(req: CreateRequest, image_cid: str) -> dict:
    validate_identity(req.name, req.symbol, req.description)
    links = {key: (req.socials or {}).get(key, "") for key in SOCIALS}
    if any(value and not https_link(value) for value in links.values()):
        raise AdapterError("Elpida social links must be full HTTPS URLs.")
    return {"schema": "elpida-token-v1", "name": req.name, "symbol": req.symbol,
            "description": req.description, "image": ipfs_uri(image_cid), **links}


def minimum_out(amount: int, slippage_pct: float) -> int:
    slip, scale = Decimal(str(slippage_pct)).as_integer_ratio()
    return max(1, amount * (100 * scale - slip) // (100 * scale)) if amount else 0


def raw_amount(amount: float, decimals: int) -> int:
    value = Decimal(str(amount))
    if not value.is_finite() or value < 0:
        raise AdapterError("Enter a finite nonnegative amount.")
    significant, divisor = value.as_integer_ratio()
    raw, remainder = divmod(significant * 10**decimals, divisor)
    if remainder:
        raise AdapterError(f"Enter a nonnegative amount with at most {decimals} decimals.")
    return raw


def amount_label(raw: int, decimals: int, symbol: str) -> str:
    return f"{Decimal(raw).scaleb(-decimals):f} {symbol}"


class ElpidaAdapter:
    kind = "evm"
    deploy_gas_limit = 3_000_000
    trade_gas_limit = 650_000

    def __init__(self, network: str, rpc: EvmRPC, pinning: Pinata | None = None):
        self.id = f"elpida-{network}"
        self.network = network
        self.rpc = rpc
        self.pinning = pinning
        self.config = NETWORKS[network]
        self.chain_id = EVM_CHAINS[network]["chain_id"]
        self.native = EVM_CHAINS[network]["native"]
        self.quote_assets = ["native", self.config.stable_symbol]

    async def _read(self, to, signature, outputs, types=(), args=(), *, sender=None, value=0):
        data = calldata(signature, types, args)
        tx = {"to": to, "data": "0x" + data.hex()}
        if sender:
            tx["from"] = sender
        if value:
            tx["value"] = hex(value)
        try:
            result = await self.rpc.call("eth_call", [tx, "latest"])
            return decode(outputs, bytes.fromhex(result[2:])) if outputs else ()
        except EvmRPCError as error:
            raise AdapterError(explain(error)) from error
        except EvmRPCUnavailable as error:
            raise AdapterError("Elpida's chain RPC is unavailable. Try again shortly.") from error
        except (TypeError, ValueError, IndexError, DecodingError) as error:
            raise AdapterError("Elpida returned an unreadable contract response.") from error

    def _options(self, minimum: int, pairs: list[str]) -> dict:
        return {"requires_image": True, "socials": list(SOCIALS), "fields": [
            {"key": "elpida_pair", "type": "select", "label": "Pool quote", "options": pairs, "default": pairs[0],
             "option_labels": {"native": self.native, "stable": self.config.stable_symbol}},
            {"key": "elpida_fee_payment", "type": "select", "label": "Pay Elpida launch fee in",
             "options": ["native", "stable"], "default": "native",
             "option_labels": {"native": self.native, "stable": self.config.stable_symbol}},
            {"key": "elpida_fee_bps", "type": "number", "label": "Trading fee (basis points)",
             "min": minimum, "max": 400, "step": 1, "default": minimum, "hint": "100 basis points is 1%. The fee is fixed at launch."},
            {"key": "dev_buy_sol", "type": "number", "label": "First buy", "min": 0, "default": 0,
             "hint": "The first buy can acquire at most 3% of supply."},
            {"key": "slippage_pct", "type": "number", "min": 0, "max": 50, "default": 10},
        ]}

    def options_schema(self) -> dict:
        return self._options(0, ["native", "stable"])

    async def supported_quotes(self) -> list[dict]:
        assets = (("native", ZERO, self.native), ("stable", self.config.stable, self.config.stable_symbol))
        values = await asyncio.gather(*(self._read(
            self.config.current.factory, "quoteVirtual(address)", ["uint256"], ["address"], [asset]
        ) for _, asset, _ in assets))
        return [{"kind": kind, "mint": asset, "symbol": symbol, "name": symbol,
                 "decimals": (await self.asset_info(asset))["decimals"], "pool": "Elpida Uniswap v4"}
                for (kind, asset, symbol), (virtual,) in zip(assets, values) if virtual]

    async def live_options_schema(self) -> dict:
        (minimum,), assets = await asyncio.gather(
            self._read(self.config.current.hook, "minFeeBps()", ["uint256"]), self.supported_quotes())
        if not assets or minimum > 400:
            raise AdapterError("Elpida has no supported launch terms on this chain.")
        return self._options(minimum, [a["kind"] for a in assets])

    def creator_share(self) -> dict:
        return {"bps": 0, "recipient": "", "supported": True, "collection": "protocol",
                "note": "Elpida allocates 30% of its trading fees to the launching wallet."}

    async def asset_info(self, asset: str) -> dict:
        if asset.lower() == ZERO:
            return {"token": None, "asset": self.native, "decimals": 18}
        if asset.lower() == self.config.stable.lower():
            symbol, decimals = self.config.stable_symbol, await self.rpc.erc20_decimals(asset)
        else:
            info = await self.rpc.erc20_meta(asset)
            symbol, decimals = info["symbol"], info["decimals"]
        return {"token": to_checksum_address(asset), "asset": symbol, "decimals": decimals}

    async def launch_payment(self, options: dict, amount: float) -> dict:
        deploy = self.config.current
        asset = self.config.stable if options.get("elpida_pair") == "stable" else ZERO
        stable_fee = options.get("elpida_fee_payment") == "stable"
        (hook,), (minimum,), (virtual,), (left,), (paused,), (requires_metadata,), (native_fee,) = await asyncio.gather(
            self._read(deploy.factory, "hook()", ["address"]),
            self._read(deploy.hook, "minFeeBps()", ["uint256"]),
            self._read(deploy.factory, "quoteVirtual(address)", ["uint256"], ["address"], [asset]),
            self._read(deploy.factory, "launchesLeftToday()", ["uint256"]),
            self._read(deploy.factory, "launchesPaused()", ["bool"]),
            self._read(deploy.factory, "requiresMetadata()", ["bool"]),
            self._read(deploy.factory, "launchFee()", ["uint256"]),
        )
        if hook.lower() != deploy.hook.lower():
            raise AdapterError("Elpida's factory hook changed. Deployment verification is required.")
        if paused or not left:
            raise AdapterError(ERROR_MESSAGES["launches paused" if paused else "daily launch cap"])
        if not virtual:
            raise AdapterError(ERROR_MESSAGES["quote not approved"])
        fee_bps = options.get("elpida_fee_bps", minimum)
        if isinstance(fee_bps, bool) or not isinstance(fee_bps, (int, float)) or not minimum <= fee_bps <= 400 or int(fee_bps) != fee_bps:
            raise AdapterError(ERROR_MESSAGES["FeeOutOfRange"])
        quote = await self.asset_info(asset)
        first_buy = raw_amount(amount, quote["decimals"])
        if stable_fee:
            (fee_asset,) = await self._read(deploy.factory, "feeUsdc()", ["address"])
            if fee_asset.lower() != self.config.stable.lower():
                raise AdapterError(ERROR_MESSAGES["UsdcUnavailable"])
            fee = await self.asset_info(fee_asset)
            fee_raw = 10 * 10**fee["decimals"]
            fee_limit = 0
        else:
            fee = await self.asset_info(ZERO)
            fee_raw = native_fee
            fee_limit = (native_fee * 101 + 99) // 100
        return {"quote": quote, "first_buy": str(first_buy), "first_buy_label": amount_label(first_buy, quote["decimals"], quote["asset"]),
                "fee": fee, "fee_raw": str(fee_raw), "fee_bps": int(fee_bps), "stable_fee": stable_fee,
                "fee_label": amount_label(fee_raw, fee["decimals"], fee["asset"]),
                "fee_limit": str(fee_limit), "requires_metadata": requires_metadata,
                "value": str(fee_limit + (first_buy if asset == ZERO else 0))}

    @staticmethod
    def allowances(payment: dict) -> dict[str, int]:
        allowances = {}
        for asset, amount in [(payment["quote"]["token"], int(payment["first_buy"])),
                              (payment["fee"]["token"], int(payment["fee_raw"]))]:
            if asset and amount:
                allowances[asset] = allowances.get(asset, 0) + amount
        return allowances

    async def upload_metadata(self, req: CreateRequest) -> str:
        image = req.image_bytes or b""
        ext = artwork_extension(image)
        if not ext or len(image) > IMAGE_MAX:
            raise AdapterError("Add a PNG, JPEG, WebP, GIF, AVIF or BMP image up to 5 MB.")
        if not self.pinning:
            raise AdapterError("Elpida metadata requires the server's IPFS pinning service.")
        validate_identity(req.name, req.symbol, req.description)
        try:
            image_cid = await self.pinning.pin_file(image, f"token.{ext}")
            payload = json.dumps(metadata(req, image_cid), ensure_ascii=False).encode("utf-8")
            return ipfs_uri(await self.pinning.pin_bytes_json(payload, "metadata.json"))
        except AdapterError:
            raise
        except Exception as error:
            raise AdapterError("Elpida's IPFS upload failed. Retry before deploying.") from error

    async def _approval(self, token: str, owner: str, spender: str, amount: int) -> list[EvmStep]:
        if await self.rpc.erc20_allowance(token, owner, spender) >= amount:
            return []
        data = calldata("approve(address,uint256)", ["address", "uint256"], [spender, amount])
        return [EvmStep("approve", token, data)]

    async def prepare_create(self, req: CreateRequest) -> EvmPlan:
        owner = req.dev_pubkey
        if not ADDRESS.fullmatch(owner) or owner.lower() == ZERO:
            raise AdapterError("Use a valid launching wallet as the Elpida reward recipient.")
        payment = req.options.get("reviewed_launch_payment") or await self.launch_payment(req.options, req.dev_buy_sol)
        factory = self.config.current.factory
        metadata_uri = await self.upload_metadata(req)
        quote = payment["quote"]["token"] or ZERO
        first_buy = int(payment["first_buy"])
        value = int(payment["value"])
        types = ["string", "string", "address", "uint256", "uint256", "uint256", "address", "string", "bool"]
        signature = "launch(" + ",".join(types) + ")"

        def args(min_out):
            return [req.name, req.symbol, quote, payment["fee_bps"], first_buy, min_out, owner,
                    metadata_uri, payment["stable_fee"]]

        async def build():
            _, bought = await self._read(factory, signature, ["address", "uint256"], types, args(0),
                                         sender=owner, value=value)
            return calldata(signature, types, args(minimum_out(bought, req.slippage_pct)))

        steps = []
        for token, amount in self.allowances(payment).items():
            steps += await self._approval(token, owner, factory, amount)
        if steps:
            launch = EvmStep("launch", factory, b"", value, build=build)
        else:
            launch = EvmStep("launch", factory, await build(), value)
        return EvmPlan([*steps, launch], resolve_token=self.resolve_launch_token)

    async def resolve_launch_token(self, logs: list[dict]) -> str | None:
        for log in logs:
            topics = log.get("topics") or []
            if (str(log.get("address", "")).lower() == self.config.current.factory.lower() and
                    len(topics) == 4 and topics[0].lower() == LAUNCHED and len(topics[2]) == 66):
                return to_checksum_address("0x" + topics[2][-40:])
        return None

    async def token_deployment(self, token: str) -> Deployment | None:
        if not ADDRESS.fullmatch(token) or token.lower() == ZERO:
            return None
        for deployment in (self.config.current, *self.config.previous):
            (exists,) = await self._read(deployment.factory, "isElpidaToken(address)", ["bool"], ["address"], [token])
            if exists:
                return deployment
        return None

    async def pool_key(self, token: str) -> tuple[Deployment, tuple]:
        deployment = await self.token_deployment(token)
        if not deployment:
            raise AdapterError("This token was not launched by a supported Elpida factory.")
        (key,) = await self._read(deployment.hook, "poolKey(address)", ["(address,address,uint24,int24,address)"],
                                  ["address"], [token])
        if key[1].lower() != token.lower() or key[4].lower() != deployment.hook.lower() or key[2:4] != (0, 10):
            raise AdapterError("Elpida returned an unexpected pool configuration.")
        return deployment, key

    async def token_pair(self, token: str) -> tuple[Deployment, dict, int]:
        deployment, key = await self.pool_key(token)
        quote, (fee,) = await asyncio.gather(
            self.asset_info(key[0]), self._read(deployment.hook, "currentFeeBps(address)", ["uint256"], ["address"], [token]))
        return deployment, quote, fee

    async def prepare_trade(self, req: EvmTradeRequest) -> EvmPlan:
        if req.amount_wei <= 0 or req.side not in ("buy", "sell"):
            raise AdapterError("Enter a positive Elpida trade amount.")
        deployment, quote, fee_bps = await self.token_pair(req.token)
        router = deployment.router
        if not router:
            raise AdapterError("The original Elpida router for this token has not been verified.")
        (hook,), (manager,) = await asyncio.gather(self._read(router, "hook()", ["address"]),
                                                   self._read(router, "poolManager()", ["address"]))
        if hook.lower() != deployment.hook.lower() or manager.lower() != self.config.pool_manager.lower():
            raise AdapterError("Elpida router does not match the token's verified pool.")
        buy = req.side == "buy"
        payer_token = quote["token"] if buy else req.token
        value = req.amount_wei if buy and not payer_token else 0
        steps = await self._approval(payer_token, req.wallet, router, req.amount_wei) if payer_token else []
        types = ["address", "uint256", "uint256", "address", "uint256"]
        signature = f"{req.side}({','.join(types)})"
        paid, out = await self._read(router, "quote(address,bool,uint256)", ["uint256", "uint256"],
                                     ["address", "bool", "uint256"], [req.token, buy, req.amount_wei], sender=req.wallet)
        if not paid or not out:
            raise AdapterError("Elpida has no output for this trade size.")
        minimum = max(req.minimum_out, minimum_out(out, req.slippage_pct))
        if out < minimum:
            raise AdapterError(ERROR_MESSAGES["Slippage"])

        async def build():
            try:
                block = await self.rpc.call("eth_getBlockByNumber", ["latest", False])
                deadline = int(block["timestamp"], 16) + 120
            except (EvmRPCError, EvmRPCUnavailable, TypeError, ValueError, KeyError) as error:
                raise AdapterError("Couldn't read the Elpida chain time. Review a fresh quote.") from error
            args = [req.token, req.amount_wei, minimum, req.wallet, deadline]
            await self._read(router, signature, ["uint256"], types, args, sender=req.wallet, value=value)
            return calldata(signature, types, args)

        return EvmPlan([*steps, EvmStep(req.side, router, b"", value, build=build)], token=req.token,
                       notes={"quote_asset": quote, "quote_paid": paid, "quote_out": out,
                              "minimum_out": minimum, "current_fee_bps": fee_bps, "router": router})

    @staticmethod
    def trade_fee_basis(logs: list[dict], plan: EvmPlan, wallet: str, token: str, side: str) -> int:
        indexed = ["0x" + address[2:].lower().rjust(64, "0") for address in (token, wallet, wallet)]
        for log in logs:
            topics = [t.lower() for t in log.get("topics", [])]
            if str(log.get("address", "")).lower() == plan.notes["router"].lower() and topics == [SWAPPED, *indexed]:
                buy, quote, _ = decode(["bool", "uint256", "uint256"], bytes.fromhex(log["data"][2:]))
                if buy == (side == "buy"):
                    return quote
        return 0

    async def prepare_claim(self, req: ClaimRequest) -> EvmPlan:
        raise AdapterError("Claim Elpida creator rewards on Elpida.")
