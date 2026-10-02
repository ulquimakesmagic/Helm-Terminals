"""Guards on transactions Helm is about to sign (audit SEC-3): a compromised provider must not be
able to turn a confirmed spend into a drain. All checks run offline against fakes."""
from eth_utils import keccak
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from app.chains.solana import build_unsigned
from app.services.evm_exec import EvmCall, EvmSender
from app.services.tx_guard import check_evm_call, check_solana_signers

from .test_evm_wallets import FakeEvm

WALLET = "0x" + "a1" * 20
ROUTER = "0x" + "cd" * 20
ATTACKER = "0x" + "e5" * 20  # a plain address with no code
TOKEN = "0x" + "77" * 20


def approve_calldata(spender: str, amount: int) -> bytes:
    return keccak(b"approve(address,uint256)")[:4] + bytes.fromhex(spender[2:]).rjust(32, b"\x00") \
        + amount.to_bytes(32, "big")


def transfer_calldata(recipient: str, amount: int) -> bytes:
    return keccak(b"transfer(address,uint256)")[:4] + bytes.fromhex(recipient[2:]).rjust(32, b"\x00") \
        + amount.to_bytes(32, "big")


def rpc_with(broken: bool = False):
    node = FakeEvm("base")
    node.no_code.add(ATTACKER)  # the drain address is a plain address with no code
    if broken:
        async def fail(a):
            raise RuntimeError("rpc down")
        node.get_code = fail
    return node


async def test_approves_must_grant_contracts():
    assert await check_evm_call(rpc_with(), EvmCall(ROUTER, approve_calldata(ROUTER, 10**6)), WALLET) is None
    reason = await check_evm_call(rpc_with(), EvmCall(TOKEN, approve_calldata(ATTACKER, 10**6)), WALLET)
    assert reason and "not a contract" in reason


async def test_cannot_approve_the_sending_wallet():
    reason = await check_evm_call(rpc_with(), EvmCall(TOKEN, approve_calldata(WALLET, 10**6)), WALLET)
    assert reason and "sending wallet" in reason


async def test_provider_route_approvals_are_capped_at_the_quote():
    # An unlimited or over-quoted approval through a Relay step is refused even though the spender is a contract.
    node = rpc_with()
    assert await check_evm_call(node, EvmCall(TOKEN, approve_calldata(ROUTER, 10**6)), WALLET,
                                max_allowance=10**6) is None
    reason = await check_evm_call(node, EvmCall(TOKEN, approve_calldata(ROUTER, 2**256 - 1)), WALLET,
                                  max_allowance=10**6)
    assert reason and "exceeds the quoted need" in reason


async def test_unverifiable_targets_fail_closed():
    reason = await check_evm_call(rpc_with(broken=True), EvmCall(TOKEN, approve_calldata(ROUTER, 10**6)), WALLET)
    assert reason and "couldn't verify" in reason


async def test_provider_route_transfers_cannot_land_on_a_plain_address():
    node = rpc_with()
    # A route may move tokens to its own contract, or return them to the wallet; nowhere else.
    assert await check_evm_call(node, EvmCall(TOKEN, transfer_calldata(ROUTER, 5)), WALLET,
                                require_contract_recipients=True) is None
    assert await check_evm_call(node, EvmCall(TOKEN, transfer_calldata(WALLET, 5)), WALLET,
                                require_contract_recipients=True) is None
    reason = await check_evm_call(node, EvmCall(TOKEN, transfer_calldata(ATTACKER, 5)), WALLET,
                                  require_contract_recipients=True)
    assert reason and "not a contract" in reason
    # Helm-built steps elsewhere (disperse to the user's own wallets) are not held to this rule.
    assert await check_evm_call(node, EvmCall(TOKEN, transfer_calldata(ATTACKER, 5)), WALLET) is None


async def test_plain_transfers_and_deploys_skip_the_guard():
    node = rpc_with(broken=True)
    assert await check_evm_call(node, EvmCall(ROUTER, b"", 10**17), WALLET) is None
    assert await check_evm_call(node, EvmCall(None, b"\x60\x80", 0), WALLET) is None


async def test_evm_sender_refuses_before_signing_or_estimating():
    sender = EvmSender(rpc_with(), signer=None)  # the guard trips before the signer is touched
    res = await sender.send(WALLET, "ref", EvmCall(TOKEN, approve_calldata(ATTACKER, 10**6)))
    assert res.status == "failed" and "refused before signing" in res.error and "not a contract" in res.error


def _solana_tx(*required_signers: str) -> bytes:
    ixs = [Instruction(Pubkey.from_string("11111111111111111111111111111111"), b"",
                       [AccountMeta(Pubkey.from_string(a), is_signer=True, is_writable=True)
                        for a in required_signers])]
    return build_unsigned(Pubkey.from_string(required_signers[0]), ixs, str(Hash.default()))


def test_solana_transactions_may_only_need_signatures_helm_holds():
    wallet, other = str(Keypair().pubkey()), str(Keypair().pubkey())
    assert check_solana_signers(_solana_tx(wallet), {wallet}) is None
    # A second required signature Helm doesn't hold is a stranger authorizing the spend: refuse.
    raw = _solana_tx(wallet, other)
    reason = check_solana_signers(raw, {wallet})
    assert reason and "does not hold" in reason and other in reason
    # Launchpad-submit routes add their own signature later, so unknown slots are tolerated there.
    assert check_solana_signers(raw, {wallet}, allow_missing=True) is None
    reason = check_solana_signers(b"\x00not-a-transaction", {wallet})
    assert reason and "could not be parsed" in reason
