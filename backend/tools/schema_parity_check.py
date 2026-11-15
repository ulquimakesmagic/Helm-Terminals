"""Read-only schema comparison between a live database and Helm's current models.

Purpose: the migrations README requires confirming that an existing (create_all-built)
database matches the baseline before stamping it. Alembic's autogenerate refuses to run
against a database with no alembic_version state, so this script performs the comparison
directly via SQLAlchemy reflection. It never writes to the database.

Usage (from backend/):
    ./.venv/Scripts/python.exe tools/schema_parity_check.py "<DATABASE_URL>"

The URL is the same DATABASE_URL form Helm uses (postgresql+asyncpg://... on production;
never paste it into chat, tickets or command history).

Exit code 0 when differences are exactly the expected adoption set; exit 1 when the schema
needs a data-preserving migration before stamping (or anything unexpected appears).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make "app" importable from backend/

from sqlalchemy import inspect  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.db import Base  # noqa: E402
from app import models  # noqa: F401,E402  (register all mapped tables)

EXPECTED_MISSING_TABLES = {"account_security", "recovery_codes", "recovery_tokens", "execution_nonces"}
EXPECTED_MISSING_COLUMNS = {("revenue_events", "receipt_evidence"), ("revenue_events", "reviewed_at")}


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    url = sys.argv[1]
    if not url.startswith(("postgresql+asyncpg://", "sqlite+aiosqlite://")):
        print("Pass the DATABASE_URL form Helm uses (postgresql+asyncpg://... or sqlite+aiosqlite:///path).")
        return 2

    async def compare() -> tuple[list[str], set[str]]:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                names = await conn.run_sync(
                    lambda sync: insp.get_table_names() if (insp := inspect(sync)) else [])
                live_tables = set(names)
                problems: list[str] = []
                model_tables = set(Base.metadata.tables)
                missing_tables = model_tables - live_tables
                unexpected = live_tables - model_tables - {"alembic_version"}
                for table in sorted(missing_tables):
                    line = f"missing table: {table}"
                    print(line)
                    if table not in EXPECTED_MISSING_TABLES:
                        problems.append(line)
                for table in sorted(unexpected):
                    line = f"unexpected table (not in models): {table}"
                    print(line)
                    problems.append(line)
                for table in sorted(model_tables & live_tables):
                    live_cols = {c["name"] for c in await conn.run_sync(
                        lambda sync, t=table: inspect(sync).get_columns(t))}
                    model_cols = {c.name for c in Base.metadata.tables[table].columns}
                    for col in sorted(model_cols - live_cols):
                        line = f"missing column: {table}.{col}"
                        print(line)
                        if (table, col) not in EXPECTED_MISSING_COLUMNS:
                            problems.append(line)
                    for col in sorted(live_cols - model_cols):
                        line = f"extra column (not in models): {table}.{col}"
                        print(line)
                        problems.append(line)
                return problems, missing_tables
        finally:
            await engine.dispose()

    problems, missing = asyncio.run(compare())
    if problems:
        print(f"\nRESULT: {len(problems)} difference(s) OUTSIDE the expected adoption set.")
        print("Do NOT stamp. Write a data-preserving migration first (see migrations/README.md).")
        return 1
    detail = f"{len(missing)} expected new tables" if missing else "no missing tables"
    print(f"\nRESULT: matches the adoption expectation ({detail} + the expected revenue_events columns)."
          " Safe to stamp baseline 11d712bf1eb8, then upgrade head.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
