"""EVM JSON-RPC client for Base, BNB Chain, Ethereum and Robinhood Chain.

Each chain takes one or more RPC URLs (comma-separated, e.g. several Alchemy/QuickNode/Infura keys). Requests
rotate across them with the same rules as the Solana client: a rate-limited, failing or key-rejected endpoint is
rested and the next one is used; a JSON-RPC error is the node's real answer and is not retried.

Safety: before an endpoint is used for the first time, its chain ID is checked. A URL that points at the wrong
chain (an easy copy-paste mistake) is refused for an hour, so Helm never reads balances or sends transactions
on the wrong network. Errors name endpoints by position only; URLs (which often embed API keys) never appear.
"""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from ..keyring import KeyRing, parse_list, retry_after

# chain id, native coin, and a public fallback RPC used when no URL is configured (fine for testing only)
EVM_CHAINS: dict[str, dict] = {
    "base": {"chain_id": 8453, "native": "ETH", "name": "Base", "public_rpc": "https://mainnet.base.org"},
    "bsc": {"chain_id": 56, "native": "BNB", "name": "BNB Chain", "public_rpc": "https://bsc-dataseed.bnbchain.org"},
    "ethereum": {"chain_id": 1, "native": "ETH", "name": "Ethereum", "public_rpc": "https://ethereum-rpc.publicnode.com"},
    "robinhood": {"chain_id": 4663, "native": "ETH", "name": "Robinhood Chain",
                  "public_rpc": "https://rpc.mainnet.chain.robinhood.com"},
}
WEI = 10**18


def evm_config(network: str) -> dict:
    """Chain settings for Helm's EVM chains and for EVM networks from the extra-network registry (Arc)."""
    if network in EVM_CHAINS:
        return EVM_CHAINS[network]
    from .networks import EXTRA_NETWORKS

    n = EXTRA_NETWORKS.get(network)
    if n is None or n.family != "evm":
        raise ValueError(f"unknown EVM network {network}")
    return {"chain_id": n.chain_id, "native": n.native, "name": n.name, "public_rpc": n.default_rpc}


class EvmRPCError(Exception):
    """The node answered with a JSON-RPC error (e.g. execution reverted). `data` holds any revert data (hex)."""

    def __init__(self, message: str, data: str | None = None) -> None:
        super().__init__(message)
        self.data = data if isinstance(data, str) and data.startswith("0x") else None


class EvmRPCUnavailable(Exception):
    """No endpoint for this chain gave a usable answer. Never contains a URL."""


# Replies meaning "this endpoint won't run that method" (a provider plan limit or a disabled method), not "the call
# itself failed". Those move on to the next endpoint instead of being returned as the answer.
_PLAN_LIMIT_HINTS = ("paid plan", "upgrade your", "method not supported", "is not supported", "not whitelisted",
                     "method not allowed", "unsupported method", "does not exist/is not available")


def _plan_limited(err: dict) -> bool:
    if err.get("code") == 3:  # execution reverted: a real answer from the contract, whatever its message says
        return False
    if err.get("code") == -32601:  # JSON-RPC "method not found"
        return True
    msg = str(err.get("message", "")).lower()
    return any(h in msg for h in _PLAN_LIMIT_HINTS)


def _hex(n: int) -> str:
    return hex(n)


def _pad_address(addr: str) -> str:
    return addr.lower().removeprefix("0x").rjust(64, "0")


def _decode_string(data: str) -> str:
    """ABI-decode a `string` return value (also tolerates old bytes32-style tokens)."""
    raw = bytes.fromhex(data.removeprefix("0x"))
    if len(raw) == 32:
        return raw.rstrip(b"\x00").decode("utf-8", "replace")
    if len(raw) < 64:
        return ""
    length = int.from_bytes(raw[32:64], "big")
    return raw[64 : 64 + length].decode("utf-8", "replace")


_HEADER_KEY_PARAMS = ("xApiKey", "x-api-key", "x_api_key")


def split_header_key(url: str) -> tuple[str, dict[str, str]]:
    """Providers like Tatum want the key in an `x-api-key` header. Write it in the URL as
    `https://base-mainnet.gateway.tatum.io?xApiKey=KEY` and Helm moves it into the header."""
    parsed = urlsplit(url)
    if not parsed.query:
        return url, {}
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    key = next((v for k, v in pairs if k in _HEADER_KEY_PARAMS and v), None)
    if not key:
        return url, {}
    rest = urlencode([(k, v) for k, v in pairs if k not in _HEADER_KEY_PARAMS])
    return urlunsplit(parsed._replace(query=rest)), {"x-api-key": key}


class EvmRPC:
    def __init__(self, network: str, urls: str | list[str], client: httpx.AsyncClient | None = None) -> None:
        cfg = evm_config(network)
        self.network = network
        self.chain_id = cfg["chain_id"]
        items = parse_list(urls) or [cfg["public_rpc"]]
        self.ring = KeyRing(items, cooldown_s=30)
        self.ring.kind = f"{cfg['name']} RPC endpoint"
        self._client = client or httpx.AsyncClient(timeout=30)
        self._verified: set[str] = set()
        self.using_public_fallback = not parse_list(urls)
        self._unsupported: dict[str, set[str]] = {}  # endpoint -> methods its plan refuses
        # When none of your endpoints can run a method (e.g. eth_call on a free tier), the chain's public RPC is
        # tried last so reads still work.
        public = cfg["public_rpc"]
        self._last_resort = [] if public in items else [public]

    async def _post(self, url: str, method: str, params: list) -> httpx.Response:
        target, headers = split_header_key(url)
        return await self._client.post(target, headers=headers,
                                       json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})

    async def _verify(self, url: str) -> str | None:
        """Return a problem description if this endpoint isn't the chain it's configured for."""
        r = await self._post(url, "eth_chainId", [])
        if r.status_code != 200:
            return f"HTTP {r.status_code}"
        got = int(r.json().get("result", "0x0"), 16)
        if got != self.chain_id:
            return f"wrong chain (id {got}, expected {self.chain_id})"
        self._verified.add(url)
        return None

    async def call(self, method: str, params: list | None = None):
        params = params or []
        problems: list[str] = []
        order = self.ring.order()
        for url in order + self._last_resort:
            last_resort = url not in order
            if last_resort and not any("not on its plan" in p for p in problems):
                break  # the public endpoint is only for methods your endpoints refuse
            name = "public endpoint" if last_resort else self.ring.label(url)
            if method in self._unsupported.get(url, ()):
                problems.append(f"{name}: {method} not on its plan")
                continue
            try:
                if url not in self._verified:
                    bad = await self._verify(url)
                    if bad:
                        self.ring.fail(url, 3600 if "wrong chain" in bad else 30)
                        problems.append(f"{name}: {bad}")
                        continue
                r = await self._post(url, method, params)
            except (httpx.HTTPError, ValueError) as e:
                self.ring.fail(url)
                problems.append(f"{name}: {type(e).__name__}")
                continue
            if r.status_code == 429 or r.status_code >= 500:
                self.ring.fail(url, retry_after(r.headers, 30 if r.status_code == 429 else 15))
                problems.append(f"{name}: HTTP {r.status_code}")
                continue
            if r.status_code in (401, 403):
                self.ring.fail(url, 600)
                problems.append(f"{name}: HTTP {r.status_code} (key rejected)")
                continue
            if r.status_code != 200:
                self.ring.fail(url)
                problems.append(f"{name}: HTTP {r.status_code}")
                continue
            try:
                data = r.json()
            except ValueError:
                self.ring.fail(url)
                problems.append(f"{name}: invalid response")
                continue
            err = data.get("error")
            if err and (err.get("code") in (-32005, 429) or "rate" in str(err.get("message", "")).lower()):
                self.ring.fail(url, 30)  # some providers signal rate limits inside a 200 response
                problems.append(f"{name}: rate limited")
                continue
            if err and _plan_limited(err):
                self._unsupported.setdefault(url, set()).add(method)  # skip it for this method; it still serves others
                problems.append(f"{name}: {method} not on its plan")
                continue
            self.ring.ok(url)
            if err:
                raise EvmRPCError(f"{method}: {err.get('message', err)}", err.get("data"))
            return data.get("result")
        raise EvmRPCUnavailable(f"{method} failed on every {evm_config(self.network)['name']} RPC endpoint "
                                f"({'; '.join(problems)})")

    # --- reads ------------------------------------------------------------------------------

    async def get_chain_id(self) -> int:
        return int(await self.call("eth_chainId"), 16)

    async def get_balance(self, address: str) -> int:
        """Native balance in wei."""
        return int(await self.call("eth_getBalance", [address, "latest"]), 16)

    async def block_number(self) -> int:
        return int(await self.call("eth_blockNumber"), 16)

    async def eth_call(self, to: str, data: str, sender: str | None = None) -> str:
        tx = {"to": to, "data": data, **({"from": sender} if sender else {})}
        return await self.call("eth_call", [tx, "latest"])

    async def erc20_balance(self, token: str, owner: str) -> int:
        return int(await self.eth_call(token, "0x70a08231" + _pad_address(owner)) or "0x0", 16)

    async def erc20_decimals(self, token: str) -> int:
        """decimals() alone: one call instead of erc20_meta's four, for when that's all you need."""
        return int(await self.eth_call(token, "0x313ce567") or "0x0", 16)

    async def erc20_meta(self, token: str) -> dict:
        """name, symbol, decimals and total supply of an ERC-20 token."""
        name = _decode_string(await self.eth_call(token, "0x06fdde03"))
        symbol = _decode_string(await self.eth_call(token, "0x95d89b41"))
        decimals = int(await self.eth_call(token, "0x313ce567") or "0x0", 16)
        supply = int(await self.eth_call(token, "0x18160ddd") or "0x0", 16)
        return {"name": name, "symbol": symbol, "decimals": decimals, "total_supply": supply}

    async def erc20_allowance(self, token: str, owner: str, spender: str) -> int:
        data = "0xdd62ed3e" + _pad_address(owner) + _pad_address(spender)
        return int(await self.eth_call(token, data) or "0x0", 16)

    async def get_code(self, address: str) -> str:
        return await self.call("eth_getCode", [address, "latest"])

    # --- sending (used by EVM launchpad adapters) ----------------------------------------------

    async def nonce(self, address: str) -> int:
        return int(await self.call("eth_getTransactionCount", [address, "pending"]), 16)

    async def base_fee(self) -> int | None:
        """The latest block's base fee in wei, or None on chains without EIP-1559."""
        block = await self.call("eth_getBlockByNumber", ["latest", False])
        fee = (block or {}).get("baseFeePerGas")
        return int(fee, 16) if fee else None

    async def gas_price(self) -> int:
        return int(await self.call("eth_gasPrice"), 16)

    async def max_priority_fee(self) -> int:
        try:
            return int(await self.call("eth_maxPriorityFeePerGas"), 16)
        except EvmRPCError:
            return 0

    async def estimate_gas(self, tx: dict) -> int:
        return int(await self.call("eth_estimateGas", [tx]), 16)

    async def send_raw(self, raw_hex: str) -> str:
        return await self.call("eth_sendRawTransaction", [raw_hex])

    async def receipt(self, tx_hash: str) -> dict | None:
        return await self.call("eth_getTransactionReceipt", [tx_hash])


_shared: dict[tuple[str, tuple[str, ...]], EvmRPC] = {}


def evm_rpc_urls(network: str) -> str:
    from ..config import get_settings

    if network not in EVM_CHAINS:  # Arc and other registry networks: {NETWORK}_RPC_URL
        return getattr(get_settings(), f"{network.replace('-', '_')}_rpc_url", "") or ""
    return getattr(get_settings(), f"evm_rpc_{network}", "") or ""


def shared_evm(network: str) -> EvmRPC:
    """One client per chain and URL list, so rotation state and chain-ID checks persist across requests."""
    urls = evm_rpc_urls(network)
    key = (network, tuple(parse_list(urls)))
    if key not in _shared:
        _shared[key] = EvmRPC(network, urls)
    return _shared[key]
