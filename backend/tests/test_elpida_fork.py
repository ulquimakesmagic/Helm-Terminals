"""Opt-in mainnet-fork tests. Only an isolated Anvil instance can send transactions."""
import asyncio
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

import httpx
import pytest
from eth_abi import encode
from eth_utils import keccak

from app.chains.base import CreateRequest, EvmTradeRequest
from app.chains.elpida import ElpidaAdapter, LAUNCHED, METADATA_PUBLISHED
from app.chains.evm import EVM_CHAINS, EvmRPC


class PinnedArtwork:
    async def pin_file(self, data, name):
        return "bafkreifakeforkimagecid"

    async def pin_bytes_json(self, data, name):
        return "bafkreifakeforkmetadatacid"


@pytest.mark.parametrize("network", ["base", "bsc", "robinhood", "ethereum"])
async def test_elpida_launch_buy_sell_on_mainnet_fork(network):
    chain_id = EVM_CHAINS[network]["chain_id"]
    source = os.environ.get(f"HELM_RPC_{chain_id}")
    binary = os.environ.get("HELM_ANVIL_BIN") or shutil.which("anvil")
    if not source or not binary:
        pytest.skip(f"Set HELM_RPC_{chain_id} and install Anvil to exercise this mainnet fork")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    local = f"http://127.0.0.1:{port}"
    with subprocess.Popen([str(Path(binary)), "--fork-url", source, "--host", "127.0.0.1",
                           "--port", str(port), "--silent"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as anvil:
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                rpc = EvmRPC(network, local, client=client)

                async def local_call(method, params):
                    response = await client.post(local, json={"jsonrpc": "2.0", "id": 1,
                                                              "method": method, "params": params})
                    result = response.json()
                    assert "error" not in result, f"{method} failed on the local fork ({result.get('error', {}).get('code')})"
                    return result["result"]

                deadline = time.monotonic() + 55
                while anvil.poll() is None:
                    try:
                        assert await local_call("eth_chainId", []) == hex(chain_id)
                        break
                    except httpx.TransportError:
                        if time.monotonic() > deadline:
                            pytest.fail("Local Anvil fork did not start")
                        await asyncio.sleep(0.25)
                else:
                    pytest.fail("Local Anvil fork exited before accepting requests")
                node = await local_call("web3_clientVersion", [])
                assert "anvil" in node.lower()
                assert rpc.using_public_fallback is False
                creator, trader = (await local_call("eth_accounts", []))[:2]
                adapter = ElpidaAdapter(network, rpc, PinnedArtwork())

                async def execute(plan, wallet):
                    receipts = []
                    for step in plan.steps:
                        data = await step.build() if step.build else step.data
                        # The sender is an unlocked Anvil account and the only write endpoint is loopback.
                        tx = {"from": wallet, "to": step.to, "data": "0x" + data.hex(), "value": hex(step.value)}
                        tx["gas"] = hex(int(await local_call("eth_estimateGas", [tx]), 16) * 12 // 10)
                        tx_hash = await local_call("eth_sendTransaction", [tx])
                        for _ in range(20):
                            receipt = await local_call("eth_getTransactionReceipt", [tx_hash])
                            if receipt:
                                break
                            await asyncio.sleep(0.25)
                        assert receipt, "Anvil did not return a receipt for the local transaction"
                        assert receipt["status"] == "0x1"
                        receipts.append(receipt)
                    return receipts

                launch = CreateRequest("Helm Fork Test", "HFT", "", b"\x89PNG\r\n\x1a\n", {}, creator,
                                       dev_buy_sol=0.0002, slippage_pct=5)
                receipts = await execute(await adapter.prepare_create(launch), creator)
                logs = receipts[-1]["logs"]
                assert {LAUNCHED, METADATA_PUBLISHED} <= {log["topics"][0] for log in logs}
                token = await adapter.resolve_launch_token(logs)
                assert token
                first_balance = await rpc.erc20_balance(token, creator)
                assert 0 < first_balance <= 30_000_000 * 10**18

                # Wait out the opening fee window on the fork before checking both directions.
                await local_call("evm_increaseTime", [65])
                await local_call("evm_mine", [])
                amount = 10**14
                trader_before = await rpc.get_balance(trader)
                buy = await adapter.prepare_trade(EvmTradeRequest(token, trader, "buy", amount, 5))
                buy_receipts = await execute(buy, trader)
                bought = await rpc.erc20_balance(token, trader)
                assert bought > 0
                assert await rpc.get_balance(trader) < trader_before
                assert adapter.trade_fee_basis(buy_receipts[-1]["logs"], buy, trader, token, "buy") > 0
                sell = await adapter.prepare_trade(EvmTradeRequest(token, trader, "sell", bought, 5))
                sell_receipts = await execute(sell, trader)
                assert await rpc.erc20_balance(token, trader) == 0
                assert adapter.trade_fee_basis(sell_receipts[-1]["logs"], sell, trader, token, "sell") > 0
                assert await rpc.erc20_balance(token, creator) == first_balance

                stable = adapter.config.stable
                decimals = await rpc.erc20_decimals(stable)
                for wallet in (creator, trader):
                    # Locate balanceOf's storage reads from its execution trace. Writes and reversions
                    # below are confined to the fork and use only the ephemeral Anvil account.
                    data = "0x70a08231" + encode(["address"], [wallet]).hex()
                    trace = await local_call("debug_traceCall", [{"to": stable, "data": data}, "latest",
                                                                {"disableMemory": True, "disableStorage": True}])
                    for log in reversed(trace["structLogs"]):
                        if log["op"] != "SLOAD":
                            continue
                        slot = "0x" + log["stack"][-1].removeprefix("0x").zfill(64)
                        snapshot = await local_call("evm_snapshot", [])
                        balance = 100 * 10**decimals
                        await local_call("anvil_setStorageAt", [stable, slot, "0x" + balance.to_bytes(32, "big").hex()])
                        if await rpc.erc20_balance(stable, wallet) == balance:
                            break
                        await local_call("evm_revert", [snapshot])
                    else:
                        pytest.fail("Cannot seed the stablecoin balance on the fork")

                paid_in_stable = CreateRequest("Helm Stable Fork", "HSF", "", b"\x89PNG\r\n\x1a\n", {}, creator,
                                               dev_buy_sol=1, slippage_pct=5,
                                               options={"elpida_pair": "stable", "elpida_fee_payment": "stable"})
                charged = await execute(await adapter.prepare_create(paid_in_stable), creator)
                stable_token = await adapter.resolve_launch_token(charged[-1]["logs"])
                assert stable_token and await rpc.erc20_balance(stable_token, creator) > 0
                assert {LAUNCHED, METADATA_PUBLISHED} <= {log["topics"][0] for log in charged[-1]["logs"]}
                assert await rpc.erc20_balance(stable, creator) == 89 * 10**decimals
                assert "0x" + keccak(text="LaunchFeePaid(address,address,uint256,uint256)").hex() in {
                    log["topics"][0] for log in charged[-1]["logs"]}
                await local_call("evm_increaseTime", [65])
                await local_call("evm_mine", [])
                stable_buy = await adapter.prepare_trade(EvmTradeRequest(stable_token, trader, "buy", 10**decimals, 5))
                await execute(stable_buy, trader)
                stable_bought = await rpc.erc20_balance(stable_token, trader)
                assert stable_bought > 0
                assert await rpc.erc20_balance(stable, trader) == 99 * 10**decimals
                stable_sell = await adapter.prepare_trade(EvmTradeRequest(stable_token, trader, "sell", stable_bought, 5))
                await execute(stable_sell, trader)
                assert await rpc.erc20_balance(stable_token, trader) == 0
                assert await rpc.erc20_balance(stable, trader) > 99 * 10**decimals
        finally:
            anvil.terminate()
