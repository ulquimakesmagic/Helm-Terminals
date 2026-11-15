"""Locally built, atomic SOL/USDC withdrawals. No swaps or provider-built instructions.

Sponsorship is restricted to USDC-bearing operations with an atomic, disclosed
reimbursement. Empty classic USDC ATAs may be closed only during an explicit sweep.
"""
import base64
from dataclasses import dataclass

from solders.instruction import AccountMeta, Instruction
from solders.message import to_bytes_versioned
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from ..chains.solana import (TOKEN_PROGRAM, SYSTEM_PROGRAM, build_unsigned,
    create_ata_idempotent_ix, find_ata, sol_transfer_ix, token_transfer_checked_ix)
from .chain_balances import stable_for


class CashError(ValueError):
    pass


@dataclass
class CashPlan:
    raw: bytes
    refs: dict[str, str]
    native: int
    stable: int
    fee: int
    rent: int
    recovered: int
    reimbursement: int
    payer: str

    def summary(self):
        return {"native_raw": str(self.native), "usdc_received_raw": str(self.stable),
                "network_fee_lamports": self.fee, "account_rent_lamports": self.rent,
                "recovered_rent_lamports": self.recovered,
                "fee_reimbursement_usdc_raw": self.reimbursement, "fee_payer": self.payer}


async def account(rpc, address):
    result = await rpc._call("getAccountInfo", [str(address),
        {"encoding": "jsonParsed", "commitment": "confirmed"}])
    return result["value"]


def token_info(value, owner, mint):
    if value is None:
        return None
    try:
        info = value["data"]["parsed"]["info"]
        if (value["owner"] != str(TOKEN_PROGRAM) or info["owner"] != str(owner)
                or info["mint"] != str(mint) or info["state"] != "initialized"):
            raise ValueError()
        return info
    except (KeyError, TypeError, ValueError):
        raise CashError("USDC token account is invalid, frozen, or has an unexpected owner")


async def message_fee(rpc, raw):
    message = VersionedTransaction.from_bytes(raw).message
    result = await rpc._call("getFeeForMessage", [
        base64.b64encode(to_bytes_versioned(message)).decode(), {"commitment": "confirmed"}])
    fee = result.get("value")
    if not isinstance(fee, int) or isinstance(fee, bool) or fee <= 0:
        raise CashError("Network fee unavailable; retry the preview")
    return fee


async def prepare(rpc, settings, wallet, destination, *, stable=False, sweep=False, amount=None):
    """amount is raw gross debit; None is Max. No signing or broadcasting here."""
    owner, dest = Pubkey.from_string(wallet.address), Pubkey.from_string(destination)
    if owner == dest:
        raise CashError("The destination must differ from the source")
    owner_account = await account(rpc, owner)
    if owner_account and (owner_account["owner"] != str(SYSTEM_PROGRAM)
                          or owner_account.get("space", 0) != 0):
        raise CashError("Source is not a plain SOL wallet")
    balance = int(await rpc.get_balance(str(owner)))
    mint = Pubkey.from_string(stable_for("solana")[1])
    src_ata = find_ata(owner, mint)
    src_account = await account(rpc, src_ata) if stable or sweep else None
    src_info = token_info(src_account, owner, mint)
    held = int(src_info["tokenAmount"]["amount"]) if src_info else 0
    gross = (held if amount is None else amount) if stable or sweep else 0
    if gross < 0 or gross > held:
        raise CashError("Insufficient USDC in the source token account")
    if stable and gross <= 0:
        raise CashError("No USDC available to withdraw")
    # Never close a delegated account or one with another close authority.
    close = bool(sweep and src_info and gross == held and not src_info.get("delegate")
                 and src_info.get("closeAuthority", str(owner)) == str(owner))
    recovered = int(src_account["lamports"]) if close else 0
    if sweep and not balance and not gross and not recovered:
        raise CashError("Nothing available to sweep")
    dest_info = token_info(await account(rpc, find_ata(dest, mint)), dest, mint) if gross else None
    rent = int(await rpc._call("getMinimumBalanceForRentExemption", [165])) if gross and not dest_info else 0
    blockhash = await rpc.get_latest_blockhash()

    def instructions(payer, reimbursement, native):
        ixs = []
        if gross:
            if rent:
                ixs.append(create_ata_idempotent_ix(payer, dest, mint))
            ixs.append(token_transfer_checked_ix(owner, dest, mint, gross - reimbursement, 6))
        if reimbursement:
            ixs.append(token_transfer_checked_ix(owner, payer, mint, reimbursement, 6))
        if close:
            ixs.append(Instruction(TOKEN_PROGRAM, bytes([9]), [AccountMeta(src_ata, False, True),
                AccountMeta(owner, False, True), AccountMeta(owner, True, False)]))
        if native:
            ixs.append(sol_transfer_ix(owner, dest, native))
        return ixs

    move_native = sweep or not stable
    probe = build_unsigned(owner, instructions(owner, 0, 1 if move_native else 0), blockhash)
    fee = await message_fee(rpc, probe)
    wallet_rent = int(await rpc._call("getMinimumBalanceForRentExemption", [0]))
    payer, reimbursement = owner, 0
    refs = {str(owner): wallet.signer_ref or ""}
    # Rent may be funded by closing an eligible account in this same transaction;
    # because destination creation precedes closure, it still needs upfront funding.
    if gross and (balance < fee + rent or (stable and 0 < balance - fee - rent < wallet_rent)):
        address = settings.solana_cash_sponsor_address
        ref = settings.solana_cash_sponsor_ref
        reimbursement = settings.solana_cash_sponsor_usdc_raw
        if not address or not ref or reimbursement <= 0:
            raise CashError("Automatic SOL fee funding is not configured; withdrawal needs SOL for fees/account creation")
        payer = Pubkey.from_string(address)
        if payer in (owner, dest):
            raise CashError("Fee sponsor must be separate from source and destination")
        if reimbursement >= gross:
            raise CashError("USDC amount is too small to cover automatic fee funding")
        sponsor_ata = token_info(await account(rpc, find_ata(payer, mint)), payer, mint)
        if not sponsor_ata:
            raise CashError("Automatic fee funding is unavailable: sponsor USDC account is not initialized")
        refs[str(payer)] = ref
        probe = build_unsigned(payer, instructions(payer, reimbursement, 1 if move_native else 0), blockhash)
        fee = await message_fee(rpc, probe)
        if fee + rent > settings.solana_cash_sponsor_max_lamports:
            raise CashError("Automatic fee funding exceeds the configured cost limit")
        sponsor_account = await account(rpc, payer)
        if (not sponsor_account or sponsor_account["owner"] != str(SYSTEM_PROGRAM)
                or int(sponsor_account["lamports"]) < fee + rent + wallet_rent):
            raise CashError("Automatic fee funding is temporarily unavailable: sponsor needs SOL")
    native = 0
    if move_native:
        available = balance + recovered - (fee + rent if payer == owner else 0)
        native = available if sweep or amount is None else amount
        if native < 0 or native > available or (not sweep and native == 0):
            raise CashError("Wallet has too little SOL for this transfer and its exact network fee")
    if payer == owner and balance < fee + rent:
        raise CashError("Insufficient SOL to pay the transaction fee before recovering rent")
    raw = build_unsigned(payer, instructions(payer, reimbursement, native), blockhash)
    if not VersionedTransaction.from_bytes(raw).message.instructions:
        raise CashError("Nothing available to sweep")
    if len(raw) > 1232:
        raise CashError("Withdrawal transaction exceeds the network size limit")
    # Block invalid rent transitions/recipient accounts before asking for confirmation.
    error = await rpc.simulate(raw, sig_verify=False)
    if error:
        raise CashError(f"Withdrawal simulation failed: {error}")
    return CashPlan(raw, refs, native, gross - reimbursement, fee, rent, recovered,
                    reimbursement, str(payer))
