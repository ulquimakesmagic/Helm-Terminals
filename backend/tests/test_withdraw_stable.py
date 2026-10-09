"""Withdrawing USDC (the chain's stablecoin) to an outside address, including from a wallet with no SOL."""

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from app.chains.solana import find_ata
from app.deps import get_executor
from app.main import app
from app.services.chain_balances import stable_for
from app.services.executor import Executor
from app.signing import get_signer

from .conftest import enable_2fa, signup, totp_code
from .test_flap import FlapNode

USDC = stable_for("solana")[1]
OUTSIDE = str(Keypair().pubkey())


async def go(client, h, body, key, secret):
    pf = (await client.post("/wallets/withdraw", json=body, headers=h)).json()
    r = await client.post("/wallets/withdraw", json={**body, "confirm": True, "preflight_hash": pf["preflight_hash"],
                                                     "idempotency_key": key, "totp": await totp_code(secret)}, headers=h)
    return pf, r


async def test_usdc_only_wallet_withdraws_with_another_wallet_paying_the_fee(client, fake_rpc):
    h = await signup(client)
    a = (await client.post("/wallets", json={"count": 1}, headers=h)).json()[0]
    fake_rpc.tokens[a["address"]] = 7_500_000  # 7.5 USDC
    fake_rpc.balances[a["address"]] = 0  # no SOL at all
    secret = await enable_2fa(client, h)
    body = {"wallet_ids": [a["id"]], "destination": OUTSIDE, "asset": "stable"}
    pf, r = await go(client, h, body, "wd-usdc-1", secret)
    p = pf["preflight"]
    assert p["warnings"] == [], p["warnings"]
    assert p["coin"] == "USDC" and p["from"] == [{"address": a["address"], "amount": 7.5}]
    assert abs(p["network_fees_max"] - (0.00001 + 0.00203928)) < 1e-12  # the outside address has no USDC account
    payer = p["fees_paid_by"][0]["payer"]["address"]
    assert payer != a["address"]  # the account's main wallet pays
    assert r.status_code == 200, r.text
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "confirmed", job
    tx = VersionedTransaction.from_bytes(fake_rpc.sent[-1])
    keys = [str(k) for k in tx.message.account_keys]
    assert keys[0] == payer and tx.message.header.num_required_signatures == 2 and a["address"] in keys[:2]
    assert str(find_ata(Pubkey.from_string(OUTSIDE), Pubkey.from_string(USDC))) in keys
    assert all(s != s.default() for s in tx.signatures)  # both wallets signed


async def test_usdc_withdraw_skips_rent_for_an_existing_account_and_pays_its_own_fee(client, fake_rpc):
    h = await signup(client)
    a = (await client.post("/wallets", json={"count": 1}, headers=h)).json()[0]
    fake_rpc.tokens[a["address"]] = 5_000_000
    fake_rpc.balances[a["address"]] = 1_000_000  # 0.001 SOL: enough for a fee, not for a new account
    fake_rpc.existing_accounts.add(str(find_ata(Pubkey.from_string(OUTSIDE), Pubkey.from_string(USDC))))
    secret = await enable_2fa(client, h)
    body = {"wallet_ids": [a["id"]], "destination": OUTSIDE, "asset": "stable", "all": False, "amount": 2}
    pf, r = await go(client, h, body, "wd-usdc-2", secret)
    p = pf["preflight"]
    assert p["warnings"] == [] and p["fees_paid_by"] == [] and abs(p["network_fees_max"] - 0.00001) < 1e-12
    assert p["from"][0]["amount"] == 2
    assert r.status_code == 200, r.text
    tx = VersionedTransaction.from_bytes(fake_rpc.sent[-1])
    assert str(tx.message.account_keys[0]) == a["address"] and tx.message.header.num_required_signatures == 1


async def test_usdc_withdraw_refuses_when_no_wallet_has_sol_or_the_amount_is_too_big(client, fake_rpc):
    h = await signup(client)
    a = (await client.post("/wallets", json={"count": 1}, headers=h)).json()[0]
    fake_rpc.tokens[a["address"]] = 1_000_000
    for w in (await client.get("/wallets", headers=h)).json():
        fake_rpc.balances[w["address"]] = 0
    secret = await enable_2fa(client, h)
    body = {"wallet_ids": [a["id"]], "destination": OUTSIDE, "asset": "stable"}
    pf, r = await go(client, h, body, "wd-usdc-3", secret)
    assert any("none of your other Solana wallets" in w for w in pf["preflight"]["warnings"])
    assert r.status_code == 400 and fake_rpc.sent == []
    too_big = (await client.post("/wallets/withdraw", json={**body, "all": False, "amount": 5}, headers=h)).json()
    assert any("less than 5" in w for w in too_big["preflight"]["warnings"])
    missing = await client.post("/wallets/withdraw", json={**body, "all": False}, headers=h)
    assert missing.status_code == 400


async def test_usdc_withdraw_on_an_evm_chain_sends_erc20_and_flags_missing_gas(client, fake_rpc):
    node = FlapNode("base")
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"base": node})
    try:
        h = await signup(client)
        a = (await client.post("/wallets", json={"network": "base", "count": 1}, headers=h)).json()[0]
        usdc = stable_for("base")[1]
        node.tokens[a["address"].lower()] = 3_000_000
        secret = await enable_2fa(client, h)
        body = {"wallet_ids": [a["id"]], "destination": "0x" + "33" * 20, "asset": "stable"}
        pf = (await client.post("/wallets/withdraw", json=body, headers=h)).json()
        assert any("for gas to send USDC" in w for w in pf["preflight"]["warnings"])  # no ETH yet
        node.balances[a["address"].lower()] = 10**16
        pf, r = await go(client, h, body, "wd-usdc-evm", secret)
        assert pf["preflight"]["warnings"] == [] and pf["preflight"]["coin"] == "USDC"
        assert r.status_code == 200, r.text
        assert (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()["status"] == "confirmed"
        assert node.sent[-1]["to"].lower() == usdc.lower()
    finally:
        app.dependency_overrides.pop(get_executor, None)


async def test_usdc_withdraw_refuses_addresses_that_cannot_hold_it(client, fake_rpc):
    """Review M2: a token account, mint or program address (or an off-curve address) would lock the USDC."""
    from app.chains.solana import find_ata as _ata

    h = await signup(client)
    a = (await client.post("/wallets", json={"count": 1}, headers=h)).json()[0]
    fake_rpc.tokens[a["address"]] = 1_000_000
    token_account = str(_ata(Pubkey.from_string(a["address"]), Pubkey.from_string(USDC)))  # off-curve PDA
    owned_by_program = str(Keypair().pubkey())
    fake_rpc.owners = {owned_by_program: "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                       USDC: "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"}  # the mint is a token-program account
    for dest in (token_account, owned_by_program, USDC):
        pf = (await client.post("/wallets/withdraw", json={"wallet_ids": [a["id"]], "destination": dest,
                                                           "asset": "stable"}, headers=h)).json()
        assert any("isn't a wallet address" in w for w in pf["preflight"]["warnings"]), dest
    ok = (await client.post("/wallets/withdraw", json={"wallet_ids": [a["id"]], "destination": OUTSIDE,
                                                       "asset": "stable"}, headers=h)).json()
    assert not any("isn't a wallet address" in w for w in ok["preflight"]["warnings"])
    dup = await client.post("/wallets/withdraw", json={"wallet_ids": [a["id"], a["id"]], "destination": OUTSIDE,
                                                       "asset": "stable"}, headers=h)
    assert dup.status_code == 400 and "only be listed once" in dup.json()["detail"]
