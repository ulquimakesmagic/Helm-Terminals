from types import SimpleNamespace

import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from app.chains.solana import TOKEN_PROGRAM, SYSTEM_PROGRAM
from app.services.solana_cash import CashError, prepare
from .conftest import FakeRPC


def setup(balance=0, usdc=10_000_000):
    rpc = FakeRPC()
    owner, dest, sponsor = (str(Keypair().pubkey()) for _ in range(3))
    rpc.balances.update({owner: balance, dest: 1_000_000, sponsor: 100_000_000})
    rpc.tokens.update({owner: usdc, sponsor: 0})
    wallet = SimpleNamespace(address=owner, signer_ref="source-reference")
    settings = SimpleNamespace(solana_cash_sponsor_address=sponsor,
        solana_cash_sponsor_ref="sponsor-reference", solana_cash_sponsor_usdc_raw=50_000,
        solana_cash_sponsor_max_lamports=3_000_000)
    return rpc, wallet, dest, settings


async def test_zero_sol_usdc_is_sponsored_with_atomic_reimbursement():
    rpc, wallet, dest, settings = setup()
    p = await prepare(rpc, settings, wallet, dest, stable=True)
    assert p.stable == 9_950_000 and p.reimbursement == 50_000
    assert p.fee == 10_000 and p.rent == 2_039_280
    tx = VersionedTransaction.from_bytes(p.raw)
    assert str(tx.message.account_keys[0]) == settings.solana_cash_sponsor_address
    assert tx.message.header.num_required_signatures == 2
    transfers = [ix for ix in tx.message.instructions if ix.data[0] == 12]
    assert [int.from_bytes(ix.data[1:9], "little") for ix in transfers] == [9_950_000, 50_000]
    assert not rpc.sent


async def test_existing_destination_needs_no_new_rent_and_no_sponsor():
    rpc, wallet, dest, settings = setup(balance=2_000_000)
    rpc.tokens[dest] = 0
    p = await prepare(rpc, settings, wallet, dest, stable=True)
    assert p.rent == 0 and p.reimbursement == 0 and p.fee == 5_000
    assert p.stable == 10_000_000 and p.payer == wallet.address


async def test_sweep_closes_usdc_then_drains_native_without_retained_reserve():
    rpc, wallet, dest, settings = setup(balance=5_000_000)
    rpc.tokens[dest] = 0
    p = await prepare(rpc, settings, wallet, dest, sweep=True)
    assert p.native == 5_000_000 + 2_039_280 - 5_000
    assert p.recovered == 2_039_280
    tx = VersionedTransaction.from_bytes(p.raw)
    ixs = tx.message.instructions
    assert [ix.data[0] for ix in ixs] == [12, 9, 2]
    assert tx.message.account_keys[ixs[1].program_id_index] == TOKEN_PROGRAM
    assert tx.message.account_keys[ixs[2].program_id_index] == SYSTEM_PROGRAM


async def test_sponsored_sweep_returns_rent_to_user_not_sponsor():
    rpc, wallet, dest, settings = setup()
    p = await prepare(rpc, settings, wallet, dest, sweep=True)
    assert p.native == 2_039_280 and p.recovered == 2_039_280
    assert p.stable == 9_950_000
    tx = VersionedTransaction.from_bytes(p.raw)
    close = next(ix for ix in tx.message.instructions if ix.data[0] == 9)
    assert tx.message.account_keys[close.accounts[1]] == Pubkey.from_string(wallet.address)


async def test_native_max_uses_exact_fee_not_rent_reserve():
    rpc, wallet, dest, settings = setup(balance=1_000_000)
    p = await prepare(rpc, settings, wallet, dest)
    assert p.native == 995_000 and p.fee == 5_000 and not p.recovered


@pytest.mark.parametrize("problem", ["unconfigured", "poor_sponsor", "cost_cap", "tiny_amount", "sponsor_ata"])
async def test_sponsorship_fails_closed(problem):
    rpc, wallet, dest, settings = setup()
    if problem == "unconfigured":
        settings.solana_cash_sponsor_ref = ""
    elif problem == "poor_sponsor":
        rpc.balances[settings.solana_cash_sponsor_address] = 0
    elif problem == "cost_cap":
        settings.solana_cash_sponsor_max_lamports = 1
    elif problem == "tiny_amount":
        rpc.tokens[wallet.address] = 50_000
    else:
        del rpc.tokens[settings.solana_cash_sponsor_address]
    with pytest.raises(CashError):
        await prepare(rpc, settings, wallet, dest, stable=True)
    assert not rpc.sent


async def test_failed_simulation_never_broadcasts():
    rpc, wallet, dest, settings = setup()
    rpc.sim_error = "InsufficientFundsForRent"
    with pytest.raises(CashError, match="simulation"):
        await prepare(rpc, settings, wallet, dest, stable=True)
    assert not rpc.sent


async def test_sweep_does_not_close_account_with_external_close_authority():
    rpc, wallet, dest, settings = setup(balance=5_000_000)
    original = rpc._call
    async def altered(method, params):
        result = await original(method, params)
        if method == "getAccountInfo" and result["value"] and "data" in result["value"]:
            result["value"]["data"]["parsed"]["info"]["closeAuthority"] = dest
        return result
    rpc._call = altered
    p = await prepare(rpc, settings, wallet, dest, sweep=True)
    assert p.recovered == 0
    tx = VersionedTransaction.from_bytes(p.raw)
    assert not any(ix.data[0] == 9 for ix in tx.message.instructions)
