"""Recovery exercises local ASGI/SQLite and a captured mailbox only."""
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from .conftest import TEST_NEW_PW, TEST_PW, signup
from app.db import get_sessionmaker
from app.models import AccountSecurity, AccountStatus, RecoveryToken, User, utcnow
from app.services import account_recovery as recovery
from app.security import token_hash


@pytest.fixture
def mailbox(monkeypatch):
    monkeypatch.setattr(recovery, "require_mail", lambda: None)
    captured = AsyncMock()
    monkeypatch.setattr(recovery, "deliver", captured)
    return captured


async def test_verify_reset_revokes_sessions_and_holds_spending(client, mailbox):
    headers = await signup(client)
    r = await client.post("/auth/email/request", headers=headers, json={"password": TEST_PW})
    assert r.status_code == 200, r.text
    link = mailbox.call_args.args[2].split("#verify:")[1].split()[0]
    async with get_sessionmaker()() as db:
        row = (await db.execute(select(RecoveryToken))).scalar_one()
        assert row.token_hash == token_hash(link) and row.token_hash != link
    assert (await client.post("/auth/email/verify", json={"token": link})).status_code == 200
    assert (await client.post("/auth/email/verify", json={"token": link})).status_code == 400
    assert (await client.get("/auth/me", headers=headers)).json()["email_verified"]
    response = await client.post("/auth/password/request", json={"email": "a@example.com"})
    reset = mailbox.call_args.args[2].split("#reset:")[1].split()[0]
    missing = await client.post("/auth/password/request", json={"email": "unknown@example.com"})
    assert response.json() == missing.json()
    r = await client.post("/auth/password/reset", json={"token": reset, "new_password": TEST_NEW_PW})
    assert r.status_code == 200, r.text
    assert (await client.get("/auth/me", headers=headers)).status_code == 401
    assert (await client.post("/auth/password/reset", json={"token": reset, "new_password": TEST_PW})).status_code == 400
    async with get_sessionmaker()() as db:
        state = (await db.execute(select(AccountSecurity))).scalar_one()
        assert state.recovery_hold_until > utcnow() + timedelta(hours=23)
    assert "security" in mailbox.call_args.args[1]


async def test_recovery_codes_are_user_bound_and_single_use(client, mailbox):
    await signup(client)
    async with get_sessionmaker()() as db:
        user = (await db.execute(select(User))).scalar_one()
        codes = await recovery.new_codes(db, user.id)
        assert not await recovery.consume_code(db, "someone-else", codes[0])
        assert await recovery.consume_code(db, user.id, codes[0])
        assert not await recovery.consume_code(db, user.id, codes[0])
        await recovery.new_codes(db, user.id)
        assert not await recovery.consume_code(db, user.id, codes[1])


async def test_expired_and_suspended_recovery_rejected(client, mailbox):
    await signup(client)
    async with get_sessionmaker()() as db:
        user = (await db.execute(select(User))).scalar_one()
        db.add(RecoveryToken(user_id=user.id, purpose="reset", token_hash=token_hash("x" * 24), expires_at=utcnow()-timedelta(seconds=1)))
        db.add(RecoveryToken(user_id=user.id, purpose="verify", token_hash=token_hash("y" * 24), expires_at=utcnow()+timedelta(minutes=1)))
        db.add(AccountStatus(user_id=user.id, status="suspended"))
        await db.commit()
    assert (await client.post("/auth/password/reset", json={"token": "x" * 24, "new_password": "new-long-password"})).status_code == 400
    assert (await client.post("/auth/email/verify", json={"token": "y" * 24})).status_code == 403


async def test_email_reset_cannot_bypass_mfa(client, mailbox):
    await signup(client)
    async with get_sessionmaker()() as db:
        user = (await db.execute(select(User))).scalar_one()
        user.totp_enabled = True
        db.add(AccountSecurity(user_id=user.id, email_verified_at=utcnow()))
        db.add(RecoveryToken(user_id=user.id, purpose="reset", token_hash=token_hash("z" * 24), expires_at=utcnow()+timedelta(minutes=1)))
        await db.commit()
    r = await client.post("/auth/password/reset", json={"token": "z" * 24, "new_password": "new-long-password"})
    assert r.status_code == 401 and r.json()["detail"] == "totp_required"
