import json
from types import SimpleNamespace

from eth_abi import decode, encode
from eth_utils import keccak
import pytest

from app.chains.base import AdapterError, CreateRequest, EvmTradeRequest
from app.chains.elpida import ElpidaAdapter, LAUNCHED, ZERO, explain, metadata
from app.chains.evm import EvmRPCError
from app.services.elpida_tokens import _launch_event, parse_feed

OWNER = "0x" + "11" * 20
TOKEN = "0x" + "22" * 20
CID = "bafkreihelmtestcid"
PNG = b"\x89PNG\r\n\x1a\n"


class Pinning:
    def __init__(self):
        self.operations = []

    async def pin_file(self, data, name):
        self.operations.append((name, data))
        return CID

    async def pin_bytes_json(self, data, name):
        self.operations.append((name, json.loads(data)))
        return CID


class RPC:
    def __init__(self, stable=False):
        self.adapter = ElpidaAdapter("base", self, Pinning())
        self.quote = self.adapter.config.stable if stable else ZERO
        self.calls = []
        self.replies = {
            "hook()": ("address", self.adapter.config.current.hook),
            "poolManager()": ("address", self.adapter.config.pool_manager),
            "minFeeBps()": ("uint256", 100),
            "quoteVirtual(address)": ("uint256", 10),
            "launchesLeftToday()": ("uint256", 1000),
            "launchesPaused()": ("bool", False),
            "requiresMetadata()": ("bool", True),
            "launchFee()": ("uint256", 200),
            "feeUsdc()": ("address", self.adapter.config.stable),
            "isElpidaToken(address)": ("bool", True),
            "poolKey(address)": ("(address,address,uint24,int24,address)", (self.quote, TOKEN, 0, 10, self.adapter.config.current.hook)),
            "currentFeeBps(address)": ("uint256", 135),
            "launch(string,string,address,uint256,uint256,uint256,address,string,bool)": (("address", "uint256"), (TOKEN, 400)),
            "quote(address,bool,uint256)": (("uint256", "uint256"), (100, 400)),
            "buy(address,uint256,uint256,address,uint256)": ("uint256", 400),
            "sell(address,uint256,uint256,address,uint256)": ("uint256", 400),
        }

    async def call(self, method, params):
        if method == "eth_getBlockByNumber":
            return {"timestamp": "0x3039"}
        assert method == "eth_call"
        tx = params[0]
        self.calls.append(tx)
        for signature, reply in self.replies.items():
            if tx["data"][2:10] == keccak(text=signature)[:4].hex():
                types, values = reply
                if isinstance(types, str):
                    types, values = [types], [values]
                return "0x" + encode(types, values).hex()
        raise AssertionError("unexpected calldata")

    async def erc20_decimals(self, token):
        return 6

    async def erc20_allowance(self, token, owner, spender):
        return 0


@pytest.mark.parametrize("pair,pay", [("native", "native"), ("stable", "native"), ("native", "stable"), ("stable", "stable")])
async def test_launch_calldata_metadata_and_exact_allowance(pair, pay):
    rpc = RPC()
    request = CreateRequest("This Token", "This1", "Description", PNG, {"discord": "https://discord.gg/helm"}, OWNER,
                            dev_buy_sol=2, slippage_pct=5,
                            options={"elpida_pair": pair, "elpida_fee_payment": pay, "elpida_fee_bps": 175})
    adapter = rpc.adapter
    plan = await adapter.prepare_create(request)
    stable = adapter.config.stable.lower()
    address = adapter.config.current.factory.lower()
    launch = plan.steps[-1]
    assert launch.to.lower() == address
    expected = 2 * 10**18 if pair == "native" else 2_000_000
    assert launch.value == (202 if pay == "native" else 0) + (expected if pair == "native" else 0)
    required = (expected if pair == "stable" else 0) + (10_000_000 if pay == "stable" else 0)
    if required:
        approval = plan.steps[0]
        assert len(plan.steps) == 2 and approval.to.lower() == stable
        assert decode(["address", "uint256"], approval.data[4:]) == (address, required)
    raw = await launch.build() if launch.build else launch.data
    signature = "launch(string,string,address,uint256,uint256,uint256,address,string,bool)"
    assert raw[:4] == keccak(text=signature)[:4]
    assert decode(["string", "string", "address", "uint256", "uint256", "uint256", "address", "string", "bool"], raw[4:]) == (
        request.name, request.symbol, stable if pair == "stable" else ZERO, 175, expected, 380, OWNER, "ipfs://" + CID, pay == "stable")
    assert rpc.calls[-1]["from"] == OWNER
    assert int(rpc.calls[-1].get("value", "0x0"), 16) == launch.value
    assert adapter.pinning.operations == [
        ("token.png", PNG),
        ("metadata.json", {"schema": "elpida-token-v1", "name": request.name, "symbol": request.symbol,
                           "description": "Description", "image": "ipfs://" + CID, "website": "",
                           "twitter": "", "telegram": "", "discord": "https://discord.gg/helm"}),
    ]


@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("stable", [False, True])
async def test_trade_calldata_and_allowance(side, stable):
    rpc = RPC(stable)
    adapter = rpc.adapter
    plan = await adapter.prepare_trade(EvmTradeRequest(TOKEN, OWNER, side, 100, 5))
    step = plan.steps[-1]
    router = adapter.config.current.router.lower()
    assert step.to.lower() == router
    assert step.value == (100 if side == "buy" and not stable else 0)
    if stable or side == "sell":
        approval = plan.steps[0]
        assert len(plan.steps) == 2
        assert approval.to.lower() == (TOKEN if side == "sell" else rpc.quote.lower())
        assert decode(["address", "uint256"], approval.data[4:]) == (router, 100)
    data = await step.build()
    assert data[:4] == keccak(text=f"{side}(address,uint256,uint256,address,uint256)")[:4]
    assert decode(["address", "uint256", "uint256", "address", "uint256"], data[4:])[:4] == (TOKEN, 100, 380, OWNER)
    assert plan.notes["current_fee_bps"] == 135


@pytest.mark.parametrize("attribute,bad", [("name", "é" * 33), ("symbol", "💚"), ("description", "a" * 1001),
                                           ("socials", {"twitter": "javascript:alert(1)"})])
def test_metadata_rejects_invalid_fields(attribute, bad):
    req = CreateRequest("Name", "T123", "", PNG, {}, OWNER)
    setattr(req, attribute, bad)
    with pytest.raises(AdapterError):
        metadata(req, CID)


@pytest.mark.parametrize("revert,fragment", [
    ("metadata required", "requires token artwork"), ("IPFS URI required", "requires an IPFS"),
    ("quote not approved", "quote asset"), ("launches paused", "paused launches"),
    ("daily launch cap", "daily launch cap"), ("token address", "another wallet"),
    ("FeeOutOfRange()", "4%"), ("FirstBuyAboveCap()", "3%"),
])
def test_revert_messages(revert, fragment):
    raw = keccak(text=revert)[:4] if revert.endswith("()") else bytes.fromhex("08c379a0") + encode(["string"], [revert])
    assert fragment in explain(EvmRPCError("execution reverted", "0x" + raw.hex()))


def test_feed_is_chain_scoped_deduplicated_and_safe():
    row = {"network": "base-mainnet", "chainId": 8453, "address": TOKEN, "name": "Helm", "symbol": "H",
           "image": "https://elpida.fun/logo.png", "links": {"website": "https://helmterminals.xyz", "twitter": "javascript:test"}}
    payload = {"version": 1, "tokens": [row, row, {**row, "chainId": 1}, {**row, "address": None}]}
    result, = parse_feed(payload, "base")
    assert result["socials"] == {"website": "https://helmterminals.xyz"}
    assert result["image"] == row["image"]
    assert result["url"].endswith("base-mainnet/" + TOKEN)
    with pytest.raises(AdapterError):
        parse_feed({"version": 2, "tokens": []}, "base")


def test_event_verification_checks_factory_block_and_token():
    deployment = SimpleNamespace(factory=OWNER, start_block=512)
    token_topic = "0x" + TOKEN[2:].zfill(64)
    log = {"address": OWNER, "topics": [LAUNCHED, token_topic, token_topic, token_topic]}
    receipt = {"status": "0x1", "logs": [log], "blockNumber": "0x200"}
    assert _launch_event(receipt, [deployment], TOKEN) == log
    assert _launch_event({**receipt, "blockNumber": "0x1ff"}, [deployment], TOKEN) is None
    assert _launch_event(receipt, [deployment], OWNER) is None


async def test_elpida_wallet_swap_uses_registered_quote(monkeypatch):
    from app.services import elpida_swap

    rpc = RPC(stable=True)

    async def deployment(token):
        return rpc.adapter.config.current if token == TOKEN else None

    monkeypatch.setattr(rpc.adapter, "token_deployment", deployment)
    monkeypatch.setattr(elpida_swap, "get_adapter", lambda _: rpc.adapter)
    adapter, token, side, quote = await elpida_swap.quote_pair("base", rpc.quote, TOKEN)
    assert adapter is rpc.adapter and token == TOKEN and side == "buy"
    assert quote["token"].lower() == rpc.quote.lower() and quote["decimals"] == 6
    assert (await elpida_swap.quote_pair("base", TOKEN, rpc.quote))[2] == "sell"
    from fastapi import HTTPException

    with pytest.raises(HTTPException, match="trades in USDC"):
        await elpida_swap.quote_pair("base", ZERO, TOKEN)


async def test_telegram_elpida_handoff_preserves_web_review():
    from helm_bot.controller import Controller

    class BotAPI:
        async def call(self, tg_id, method, path):
            assert method == "GET"
            if path == "/launchpads":
                return 200, [{"id": "elpida-bsc", "status": "available"}]
            return 200, [{"id": "token-workspace", "launchpad": "elpida-bsc"}]

    responses = []

    async def send(reply):
        responses.append(reply.text)

    bot = Controller(BotAPI(), web_url="https://helm.example")
    await bot.on_message(12, 12, "private", "/launch elpida bnb", send)
    await bot.on_message(12, 12, "private", "/buy token-workspace 0.00000001", send)
    await bot.on_message(12, 12, "private", "/sell token-workspace 25", send)
    assert "https://helm.example/launches?launchpad=elpida-bsc" in responses[0]
    assert "side=buy&amp;amount=0.00000001" in responses[1]
    assert "side=sell&amp;amount=25.0" in responses[2]
    assert all("Review and confirm in the Helm web app" in reply for reply in responses)
