"""The factory deployment script, exercised end to end against a mock RPC; no network, no real key.

The script broadcasts real funds in production, so its gates are tested here: chain-id refusal,
simulation refusal, mandatory typed confirmation, dry-run, and the post-deploy verification that
compares on-chain code against the checked-in artifact."""
import importlib.util
import json
import time
from pathlib import Path

import httpx

SCRIPT = Path(__file__).parents[2] / "contracts" / "deploy-factory.py"
_spec = importlib.util.spec_from_file_location("deploy_factory", SCRIPT)
deploy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(deploy)

ARTIFACT = json.loads((Path(__file__).parents[2] / "backend" / "app" / "chains" / "contracts"
                       / "HelmPonsRewardsFactory.json").read_text())
RUNTIME = "0x" + ARTIFACT["deployedBytecode"]
KEY = "0x" + "11" * 32  # a valid secp256k1 scalar; never used on any real network
RPC_URL = "http://mock-rpc.invalid"
DEPLOYED_AT = "0x" + "fa" * 19 + "c7"


class Node:
    """Scripted JSON-RPC node; records broadcast attempts so tests can assert nothing was sent."""

    def __init__(self, chain_id=0x1237, code=RUNTIME, simulate="0x6080"):
        self.chain_id, self.code, self.simulate = chain_id, code, simulate
        self.broadcasts: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        method = body["method"]
        if method == "eth_sendRawTransaction":
            self.broadcasts.append(body["params"][0])
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x" + "ab" * 32})
        if method == "eth_getTransactionReceipt":
            # The script only polls after broadcasting; a receipt with the deployed contract ends the wait.
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                             "result": {"status": "0x1", "contractAddress": DEPLOYED_AT}})
        result = {"eth_chainId": hex(self.chain_id),
                  "eth_getTransactionCount": "0x5",
                  "eth_gasPrice": "0x3b9aca00",
                  "eth_estimateGas": "0x927c0",
                  "eth_call": self.simulate,
                  "eth_getCode": self.code}[method]
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})


def run(node: Node, key=KEY, confirm="deploy", extra=()) -> int:
    return deploy.main(["--rpc-url", RPC_URL, *extra], transport=httpx.MockTransport(node.handler),
                       key=key, confirm=confirm)


def test_deployment_broadcasts_once_and_verifies_on_chain_code(capsys, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    node = Node()
    code = run(node)
    out = capsys.readouterr().out
    assert code == 0, out
    assert len(node.broadcasts) == 1
    assert "verified" in out and DEPLOYED_AT in out.lower()  # the script prints the checksummed address


def test_aborted_confirmation_never_broadcasts(capsys, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    node = Node()
    code = run(node, confirm="no")
    assert code == 2 and node.broadcasts == []
    assert "aborted" in capsys.readouterr().out


def test_dry_run_signs_but_never_broadcasts(capsys, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    node = Node()
    code = run(node, extra=("--dry-run",))
    assert code == 0 and node.broadcasts == []
    assert "DRY-RUN" in capsys.readouterr().out


def test_wrong_chain_is_refused_before_any_broadcast(capsys):
    node = Node(chain_id=1)
    code = run(node)
    assert code == 1 and node.broadcasts == []
    assert "refusing" in capsys.readouterr().err


def test_empty_simulation_is_refused(capsys):
    node = Node(simulate="")  # a creation call that returns no runtime code
    code = run(node)
    assert code == 1 and node.broadcasts == []
    assert "simulated" in capsys.readouterr().err


def test_mismatched_deployed_code_fails_verification(capsys, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    node = Node(code="0xdeadbeef")
    code = run(node)
    captured = capsys.readouterr()
    assert code == 1 and len(node.broadcasts) == 1
    assert "FAILED VERIFICATION" in captured.out + captured.err
