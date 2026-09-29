"""Alembic bootstrap and adoption checks against isolated SQLite files only."""

import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from app import models  # noqa: F401 - register model metadata
from app.db import Base

BACKEND = Path(__file__).resolve().parents[1]
ALEMBIC = [sys.executable, "-m", "alembic", "-c", "alembic.ini"]


def run_alembic(db_url: str, *args: str) -> None:
    result = subprocess.run(
        [*ALEMBIC, "-x", f"db_url={db_url}", *args],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"Alembic failed:\n{result.stdout}\n{result.stderr}"


def test_alembic_bootstraps_a_fresh_database(tmp_path):
    path = tmp_path / "fresh.db"
    url = f"sqlite+aiosqlite:///{path.as_posix()}"
    run_alembic(url, "upgrade", "head")

    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "users" in tables and "transactions" in tables and "alembic_version" in tables
        version = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert version == "11d712bf1eb8"


def test_existing_create_all_schema_can_be_adopted_without_data_loss(tmp_path):
    path = tmp_path / "existing.db"
    url = f"sqlite+aiosqlite:///{path.as_posix()}"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO auth_failures (id, key, created_at) VALUES (:id, :key, :created_at)"
        ), {"id": "keep-me", "key": "redacted-test-key", "created_at": "2026-09-29 00:00:00"})
    engine.dispose()

    # Production's current create_all-created schema is adopted explicitly after the owner verifies the schema;
    # Alembic does not replay the baseline against existing tables.
    run_alembic(url, "stamp", "head")
    run_alembic(url, "upgrade", "head")

    with sqlite3.connect(path) as db:
        row = db.execute("SELECT key FROM auth_failures WHERE id='keep-me'").fetchone()
        assert row == ("redacted-test-key",)
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "11d712bf1eb8"
