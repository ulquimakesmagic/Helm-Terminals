"""Sanity checks on transactions Helm is about to sign (audit SEC-3).

Helm signs transactions it did not assemble line by line: launchpad APIs (PumpPortal), aggregators
(KyberSwap) and cross-chain routes (Relay) return calldata and instructions that Helm verifies but
does not build. A compromised or malicious provider must not be able to turn a confirmed spend into
a drain of the wallet it signs for.

These are supplemental shape checks, not a complete intent validator. Contract-code presence and
known signers do not establish a trusted recipient or constrain opaque program effects. Protocol
validators must separately enforce the user's economic intent before provider payloads are signed.
"""

from solders.transaction import VersionedTransaction
from eth_utils import keccak

APPROVE = keccak(b"approve(address,uint256)")[:4]
INCREASE_ALLOWANCE = keccak(b"increaseAllowance(address,uint256)")[:4]
TRANSFER = keccak(b"transfer(address,uint256)")[:4]


def _word(data: bytes, i: int) -> bytes:
    return data[4 + i * 32: 4 + (i + 1) * 32]


def _address(word: bytes) -> str | None:
    if len(word) != 32 or any(word[:12]):
        return None
    return "0x" + word[12:].hex()


async def _has_code(rpc, address: str) -> str | None:
    """None when the address is a contract; otherwise why that could not be verified."""
    try:
        code = await rpc.get_code(address)
    except Exception as e:  # RPC down: fail closed, not open
        return f"couldn't verify the target is a contract ({type(e).__name__})"
    return None if code and code != "0x" else "the target is not a contract"


async def check_evm_call(rpc, call, wallet: str, *, max_allowance: int | None = None,
                         require_contract_recipients: bool = False) -> str | None:
    """Reason to refuse this call, or None. `wallet` is the address that signs.

    ERC-20 approvals must grant a contract, never a plain address, and — when the call comes from a
    provider-built route — cover no more than the quoted need. In provider-built routes a token
    transfer must land on the route's contract or the wallet itself, so a malicious route cannot
    sweep tokens to an address it chooses.
    """
    data = call.data or b""
    if call.to is None or len(data) < 4:
        return None  # contract deployments and plain value transfers carry no selectors to check
    selector = data[:4]
    if selector in (APPROVE, INCREASE_ALLOWANCE):
        spender, amount_word = _address(_word(data, 0)), _word(data, 1)
        if spender is None or len(amount_word) != 32:
            return "approval calldata is malformed"
        if spender.lower() == wallet.lower():
            return "the approval grants the sending wallet itself"
        if reason := await _has_code(rpc, spender):
            return f"approval target rejected: {reason}"
        amount = int.from_bytes(amount_word, "big")
        if max_allowance is not None and amount > max_allowance:
            return (f"approval amount {amount} exceeds the quoted need ({max_allowance}); "
                    "provider-built approvals are capped at the quote")
    elif require_contract_recipients and selector == TRANSFER:
        recipient = _address(_word(data, 0))
        if recipient is None:
            return "transfer calldata is malformed"
        if recipient.lower() != wallet.lower() and (reason := await _has_code(rpc, recipient)):
            return f"route transfer recipient rejected: {reason}"
    return None


def check_solana_signers(raw: bytes, held: set[str], *, allow_missing: bool = False) -> str | None:
    """Reason to refuse this provider-built transaction, or None.

    Every required signature slot must belong to a signer Helm holds (the user's wallet, or an
    ephemeral keypair such as a new token mint). A provider whose transaction needs some other
    human's signature is asking a stranger to authorize the spend. With `allow_missing`, the
    launchpad adds its own signature when it submits, so unknown slots are tolerated there.
    """
    try:
        msg = VersionedTransaction.from_bytes(raw).message
    except Exception:
        return "provider transaction could not be parsed"
    n = msg.header.num_required_signatures
    if n < 1:
        return "provider transaction has no signers"
    for key in list(msg.account_keys)[:n]:
        if str(key) not in held and not allow_missing:
            return f"transaction needs a signature from {key}, which Helm does not hold"
    return None
