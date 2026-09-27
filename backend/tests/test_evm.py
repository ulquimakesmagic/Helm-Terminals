"""EVM RPC client: key rotation, wrong-chain guard, ERC-20 reads, and balances for EVM wallets."""

import httpx
import pytest

from app.chains.evm import EvmRPC, EvmRPCError, EvmRPCUnavailable, _decode_string


def rpc_handler(chain_ids: dict[str, int], results: dict[str, object], fail: dict[str, int] | None = None, log=None):
    def handler(req: httpx.Request) -> httpx.Response:
        host = req.url.host
        body = __import__("json").loads(req.content)
        if log is not None:
            log.append((host, body["method"]))
        if fail and host in fail:
            return httpx.Response(fail[host])
        if body["method"] == "eth_chainId":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": hex(chain_ids[host])})
        res = results[body["method"]]
        if isinstance(res, dict) and "error" in res:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, **res})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": res})
    return handler


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_refuses_an_endpoint_that_is_the_wrong_chain():
    """A Base URL pasted into the Robinhood setting must never be used for Robinhood."""
    log: list = []
    h = rpc_handler({"base.test": 8453, "rh.test": 4663}, {"eth_getBalance": hex(5 * 10**18)}, log=log)
    rpc = EvmRPC("robinhood", "https://base.test/k=SECRET, https://rh.test/k=SECRET2", client=client(h))
    assert await rpc.get_balance("0x" + "11" * 20) == 5 * 10**18
    assert ("base.test", "eth_getBalance") not in log  # the wrong-chain endpoint was only asked its chain id
    log.clear()
    await rpc.get_balance("0x" + "11" * 20)
    assert [x for x in log if x[0] == "base.test"] == []  # and it stays benched

    only_wrong = EvmRPC("robinhood", "https://base.test/k=SECRET", client=client(h))
    with pytest.raises(EvmRPCUnavailable) as e:
        await only_wrong.get_balance("0x" + "11" * 20)
    assert "wrong chain (id 8453, expected 4663)" in str(e.value) and "SECRET" not in str(e.value)


async def test_rotates_past_rate_limits_and_keeps_node_errors():
    log: list = []
    h = rpc_handler({"a.test": 56, "b.test": 56}, {"eth_blockNumber": "0x10", "eth_call": {"error": {"code": 3, "message": "execution reverted"}}},
                    fail={"a.test": 429}, log=log)
    rpc = EvmRPC("bsc", ["https://a.test", "https://b.test"], client=client(h))
    assert await rpc.block_number() == 16
    log.clear()
    with pytest.raises(EvmRPCError, match="reverted"):
        await rpc.eth_call("0x" + "22" * 20, "0x")
    assert [x for x in log if x[1] == "eth_call"] == [("b.test", "eth_call")]  # not retried on another key


async def test_rate_limit_inside_a_200_response_also_rotates():
    calls = {"n": 0}

    def h(req):
        body = __import__("json").loads(req.content)
        if body["method"] == "eth_chainId":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"})
        calls["n"] += 1
        if req.url.host == "x.test":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32005, "message": "rate limit exceeded"}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2a"})

    rpc = EvmRPC("ethereum", "https://x.test,https://y.test", client=client(h))
    assert await rpc.block_number() == 42


async def test_erc20_metadata_and_balance_decoding():
    def abi_string(s: str) -> str:
        b = s.encode()
        return "0x" + (32).to_bytes(32, "big").hex() + len(b).to_bytes(32, "big").hex() + b.ljust(32, b"\0").hex()

    answers = {"0x06fdde03": abi_string("Helm Cat"), "0x95d89b41": abi_string("HCAT"),
               "0x313ce567": hex(18), "0x18160ddd": hex(10**27), "0x70a08231": hex(123)}

    def h(req):
        body = __import__("json").loads(req.content)
        if body["method"] == "eth_chainId":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": hex(8453)})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": answers[body["params"][0]["data"][:10]]})

    rpc = EvmRPC("base", "https://base.test", client=client(h))
    assert await rpc.erc20_meta("0x" + "33" * 20) == {"name": "Helm Cat", "symbol": "HCAT", "decimals": 18, "total_supply": 10**27}
    assert await rpc.erc20_balance("0x" + "33" * 20, "0x" + "44" * 20) == 123
    assert _decode_string("0x" + b"MKR".ljust(32, b"\0").hex()) == "MKR"  # old bytes32-style token


def test_blank_config_falls_back_to_the_public_endpoint():
    rpc = EvmRPC("robinhood", "")
    assert rpc.using_public_fallback and rpc.ring.items == ["https://rpc.mainnet.chain.robinhood.com"]
    with pytest.raises(ValueError):
        EvmRPC("solana", "https://x")




async def test_header_key_moved_out_of_url():
    import httpx
    from app.chains.evm import EvmRPC, split_header_key

    assert split_header_key("https://x.io") == ("https://x.io", {})
    assert split_header_key("https://base-mainnet.gateway.tatum.io?xApiKey=K1") == (
        "https://base-mainnet.gateway.tatum.io", {"x-api-key": "K1"})
    assert split_header_key("https://a.io/rpc?foo=1&x-api-key=K2") == ("https://a.io/rpc?foo=1", {"x-api-key": "K2"})

    seen = []

    def handler(req: httpx.Request):
        seen.append((str(req.url), req.headers.get("x-api-key")))
        body = req.read().decode()
        result = hex(8453) if "eth_chainId" in body else hex(5)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    rpc = EvmRPC("base", "https://base-mainnet.gateway.tatum.io?xApiKey=SECRET",
                 client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await rpc.get_balance("0x" + "1" * 40) == 5
    assert all(u == "https://base-mainnet.gateway.tatum.io" and k == "SECRET" for u, k in seen)


async def test_endpoint_whose_plan_refuses_a_method_is_skipped_for_that_method_only():
    """A free-tier key that refuses eth_call ("available for paid plans only") mustn't break balance reads."""
    import json

    log: list = []
    paid = {"code": -32000, "message": "Method 'eth_call' is available for paid plans only. To access this feature, "
                                       "please upgrade your subscription at https://provider.test"}

    def handler(req: httpx.Request) -> httpx.Response:
        host, body = req.url.host, json.loads(req.content)
        log.append((host, body["method"]))
        if body["method"] == "eth_chainId":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": hex(4663)})
        if body["method"] == "eth_call" and host in ("free.test", "free2.test"):
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": paid})
        if body["method"] == "eth_call":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": hex(7 * 10**6)})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": hex(10**18)})

    owner, token = "0x" + "11" * 20, "0x" + "22" * 20
    rpc = EvmRPC("robinhood", ["https://free.test", "https://full.test"], client=client(handler))
    for _ in range(3):  # whichever endpoint the rotation starts at, the answer comes from the one that can serve it
        assert await rpc.erc20_balance(token, owner) == 7 * 10**6
    assert sum(1 for x in log if x == ("free.test", "eth_call")) == 1  # asked once, then skipped for eth_call
    log.clear()
    await rpc.get_balance(owner)
    await rpc.get_balance(owner)
    assert ("free.test", "eth_getBalance") in log  # still used for everything its plan allows

    # Every configured endpoint refuses: the chain's public RPC answers instead of failing the read.
    import app.chains.evm as evm_mod
    only_free = EvmRPC("robinhood", ["https://free.test", "https://free2.test"], client=client(handler))
    only_free._last_resort = ["https://public.test"]
    assert await only_free.erc20_balance(token, owner) == 7 * 10**6
    assert evm_mod._plan_limited(paid) and not evm_mod._plan_limited({"code": 3, "message": "execution reverted"})
