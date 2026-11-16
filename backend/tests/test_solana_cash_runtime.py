"""Execute real SPL/System instructions in an isolated in-process Solana VM."""
from types import SimpleNamespace

import pytest
from solders.account import Account
from solders.keypair import Keypair
from solders.litesvm import LiteSVM
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from solders.transaction_metadata import FailedTransactionMetadata

from app.chains.solana import TOKEN_PROGRAM, find_ata
from app.services.chain_balances import stable_for
from app.services.solana_cash import prepare
from .conftest import FakeRPC


@pytest.mark.parametrize("sponsored", [False, True])
async def test_atomic_usdc_sweep_executes_in_solana_runtime(sponsored):
    vm = LiteSVM()
    source, destination, sponsor = Keypair(), Keypair(), Keypair()
    owner, dest, payer = str(source.pubkey()), str(destination.pubkey()), str(sponsor.pubkey())
    mint = Pubkey.from_string(stable_for("solana")[1])
    mint_data = bytes(36) + (10_000_000).to_bytes(8, "little") + bytes([6, 1]) + bytes(36)
    vm.set_account(mint, Account(vm.minimum_balance_for_rent_exemption(82), mint_data, TOKEN_PROGRAM))

    def install_token(owner_key, amount):
        data = (bytes(mint) + bytes(owner_key) + amount.to_bytes(8, "little")
                + bytes(36) + bytes([1]) + bytes(12) + bytes(8) + bytes(36))
        assert len(data) == 165
        vm.set_account(find_ata(owner_key, mint), Account(
            vm.minimum_balance_for_rent_exemption(165), data, TOKEN_PROGRAM))

    install_token(source.pubkey(), 10_000_000)
    install_token(sponsor.pubkey(), 0)
    if not sponsored:
        vm.airdrop(source.pubkey(), 5_000_000)
    vm.airdrop(destination.pubkey(), 1_000_000)
    vm.airdrop(sponsor.pubkey(), 100_000_000)
    rpc = FakeRPC()
    rpc.balances.update({owner: 0 if sponsored else 5_000_000, dest: 1_000_000, payer: 100_000_000})
    rpc.tokens.update({owner: 10_000_000, payer: 0})
    async def blockhash():
        return str(vm.latest_blockhash())
    rpc.get_latest_blockhash = blockhash
    settings = SimpleNamespace(solana_cash_sponsor_address=payer, solana_cash_sponsor_ref="payer",
        solana_cash_sponsor_usdc_raw=50_000, solana_cash_sponsor_max_lamports=3_000_000)
    plan = await prepare(rpc, settings, SimpleNamespace(address=owner, signer_ref="source"), dest, sweep=True)
    message = VersionedTransaction.from_bytes(plan.raw).message
    signed = VersionedTransaction(message, [source, sponsor] if sponsored else [source])
    result = vm.send_transaction(signed)
    assert not isinstance(result, FailedTransactionMetadata), str(result)
    assert vm.get_balance(source.pubkey()) in (None, 0)
    assert vm.get_account(find_ata(source.pubkey(), mint)) is None
    recipient_account = vm.get_account(find_ata(destination.pubkey(), mint))
    assert int.from_bytes(recipient_account.data[64:72], "little") == plan.stable
    sponsor_account = vm.get_account(find_ata(sponsor.pubkey(), mint))
    assert int.from_bytes(sponsor_account.data[64:72], "little") == plan.reimbursement
    assert vm.get_balance(destination.pubkey()) == 1_000_000 + plan.native


@pytest.mark.parametrize('token', [False, True])
async def test_owned_fee_wallet_executes_without_source_gas(token):
    """The owner signs the asset movement; another wallet actually pays fees/rent."""
    from app.chains.solana import build_unsigned, sol_transfer_ix
    from app.services.sol_token_send import build_token_send
    vm = LiteSVM()
    source, destination, payer = Keypair(), Keypair(), Keypair()
    vm.airdrop(payer.pubkey(), 100_000_000)
    vm.airdrop(destination.pubkey(), 1_000_000)
    amount = 10_000_000
    if token:
        mint = Keypair().pubkey()
        mint_data = bytes(36) + amount.to_bytes(8, 'little') + bytes([6, 1]) + bytes(36)
        vm.set_account(mint, Account(vm.minimum_balance_for_rent_exemption(82), mint_data, TOKEN_PROGRAM))
        data = bytes(mint) + bytes(source.pubkey()) + amount.to_bytes(8, 'little') + bytes(36) + bytes([1]) + bytes(56)
        assert len(data) == 165
        vm.set_account(find_ata(source.pubkey(), mint), Account(vm.minimum_balance_for_rent_exemption(165), data, TOKEN_PROGRAM))
        raw = build_token_send(str(source.pubkey()), str(payer.pubkey()), [(str(destination.pubkey()), amount)],
                               str(mint), 6, TOKEN_PROGRAM, str(vm.latest_blockhash()))
    else:
        vm.airdrop(source.pubkey(), amount)
        raw = build_unsigned(payer.pubkey(), [sol_transfer_ix(source.pubkey(), destination.pubkey(), amount)], str(vm.latest_blockhash()))
    message = VersionedTransaction.from_bytes(raw).message
    assert message.header.num_required_signatures == 2 and message.account_keys[0] == payer.pubkey()
    signed = VersionedTransaction(message, [source, payer])
    result = vm.send_transaction(signed)
    assert not isinstance(result, FailedTransactionMetadata), str(result)
    assert vm.get_balance(source.pubkey()) in (None, 0)
    if token:
        received = vm.get_account(find_ata(destination.pubkey(), mint))
        assert int.from_bytes(received.data[64:72], 'little') == amount
        assert vm.get_balance(payer.pubkey()) == 100_000_000 - 10_000 - vm.minimum_balance_for_rent_exemption(165)
    else:
        assert vm.get_balance(destination.pubkey()) == 1_000_000 + amount
        assert vm.get_balance(payer.pubkey()) == 100_000_000 - 10_000
