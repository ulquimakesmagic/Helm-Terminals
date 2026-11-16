from typing import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
from sqlalchemy import Numeric, String
from sqlalchemy.types import TypeDecorator

from .config import get_settings


class Base(DeclarativeBase):
    pass


class ExactRawAmount(TypeDecorator):
    """Integers up to uint256: NUMERIC on PostgreSQL, decimal text on SQLite (never a float)."""
    impl = Numeric(78, 0)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(String(78) if dialect.name == "sqlite" else Numeric(78, 0))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value != int(value):
            raise ValueError("Raw amounts must be whole base units")
        return str(int(value)) if dialect.name == "sqlite" else int(value)

    def process_result_value(self, value, dialect):
        return int(value) if value is not None else None


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        url = get_settings().database_url
        kwargs = {"poolclass": NullPool} if url.startswith("sqlite") else {}
        _engine = create_async_engine(url, **kwargs)
        if url.startswith("sqlite"):
            # SQLite ignores foreign keys unless asked. Postgres (production) enforces them, so enforce here too,
            # otherwise an insert-ordering bug passes in dev/tests and only fails in production.
            @event.listens_for(_engine.sync_engine, "connect")
            def _fk_on(dbapi_conn, _record):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()

        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


async def create_all() -> None:
    from . import models  # noqa: F401  (register tables)

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


SCHEMA_REVISION = "42c9a01f0007"


async def require_schema() -> None:
    """Production never adopts or mutates a schema implicitly at API startup."""
    from sqlalchemy import text
    try:
        async with get_engine().connect() as conn:
            versions = list((await conn.execute(text("SELECT version_num FROM alembic_version"))).scalars())
    except Exception:
        raise RuntimeError("Database migration state unavailable. Back up and migrate explicitly before starting Helm.") from None
    if versions != [SCHEMA_REVISION]:
        raise RuntimeError(f"Database schema does not match {SCHEMA_REVISION}; migrate before starting Helm")
