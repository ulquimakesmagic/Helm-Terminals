"""Immutable signing constraints supplied by Helm, never synthesized from provider permissions.

Protocol validators must decode the final payload before constructing effects. Unknown instructions,
calldata and authorities fail closed. This module does not infer an output guarantee from a simulation.
"""
from dataclasses import dataclass
import time

from ..chains.base import AdapterError


@dataclass(frozen=True)
class AmountBound:
    asset: str
    amount: int


@dataclass(frozen=True)
class ApprovalBound:
    asset: str
    spender: str
    amount: int


@dataclass(frozen=True)
class ExecutionIntent:
    network: str
    wallet: str
    recipients: frozenset[str]
    programs: frozenset[str]
    maximum_debits: tuple[AmountBound, ...]
    minimum_outputs: tuple[AmountBound, ...]
    fee_limits: tuple[AmountBound, ...]
    approvals: tuple[ApprovalBound, ...]
    expires: int
    authority_changes: frozenset[tuple[str, str]] = frozenset()

    def check_live(self, network, wallet):
        if (network, wallet) != (self.network, self.wallet) or self.expires <= time.time():
            raise AdapterError("Signing intent expired or belongs to a different chain/wallet")


@dataclass(frozen=True)
class Effects:
    program: str
    debits: tuple[AmountBound, ...] = ()
    outputs: tuple[AmountBound, ...] = ()
    recipients: frozenset[str] = frozenset()
    approvals: tuple[ApprovalBound, ...] = ()
    authority_changes: frozenset[tuple[str, str]] = frozenset()
    fees: tuple[AmountBound, ...] = ()


def validate_effects(intent: ExecutionIntent, effects: list[Effects], *, network: str, wallet: str):
    intent.check_live(network, wallet)
    totals, outputs, fees = {}, {}, {}
    maximums = {b.asset: b.amount for b in intent.maximum_debits}
    fee_limits = {b.asset: b.amount for b in intent.fee_limits}
    permissions = {(a.asset, a.spender): a.amount for a in intent.approvals}
    for effect in effects:
        if effect.program not in intent.programs or not effect.recipients <= intent.recipients:
            raise AdapterError("Unapproved program or recipient")
        if not effect.authority_changes <= intent.authority_changes:
            raise AdapterError("Unapproved authority change")
        for fee in effect.fees:
            fees[fee.asset] = fees.get(fee.asset, 0) + fee.amount
            if fee.amount < 0 or fees[fee.asset] > fee_limits.get(fee.asset, -1):
                raise AdapterError("Aggregate fees exceed approval")
        for approval in effect.approvals:
            if approval.amount < 0 or approval.amount > permissions.get((approval.asset, approval.spender), -1):
                raise AdapterError("Unapproved asset, spender or allowance")
        for debit in effect.debits:
            totals[debit.asset] = totals.get(debit.asset, 0) + debit.amount
            if debit.amount < 0 or totals[debit.asset] > maximums.get(debit.asset, -1):
                raise AdapterError("Aggregate debit exceeds approval")
        for output in effect.outputs:
            if output.amount < 0:
                raise AdapterError("Invalid minimum output")
            outputs[output.asset] = outputs.get(output.asset, 0) + output.amount
    if any(outputs.get(bound.asset, 0) < bound.amount for bound in intent.minimum_outputs):
        raise AdapterError("Transaction does not enforce the approved minimum output")


def evm_transfer_effect(call) -> Effects:
    """Explicitly supported plain native / ERC-20 transfers and exact approvals only."""
    from .tx_guard import APPROVE, TRANSFER
    from eth_abi import decode, encode
    if not call.to or call.value < 0:
        raise AdapterError("Contract creation is not a supported transfer")
    target = call.to.lower()
    if not call.data:
        return Effects("native", (AmountBound("native", call.value),), recipients=frozenset({target}))
    if call.data[:4] not in (APPROVE, TRANSFER) or len(call.data) != 68 or call.value:
        raise AdapterError("Unknown or unverifiable transfer calldata")
    try:
        address, amount = decode(["address", "uint256"], call.data[4:])
    except Exception:
        raise AdapterError("Malformed transfer calldata") from None
    if encode(["address", "uint256"], [address, amount]) != call.data[4:]:
        raise AdapterError("Noncanonical transfer calldata")
    if call.data[:4] == APPROVE:
        return Effects(target, approvals=(ApprovalBound(target, address, amount),))
    return Effects(target, (AmountBound(target, amount),), recipients=frozenset({address}))
