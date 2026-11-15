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
        assert version == "42c9a01f0006"


def test_existing_create_all_schema_can_be_adopted_without_data_loss(tmp_path):
    path = tmp_path / "existing.db"
    url = f"sqlite+aiosqlite:///{path.as_posix()}"
    # Simulate the frozen legacy schema without a migration marker, not today's models.
    run_alembic(url, "upgrade", "11d712bf1eb8")
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(text(
            "INSERT INTO auth_failures (id, key, created_at) VALUES (:id, :key, :created_at)"
        ), {"id": "keep-me", "key": "redacted-test-key", "created_at": "2026-09-29 00:00:00"})
    engine.dispose()

    # Production's current create_all-created schema is adopted explicitly after the owner verifies the schema;
    # Alembic does not replay the baseline against existing tables.
    run_alembic(url, "stamp", "11d712bf1eb8")
    run_alembic(url, "upgrade", "head")

    with sqlite3.connect(path) as db:
        row = db.execute("SELECT key FROM auth_failures WHERE id='keep-me'").fetchone()
        assert row == ("redacted-test-key",)
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "42c9a01f0006"


def test_upgrade_preserves_data_and_downgrade_does_not_recertify_fees(tmp_path):
    path = tmp_path / "upgrade.db"
    url = f"sqlite+aiosqlite:///{path.as_posix()}"
    run_alembic(url, "upgrade", "11d712bf1eb8")
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO revenue_events (id, source, network, asset, amount_raw, decimals, estimated, status, note, created_at) "
                   "VALUES ('old-fee', 'dex_trade_fee', 'base', 'ETH', 100, 18, 0, 'received', '', '2026-09-29')")
        db.execute("INSERT INTO revenue_events (id, source, network, asset, amount_raw, decimals, estimated, status, note, created_at) "
                   "VALUES ('large-fee', 'dex_trade_fee', 'base', 'ETH', 1e27, 18, 1, 'pending', '', '2026-09-29')")
    run_alembic(url, "upgrade", "head")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status, estimated, receipt_evidence FROM revenue_events WHERE id='old-fee'").fetchone() == ("pending", 1, None)
        assert db.execute("SELECT amount_raw FROM revenue_events WHERE id='large-fee'").fetchone()[0] == str(10**27)
        backup = tmp_path / "backup.db"
        with sqlite3.connect(backup) as restored:
            db.backup(restored)
    run_alembic(url, "downgrade", "11d712bf1eb8")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status FROM revenue_events WHERE id='old-fee'").fetchone() == ("pending",)
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "42c9a01f0006"


def test_retirement_cancels_pending_work_preserves_submitted_receipts_and_never_reactivates(tmp_path):
    from datetime import datetime
    from sqlalchemy.orm import Session
    from app.models import Automation, AutomationLaunchSlot, Job, Launch, Profile, Transaction, User
    path=tmp_path/'retirement.db'
    url=f'sqlite+aiosqlite:///{path.as_posix()}'
    run_alembic(url,'upgrade','42c9a01f0004')
    engine=create_engine(f'sqlite:///{path.as_posix()}')
    with Session(engine) as session:
        user=User(email='retirement-fixture@example.com',password_hash='fixture-hash-not-a-real-login')
        session.add(user);session.flush()
        launch=Launch(user_id=user.id,name='Historical launch',symbol='HIST')
        session.add(launch);session.flush()
        queued=Job(user_id=user.id,launch_id=launch.id,kind='scheduled_buy',status='pending')
        submitted=Job(user_id=user.id,launch_id=launch.id,kind='scheduled_sell',status='submitted')
        manual=Job(user_id=user.id,launch_id=launch.id,kind='trade_buy',status='pending')
        session.add_all([queued,submitted,manual]);session.flush()
        record=Transaction(job_id=submitted.id,kind='sell',signature='historical-submitted-signature',status='unconfirmed')
        plan=Automation(user_id=user.id,launch_id=launch.id,status='scheduled',next_run_at=datetime(2030,1,1),
                        idempotency_key='retired-plan',preflight_hash='a'*64,last_job_id=submitted.id)
        session.add_all([record,plan,Profile(user_id=user.id,is_public=True)]);session.flush()
        session.add(AutomationLaunchSlot(launch_id=launch.id,automation_id=plan.id))
        session.commit();ids=queued.id,submitted.id,manual.id
    engine.dispose()
    run_alembic(url,'upgrade','head')
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT status FROM automations').fetchone()==('cancelled',)
        assert db.execute('SELECT COUNT(*) FROM automation_launch_slots').fetchone()==(0,)
        assert db.execute('SELECT signature,status FROM transactions').fetchone()==('historical-submitted-signature','unconfirmed')
        assert [db.execute('SELECT status FROM jobs WHERE id=?',(identifier,)).fetchone()[0] for identifier in ids]==['failed','submitted','pending']
        assert db.execute('SELECT is_public FROM profiles').fetchone()==(0,)
    run_alembic(url,'downgrade','42c9a01f0004')
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT status FROM automations').fetchone()==('cancelled',)
        assert db.execute('SELECT is_public FROM profiles').fetchone()==(0,)

