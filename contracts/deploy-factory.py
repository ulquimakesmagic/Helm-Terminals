"""One-shot deployment of the reviewed HelmPonsRewardsFactory bytecode to Robinhood Chain.

Deliberately outside the backend: nothing here runs in production or keeps the key longer than this
process. The factory has no constructor and takes no value, so deployment is a single create
transaction whose data is the exact creation bytecode from the checked-in artifact — the same bytes
`npm run check` pins. The deployer address never affects splitter addresses: those derive from
(factory, launchKey, init code hash) alone.

The script checks the chain id, simulates the creation before sending, requires a typed
confirmation, and after the receipt verifies keccak(eth_getCode) against the artifact's
deployedBytecode before reporting success. Re-running deploys a second factory; prefer reusing the
configured one. Usage:

    HELM_DEPLOYER_KEY=0x... ../backend/.venv/Scripts/python.exe deploy-factory.py --rpc-url <url>
    # or run without the env var to be prompted (input hidden); add --dry-run to stop before sending
"""
import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path

import httpx
from eth_account import Account
from eth_utils import keccak, to_checksum_address

ARTIFACT = Path(__file__).resolve().parent.parent / "backend/app/chains/contracts/HelmPonsRewardsFactory.json"
DEFAULT_CHAIN_ID = 4663  # Robinhood Chain
CONFIRM_WORD = "deploy"
RECEIPT_TIMEOUT_S = 120


def rpc(client: httpx.Client, url: str, method: str, params: list):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    response = client.post(url, json=body)
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise RuntimeError(f"{method}: {payload['error']}")
    return payload["result"]


def main(argv=None, transport=None, key: str | None = None, confirm: str | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deploy the reviewed HelmPonsRewardsFactory bytecode.")
    parser.add_argument("--rpc-url", required=True, help="JSON-RPC endpoint for the target chain")
    parser.add_argument("--chain-id", type=int, default=DEFAULT_CHAIN_ID,
                        help=f"expected chain id (default {DEFAULT_CHAIN_ID}); override only for a rehearsal")
    parser.add_argument("--dry-run", action="store_true", help="print and sign locally; never broadcast")
    args = parser.parse_args(argv)

    if not ARTIFACT.exists():
        print(f"artifact missing: {ARTIFACT}", file=sys.stderr)
        return 1
    artifact = json.loads(ARTIFACT.read_text())
    creation, runtime = bytes.fromhex(artifact["bytecode"]), bytes.fromhex(artifact["deployedBytecode"])

    client = httpx.Client(transport=transport, timeout=30.0)
    url = args.rpc_url
    chain_id = int(rpc(client, url, "eth_chainId", []), 16)
    if chain_id != args.chain_id:
        print(f"refusing: {url} reports chain id {chain_id}, expected {args.chain_id}", file=sys.stderr)
        return 1

    if key is None:
        key = os.environ.get("HELM_DEPLOYER_KEY") or getpass.getpass("Deployer private key (input hidden): ")
    try:
        acct = Account.from_key(key)
    except Exception as e:
        print(f"invalid deployer key: {type(e).__name__}", file=sys.stderr)
        return 1
    deployer = to_checksum_address(acct.address)

    nonce = int(rpc(client, url, "eth_getTransactionCount", [deployer, "pending"]), 16)
    gas_price = int(rpc(client, url, "eth_gasPrice", []), 16)
    create = {"from": deployer, "data": "0x" + creation.hex(), "value": "0x0"}
    try:
        gas = int(rpc(client, url, "eth_estimateGas", [create]), 16)
        simulated = rpc(client, url, "eth_call", [create, "latest"])
    except RuntimeError as e:
        print(f"refusing: creation simulation failed: {e}", file=sys.stderr)
        return 1
    if len(simulated) < 4:
        print("refusing: simulated creation returned no runtime code", file=sys.stderr)
        return 1

    tx = {"nonce": nonce, "gasPrice": gas_price, "gas": gas, "value": 0, "data": create["data"],
          "chainId": chain_id}
    max_cost_wei = gas * gas_price
    print(f"chain id        {chain_id}")
    print(f"deployer        {deployer} (nonce {nonce})")
    print(f"creation bytes  {len(creation)} (keccak {keccak(creation).hex()})")
    print(f"gas             {gas} @ {gas_price} wei  ~{max_cost_wei / 10**18:.6f} ETH max")
    signed = acct.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction

    if args.dry_run:
        print("DRY-RUN: transaction signed locally; nothing was broadcast.")
        return 0

    answer = (confirm if confirm is not None
              else input(f"Type '{CONFIRM_WORD}' to broadcast (anything else aborts): ")).strip()
    if answer != CONFIRM_WORD:
        print("aborted; nothing was sent.")
        return 2

    tx_hash = rpc(client, url, "eth_sendRawTransaction", ["0x" + raw.hex()])
    print(f"broadcast       {tx_hash}")
    receipt = None
    deadline = time.monotonic() + RECEIPT_TIMEOUT_S
    while receipt is None and time.monotonic() < deadline:
        time.sleep(2)
        receipt = rpc(client, url, "eth_getTransactionReceipt", [tx_hash])
    if receipt is None:
        print(f"no receipt after {RECEIPT_TIMEOUT_S}s; the transaction may still land — check the explorer "
              f"before ever re-running this script.", file=sys.stderr)
        return 1
    if receipt.get("status") != "0x1":
        print("deployment reverted on-chain; nothing was deployed.", file=sys.stderr)
        return 1

    address = to_checksum_address(receipt["contractAddress"])
    on_chain = bytes.fromhex(rpc(client, url, "eth_getCode", [address, "latest"]).removeprefix("0x"))
    if keccak(on_chain) != keccak(runtime):
        print(f"FAILED VERIFICATION: code at {address} does not match the artifact's deployedBytecode. "
              f"Do not configure this address.", file=sys.stderr)
        return 1

    print(f"deployed        {address}")
    print("verified        on-chain code matches the artifact byte for byte")
    print("next            set PONS_REWARDS_FACTORY to this address, then follow "
          "docs/runbooks/pons-activation.md before enabling PONS_REWARDS_ENABLED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
