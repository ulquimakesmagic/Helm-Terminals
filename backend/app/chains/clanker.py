"""Clanker v4 adapter (Base and Robinhood Chain), calling the Clanker factory's deployToken directly.

The deployment config mirrors the official clanker-sdk defaults (v4.2.x):
  - 100B supply, paired with WETH, one LP position from tick -230400 to -120000 (tick spacing 200), locked in
    Clanker's LP locker forever;
  - static pool fee of 1% on each side through Clanker's static-fee hook (Clanker takes 20% on top of LP fees);
  - Clanker's anti-sniper module: fees start at ~66.7% and decay to ~4.2% over the first 15 seconds;
  - LP rewards go to the dev wallet (100%), or, if Helm's reward share is set, a disclosed part to Helm's EVM
    fee wallet;
  - an optional dev buy through Clanker's ETH dev-buy extension, in the same transaction.
There's no bonding curve: tokens trade on their Uniswap V4 pool from the first block, so Helm routes trades
through the DEX aggregator. Creator rewards are claimed from Clanker's fee locker (in WETH and in the token).

Addresses: clanker-sdk `clankerConfigFor(chainId, "clanker_v4")`.
"""

import json
import os

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak, to_checksum_address

from .base import AdapterError, ClaimRequest, CreateRequest, EvmPlan, EvmStep, EvmTradeRequest
from .evm import EvmRPC, EvmRPCError
from .kyber import Graduated

ZERO = "0x0000000000000000000000000000000000000000"
CLANKER_CHAINS = {
    "base": {
        "chain_id": 8453, "factory": "0xE85A59c628F7d27878ACeB4bf3b35733630083a9",
        "locker": "0xffA37784D619F228D8B379d287a4D7282e500762", "devbuy": "0x1331f0788F9c08C8F38D52c7a1152250A9dE00be",
        "mev": "0xebB25BB797D82CB78E1bc70406b13233c0854413", "hook": "0xb429d62f8f3bFFb98CdB9569533eA23bF0Ba28CC",
        "fee_locker": "0xF3622742b1E446D92e45E22923Ef11C2fcD55D68", "weth": "0x4200000000000000000000000000000000000006",
    },
    "robinhood": {
        "chain_id": 4663, "factory": "0xD3f2cC1731b7Fd17f28798835C2E02f0a1839A94",
        "locker": "0xE4910b09709423aFafD995dD76D8A81CF9134A09", "devbuy": "0xa27b1986e5c7e5371Cb6507f87918fBD0302fF5a",
        "mev": "0xEA1Fe197dF140e5d88fC6B49f2d21Ea05092299e", "hook": "0x48B8F6AD3A1b4aA477314c9a23035b8F84dDe8cc",
        "fee_locker": "0x88db2340bE5991B2b5Fca2Baee39B5CE048Cd70c", "weth": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
    },
}
TICK_START, TICK_UPPER, TICK_SPACING = -230400, -120000, 200
POOL_FEE_UNI_BPS = 10_000  # 1% (Uniswap fee units: 1_000_000 = 100%)
SNIPER = (666777, 41673, 15)  # starting fee, ending fee (uni bps), seconds to decay
FEE_PREF_BOTH = 0

TOKEN_CONFIG_T = "(address,string,string,bytes32,string,string,string,uint256)"
POOL_CONFIG_T = "(address,address,int24,int24,bytes)"
LOCKER_CONFIG_T = "(address,address[],address[],uint16[],int24[],int24[],uint16[],bytes)"
MEV_CONFIG_T = "(address,bytes)"
EXTENSION_T = "(address,uint256,uint16,bytes)"
DEPLOYMENT_T = f"({TOKEN_CONFIG_T},{POOL_CONFIG_T},{LOCKER_CONFIG_T},{MEV_CONFIG_T},{EXTENSION_T}[])"
DEPLOY_SIG = f"deployToken({DEPLOYMENT_T})"
TOKEN_CREATED_TOPIC = "0x" + keccak(text=(
    "TokenCreated(address,address,address,string,string,string,string,string,int24,address,bytes32,address,"
    "address,address,uint256,address[])")).hex()


def selector(sig: str) -> bytes:
    return keccak(text=sig)[:4]


class ClankerAdapter:
    kind = "evm"
    quote_assets = ["native"]

    def __init__(self, launchpad_id: str, network: str, rpc: EvmRPC, platform_wallet: str = "",
                 platform_reward_bps: int = 0) -> None:
        if network not in CLANKER_CHAINS:
            raise ValueError(f"Clanker isn't configured for {network}")
        self.id = launchpad_id
        self.network = network
        self.rpc = rpc
        self.cfg = CLANKER_CHAINS[network]
        # Helm's share of LP rewards (bps of the creator side), only when a platform wallet is set. Max 20%.
        self.platform_wallet = platform_wallet
        self.platform_reward_bps = min(max(platform_reward_bps, 0), 2000) if platform_wallet else 0

    def options_schema(self) -> dict:
        return {
            "fields": [
                {"key": "dev_buy_sol", "type": "number", "min": 0, "label": "Dev buy (ETH)", "default": 0},
                {"key": "slippage_pct", "type": "number", "min": 0, "max": 50, "default": 10},
            ],
            "socials": ["twitter", "telegram", "website"],
            "requires_image": False,
        }

    def deploy_notes(self, params: dict) -> dict:
        creator = 100 - self.platform_reward_bps / 100
        notes = {
            "pool": "Uniswap V4 pool paired with WETH, liquidity locked in Clanker's locker for good; no bonding curve",
            "pool_fee": "1% per trade; Clanker takes 20% on top of the LP fees",
            "sniper_fees": "about 67% for the first seconds, falling to about 4% over 15 seconds",
            "rewards": f"{creator:g}% of LP rewards to your dev wallet, claimable in WETH and the token",
        }
        if self.platform_reward_bps:
            notes["platform_share"] = f"{self.platform_reward_bps / 100:g}% of LP rewards to Helm"
        return notes

    def _config(self, req: CreateRequest, salt: bytes, dev_buy: int) -> tuple:
        s = req.socials or {}
        metadata = json.dumps({"description": req.description or "",
                               "socialMediaUrls": [{"platform": k, "url": s[k]} for k in ("twitter", "telegram", "website")
                                                   if s.get(k)]}, separators=(",", ":"))
        context = json.dumps({"interface": "Helm Terminals"}, separators=(",", ":"))
        token = (req.dev_pubkey, req.name, req.symbol, salt, (req.options or {}).get("logo_url") or "", metadata,
                 context, self.cfg["chain_id"])
        fee_data = abi_encode(["uint24", "uint24"], [POOL_FEE_UNI_BPS, POOL_FEE_UNI_BPS])
        pool = (self.cfg["hook"], self.cfg["weth"], TICK_START, TICK_SPACING,
                abi_encode(["(address,bytes,bytes)"], [(ZERO, b"", fee_data)]))
        recipients = [(req.dev_pubkey, req.dev_pubkey, 10_000 - self.platform_reward_bps)]
        if self.platform_reward_bps:
            recipients.append((self.platform_wallet, self.platform_wallet, self.platform_reward_bps))
        locker = (self.cfg["locker"], [r[0] for r in recipients], [r[1] for r in recipients],
                  [r[2] for r in recipients], [TICK_START], [TICK_UPPER], [10_000],
                  abi_encode(["(uint8[])"], [([FEE_PREF_BOTH] * len(recipients),)]))
        mev = (self.cfg["mev"], abi_encode(["(uint24,uint24,uint256)"], [SNIPER]))
        extensions = []
        if dev_buy:
            data = abi_encode(["(address,address,uint24,int24,address)", "uint128", "address"],
                              [(ZERO, ZERO, 0, 0, ZERO), 0, req.dev_pubkey])
            extensions.append((self.cfg["devbuy"], dev_buy, 0, data))
        return (token, pool, locker, mev, extensions)

    async def prepare_create(self, req: CreateRequest) -> EvmPlan:
        dev_buy = int(round(req.dev_buy_sol * 10**18))
        salt = os.urandom(32)
        cfg = self._config(req, salt, dev_buy)
        data = selector(DEPLOY_SIG) + abi_encode([DEPLOYMENT_T], [cfg])
        try:
            ret = await self.rpc.call("eth_call", [{"from": req.dev_pubkey, "to": self.cfg["factory"],
                                                    "value": hex(dev_buy), "data": "0x" + data.hex()}, "latest"])
        except EvmRPCError as e:
            raise AdapterError(f"Clanker launch dry run failed: {e}")
        predicted = to_checksum_address(abi_decode(["address"], bytes.fromhex(ret[2:]))[0])

        async def resolve(logs: list[dict]) -> str | None:
            for log in logs:
                t = log.get("topics") or []
                if log.get("address", "").lower() == self.cfg["factory"].lower() and len(t) > 1 \
                        and t[0].lower() == TOKEN_CREATED_TOPIC:
                    return to_checksum_address("0x" + t[1][-40:])
            return predicted

        return EvmPlan([EvmStep("launch", self.cfg["factory"], data, dev_buy)], token=predicted,
                       resolve_token=resolve, notes=self.deploy_notes({}))

    async def prepare_trade(self, req: EvmTradeRequest) -> EvmPlan:
        raise Graduated("Clanker tokens trade on their Uniswap V4 pool")

    async def _available(self, owner: str, fee_token: str) -> int:
        try:
            ret = await self.rpc.call("eth_call", [{"to": self.cfg["fee_locker"], "data": "0x" + (
                selector("availableFees(address,address)")
                + abi_encode(["address", "address"], [owner, fee_token])).hex()}, "latest"])
            return int(ret, 16) if ret and ret != "0x" else 0
        except EvmRPCError:
            return 0

    async def prepare_claim(self, req: ClaimRequest) -> EvmPlan:
        if not req.mint:
            raise AdapterError("this launch has no token yet")
        steps = [EvmStep("collect", self.cfg["locker"], selector("collectRewards(address)")
                         + abi_encode(["address"], [req.mint]), essential=False)]
        found = [(t, label, await self._available(req.wallet_pubkey, t))
                 for t, label in ((self.cfg["weth"], "WETH"), (req.mint, "token"))]

        # Claim each fee token the locker holds once the collect step has run; skip the ones with nothing in them.
        def claim_step(fee_token: str, label: str) -> EvmStep:
            data = selector("claim(address,address)") + abi_encode(["address", "address"], [req.wallet_pubkey, fee_token])

            async def build():
                return data if await self._available(req.wallet_pubkey, fee_token) else None
            return EvmStep(f"claim_{label.lower()}", self.cfg["fee_locker"], data, essential=False, build=build)

        steps += [claim_step(t, label) for t, label, _ in found]
        return EvmPlan(steps, token=req.mint, notes={"pending": {label: amt for _, label, amt in found}})
