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
        await _acquire_single_replica_lock(settings, wait_s=0.05, poll_s=0.01)
    assert conn.closed


async def test_replica_lock_waits_out_an_overlapping_deploy_and_then_acquires(monkeypatch):
    class _FlakyConnection(_FakeConnection):
        def __init__(self):
            super().__init__(acquired=False)
            self.tries = 0

        async def scalar(self, *_args, **_kwargs):
            self.tries += 1
            return self.tries >= 3  # the old replica stops after two failed attempts

    conn = _FlakyConnection()
    monkeypatch.setattr(dbmod, "get_engine", lambda: _FakeEngine(conn))
    settings = type("Settings", (), {"database_url": "postgresql+asyncpg://db/helm"})()

    locked = await _acquire_single_replica_lock(settings, wait_s=5, poll_s=0.01)
    assert locked is conn and not conn.closed and conn.tries == 3


async def test_sqlite_does_not_use_a_postgres_single_replica_lock(monkeypatch):
    def unexpected():
        raise AssertionError("SQLite should not open a PostgreSQL advisory lock")

    monkeypatch.setattr(dbmod, "get_engine", unexpected)
    settings = type("Settings", (), {"database_url": "sqlite+aiosqlite:///./test.db"})()
    assert await _acquire_single_replica_lock(settings) is None
