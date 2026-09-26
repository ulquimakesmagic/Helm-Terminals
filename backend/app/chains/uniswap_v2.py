"""Uniswap V2 launches on Ethereum: deploy a plain ERC-20 and open a Uniswap V2 pool with it.

There's no bonding curve and no launchpad contract. Helm:
  1. deploys HelmToken (contracts/HelmToken.sol): fixed supply minted once to the dev wallet, no owner, no
     minting, no tax, no blacklist, no pause;
  2. approves Uniswap's V2 Router02 for the tokens going into the pool;
  3. calls Router02.addLiquidityETH, which creates the pair and seeds it with the dev's tokens and ETH.
The LP tokens go either to the dev wallet (removable liquidity) or to the dead address (liquidity locked for
good). Both choices are shown in the preview.

Trades go through the DEX route (KyberSwap), which covers Uniswap V2 pools.

Addresses: Uniswap docs, "V2 deployment addresses" (Ethereum mainnet).
"""

import json
import time
from pathlib import Path

from eth_abi import encode as abi_encode
from eth_utils import keccak, to_checksum_address

from .base import AdapterError, ClaimRequest, CreateRequest, EvmPlan, EvmStep, EvmTradeRequest
from .evm import EvmRPC
from .kyber import Graduated

ROUTER02 = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
DEAD = "0x000000000000000000000000000000000000dEaD"
SUPPLY = 1_000_000_000 * 10**18
LP_SHARES = (100, 90, 80, 50)  # % of supply that goes into the pool; the rest stays in the dev wallet
_ARTIFACT = json.loads((Path(__file__).parent / "contracts" / "HelmToken.json").read_text())
TOKEN_BYTECODE = bytes.fromhex(_ARTIFACT["bytecode"].removeprefix("0x"))


def selector(sig: str) -> bytes:
    return keccak(text=sig)[:4]


class UniswapV2Adapter:
    id = "uniswap-v2"
    network = "ethereum"
    kind = "evm"
    quote_assets = ["native"]

    def __init__(self, rpc: EvmRPC) -> None:
        self.rpc = rpc

    def options_schema(self) -> dict:
        return {
            "fields": [
                {"key": "dev_buy_sol", "type": "number", "min": 0, "label": "Starting liquidity (ETH)", "default": 0.1,
                 "hint": "The ETH you put in the pool next to your tokens. It sets the starting price."},
                {"key": "lp_share_pct", "type": "select", "options": list(LP_SHARES), "default": 100,
                 "label": "Supply into the pool (%)",
                 "hint": "The rest stays in your dev wallet and shows in the supply panel."},
                {"key": "lp_tokens", "type": "select", "options": ["burn", "keep"], "default": "burn",
                 "label": "LP tokens",
                 "hint": "burn locks the liquidity for good (sent to the dead address); keep lets you remove it later."},
                {"key": "slippage_pct", "type": "number", "min": 0, "max": 50, "default": 10},
            ],
            "socials": [],
            "requires_image": False,
        }

    def deploy_warnings(self, dev_buy: float, params: dict) -> list[str]:
        return [] if dev_buy > 0 else ["Set the starting liquidity (the ETH that goes in the pool) above 0."]

    def deploy_notes(self, params: dict) -> dict:
        share = int(params.get("lp_share_pct") or 100)
        burn = (params.get("lp_tokens") or "burn") == "burn"
        return {
            "token_contract": "HelmToken: fixed 1,000,000,000 supply, no owner, no minting, no tax, no blacklist",
            "pool": f"Uniswap V2, {share}% of supply plus your ETH" + (
                "" if share == 100 else f"; {100 - share}% stays in your dev wallet"),
            "lp_tokens": "burned: liquidity locked for good" if burn else "kept in your dev wallet: you can remove liquidity",
            "steps": "3 transactions: deploy token, approve router, add liquidity",
        }

    async def prepare_create(self, req: CreateRequest) -> EvmPlan:
        opts = req.options or {}
        share = int(opts.get("lp_share_pct") or 100)
        if share not in LP_SHARES:
            raise AdapterError(f"supply into the pool must be one of {LP_SHARES}%")
        lp_to = DEAD if (opts.get("lp_tokens") or "burn") == "burn" else req.dev_pubkey
        eth = int(round(req.dev_buy_sol * 10**18))
        if eth <= 0:
            raise AdapterError("set the starting liquidity (ETH) above 0")
        pool_tokens = SUPPLY * share // 100
        slip = max(0.0, min(req.slippage_pct, 50.0)) / 100
        creation = TOKEN_BYTECODE + abi_encode(["string", "string", "uint256", "address"],
                                               [req.name, req.symbol, SUPPLY, req.dev_pubkey])
        state: dict = {}

        def remember(res) -> None:
            if getattr(res, "contract_address", None):
                state["token"] = to_checksum_address(res.contract_address)

        async def approve():
            if "token" not in state:
                raise AdapterError("the token deployed but its address wasn't in the receipt")
            return state["token"], selector("approve(address,uint256)") + abi_encode(["address", "uint256"],
                                                                                      [ROUTER02, pool_tokens])

        async def add_liquidity():
            args = [state["token"], pool_tokens, int(pool_tokens * (1 - slip)), int(eth * (1 - slip)), lp_to,
                    int(time.time()) + 20 * 60]
            return ROUTER02, selector("addLiquidityETH(address,uint256,uint256,uint256,address,uint256)") + abi_encode(
                ["address", "uint256", "uint256", "uint256", "address", "uint256"], args)

        async def resolve(_logs) -> str | None:
            return state.get("token")

        return EvmPlan([
            EvmStep("token", None, creation, after=remember),
            EvmStep("approve", None, b"", build=approve),
            EvmStep("liquidity", None, b"", value=eth, build=add_liquidity),
        ], token=None, notes=self.deploy_notes(opts), resolve_token=resolve)

    async def prepare_trade(self, req: EvmTradeRequest) -> EvmPlan:
        raise Graduated("Uniswap V2 launches trade on the DEX")

    async def prepare_claim(self, req: ClaimRequest) -> EvmPlan:
        raise AdapterError("Uniswap V2 has no creator fees: pool fees go to whoever holds the LP tokens "
                           "(nobody, if they were burned).")
