"""Execute real Solidity bytecode in a local EVM; no live chain, wallet keys or funds.

The generalized splitter (contracts/HelmRewardsSplitter.sol) pays the chain's native coin and any
ERC-20 with the same immutable 94/6 accounting as the Pons splitter. These tests run its compiled
bytecode, plus a mock token, against counterfactual accrual, front-running, reentrancy and dust."""
import json
import random
from pathlib import Path

import pytest
from eth_abi import decode
from eth_tester import EthereumTester, PyEVMBackend
from eth_utils import to_canonical_address

from app.chains.pons_rewards import calldata

HELPERS = json.loads((Path(__file__).parent / "fixtures/reward-helpers.json").read_text())
FACTORY = json.loads((Path(__file__).parents[1] / "app/chains/contracts/HelmRewardsSplitterFactory.json").read_text())
NATIVE = "0x" + "00" * 20
KEY_TYPES = ["address", "address", "bytes32"]


@pytest.fixture
def chain():
    return EthereumTester(PyEVMBackend())


def send(chain, to=None, data=b"", value=0, sender=None, success=True):
    tx = {"from": sender or chain.get_accounts()[0], "gas": 6_000_000, "value": value, "data": "0x" + data.hex()}
    if to:
        tx["to"] = to
    receipt = chain.get_transaction_receipt(chain.send_transaction(tx))
    assert receipt["status"] == int(success), receipt
    return receipt


def read(chain, to, signature, types=(), values=(), out="uint256"):
    result = chain.call({"from": chain.get_accounts()[0], "to": to,
                         "data": "0x" + calldata(signature, types, values).hex()})
    return decode([out], bytes.fromhex(result[2:]))[0]


def token(chain):
    return send(chain, data=bytes.fromhex(HELPERS["MockERC20"]["bytecode"]))["contract_address"]


def mint(chain, tkn, to, amount):
    send(chain, tkn, calldata("mint(address,uint256)", ["address", "uint256"], [to, amount]))


def setup(chain, creator=None, treasury=None, key=b"1" * 32):
    factory = send(chain, data=bytes.fromhex(FACTORY["bytecode"]))["contract_address"]
    args = [creator or chain.get_accounts()[1], treasury or chain.get_accounts()[2], key]
    splitter = read(chain, factory, "predict(address,address,bytes32)", KEY_TYPES, args, "address")
    return factory, args, splitter


def distribute(chain, factory, args, asset, sender=None, success=True):
    send(chain, factory, calldata("distribute(address,address,bytes32,address)", [*KEY_TYPES, "address"],
                                  [*args, asset]), sender=sender, success=success)


def test_native_and_token_rewards_split_94_6_and_are_isolated(chain):
    creator, treasury = chain.get_accounts()[1], chain.get_accounts()[2]
    factory, args, splitter = setup(chain)
    tkn1, tkn2 = token(chain), token(chain)
    mint(chain, tkn1, splitter, 10**18)
    mint(chain, tkn2, splitter, 5 * 10**18)
    send(chain, splitter, value=10**18)  # receive(): native rewards land the same way
    eth_before = [chain.get_balance(a) for a in (creator, treasury)]
    distribute(chain, factory, args, NATIVE, sender=chain.get_accounts()[3])  # permissionless
    assert [chain.get_balance(a) - b for a, b in zip((creator, treasury), eth_before)] == [94 * 10**16, 6 * 10**16]
    distribute(chain, factory, args, tkn1)
    distribute(chain, factory, args, tkn2)
    for tkn, total in ((tkn1, 10**18), (tkn2, 5 * 10**18)):
        for who, share in ((creator, 94), (treasury, 6)):
            got = read(chain, tkn, "balanceOf(address)", ["address"], [who])
            assert got == total * share // 100
        assert read(chain, tkn, "balanceOf(address)", ["address"], [splitter]) == 0
    # Assets are tracked separately: a repeated distribution of any asset pays nothing more.
    before = [chain.get_balance(a) for a in (creator, treasury)]
    distribute(chain, factory, args, NATIVE)
    assert [chain.get_balance(a) for a in (creator, treasury)] == before
    assert read(chain, factory, "deploy(address,address,bytes32)", KEY_TYPES, args, "address") == splitter


def test_rewards_accrue_before_deployment_at_the_predicted_address(chain):
    factory, args, splitter = setup(chain)
    tkn = token(chain)
    assert chain.get_code(splitter) == "0x"
    mint(chain, tkn, splitter, 2 * 10**18)
    send(chain, splitter, value=10**18)
    send(chain, factory, calldata("deploy(address,address,bytes32)", KEY_TYPES, args))
    eth_before = chain.get_balance(args[1])
    distribute(chain, factory, args, NATIVE)
    distribute(chain, factory, args, tkn)
    assert read(chain, tkn, "balanceOf(address)", ["address"], [args[1]]) == 12 * 10**16  # 6% of both assets
    assert chain.get_balance(args[1]) - eth_before == 6 * 10**16


def test_substitution_and_replay_cannot_redirect_or_duplicate_payouts(chain):
    creator, treasury = chain.get_accounts()[1], chain.get_accounts()[2]
    factory, args, splitter = setup(chain)
    mint(chain, token(chain), splitter, 10**18)
    send(chain, splitter, value=10**18)
    send(chain, factory, calldata("deploy(address,address,bytes32)", KEY_TYPES, args))
    send(chain, factory, calldata("deploy(address,address,bytes32)", KEY_TYPES, args))  # idempotent
    eth_before = [chain.get_balance(a) for a in (creator, treasury)]
    distribute(chain, factory, args, NATIVE)
    distribute(chain, factory, args, NATIVE)  # a replayed distribution pays nothing the second time
    assert [chain.get_balance(a) - b for a, b in zip((creator, treasury), eth_before)] == [94 * 10**16, 6 * 10**16]
    for changed in ([creator, chain.get_accounts()[4], args[2]], [creator, treasury, b"2" * 32]):
        assert read(chain, factory, "predict(address,address,bytes32)", KEY_TYPES, changed, "address") != splitter
    send(chain, splitter, calldata("release(address,address)", ["address", "address"],
                                   [NATIVE, chain.get_accounts()[4]]), success=False)


def test_cumulative_rounding_and_donations_conserve_every_wei(chain):
    factory, args, splitter = setup(chain)
    tkn = token(chain)
    rng, totals = random.Random(7), {NATIVE: 0, tkn: 0}
    for i in range(30):
        amount = 1 if i < 10 else rng.randrange(1, 10**15)
        for asset in (NATIVE, tkn):
            totals[asset] += amount
            if asset == NATIVE:
                send(chain, splitter, value=amount)
            else:
                mint(chain, tkn, splitter, amount)
        distribute(chain, factory, args, NATIVE)
        distribute(chain, factory, args, tkn)
        for asset in (NATIVE, tkn):
            helm = read(chain, splitter, "released(address,address)", ["address", "address"], [asset, args[1]])
            assert helm == totals[asset] * 600 // 10000
            creator = read(chain, splitter, "released(address,address)", ["address", "address"], [asset, args[0]])
            assert creator + helm == totals[asset]
    assert chain.get_balance(splitter) == 0 and read(chain, tkn, "balanceOf(address)", ["address"], [splitter]) == 0


@pytest.mark.parametrize("reject_creator", [True, False])
def test_rejecting_native_beneficiary_does_not_block_the_other(chain, reject_creator):
    reject = send(chain, data=bytes.fromhex(HELPERS["RejectEther"]["bytecode"]))["contract_address"]
    factory, args, splitter = setup(chain, creator=reject if reject_creator else None,
                                    treasury=None if reject_creator else reject)
    send(chain, splitter, value=10000)
    distribute(chain, factory, args, NATIVE)
    assert read(chain, splitter, "released(address,address)", ["address", "address"],
                [NATIVE, args[0]]) == (0 if reject_creator else 9400)
    assert read(chain, splitter, "released(address,address)", ["address", "address"],
                [NATIVE, args[1]]) == (600 if reject_creator else 0)
    assert chain.get_balance(splitter) == (9400 if reject_creator else 600)
    # The deferred party can still be paid later directly once they accept funds; a rejecting one reverts.
    send(chain, splitter, calldata("release(address,address)", ["address", "address"],
                                   [NATIVE, args[0] if reject_creator else args[1]]), success=False)


def test_reentrant_recipient_cannot_double_withdraw(chain):
    attacker = send(chain, data=bytes.fromhex(HELPERS["Reenter"]["bytecode"]))["contract_address"]
    factory, args, splitter = setup(chain, creator=attacker)
    payload = calldata("release(address,address)", ["address", "address"], [NATIVE, attacker])
    send(chain, attacker, calldata("setTarget(address)", ["address"], [splitter]))
    send(chain, attacker, calldata("setPayload(bytes)", ["bytes"], [payload]))
    send(chain, splitter, value=10000)
    distribute(chain, factory, args, NATIVE)
    assert chain.get_balance(attacker) == 9400 and chain.get_balance(splitter) == 0
    assert read(chain, splitter, "totalReleased(address)", ["address"], [NATIVE]) == 10000


def test_zero_or_equal_recipients_rejected(chain):
    factory, args, _ = setup(chain)
    for invalid in ([args[0], args[0], args[2]], [args[0], "0x" + "00" * 20, args[2]]):
        send(chain, factory, calldata("deploy(address,address,bytes32)", KEY_TYPES, invalid), success=False)
