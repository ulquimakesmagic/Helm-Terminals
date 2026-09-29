"""Regression tests for the September 2026 security review: ways a stolen session or bot key could move funds out
without 2FA, 2FA brute force and replay, sign-out, and values echoed in errors."""

import base64

import pyotp
import pytest
from solders.keypair import Keypair
from sqlalchemy import select

from app.db import get_sessionmaker
from app.main import check_production_config
from app.models import User
from app.services.token_meta import _public_host

from .conftest import SOL, enable_2fa, signup, totp_code

PW = "correct-horse-battery"
ATTACKER = str(Keypair().pubkey())


async def _managed(client, h, n=1):
    return (await client.post("/wallets", json={"count": n}, headers=h)).json()


async def test_funding_a_watch_only_address_needs_2fa(client, fake_rpc):
    h = await signup(client, main=False)
    secret = await enable_2fa(client, h)
    src = (await _managed(client, h))[0]
    fake_rpc.balances[src["address"]] = 5 * SOL
    watch = (await client.post("/wallets/watch", json={"address": ATTACKER}, headers=h)).json()
    body = {"funder_wallet_id": src["id"], "allocations": [{"wallet_id": watch["id"], "sol": 1}]}
    pf = (await client.post("/wallets/fund", json=body, headers=h)).json()
    assert pf["requires_2fa"] is True and pf["preflight"]["kind"] == "fund_external"
    confirm = {**body, "confirm": True, "preflight_hash": pf["preflight_hash"], "idempotency_key": "f1"}
    r = await client.post("/wallets/fund", json=confirm, headers=h)
    assert r.status_code == 401 and r.json()["detail"].startswith("totp_required")
    r = await client.post("/wallets/fund", json={**confirm, "totp": await totp_code(secret)}, headers=h)
    assert r.status_code == 200
    # funding the account's own managed wallets still needs no code
    own = (await _managed(client, h))[0]
    body = {"funder_wallet_id": src["id"], "allocations": [{"wallet_id": own["id"], "sol": 0.1}]}
    pf = (await client.post("/wallets/fund", json=body, headers=h)).json()
    assert pf["requires_2fa"] is False and pf["preflight"]["kind"] == "fund"


async def test_withdraw_kinds_need_2fa_even_if_config_drops_them(client, monkeypatch):
    from app.config import get_settings
    from app.deps import needs_2fa

    monkeypatch.setattr(get_settings(), "tfa_required_kinds", "")
    assert needs_2fa("withdraw") and needs_2fa("send_tokens") and needs_2fa("fund_external")
    assert not needs_2fa("fund") and not needs_2fa("trade")


async def test_importing_a_wallet_needs_the_password_the_2fa_code_and_the_web_app(client):
    h = await signup(client, main=False)
    key = str(Keypair())
    r = await client.post("/wallets/import", json={"network": "solana", "private_key": key, "password": "nope"}, headers=h)
    assert r.status_code == 400 and r.json()["detail"] == "wrong password"
    secret = await enable_2fa(client, h)
    r = await client.post("/wallets/import", json={"network": "solana", "private_key": key, "password": PW}, headers=h)
    assert r.status_code == 401 and r.json()["detail"].startswith("totp_required")
    r = await client.post("/wallets/import", json={"network": "solana", "private_key": key, "password": PW,
                                                   "totp": await totp_code(secret)}, headers=h)
    assert r.status_code == 201


async def test_enabling_2fa_needs_the_password(client):
    h = await signup(client, main=False)
    r = await client.post("/auth/totp/enable", json={"password": "stolen-session-guess"}, headers=h)
    assert r.status_code == 400
    r = await client.post("/auth/totp/enable", json={"password": PW}, headers=h)
    assert r.status_code == 200 and r.json()["secret"]


async def test_a_2fa_code_works_once(client):
    h = await signup(client, "replay@example.com", main=False)
    secret = await enable_2fa(client, h)
    code = await totp_code(secret)
    login = {"email": "replay@example.com", "password": PW, "totp": code}
    assert (await client.post("/auth/login", json=login)).status_code == 200
    r = await client.post("/auth/login", json=login)  # same code again
    assert r.status_code == 401


async def test_password_guessing_is_locked_out(client):
    await signup(client, "guess@example.com", main=False)
    for _ in range(10):
        r = await client.post("/auth/login", json={"email": "guess@example.com", "password": "wrong"})
        assert r.status_code == 401
    r = await client.post("/auth/login", json={"email": "guess@example.com", "password": PW})
    assert r.status_code == 429  # even the right password waits out the window
    # unknown emails are counted too, and say nothing different
    r = await client.post("/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert r.status_code == 401 and r.json()["detail"] == "invalid credentials"


async def test_2fa_guessing_is_locked_out(client, fake_rpc):
    h = await signup(client, "tfa@example.com", main=False)
    secret = await enable_2fa(client, h)
    for _ in range(8):
        r = await client.post("/auth/login", json={"email": "tfa@example.com", "password": PW, "totp": "000000"})
        assert r.status_code == 401
    r = await client.post("/auth/login", json={"email": "tfa@example.com", "password": PW,
                                               "totp": await totp_code(secret)})
    assert r.status_code == 429


async def test_sign_out_ends_the_session_on_the_server(client):
    h = await signup(client, main=False)
    assert (await client.get("/wallets", headers=h)).status_code == 200
    assert (await client.post("/auth/logout", json={}, headers=h)).status_code == 204
    assert (await client.get("/wallets", headers=h)).status_code == 401


async def test_validation_errors_never_echo_submitted_values(client):
    h = await signup(client, main=False)
    secret_bytes = list(range(64))
    r = await client.post("/wallets/import", json={"network": "solana", "private_key": secret_bytes, "password": PW},
                          headers=h)
    assert r.status_code == 422 and "input" not in r.text and "63" not in r.text


async def test_token_images_must_really_be_images(client):
    h = await signup(client, main=False)
    html = base64.b64encode(b"<html><script>alert(1)</script></html>").decode()
    r = await client.post("/launches", json={"name": "X", "symbol": "X", "image_b64": html}, headers=h)
    assert r.status_code == 400 and "PNG" in r.json()["detail"]
    r = await client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"


async def test_registration_attempts_are_rate_limited_by_client_address(client):
    body = {"email": "signup-limit@example.com", "password": PW}
    assert (await client.post("/auth/register", json=body)).status_code == 201
    for _ in range(4):
        assert (await client.post("/auth/register", json=body)).status_code == 409
    assert (await client.post("/auth/register", json=body)).status_code == 429


async def test_readiness_checks_database_without_exposing_configuration(client):
    response = await client.get("/health/ready")
    assert response.status_code == 200 and response.json() == {"ready": True}
    assert (await client.get("/health")).json() == {"ok": True}


def test_production_refuses_the_default_secret_key():
    class S:
        env, secret_key, signer, superadmin_user_id = "production", "dev-secret-change-me", "turnkey", ""
    with pytest.raises(RuntimeError):
        check_production_config(S())
    S.secret_key = "x" * 40
    check_production_config(S())
    S.signer = "local"
    with pytest.raises(RuntimeError, match="SIGNER=turnkey"):
        check_production_config(S())
    S.env, S.secret_key = "development", "dev-secret-change-me"
    check_production_config(S())


def test_test_environment_refuses_local_signer():
    class S:
        env, signer = "test", "local"
    with pytest.raises(RuntimeError, match="ENV=test"):
        check_production_config(S())


def test_forwarded_ip_is_ignored_unless_peer_is_trusted(monkeypatch):
    from types import SimpleNamespace

    from app.config import get_settings
    from app.services.guard import client_address

    request = SimpleNamespace(client=SimpleNamespace(host="198.51.100.20"), headers={"x-forwarded-for": "1.2.3.4"})
    monkeypatch.setattr(get_settings(), "trusted_proxy_cidrs", "")
    assert client_address(request) == "198.51.100.20"

    request.client.host = "10.10.0.2"
    request.headers["x-forwarded-for"] = "203.0.113.55"
    monkeypatch.setattr(get_settings(), "trusted_proxy_cidrs", "10.10.0.0/24")
    assert client_address(request) == "203.0.113.55"

    # A spoofed value before the proxy-appended client IP cannot win the right-to-left chain walk.
    request.headers["x-forwarded-for"] = "1.2.3.4, 203.0.113.55"
    assert client_address(request) == "203.0.113.55"


async def test_metadata_fetch_refuses_internal_addresses(monkeypatch):
    import socket

    for ip in ("100.64.0.7", "10.0.0.1", "127.0.0.1", "169.254.169.254"):
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, _ip=ip, **k: [(2, 1, 6, "", (_ip, 443))])
        assert await _public_host("https://example.com/x.json") is False
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    assert await _public_host("https://example.com/x.json") is True


async def test_metadata_transport_uses_only_the_validated_dns_answer():
    import socket

    import pytest

    from app.services.token_meta import _PinnedResolver

    resolver = _PinnedResolver("metadata.example", [("93.184.216.34", socket.AF_INET)])
    result = await resolver.resolve("metadata.example", 443, socket.AF_UNSPEC)
    assert [item["host"] for item in result] == ["93.184.216.34"]
    with pytest.raises(OSError):
        await resolver.resolve("attacker.example", 443, socket.AF_UNSPEC)
    with pytest.raises(OSError):
        await resolver.resolve("metadata.example", 80, socket.AF_UNSPEC)


async def test_a_timed_out_transaction_is_unconfirmed_not_failed():
    from app.models import Job
    from app.services.executor import SubmitResult, _finish

    j = Job(user_id="u", kind="trade_buy", params={})
    _finish(j, [SubmitResult("s1", "confirmed", None), SubmitResult("s2", "timeout", "not confirmed in time")])
    assert j.status == "unconfirmed"
    _finish(j, [SubmitResult(None, "failed", "sim")])
    assert j.status == "failed"
    _finish(j, [SubmitResult("s1", "confirmed", None), SubmitResult(None, "failed", "x")])
    assert j.status == "partial"


async def test_an_orphaned_job_stops_spinning(client):
    from datetime import timedelta

    from app.models import Job, utcnow

    h = await signup(client, main=False)
    async with get_sessionmaker()() as db:
        uid = (await db.execute(select(User.id))).scalars().first()
        j = Job(user_id=uid, kind="trade_buy", params={}, status="submitted")
        db.add(j)
        await db.commit()
        j.updated_at = utcnow() - timedelta(minutes=20)
        await db.commit()
        jid = j.id
    r = (await client.get(f"/jobs/{jid}", headers=h)).json()
    assert r["status"] == "unconfirmed" and "explorer" in r["error"]


async def test_confirmation_polling_is_fast_then_backs_off():
    import httpx

    from app.chains.solana import SolanaRPC

    seen = []

    def handler(req):
        import json as _j
        seen.append(_j.loads(req.content)["params"][1]["searchTransactionHistory"])
        return httpx.Response(200, json={"result": {"value": [None]}})

    rpc = SolanaRPC("https://rpc.test", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    status, _ = await rpc.confirm("sig", timeout_s=1.0)
    assert status == "timeout"
    assert 4 <= len(seen) <= 7  # about every 0.25 s within the first second
    assert seen[-1] is True and not any(seen[:-1])  # full history only on the final check


async def test_a_deploy_that_landed_late_is_found(client, fake_rpc):
    from app.models import Launch

    h = await signup(client, main=False)
    async with get_sessionmaker()() as db:
        uid = (await db.execute(select(User.id))).scalars().first()
        l = Launch(user_id=uid, name="Late", symbol="LATE", launchpad="pumpfun", network="solana", status="deploying",
                   mint_address=str(Keypair().pubkey()), launchpad_params={"unconfirmed_since": "2026-01-01T00:00:00"})
        db.add(l)
        await db.commit()
        lid = l.id
    r = (await client.post(f"/launches/{lid}/recheck", json={}, headers=h)).json()
    assert r["status"] == "live"


async def test_a_send_or_confirm_outage_is_unconfirmed_not_failed():
    import httpx
    from cryptography.fernet import Fernet
    from solders.pubkey import Pubkey

    from app.chains.solana import SolanaRPC, build_unsigned, sol_transfer_ix
    from app.services.executor import Executor
    from app.signing.local import LocalDevSigner

    state = {"fail": "confirm"}

    def handler(req):
        import json as _j
        m = _j.loads(req.content)["method"]
        if m == "simulateTransaction":
            return httpx.Response(200, json={"result": {"value": {"err": None}}})
        if m == "sendTransaction":
            if state["fail"] == "send":
                return httpx.Response(503)
            return httpx.Response(200, json={"result": "sig"})
        return httpx.Response(503)  # getSignatureStatuses: the node is down

    rpc = SolanaRPC("https://rpc.test", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    signer = LocalDevSigner(Fernet.generate_key(), "development")
    w = await signer.create_wallet("u", "solana", "w")
    p = Pubkey.from_string(w.address)
    raw = build_unsigned(p, [sol_transfer_ix(p, p, 1)], "11111111111111111111111111111111")
    ex = Executor(rpc, signer)
    res = await ex.submit(raw, {w.address: w.signer_ref})
    assert res.status == "timeout" and res.signature
    state["fail"] = "send"
    rpc.ring.items[:] = rpc.ring.items  # same endpoint; the outage is on send now
    res = await ex.submit(raw, {w.address: w.signer_ref})
    assert res.status == "timeout" and res.signature and "check the explorer" in res.error
