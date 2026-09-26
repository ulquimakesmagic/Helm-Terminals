"""Uniswap V2 launch: deploy HelmToken, approve Router02, addLiquidityETH, with the token address from the receipt."""

from eth_abi import decode as abi_decode

from app.chains import registry
from app.chains.uniswap_v2 import DEAD, ROUTER02, SUPPLY, TOKEN_BYTECODE, UniswapV2Adapter, selector
from app.deps import get_executor
from app.main import app
from app.services.executor import Executor
from app.signing import get_signer

from .conftest import signup
from .test_evm_wallets import ETH, FakeEvm

NEW_TOKEN = "0x" + "7e" * 20


class UniNode(FakeEvm):
    def __init__(self):
        super().__init__("ethereum")
        self.raw: list[bytes] = []

    async def estimate_gas(self, tx):
        return 500_000

    async def send_raw(self, raw_hex):
        self.raw.append(bytes.fromhex(raw_hex[2:]))
        return await super().send_raw(raw_hex)

    async def receipt(self, h):
        rec = {"status": "0x1", "gasUsed": hex(21_000), "effectiveGasPrice": hex(10**9), "logs": []}
        if self.sent[-1]["to"] in ("0x", ""):
            rec["contractAddress"] = NEW_TOKEN
        return rec


async def test_uniswap_v2_launch_through_the_api(client, fake_rpc):
    node = UniNode()
    registry._cache["uniswap-v2"] = UniswapV2Adapter(node)
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"ethereum": node})
    try:
        h = await signup(client)
        (dev,) = (await client.post("/wallets", json={"network": "ethereum", "count": 1}, headers=h)).json()
        node.balances[dev["address"].lower()] = 2 * ETH
        r = await client.post("/launches", json={"launchpad": "uniswap-v2", "name": "Helm Coin", "symbol": "helm",
                                                 "dev_wallet_id": dev["id"], "dev_buy_sol": 0,
                                                 "options": {"lp_share_pct": 90, "lp_tokens": "burn"}}, headers=h)
        lid = r.json()["id"]
        pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
        assert any("starting liquidity" in w for w in pf["preflight"]["warnings"])  # 0 ETH liquidity is refused

        r = await client.post("/launches", json={"launchpad": "uniswap-v2", "name": "Helm Coin", "symbol": "helm",
                                                 "dev_wallet_id": dev["id"], "dev_buy_sol": 0.5,
                                                 "options": {"lp_share_pct": 90, "lp_tokens": "burn"}}, headers=h)
        lid = r.json()["id"]
        pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
        p = pf["preflight"]
        assert not p["warnings"], p["warnings"]
        assert "burned" in p["lp_tokens"] and "10% stays" in p["pool"] and "no owner" in p["token_contract"]
        r = await client.post(f"/launches/{lid}/deploy", json={"confirm": True, "preflight_hash": pf["preflight_hash"],
                                                              "idempotency_key": "u1"}, headers=h)
        job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
        assert job["status"] == "confirmed", job
        assert [t["kind"] for t in job["transactions"]] == ["deploy_token", "deploy_approve", "deploy"]
        launch = (await client.get(f"/launches/{lid}", headers=h)).json()
        assert launch["status"] == "live" and launch["mint_address"].lower() == NEW_TOKEN

        from eth_account.typed_transactions import TypedTransaction
        from hexbytes import HexBytes
        deploy, approve, liq = (TypedTransaction.from_bytes(HexBytes(r)).as_dict() for r in node.raw)
        assert bytes(deploy["data"]).startswith(TOKEN_BYTECODE)
        name, symbol, supply, to = abi_decode(["string", "string", "uint256", "address"], bytes(deploy["data"])[len(TOKEN_BYTECODE):])
        assert (name, symbol, supply, to.lower()) == ("Helm Coin", "HELM", SUPPLY, dev["address"].lower())
        assert "0x" + bytes(approve["to"]).hex() == NEW_TOKEN
        assert abi_decode(["address", "uint256"], bytes(approve["data"])[4:]) == (ROUTER02.lower(), SUPPLY * 9 // 10)
        assert "0x" + bytes(liq["to"]).hex() == ROUTER02.lower() and liq["value"] == ETH // 2
        data = bytes(liq["data"])
        assert data[:4] == selector("addLiquidityETH(address,uint256,uint256,uint256,address,uint256)")
        args = abi_decode(["address", "uint256", "uint256", "uint256", "address", "uint256"], data[4:])
        assert args[0] == NEW_TOKEN and args[1] == SUPPLY * 9 // 10 and args[4] == DEAD.lower()
    finally:
        registry._cache.pop("uniswap-v2", None)
