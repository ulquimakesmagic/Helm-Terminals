"""Opt-in destructive tests against a dedicated local database named helm_audit_test only."""
import asyncio
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models import ExecutionNonce
from app.services import execution_lease, execution_nonce
from app.signing.base import SigningError


@pytest.fixture
async def pg(monkeypatch):
    url = os.environ.get("HELM_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("HELM_TEST_POSTGRES_URL is not configured; real PostgreSQL proof is required before release")
    parsed = make_url(url)
    assert parsed.host in {"localhost", "127.0.0.1"} and parsed.database == "helm_audit_test", "Only an isolated local audit database is allowed"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(ExecutionNonce.__table__.drop, checkfirst=True)
        await conn.run_sync(ExecutionNonce.__table__.create)
    monkeypatch.setattr(execution_nonce, "get_sessionmaker", lambda: async_sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(execution_nonce, "get_settings", lambda: SimpleNamespace(database_url=url))
    monkeypatch.setattr(execution_lease, "get_settings", lambda: SimpleNamespace(env="production"))
    yield engine
    execution_lease.invalidate()
    await engine.dispose()


async def test_overlapping_workers_reserve_distinct_persistent_nonces(pg):
    values = await asyncio.gather(*(execution_nonce.reserve(8453, "0x" + "ab" * 20, 7) for _ in range(8)))
    assert sorted(values) == list(range(7, 15))
    # A newly opened session still cannot reuse a signed-but-uncertain nonce, even with stale RPC state.
    assert await execution_nonce.reserve(8453, "0x" + "ab" * 20, 7) == 15


async def test_terminated_lock_session_cannot_sign_after_new_worker_acquires(pg):
    key = 0x48454C4D
    first = await pg.connect()
    second = await pg.connect()
    try:
        assert await first.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
        await execution_lease.bind(first, key)
        await execution_lease.assert_owned()
        assert not await second.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
        pid = execution_lease._pid
        await second.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        await second.commit()
        with pytest.raises(SigningError): await execution_lease.assert_owned()
        assert execution_lease._lost
        assert await second.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
        with pytest.raises(SigningError): await execution_lease.assert_owned()
    finally:
        await first.close()
        await second.execute(text("SELECT pg_advisory_unlock_all()"))
        await second.close()
