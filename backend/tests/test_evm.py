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


