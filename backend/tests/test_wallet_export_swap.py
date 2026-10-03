"""Private-key export and Jupiter swaps: the guardrails around custody hand-off and a third-party
router, checked end to end offline (fake signer RPC, fake Jupiter)."""
import base64

import pytest
from solders.hash import Hash
from solders.instruction import Instruction
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from sqlalchemy import select

from app.main import app
from app.models import Activity, AdminAudit, Transaction
from app.routes import wallets as wallets_module

from .conftest import enable_2fa, signup, totp_code
from .test_api import confirm, make_wallets

PW = "correct-horse-battery"


# --- export ----------------------------------------------------------------------------------------


async def test_export_requires_2fa_password_and_a_fresh_code(client, fake_rpc):
    h = await signup(client, "ex@example.com", main=False)
    w = (await make_wallets(client, h, 1))[0]
    body = {"password": PW, "totp": "123456"}
    assert (await client.post(f"/wallets/{w['id']}/export-key", json=body, headers=h)).status_code == 403
    secret = await enable_2fa(client, h)
    assert (await client.post(f"/wallets/{w['id']}/export-key",
                              json={"password": "wrong", "totp": await totp_code(secret)}, headers=h)).status_code == 400
    code = await totp_code(secret)
    r = await client.post(f"/wallets/{w['id']}/export-key", json={"password": PW, "totp": code}, headers=h)
    assert r.status_code == 200 and r.json()["secret"]
    # the same code is single-use: a replayed confirmation cannot export again
    assert (await client.post(f"/wallets/{w['id']}/export-key",
                              json={"password": PW, "totp": code}, headers=h)).status_code == 401


async def test_exported_secret_round_trips_to_the_same_address(client, fake_rpc):
    h = await signup(client, "round@example.com", main=False)
    secret2fa = await enable_2fa(client, h)
    w = (await make_wallets(client, h, 1))[0]
    r = await client.post(f"/wallets/{w['id']}/export-key",
                          json={"password": PW, "totp": await totp_code(secret2fa)}, headers=h)
    exported = r.json()["secret"]
    from app.chains.solana import b58decode

    assert str(Keypair.from_bytes(b58decode(exported)).pubkey()) == w["address"]


async def test_export_is_audited_and_never_logged(client, fake_rpc, monkeypatch):
    h = await signup(client, "aud@example.com", main=False)
    secret2fa = await enable_2fa(client, h)
    w = (await make_wallets(client, h, 1))[0]
    r = await client.post(f"/wallets/{w['id']}/export-key",
                          json={"password": PW, "totp": await totp_code(secret2fa)}, headers=h)
    exported = r.json()["secret"]
    feed = (await client.get("/activity", headers=h)).text
    assert exported[:12] not in feed and "key_exported" in feed.replace(".", "_")
    from app.db import get_sessionmaker

    async with get_sessionmaker()() as db:
        rows = (await db.execute(select(Activity).where(Activity.kind == "wallet.key_exported"))).scalars().all()
        assert rows and all(exported[:12] not in (str(r2.payload) + str(r2.kind)) for r2 in rows)
        assert (await db.execute(select(AdminAudit).where(
            AdminAudit.action == "wallet.key_exported", AdminAudit.target == w["id"]))).scalar_one()


async def test_watch_only_wallets_cannot_export(client, fake_rpc):
    h = await signup(client, "watch@example.com", main=False)
    secret2fa = await enable_2fa(client, h)
    r = await client.post("/wallets/watch", json={"network": "solana",
                                                  "address": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZ8DsKHX2eDPbPPi7ny"}, headers=h)
    wid = r.json()["id"]
    out = await client.post(f"/wallets/{wid}/export-key",
                            json={"password": PW, "totp": await totp_code(secret2fa)}, headers=h)
    assert out.status_code == 400


async def test_export_attempts_are_throttled(client, fake_rpc):
    h = await signup(client, "rate@example.com", main=False)
    await enable_2fa(client, h)
    w = (await make_wallets(client, h, 1))[0]
    for _ in range(5):
        await client.post(f"/wallets/{w['id']}/export-key",
                          json={"password": "wrong", "totp": "000000"}, headers=h)
    r = await client.post(f"/wallets/{w['id']}/export-key",
                          json={"password": PW, "totp": "000000"}, headers=h)
    assert r.status_code == 429


# --- Jupiter swaps ---------------------------------------------------------------------------------

QUOTE = {
    "inputMint": "So11111111111111111111111111111111111111112",
    "outputMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "inAmount": "100000000", "outAmount": "12225952", "otherAmountThreshold": "12164823",
    "priceImpactPct": "0", "routePlan": [{"swapInfo": {"label": "GoonFi V2"}, "percent": 100}],
}


class FakeJupiter:
    def __init__(self):
        pass

    async def quote(self, input_mint, output_mint, amount_raw, slippage_bps):
        q = dict(QUOTE)
        q["inAmount"] = str(amount_raw)
        return q

    async def swap_tx(self, quote, payer):
        # A real unsigned versioned transaction whose only required signer is the wallet itself.
        payer_key = Pubkey.from_string(payer)
        ix = Instruction(Pubkey.from_string("11111111111111111111111111111111"), b"", [])
        from app.chains.solana import build_unsigned

        raw = build_unsigned(payer_key, [ix], str(Hash.default()))
        assert base64.b64decode(base64.b64encode(raw)) == raw
        return raw

    async def token(self, mint):
        return {"symbol": "USDC", "decimals": 6}


@pytest.fixture
def fake_jupiter(monkeypatch):
    made = FakeJupiter()
    monkeypatch.setattr(wallets_module, "Jupiter", lambda: made)
    return made


async def test_swap_preflight_then_confirmed_job(client, fake_rpc, fake_jupiter):
    h = await signup(client, "swap@example.com", main=False)
    w = (await make_wallets(client, h, 1))[0]
    body = {"wallet_id": w["id"], "input_mint": "native", "output_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "amount": 0.1, "slippage_pct": 0.5}
    pf, r = await confirm(client, h, "/wallets/swap", body)
    assert r.status_code == 200, r.text
    summary = pf["preflight"]
    assert summary["input"].startswith("0.1 SOL") and "USDC" in summary["output"]
    assert summary["route"] == "GoonFi V2" and summary["platform_fee"] == "none"
    j = r.json()
    status = (await client.get(f"/jobs/{j['job_id']}", headers=h)).json()["status"]
    assert status == "confirmed", j
    from app.db import get_sessionmaker

    async with get_sessionmaker()() as db:
        rows = (await db.execute(select(Transaction).where(Transaction.kind == "swap"))).scalars().all()
        assert rows and rows[-1].status == "confirmed"


async def test_swap_refuses_unknown_tokens_and_same_token(client, fake_rpc, fake_jupiter):
    h = await signup(client, "bad@example.com", main=False)
    w = (await make_wallets(client, h, 1))[0]
    same = {"wallet_id": w["id"], "input_mint": "native", "output_mint": "native", "amount": 1}
    assert (await client.post("/wallets/swap", json=same, headers=h)).status_code == 400


async def test_swap_unsupported_chain_is_refused(client, fake_rpc, fake_jupiter):
    h = await signup(client, "arc@example.com", main=False)
    r = await client.post("/wallets", json={"label": "Arc", "network": "arc"}, headers=h)
    wid = r.json()[-1]["id"]
    out = await client.post("/wallets/swap", json={"wallet_id": wid, "input_mint": "native",
                                                   "output_mint": "native", "amount": 1}, headers=h)
    assert out.status_code == 400 and "Bridge route" in out.text


async def test_evm_swap_routes_through_relay(client, fake_rpc, monkeypatch):
    """Same-chain EVM swap: Relay quote for the preflight, approve capped at the quoted need, then the swap."""
    import httpx as _httpx

    from app.chains.relay import EVM_NATIVE, Relay
    from app.deps import get_executor
    from app.services.executor import Executor
    from app.signing import get_signer

    from .test_bridge import BASE_USDC, ETH, TokenNode, relay_mock

    seen: list = []
    relay = Relay("https://relay.test", client=_httpx.AsyncClient(transport=relay_mock(seen, ["success"])))
    base = TokenNode("base")
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"base": base}, relay=relay)
    try:
        h = await signup(client, "evmswap@example.com", main=False)
        w = (await client.post("/wallets", json={"network": "base", "count": 1}, headers=h)).json()[-1]
        base.tokens[w["address"].lower()] = 50 * 10**6          # 50 USDC
        base.balances[w["address"].lower()] = ETH // 100        # gas money
        body = {"wallet_id": w["id"], "input_mint": BASE_USDC, "output_mint": "native", "amount": 20}
        pre = (await client.post("/wallets/swap", json=body, headers=h)).json()
        p = pre["preflight"]
        assert p["kind"] == "swap" and p["network"] == "base" and p["chain"] == "Base"
        assert p["input"] == "20 USDC" and p["estimated_output"] == "0.03 ETH"
        assert p["quote"]["fees_usd"]["helm_route_fee"] == 0.06 and p["platform_fee"] == "none"
        same = await client.post("/wallets/swap", json={**body, "output_mint": BASE_USDC}, headers=h)
        assert same.status_code == 400 and "same token" in same.text
        bad = await client.post("/wallets/swap", json={**body, "input_mint": "0x1234"}, headers=h)
        assert bad.status_code == 400 and "valid token address" in bad.text

        r = await client.post("/wallets/swap", json={**body, "confirm": True, "preflight_hash": pre["preflight_hash"],
                                                     "idempotency_key": "evm-swap"}, headers=h)
        assert r.status_code == 200, r.text
        job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
        assert job["status"] == "confirmed", job
        assert [t["kind"] for t in job["transactions"]] == ["swap_approve", "swap"]
        # Provider-built transactions go out byte-for-byte (the guard checked, not rewrote, them).
        assert base.sent[-2]["data"] == bytes.fromhex("095ea7b3" + "00" * 12 + "cd" * 20 + "00" * 32)
        assert base.sent[-1]["to"].lower() == "0x" + "cd" * 20 and base.sent[-1]["data"] == bytes.fromhex("abcdef")
        q = seen[-1]
        assert q["originChainId"] == 8453 and q["destinationChainId"] == 8453  # same-chain route
        assert q["originCurrency"] == BASE_USDC and q["destinationCurrency"] == EVM_NATIVE
        assert q["user"] == q["recipient"] == w["address"]
    finally:
        app.dependency_overrides.pop(get_executor, None)


async def test_export_capability_reports_the_signer(client, monkeypatch):
    h = await signup(client, "cap@example.com", main=False)
    r = (await client.get("/wallets/export-capability", headers=h)).json()
    assert r == {"available": True, "reason": None}

    class _TurnkeyLike:
        pass

    monkeypatch.setattr(wallets_module, "get_signer", lambda: _TurnkeyLike())
    r = (await client.get("/wallets/export-capability", headers=h)).json()
    assert r["available"] is False and "Turnkey" in r["reason"]
    # The export endpoint refuses on capability before burning password/2FA attempts.
    w = (await make_wallets(client, h, 1))[0]
    out = await client.post(f"/wallets/{w['id']}/export-key",
                            json={"password": "definitely-wrong", "totp": "000000"}, headers=h)
    assert out.status_code == 400 and "Turnkey" in out.text
