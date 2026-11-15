"""Bounds on transactions a third party built (audit 2026-10-06: HT-01, HT-02, HT-03, HT-04).

Each test started life as a reproduction of the defect; they now assert the fixed behaviour."""
import base64
import json

import httpx
import pytest
from solders.hash import Hash
from solders.keypair import Keypair

from app.chains.solana import SolanaRPC, build_unsigned, sol_transfer_ix
from app.services.evm_exec import EvmCall
from app.services.routing import check_route_call
from app.services.solana_guard import (
    SYSTEM_OWNER, TRADE_OVERHEAD_LAMPORTS, SpendLimits, TokenState, WalletEffects, budget, check,
)

from .conftest import signup
from .test_api import live_launch, make_wallets

SOL = 1_000_000_000
W = str(Keypair().pubkey())
MINT = str(Keypair().pubkey())
OTHER = str(Keypair().pubkey())


def effects(lamports_out=5_000, before_tokens=None, after_tokens=None, owner=SYSTEM_OWNER, wallet_gone=False):
    return WalletEffects(10 * SOL, None if wallet_gone else 10 * SOL - lamports_out, owner,
                         before_tokens or {}, after_tokens or {})


# --- the pure check -------------------------------------------------------------------------------------

def test_drain_of_native_balance_is_refused():
    limits = SpendLimits(W, budget(SOL // 10))
    assert check(effects(SOL // 10 + 10_000), limits) is None
    assert "more than the approved" in check(effects(9 * SOL), limits)


def test_wallet_reassignment_and_closure_are_refused():
    limits = SpendLimits(W, budget(SOL))
    assert "reassign" in check(effects(owner="BadProgram1111111111111111111111111111111"), limits)
    assert "close the signing wallet" in check(effects(wallet_gone=True), limits)


def test_token_drain_delegate_and_owner_change_are_refused():
    before = {"acc": TokenState("acc", MINT, 1_000, W)}
    limits = SpendLimits(W, TRADE_OVERHEAD_LAMPORTS, token_out={MINT: 500})
    assert check(effects(before_tokens=before, after_tokens={"acc": TokenState("acc", MINT, 500, W)}), limits) is None
    assert "more than the approved" in check(effects(before_tokens=before, after_tokens={"acc": TokenState("acc", MINT, 0, W)}), limits)
    assert "delegate" in check(effects(before_tokens=before, after_tokens={"acc": TokenState("acc", MINT, 1_000, W, delegate=OTHER)}), limits)
    assert "ownership" in check(effects(before_tokens=before, after_tokens={"acc": TokenState("acc", MINT, 1_000, OTHER)}), limits)
    assert "close" in check(effects(before_tokens=before, after_tokens={"acc": TokenState("acc", MINT, 1_000, W, close_authority=OTHER)}), limits)
    # Closing an account counts everything it held as leaving the wallet.
    assert "more than the approved" in check(effects(before_tokens=before, after_tokens={"acc": None}), limits)


def test_untouched_tokens_may_not_move_and_floor_is_enforced():
    before = {"other": TokenState("other", OTHER, 7, W)}
    limits = SpendLimits(W, budget(SOL), token_in_min={MINT: 100}, watch_mints=(MINT,))
    received = {"other": before["other"], "ata": TokenState("ata", MINT, 100, W)}
    assert check(effects(before_tokens=before, after_tokens=received), limits) is None
    short = {"other": before["other"], "ata": TokenState("ata", MINT, 99, W)}
    assert "less of token" in check(effects(before_tokens=before, after_tokens=short), limits)
    stolen = {"other": TokenState("other", OTHER, 0, W), "ata": TokenState("ata", MINT, 100, W)}
    assert "more than the approved 0" in check(effects(before_tokens=before, after_tokens=stolen), limits)


def test_signer_only_guard_no_longer_decides():
    """Reproduction r1: a 1,000 SOL transfer to a stranger had only a signer check. The spend guard refuses it."""
    wallet, attacker = Keypair().pubkey(), Keypair().pubkey()
    build_unsigned(wallet, [sol_transfer_ix(wallet, attacker, 1000 * SOL)], str(Hash.default()))
    assert check(effects(1000 * SOL), SpendLimits(str(wallet), budget(SOL // 10))) is not None


# --- reading effects from a real JSON-RPC shape ---------------------------------------------------------

def _token_account(mint, owner, amount, delegate=None):
    info = {"mint": mint, "owner": owner, "tokenAmount": {"amount": str(amount), "decimals": 6}, "state": "initialized"}
    if delegate:
        info["delegate"] = delegate
    return {"lamports": 2_039_280, "owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            "data": {"program": "spl-token", "parsed": {"type": "account", "info": info}, "space": 165}}


async def test_wallet_effects_reads_pre_and_simulated_state():
    holding = "Hold1111111111111111111111111111111111111111"
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body["method"])
        if body["method"] == "getAccountInfo":
            return httpx.Response(200, json={"result": {"value": {"lamports": 3 * SOL, "owner": SYSTEM_OWNER}}})
        if body["method"] == "getTokenAccountsByOwner":
            program = body["params"][1]["programId"]
            value = [{"pubkey": holding, "account": _token_account(MINT, W, 500)}] if program.startswith("Tokenkeg") else []
            return httpx.Response(200, json={"result": {"value": value}})
        if body["method"] == "simulateTransaction":
            addresses = body["params"][1]["accounts"]["addresses"]
            assert addresses[0] == W and holding in addresses
            post = [{"lamports": 2 * SOL, "owner": SYSTEM_OWNER, "data": ["", "base64"]}]
            post += [_token_account(MINT, W, 500, delegate=OTHER) if a == holding else None for a in addresses[1:]]
            return httpx.Response(200, json={"result": {"value": {"err": None, "accounts": post}}})
        raise AssertionError(body["method"])

    rpc = SolanaRPC("https://rpc.test", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = await rpc.wallet_effects(b"\x00" * 10, W, (OTHER,))
    assert out.lamports_before - out.lamports_after == SOL
    assert out.tokens_after[holding].delegate == OTHER
    assert "delegate" in check(out, SpendLimits(W, 2 * SOL))
    assert calls[-1] == "simulateTransaction"


async def test_unreadable_effects_fail_closed():
    from app.services.solana_guard import enforce

    class Broken:
        async def wallet_effects(self, *a):
            raise RuntimeError("node down")

    reason = await enforce(Broken(), b"", SpendLimits(W, SOL))
    assert reason and "couldn't verify" in reason


# --- launchpad trades -----------------------------------------------------------------------------------

async def test_malicious_provider_buy_is_refused_before_signing(client, fake_rpc):
    h = await signup(client, "drain@example.com", main=False)
    lid, _, others = await live_launch(client, h, fake_rpc)
    launch, ws = {"id": lid}, [None, *others]
    fake_rpc.effects_override = lambda tx, wallet: effects(4 * SOL)  # the "buy" would empty the wallet
    sent_before = len(fake_rpc.sent)
    body = {"side": "buy", "wallet_ids": [ws[1]["id"]], "amount": 0.1}
    pre = (await client.post(f"/launches/{launch['id']}/trade", json=body, headers=h)).json()
    r = await client.post(f"/launches/{launch['id']}/trade", json={**body, "confirm": True,
                          "preflight_hash": pre["preflight_hash"], "idempotency_key": "drain"}, headers=h)
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "failed"
    assert "refused before signing" in job["transactions"][0]["error"]
    assert len(fake_rpc.sent) == sent_before  # nothing was broadcast


async def test_sell_may_not_take_more_tokens_than_the_percentage(client, fake_rpc):
    h = await signup(client, "oversell@example.com", main=False)
    lid, _, others = await live_launch(client, h, fake_rpc)
    launch, ws = (await client.get(f"/launches/{lid}", headers=h)).json(), [None, *others]
    wallet = ws[1]["address"]
    fake_rpc.tokens[wallet] = 1_000
    before = {"ata": TokenState("ata", launch["mint_address"], 1_000, wallet)}
    fake_rpc.effects_override = lambda tx, w: effects(0, before, {"ata": TokenState("ata", launch["mint_address"], 0, w)})
    body = {"side": "sell", "wallet_ids": [ws[1]["id"]], "amount": 25}
    pre = (await client.post(f"/launches/{launch['id']}/trade", json=body, headers=h)).json()
    r = await client.post(f"/launches/{launch['id']}/trade", json={**body, "confirm": True,
                          "preflight_hash": pre["preflight_hash"], "idempotency_key": "oversell"}, headers=h)
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "failed" and "more than the approved 250" in job["transactions"][0]["error"]


# --- Relay steps ----------------------------------------------------------------------------------------

TOKEN = "0x" + "aa" * 20
ROUTER = "0x" + "cd" * 20


def _erc20(selector: str, to: str, amount: int) -> bytes:
    from eth_abi import encode
    return bytes.fromhex(selector) + encode(["address", "uint256"], [to, amount])


def test_relay_native_route_value_is_bounded():
    """Reproduction r2: a 1,000 ETH plain transfer to an EOA used to pass. Now the approved amount bounds it."""
    assert check_route_call(EvmCall(ROUTER, b"", 10**18), W, None, 10**18) is None
    assert "more native coin" in check_route_call(EvmCall("0x" + "11" * 20, b"", 1000 * 10**18), W, None, 10**18)
    assert "on a token" in check_route_call(EvmCall(TOKEN, _erc20("a9059cbb", ROUTER, 1), 0), W, None, 10**18)


def test_relay_token_route_may_only_use_the_input_token_within_amount():
    ok_approve = EvmCall(TOKEN, _erc20("095ea7b3", ROUTER, 20), 0)
    assert check_route_call(ok_approve, W, TOKEN, 20) is None
    assert check_route_call(EvmCall(ROUTER, b"\xab\xcd\xef", 0), W, TOKEN, 20) is None  # deposit pulls the allowance
    assert "more of the input token" in check_route_call(EvmCall(TOKEN, _erc20("095ea7b3", ROUTER, 21), 0), W, TOKEN, 20)
    assert "native coin" in check_route_call(EvmCall(ROUTER, b"\xab\xcd\xef", 1), W, TOKEN, 20)
    other = "0x" + "bb" * 20
    assert "other than its input token" in check_route_call(EvmCall(other, _erc20("a9059cbb", ROUTER, 1), 0), W, TOKEN, 20)
    assert "unexpected function" in check_route_call(EvmCall(TOKEN, bytes.fromhex("40c10f19") + b"\x00" * 64, 0), W, TOKEN, 20)
    assert "deploy" in check_route_call(EvmCall(None, b"\x60", 0), W, TOKEN, 20)


def test_relay_recipient_must_be_the_destination():
    from app.chains.base import AdapterError
    from app.services.quote_approval import check_relay_recipient

    check_relay_recipient({"details": {}}, W, "base")  # not stated: nothing to compare
    check_relay_recipient({"details": {"recipient": ROUTER.upper()}}, ROUTER, "base")
    with pytest.raises(AdapterError):
        check_relay_recipient({"details": {"recipient": "0x" + "99" * 20}}, ROUTER, "base")


# --- swaps ----------------------------------------------------------------------------------------------

def _moving(base_cls, step):
    class Moving(base_cls):
        n = 0

        async def quote(self, *a, **k):
            q = await super().quote(*a, **k)
            type(self).n += 1
            out, floor = step(type(self).n, int(q["outAmount"]), int(q["otherAmountThreshold"]))
            q["outAmount"], q["otherAmountThreshold"] = str(out), str(floor)
            return q
    return Moving


async def test_price_moves_no_longer_invalidate_a_swap_confirmation(client, fake_rpc, monkeypatch):
    """Reproduction r3: a 1.6% move between preview and confirm returned 409. The hash now covers only the intent."""
    from app.routes import wallets as wallets_module
    from .test_wallet_export_swap import FakeJupiter, deliver

    Moving = _moving(FakeJupiter, lambda n, out, floor: (out + n * 200_000, floor + n * 200_000))
    monkeypatch.setattr(wallets_module, "Jupiter", lambda: Moving())
    h = await signup(client, "moving@example.com", main=False)
    w = (await make_wallets(client, h, 1))[0]
    usdc = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    deliver(fake_rpc, usdc, 13_000_000)
    body = {"wallet_id": w["id"], "input_mint": "native", "output_mint": usdc, "amount": 0.1, "slippage_pct": 0.5}
    pre = (await client.post("/wallets/swap", json=body, headers=h)).json()
    r = await client.post("/wallets/swap", json={**body, "confirm": True, "preflight_hash": pre["preflight_hash"],
                                                 "quote_approval": pre["quote_approval"], "idempotency_key": "k1"}, headers=h)
    assert r.status_code == 200, r.text
    assert (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()["status"] == "confirmed"


async def test_swap_never_signs_below_the_reviewed_floor(client, fake_rpc, monkeypatch):
    """Reproduction r4: the execution re-quote's lower floor used to be signed. Now nothing is signed."""
    from app.routes import wallets as wallets_module
    from .test_wallet_export_swap import FakeJupiter

    Collapsing = _moving(FakeJupiter, lambda n, out, floor: (out, floor) if n < 3 else (6_000_000, 5_000_000))
    monkeypatch.setattr(wallets_module, "Jupiter", lambda: Collapsing())
    h = await signup(client, "collapse@example.com", main=False)
    w = (await make_wallets(client, h, 1))[0]
    body = {"wallet_id": w["id"], "input_mint": "native", "output_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "amount": 0.1, "slippage_pct": 0.5}
    pre = (await client.post("/wallets/swap", json=body, headers=h)).json()
    sent_before = len(fake_rpc.sent)
    r = await client.post("/wallets/swap", json={**body, "confirm": True, "preflight_hash": pre["preflight_hash"],
                                                 "quote_approval": pre["quote_approval"], "idempotency_key": "k2"}, headers=h)
    job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
    assert job["status"] == "failed" and "below the approved minimum" in (job["error"] or "")
    assert len(fake_rpc.sent) == sent_before


async def test_swap_confirmation_requires_the_quote_approval(client, fake_rpc, monkeypatch):
    from app.routes import wallets as wallets_module
    from .test_wallet_export_swap import FakeJupiter

    monkeypatch.setattr(wallets_module, "Jupiter", lambda: FakeJupiter())
    h = await signup(client, "noapproval@example.com", main=False)
    w = (await make_wallets(client, h, 1))[0]
    body = {"wallet_id": w["id"], "input_mint": "native", "output_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "amount": 0.1}
    pre = (await client.post("/wallets/swap", json=body, headers=h)).json()
    forged = {**body, "confirm": True, "preflight_hash": pre["preflight_hash"], "idempotency_key": "k3"}
    assert (await client.post("/wallets/swap", json=forged, headers=h)).status_code == 409
    tampered = pre["quote_approval"].split(".")[0] + "." + "0" * 64
    r = await client.post("/wallets/swap", json={**forged, "quote_approval": tampered}, headers=h)
    assert r.status_code == 409


def test_sol_amounts_convert_exactly():
    from app.services.executor import sol_to_lamports
    assert sol_to_lamports(1.001) == 1_001_000_000 and sol_to_lamports("0.000000001") == 1
    assert int(float(1.001) * SOL) == 1_000_999_999  # the truncation the old code produced
