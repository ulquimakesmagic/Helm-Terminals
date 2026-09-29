# Database migrations

Helm now uses Alembic with a frozen baseline at revision `11d712bf1eb8`. The application still calls SQLAlchemy
`create_all` at startup for compatibility with databases already in use; that only creates missing tables and does not
upgrade changed columns. Every future schema change must have a reviewed Alembic revision.

Run these commands from `backend/` against a local database or an isolated staging copy. The URL comes from the
existing `DATABASE_URL` setting; keep it in the deployment secret store and out of command history and logs.

For a new empty database, run `python -m alembic -c alembic.ini upgrade head` before starting the app.

For an existing database created by `create_all`, first take and verify a backup, then compare its tables and
constraints with this baseline. Only after confirming that it matches, record the baseline without replaying its DDL:
`python -m alembic -c alembic.ini stamp 11d712bf1eb8`. Future revisions can then be applied with
`python -m alembic -c alembic.ini upgrade head`. Never stamp an unknown or partial schema: inspect it and write a
data-preserving migration first.

To create a future revision, update the model, run
`python -m alembic -c alembic.ini revision --autogenerate -m "describe the schema change"`, review the generated
operations by hand, and test both a fresh database and an upgrade from the prior revision. Autogenerate is a draft,
not a correctness check. Do not edit the baseline to represent later schema changes.

The repository change does not run or stamp migrations against production. Production adoption needs a separately
scheduled backup, schema comparison, and operator-run stamp; later production upgrades should be a separately
reviewed release step.
