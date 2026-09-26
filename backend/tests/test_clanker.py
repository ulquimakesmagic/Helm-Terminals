"""Clanker v4: deployToken config matches the SDK defaults, dev buy extension, rewards split, claims."""

import httpx
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode

from app.chains.base import ClaimRequest, CreateRequest, EvmTradeRequest
from app.chains.clanker import (
    CLANKER_CHAINS, DEPLOYMENT_T, DEPLOY_SIG, SNIPER, TOKEN_CREATED_TOPIC, ClankerAdapter, selector,
)
from app.chains.kyber import Graduated

from .test_evm_wallets import ETH, FakeEvm

TOKEN = "0x" + "c1" * 20
DEV = "0x" + "ab" * 20
HELM = "0x" + "fe" * 20


class ClankerNode(FakeEvm):
    def __init__(self, network="base"):
        super().__init__(network)
        self.cfg = CLANKER_CHAINS[network]
        self.fees = {}
        self.deployed = None

    async def call(self, method, params):
        tx = params[0]
        data = bytes.fromhex(tx["data"][2:])
        if tx["to"].lower() == self.cfg["factory"].lower() and data[:4] == selector(DEPLOY_SIG):
            (cfg,) = abi_decode([DEPLOYMENT_T], data[4:])
            self.deployed = (cfg, int(tx.get("value", "0x0"), 16))
            return "0x" + abi_encode(["address"], [TOKEN]).hex()
        if tx["to"].lower() == self.cfg["fee_locker"].lower():
            owner, tok = abi_decode(["address", "address"], data[4:])
            return hex(self.fees.get(tok.lower(), 0))
        raise AssertionError("unexpected call")


async def test_deploy_config_matches_sdk_defaults_with_dev_buy_and_helm_share():
    node = ClankerNode("base")
    a = ClankerAdapter("clanker", "base", node, platform_wallet=HELM, platform_reward_bps=1000)
    req = CreateRequest(name="Base Cat", symbol="BCAT", description="gm", image_bytes=None,
                        socials={"twitter": "https://x.com/b"}, dev_pubkey=DEV, dev_buy_sol=0.01,
                        options={"logo_url": "https://api.helmterminals.xyz/public/launch-images/x"})
    plan = await a.prepare_create(req)
    assert plan.token.lower() == TOKEN and plan.steps[0].value == ETH // 100
    (token, pool, locker, mev, ext), value = node.deployed
    assert value == ETH // 100
    assert token[0].lower() == DEV and token[1:3] == ("Base Cat", "BCAT") and token[4].endswith("/launch-images/x")
    assert '"interface":"Helm Terminals"' in token[6] and "https://x.com/b" in token[5] and token[7] == 8453
    assert pool[0].lower() == CLANKER_CHAINS["base"]["hook"].lower() and pool[1].lower() == CLANKER_CHAINS["base"]["weth"].lower()
    assert pool[2:4] == (-230400, 200)
    ext_addr, ext_data, fee_data = abi_decode(["(address,bytes,bytes)"], pool[4])[0]
    assert abi_decode(["uint24", "uint24"], fee_data) == (10_000, 10_000)
    assert [x.lower() for x in locker[2]] == [DEV, HELM] and list(locker[3]) == [9000, 1000]
    assert list(locker[4]) == [-230400] and list(locker[5]) == [-120000] and list(locker[6]) == [10_000]
    assert abi_decode(["(uint24,uint24,uint256)"], mev[1])[0] == SNIPER
    assert len(ext) == 1 and ext[0][0].lower() == CLANKER_CHAINS["base"]["devbuy"].lower() and ext[0][1] == ETH // 100
    notes = a.deploy_notes({})
    assert notes["platform_share"] == "10% of LP rewards to Helm" and notes["rewards"].startswith("90%")
    log = {"address": CLANKER_CHAINS["base"]["factory"], "topics": [TOKEN_CREATED_TOPIC, "0x" + "00" * 12 + "d2" * 20]}
    assert (await plan.resolve_token([log])).lower() == "0x" + "d2" * 20


async def test_no_platform_share_without_wallet_and_trades_go_to_dex():
    node = ClankerNode("robinhood")
    a = ClankerAdapter("clanker-robinhood", "robinhood", node, platform_wallet="", platform_reward_bps=1000)
    await a.prepare_create(CreateRequest(name="R", symbol="R", description="", image_bytes=None, socials={},
                                         dev_pubkey=DEV, dev_buy_sol=0))
    (token, pool, locker, mev, ext), value = node.deployed
    assert list(locker[3]) == [10_000] and ext == () and value == 0 and token[7] == 4663
    assert "platform_share" not in a.deploy_notes({})
    try:
        await a.prepare_trade(EvmTradeRequest(TOKEN, DEV, "buy", 1, 10))
        raise AssertionError
    except Graduated:
        pass


async def test_claim_collects_then_claims_only_tokens_with_fees():
    node = ClankerNode("base")
    a = ClankerAdapter("clanker", "base", node)
    node.fees = {CLANKER_CHAINS["base"]["weth"].lower(): 5}
    plan = await a.prepare_claim(ClaimRequest(wallet_pubkey=DEV, mint=TOKEN))
    assert [s.label for s in plan.steps] == ["collect", "claim_weth", "claim_token"]
    assert await plan.steps[1].build() is not None and await plan.steps[2].build() is None
