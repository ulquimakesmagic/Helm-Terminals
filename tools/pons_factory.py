"""Prepare or verify the pinned Pons V2 factory. Never signs or sends transactions.

Run from any directory using Python 3.10+. Only the standard library is required.
The RPC URL is read from an environment variable and never written to output.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CHAIN_ID = 4663
ARTIFACT = ROOT / "backend/app/chains/contracts/HelmPonsRewardsV2Factory.json"


def rpc_call(url, method, params):
    request = urllib.request.Request(url, json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
    }).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.load(response)
        if "error" in body or "result" not in body:
            raise ValueError("RPC rejected the read-only request")
        return body["result"]
    except Exception:
        raise SystemExit(f"Read-only RPC request failed: {method}. Check the endpoint privately.") from None


def address(value):
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", value) or int(value, 16) == 0:
        raise argparse.ArgumentTypeError("Expected a nonzero public EVM address")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("--rpc-env", default="EVM_RPC_ROBINHOOD")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--from-address", type=address)
    parser.add_argument("--address", type=address)
    args = parser.parse_args()
    # Helm accepts comma-separated RPC endpoints. Use the first configured one;
    # do not silently switch providers while checking a deployment.
    url = os.environ.get(args.rpc_env, "").split(",")[0].strip()
    if not url.startswith(("https://", "http://127.0.0.1:", "http://localhost:")):
        parser.error("Set the RPC environment variable to an HTTPS endpoint (or localhost fork)")
    if int(rpc_call(url, "eth_chainId", []), 16) != CHAIN_ID:
        parser.error("Wrong chain. Expected Robinhood Chain MAINNET, chain ID 4663")
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    runtime = bytes.fromhex(artifact["deployedBytecode"].removeprefix("0x"))
    report = {"chainId": CHAIN_ID, "factoryRuntimeSha256": hashlib.sha256(runtime).hexdigest(),
              "checkedAt": datetime.now(timezone.utc).isoformat(), "signed": False, "broadcast": False}
    if args.action == "verify":
        if not args.address:
            parser.error("verify requires --address")
        code = bytes.fromhex(rpc_call(url, "eth_getCode", [args.address, "latest"]).removeprefix("0x"))
        if code != runtime:
            raise SystemExit("Factory runtime is missing or differs from the pinned V2 build. Do not enable it.")
        report.update(address=args.address, runtimeMatches=True)
    else:
        if not args.out:
            parser.error("prepare requires --out")
        tx = {"data": "0x" + artifact["bytecode"].removeprefix("0x"), "value": "0x0"}
        if args.from_address:
            tx["from"] = args.from_address
        gas = int(rpc_call(url, "eth_estimateGas", [tx]), 16)
        gas_price = int(rpc_call(url, "eth_gasPrice", []), 16)
        report.update(transaction=tx, estimatedGas=str(gas), currentGasPriceWei=str(gas_price),
                      estimatedFeeWei=str(gas * gas_price),
                      estimatedFeeETH=str(Decimal(gas * gas_price) / Decimal(10**18)),
                      note="Unsigned factory creation; no constructor arguments. Refresh nonce and fees in your wallet. No token launch is included.")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "transaction"}, indent=2))


if __name__ == "__main__":
    main()
