"""Counterfactual, per-launch native reward recipients; pinned, non-upgradeable factory.

No runtime compilation, private keys, custody, remote transaction builders or arbitrary fee addresses.
The immutable factory embeds the splitter creation code; anyone can materialize a recipient and pay it out.
"""
import json
from pathlib import Path

from eth_abi import encode
from eth_utils import is_address, keccak, to_checksum_address

from .base import AdapterError
from .evm import EvmRPCError, EvmRPCUnavailable

ARTIFACTS = Path(__file__).parent / "contracts"
FACTORY_ARTIFACT = json.loads((ARTIFACTS / "HelmPonsRewardsFactory.json").read_text())
SPLITTER_ARTIFACT = json.loads((ARTIFACTS / "HelmPonsRewards.json").read_text())
FACTORY_CODE = bytes.fromhex(FACTORY_ARTIFACT["deployedBytecode"])
ESCROW = "0xd3afeb2a57f70ef218aa82451c51b2fb0416ac9e"
PONS_FACTORY = "0x7eD598BcEf8bd9Edd8C97A195C6d13f40801EC7e"
# Sourcify verified on-chain runtime, checked 2026-10-02. Unexpected code fails closed.
ESCROW_CODE_HASH = "f25f75cfbc1637ba068dc34f69098fa4e8a80f8ee8fe7bf7820594e0b3fed2f1"
PONS_CODE_HASH = "89a27da6f703e0a7cdd4f233e7cb57604ff75b164530962d3ff7cf8483a67d84"
RELEASED = "0x" + keccak(text="Released(address,uint256)").hex()


def calldata(signature, types=(), values=()):
    return keccak(text=signature)[:4] + encode(types, values)


def predicted(factory: str, creator: str, treasury: str, key: bytes) -> str:
    init = bytes.fromhex(SPLITTER_ARTIFACT["bytecode"]) + encode(["address", "address"], [creator, treasury])
    return to_checksum_address(keccak(b"\xff" + bytes.fromhex(factory[2:]) + key + keccak(init))[-20:])


def configuration(launch_id: str, creator: str, share: dict, factory: str) -> dict:
    treasury = share.get("recipient", "")
    if share.get("bps") != 600 or any(not is_address(a) or int(a, 16) == 0 for a in (factory, creator, treasury)):
        raise AdapterError("Pons rewards require a verified factory, creator, treasury and exactly 6% Helm share")
    if creator.lower() == treasury.lower():
        raise AdapterError("Pons creator and treasury must be different")
    key = keccak(text=f"helm:pons:4663:{launch_id}")
    return {"version": 1, "factory": to_checksum_address(factory), "creator": to_checksum_address(creator),
            "treasury": to_checksum_address(treasury), "bps": 600, "launch_key": "0x" + key.hex(),
            "splitter": predicted(factory, creator, treasury, key)}


def validate(config: dict) -> None:
    try:
        addresses = [config[k] for k in ("factory", "creator", "treasury", "splitter")]
        key = bytes.fromhex(config["launch_key"].removeprefix("0x"))
        valid = (config["version"] == 1 and config["bps"] == 600 and len(key) == 32
                 and all(is_address(a) and int(a, 16) != 0 for a in addresses)
                 and addresses[1].lower() != addresses[2].lower()
                 and predicted(*addresses[:3], key).lower() == addresses[3].lower())
    except (KeyError, ValueError, TypeError):
        valid = False
    if not valid:
        raise AdapterError("Invalid saved Pons reward configuration; no transaction was prepared")


async def verify(rpc, config: dict) -> None:
    validate(config)
    if rpc.network != "robinhood":
        raise AdapterError("Pons rewards are only supported on Robinhood Chain")
    for address, expected in ((config["factory"], keccak(FACTORY_CODE).hex()),
                              (ESCROW, ESCROW_CODE_HASH), (PONS_FACTORY, PONS_CODE_HASH)):
        try:
            code = await rpc.call("eth_getCode", [address, "latest"])
        except (EvmRPCError, EvmRPCUnavailable) as e:
            raise AdapterError("Pons reward contracts could not be verified; retry when the RPC is available") from e
        if keccak(bytes.fromhex(code.removeprefix("0x"))).hex() != expected:
            raise AdapterError("Pons reward contract code is missing or does not match the reviewed build")


def distribution_data(config: dict) -> bytes:
    validate(config)
    return calldata("distribute(address,address,bytes32)", ["address", "address", "bytes32"],
                    [config["creator"], config["treasury"], bytes.fromhex(config["launch_key"][2:])])


def treasury_released(config: dict, logs: list) -> int:
    """Only successful transfer events from this exact splitter count; never estimates or balances."""
    validate(config)
    amount = 0
    for log in logs:
        topics = log.get("topics") or []
        if (log.get("address", "").lower() == config["splitter"].lower() and len(topics) == 2
                and topics[0].lower() == RELEASED.lower()
                and topics[1].lower() == "0x" + "0" * 24 + config["treasury"][2:].lower()
                and len(log.get("data", "")) == 66 and not log.get("removed")):
            amount += int(log["data"], 16)
    return amount
