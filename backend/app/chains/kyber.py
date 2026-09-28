"""DEX trading on EVM chains through the KyberSwap Aggregator API.

Used for tokens that trade on a DEX rather than a launchpad's bonding curve: Flap and Pons tokens after they
graduate, and tokens launched straight onto Uniswap V2. KyberSwap finds a route across the chain's DEXes and
returns router calldata; Helm signs and sends it like any other call.

Flow: GET /{chain}/api/v1/routes -> POST /{chain}/api/v1/route/build (sender, recipient, slippage in bps).
Selling a token needs an ERC-20 approve for KyberSwap's router first. A Helm platform fee, when configured,
is charged by the router itself (feeAmount in bps, paid to Helm's EVM fee wallet) and shown in the preview.

Docs: docs.kyberswap.com, Aggregator API, EVM swaps.
"""

import httpx
from eth_abi import encode as abi_encode
from eth_utils import keccak

from .base import AdapterError, EvmPlan, EvmStep
from .evm import EvmRPC

NATIVE = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"
CHAIN_SLUGS = {"ethereum": "ethereum", "bsc": "bsc", "base": "base", "robinhood": "robinhood"}


class Graduated(AdapterError):
    """The token has left the launchpad's bonding curve; trade it on a DEX instead."""


class KyberSwap:
    def __init__(self, api_url: str = "https://aggregator-api.kyberswap.com", client_id: str = "HelmTerminals",
                 fee_bps: int = 0, fee_receiver: str = "", client: httpx.AsyncClient | None = None) -> None:
        self.api_url = api_url.rstrip("/")
        self.client_id = client_id
        self.fee_bps = fee_bps if fee_receiver else 0
        self.fee_receiver = fee_receiver
        self._client = client or httpx.AsyncClient(timeout=20)

    @property
    def fee_note(self) -> str:
        return f"{self.fee_bps / 100:g}% Helm fee, taken by the DEX router" if self.fee_bps else "none"

    async def _route(self, network: str, token_in: str, token_out: str, amount: int, charge_by: str) -> dict:
        slug = CHAIN_SLUGS.get(network)
        if not slug:
            raise AdapterError(f"DEX trading isn't available on {network}")
        params = {"tokenIn": token_in, "tokenOut": token_out, "amountIn": str(amount)}
        if self.fee_bps:
            params.update(feeAmount=str(self.fee_bps), feeReceiver=self.fee_receiver, isInBps="true",
                          chargeFeeBy=charge_by)
        try:
            r = await self._client.get(f"{self.api_url}/{slug}/api/v1/routes", params=params,
                                       headers={"X-Client-Id": self.client_id})
        except httpx.HTTPError as e:
            raise AdapterError(f"KyberSwap unreachable: {type(e).__name__}")
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code != 200 or not (data.get("data") or {}).get("routeSummary"):
            raise AdapterError(f"no DEX route for this token right now ({data.get('message') or r.status_code})")
        return data["data"]

    async def _build(self, network: str, route: dict, wallet: str, slippage_pct: float) -> dict:
        body = {"routeSummary": route["routeSummary"], "sender": wallet, "recipient": wallet,
                "slippageTolerance": int(max(0.0, min(slippage_pct, 20.0)) * 100), "source": self.client_id}
        try:
            r = await self._client.post(f"{self.api_url}/{CHAIN_SLUGS[network]}/api/v1/route/build", json=body,
                                        headers={"X-Client-Id": self.client_id})
        except httpx.HTTPError as e:
            raise AdapterError(f"KyberSwap unreachable: {type(e).__name__}")
        data = (r.json() if r.headers.get("content-type", "").startswith("application/json") else {}) or {}
        built = data.get("data") or {}
        if r.status_code != 200 or not built.get("data"):
            raise AdapterError(f"KyberSwap couldn't build the swap ({data.get('message') or r.status_code})")
        return built

    async def prepare_trade(self, network: str, rpc: EvmRPC, token: str, wallet: str, side: str, amount: int,
                            slippage_pct: float) -> EvmPlan:
        if amount <= 0:
            raise AdapterError("nothing to trade")
        token_in, token_out = (NATIVE, token) if side == "buy" else (token, NATIVE)
        # Charge Helm's fee in the native coin: on what goes in for buys, on what comes out for sells.
        charge = "currency_in" if side == "buy" else "currency_out"
        first = await self._route(network, token_in, token_out, amount, charge)
        router = first["routerAddress"]

        async def build() -> bytes:
            fresh = await self._route(network, token_in, token_out, amount, charge)  # a fresh price at send time
            built = await self._build(network, fresh, wallet, slippage_pct)
            if built.get("routerAddress", router).lower() != router.lower():
                raise AdapterError("KyberSwap changed router address between quote and swap; try again")
            return bytes.fromhex(built["data"].removeprefix("0x"))

        steps = []
        if side == "sell" and await rpc.erc20_allowance(token, wallet, router) < amount:
            steps.append(EvmStep("approve", token, keccak(text="approve(address,uint256)")[:4]
                                 + abi_encode(["address", "uint256"], [router, amount])))
        steps.append(EvmStep(side, router, b"", value=amount if side == "buy" else 0, build=build))
        notes = {"route": "KyberSwap", "expected_out": first["routeSummary"].get("amountOut")}
        if self.fee_bps:
            # Helm's cut, in native wei: exact on buys (a share of the amount going in); on sells an estimate from the
            # quoted output, which the router returns after its fee.
            if side == "buy":
                wei, estimated = amount * self.fee_bps // 10_000, False
            else:
                out = int(first["routeSummary"].get("amountOut") or 0)
                wei, estimated = out * self.fee_bps // (10_000 - self.fee_bps), True
            notes["helm_fee"] = {"bps": self.fee_bps, "receiver": self.fee_receiver, "wei": wei, "estimated": estimated}
        return EvmPlan(steps, token=token, notes=notes)
