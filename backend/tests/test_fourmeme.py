"""Four.meme: API login signed with the dev wallet, create args, msg.value, token from TokenCreate, trading."""

import json

import httpx
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_account import Account
from eth_account.messages import encode_defunct

from app.chains.base import CreateRequest, EvmTradeRequest
from app.chains.evm import EvmRPCError
from app.chains.fourmeme import HELPER3, TM2, TOKEN_CREATE_TOPIC, FourMemeAdapter, selector
from app.chains.kyber import Graduated
from app.services.evm_exec import sign_text
from app.signing import get_signer

from .test_evm_wallets import ETH, FakeEvm

TOKEN = "0x" + "44" * 20
LAUNCH_FEE = 10**15
FEE_RATE = 100  # 1%
PNG = b"\x89PNG\r\n\x1a\n" + b"2" * 16


def api_transport(log: list):
    def handler(req: httpx.Request):
        log.append(req)
        p = req.url.path
        if p.endswith("/nonce/generate"):
            return httpx.Response(200, json={"code": "0", "data": "NONCE123"})
        if p.endswith("/login/dex"):
            return httpx.Response(200, json={"code": "0", "data": "ACCESS"})
        if p.endswith("/token/upload"):
            assert req.headers["meme-web-access"] == "ACCESS"
            return httpx.Response(200, json={"code": "0", "data": "https://static.four.meme/img.png"})
        if p.endswith("/public/config"):
            return httpx.Response(200, json={"code": 0, "data": [
                {"symbol": "USD1", "status": "PUBLISH"},
                {"symbol": "BNB", "status": "PUBLISH", "totalAmount": "1000000000", "totalBAmount": "24", "saleRate": "0.8"}]})
        if p.endswith("/token/create"):
            return httpx.Response(200, json={"code": "0", "data": {"createArg": "0xaa11", "signature": "bb22"}})
        return httpx.Response(404, json={"code": "404"})
    return httpx.MockTransport(handler)


class FmNode(FakeEvm):
    def __init__(self):
        super().__init__("bsc", eip1559=False)
        self.liquidity_added = False
        self.version = 2
        self.quote = "0x" + "00" * 20
        self.allowance = 0
        self.create_value = None

    async def call(self, method, params):
        tx = params[0]
        to, data = tx["to"].lower(), bytes.fromhex(tx["data"][2:])
        sel, body = data[:4], data[4:]
        w = lambda *v: "0x" + abi_encode(["uint256"] * len(v), list(v)).hex()  # noqa: E731
        if to == TM2.lower():
            if sel == selector("_launchFee()"):
                return w(LAUNCH_FEE)
            if sel == selector("_tradingFeeRate()"):
                return w(FEE_RATE)
            if sel == selector("createToken(bytes,bytes)"):
                args, sig = abi_decode(["bytes", "bytes"], body)
                assert (args, sig) == (bytes.fromhex("aa11"), bytes.fromhex("bb22"))
                self.create_value = int(tx["value"], 16)
                return "0x"
        if to == HELPER3.lower():
            if sel == selector("getTokenInfo(address)"):
                return "0x" + abi_encode(
                    ["uint256", "address", "address", "uint256", "uint256", "uint256", "uint256", "uint256", "uint256",
                     "uint256", "uint256", "bool"],
                    [self.version, TM2, self.quote, 0, 100, 0, 0, 0, 0, 0, 0, self.liquidity_added]).hex()
            if sel == selector("tryBuy(address,uint256,uint256)"):
                _, _, funds = abi_decode(["address", "uint256", "uint256"], body)
                return "0x" + abi_encode(["address", "address", "uint256", "uint256", "uint256", "uint256", "uint256", "uint256"],
                                         [TM2, self.quote, funds * 1000, funds, funds // 100, funds + funds // 100, 0, funds]).hex()
            if sel == selector("trySell(address,uint256)"):
                _, amount = abi_decode(["address", "uint256"], body)
                return "0x" + abi_encode(["address", "address", "uint256", "uint256"], [TM2, self.quote, amount // 1000, 0]).hex()
        raise AssertionError(f"unexpected call {to} {sel.hex()}")

    async def erc20_allowance(self, token, owner, spender):
        return self.allowance


async def test_create_logs_in_with_a_real_signature_and_pays_the_right_value():
    signer = get_signer()
    ref = await signer.create_wallet("u", "bsc", "dev")
    log = []
    node = FmNode()
    a = FourMemeAdapter(node, client=httpx.AsyncClient(transport=api_transport(log)))

    async def sign(msg):
        return await sign_text(signer, ref.signer_ref, msg)

    req = CreateRequest(name="Four Cat", symbol="FCAT", description="gm", image_bytes=PNG,
                        socials={"twitter": "https://x.com/f"}, dev_pubkey=ref.address, dev_buy_sol=0.1,
                        options={"label": "AI"}, sign_text=sign)
    plan = await a.prepare_create(req)
    # the login signature is a normal personal_sign that recovers to the dev wallet
    login = json.loads(next(r for r in log if r.url.path.endswith("/login/dex")).content)
    sig = login["verifyInfo"]["signature"]
    assert Account.recover_message(encode_defunct(text="You are sign in Meme NONCE123"), signature=sig) == ref.address
    body = json.loads(next(r for r in log if r.url.path.endswith("/token/create")).content)
    assert body["raisedToken"]["symbol"] == "BNB" and body["preSale"] == "0.1" and body["label"] == "AI"
    assert body["imgUrl"].startswith("https://static.four.meme") and body["twitterUrl"] == "https://x.com/f"
    expected = LAUNCH_FEE + ETH // 10 + (ETH // 10) * FEE_RATE // 10_000
    assert plan.steps[0].value == expected == node.create_value and plan.steps[0].to == TM2
    # token address from the TokenCreate event (all fields un-indexed, so it's in the data)
    data = abi_encode(["address", "address", "uint256", "string", "string", "uint256", "uint256", "uint256"],
                      [ref.address, TOKEN, 1, "Four Cat", "FCAT", 10**27, 0, LAUNCH_FEE])
    assert (await plan.resolve_token([{"address": TM2, "topics": [TOKEN_CREATE_TOPIC], "data": "0x" + data.hex()}])).lower() == TOKEN
    # a second launch reuses the sign-in
    await a.prepare_create(req)
    assert sum(r.url.path.endswith("/login/dex") for r in log) == 1


async def test_trading_rules():
    node = FmNode()
    a = FourMemeAdapter(node)
    wallet = "0x" + "ab" * 20
    buy = await a.prepare_trade(EvmTradeRequest(TOKEN, wallet, "buy", 10**15 + 123, 10))
    funds = 10**15  # rounded down to whole gwei
    assert buy.steps[0].value == funds + funds // 100 and buy.steps[0].to == TM2
    t, f, m = abi_decode(["address", "uint256", "uint256"], buy.steps[0].data[4:])
    assert f == funds and m == funds * 1000 * 9 // 10
    sell = await a.prepare_trade(EvmTradeRequest(TOKEN, wallet, "sell", 5 * 10**18 + 7, 10))
    assert [s.label for s in sell.steps] == ["approve", "sell"]
    o, tok, amt, minf = abi_decode(["uint256", "address", "uint256", "uint256"], sell.steps[1].data[4:])
    assert amt == 5 * 10**18 and minf == 5 * 10**15 * 9 // 10
    node.liquidity_added = True
    try:
        await a.prepare_trade(EvmTradeRequest(TOKEN, wallet, "buy", 10**15, 10))
        raise AssertionError
    except Graduated:
        pass
    node.liquidity_added, node.quote = False, "0x" + "55" * 20
    try:
        await a.prepare_trade(EvmTradeRequest(TOKEN, wallet, "buy", 10**15, 10))
        raise AssertionError
    except Exception as e:
        assert "BNB-paired" in str(e)
