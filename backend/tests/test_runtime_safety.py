import pytest

from app import db as dbmod
from app.main import _acquire_single_replica_lock, _release_single_replica_lock


class _FakeConnection:
    def __init__(self, acquired: bool):
        self.acquired = acquired
        self.closed = False
        self.unlocked = False

    async def scalar(self, *_args, **_kwargs):
        return self.acquired

    async def execute(self, *_args, **_kwargs):
        self.unlocked = True

    async def close(self):
        self.closed = True


class _FakeEngine:
    def __init__(self, connection):
        self.connection = connection

    async def connect(self):
        return self.connection


async def test_postgres_single_replica_lock_is_held_and_released(monkeypatch):
    conn = _FakeConnection(acquired=True)
    monkeypatch.setattr(dbmod, "get_engine", lambda: _FakeEngine(conn))
    settings = type("Settings", (), {"database_url": "postgresql+asyncpg://db/helm"})()

    locked = await _acquire_single_replica_lock(settings)
    assert locked is conn and not conn.closed
    await _release_single_replica_lock(locked)
    assert conn.unlocked and conn.closed


async def test_second_postgres_replica_fails_startup_and_closes_its_connection(monkeypatch):
    conn = _FakeConnection(acquired=False)
    monkeypatch.setattr(dbmod, "get_engine", lambda: _FakeEngine(conn))
    settings = type("Settings", (), {"database_url": "postgresql+asyncpg://db/helm"})()

    with pytest.raises(RuntimeError, match="another Helm API replica"):
        await _acquire_single_replica_lock(settings)
    assert conn.closed


async def test_sqlite_does_not_use_a_postgres_single_replica_lock(monkeypatch):
    def unexpected():
        raise AssertionError("SQLite should not open a PostgreSQL advisory lock")

    monkeypatch.setattr(dbmod, "get_engine", unexpected)
    settings = type("Settings", (), {"database_url": "sqlite+aiosqlite:///./test.db"})()
    assert await _acquire_single_replica_lock(settings) is None
