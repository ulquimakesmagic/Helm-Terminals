# Database migrations

Helm uses Alembic with frozen baseline `11d712bf1eb8` and current head `42c9a01f0004`.
Production startup refuses a missing or mismatched migration revision and does not call `create_all`.
Development retains `create_all` for disposable databases; it cannot upgrade changed columns.
Every schema change needs a reviewed Alembic revision.

Run these commands from `backend/` against a local database or an isolated staging copy. The URL comes from the
existing `DATABASE_URL` setting; keep it in the deployment secret store and out of command history and logs.

For a new empty database, run `python -m alembic -c alembic.ini upgrade head` before starting the app.

For an existing database created by `create_all`, first take and verify a backup, then compare its tables and
constraints with this baseline. Only after confirming that it matches, record the baseline without replaying its DDL:
`python -m alembic -c alembic.ini stamp 11d712bf1eb8`. Future revisions can then be applied with
`python -m alembic -c alembic.ini upgrade head`. Never stamp an unknown or partial schema: inspect it and write a
data-preserving migration first.

This remediation adds account recovery tables (`0001`), receipt evidence and manual-review fields (`0002`),
durable nonce reservations (`0003`), and reviewed platform payments, launch exceptions and automation fee consent (`0004`).
Revision `0004` stores large integer amounts as decimal text on SQLite to avoid floating-point rounding;
PostgreSQL continues to use exact NUMERIC(78,0). Its downgrade preserves SQLite text amounts rather than risk
rounding them back into SQLite's numeric affinity. Revision `0002` reclassifies historical unproven DEX fees as
pending estimates; it does not delete the entries. Downgrading does not recertify those fees as received.
Do not stamp the new head to skip these migrations. Stop all writers before a production migration,
back up, apply the migrations once, then start the matching build and check `/health/ready`.

To create a future revision, update the model, run
`python -m alembic -c alembic.ini revision --autogenerate -m "describe the schema change"`, review the generated
operations by hand, and test both a fresh database and an upgrade from the prior revision. Autogenerate is a draft,
not a correctness check. Do not edit the baseline to represent later schema changes.

The repository change does not run or stamp migrations against production. Production adoption needs a separately
scheduled backup, schema comparison, and operator-run stamp; later production upgrades should be a separately
reviewed release step.
