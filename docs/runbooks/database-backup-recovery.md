# Database backup and recovery runbook

This is an operator procedure for Helm's SQL database. It is not evidence that a production backup or restore has
been tested. Do a restore drill against an isolated local or staging database before relying on the procedure.

## Operating constraints

- Run a single API replica until jobs have durable worker claims, cross-process nonce locks, and shared/distributed
  caches. PostgreSQL API startup holds a session-level advisory lock and refuses a second replica; SQLite development
  processes do not use this PostgreSQL-only guard.
- A job may contain a transaction that was sent but whose result was not observed. Its state must remain
  `unconfirmed`; check the saved signature/hash on the correct explorer or use the idempotent recheck endpoint.
  Never retry a deploy or payment just because a process restarted.
- If a launch is still `deploying` and no transaction identifier was saved, do not reset it to a draft or deploy it
  again. Establish the outcome from the chain and reconcile it through a reviewed operator procedure.
- The database stores signer references, not signing keys. Preserve the separate Turnkey configuration and access
  controls through the approved secrets-management system; never put signing material in a database backup.

## Create and verify a backup

1. Schedule a maintenance window and stop or quiesce all API writers. Confirm there is only one service instance.
2. Create a database-native consistent snapshot or encrypted PostgreSQL custom-format dump from a secured operator
   environment. Read the database URL from the deployment secret store; do not paste it into chat, a ticket, shell
   history, a command log, or this repository.
3. Store the backup in the approved encrypted backup location with restricted access and a retention date. Keep it
   outside the source repository and deployment artifact.
4. Record the backup timestamp, database engine/version, Helm release identifier, and an integrity checksum in the
   operator's protected run record. Verify that the snapshot completed and that its size is plausible.
5. Resume the single API instance and confirm readiness. Do not run schema migrations as part of backup creation.

## Restore drill (local or isolated staging only)

1. Create a new empty database that is isolated from production RPCs and cannot submit transactions. Use test signer
   settings and fake or read-only providers.
2. Restore the selected backup into that database. Do not restore over production during a drill.
3. Compare table names, constraints, row counts for critical ledgers, and the `alembic_version` state. Check a sample
   of pending and unconfirmed jobs and ensure their transaction identifiers remain intact.
4. Start the matching Helm build against the restored database. Check `/health/ready`, sign-in using a designated
   test account, and read-only launch, wallet metadata, admin revenue, and audit-log pages. Do not confirm a spend or
   broadcast a transaction.
5. Record the build, backup, restore duration, integrity checks, errors, and follow-up work. Remove the isolated
   database and its copy of personal data according to the approved retention policy.

## Migration handling

Use the steps in [`backend/migrations/README.md`](../../backend/migrations/README.md). Fresh databases use
`alembic upgrade head`. Existing `create_all` databases require a verified backup and schema comparison before
stamping the frozen baseline `11d712bf1eb8`; never stamp an unknown or partial schema. Production stamping and
upgrades require a separately approved, scheduled release and were not performed by this code change.

## Recovery from an uncertain transaction

1. Keep the affected job and launch in their current pending, unconfirmed, or deploying state.
2. Read the saved transaction signature/hash and network from Helm's job details; verify the result on that chain's
   explorer or through the supported recheck endpoint.
3. A missing receipt is not proof of failure. Wait and check again; if the identifier is absent after a crash, use the
   launch wallet's chain history and have an operator resolve the state. Do not create a second transaction to
   “test” whether the first one landed.
4. Record any manual resolution in the normal audit trail and verify the transaction ledger and fee/revenue entries
   agree with the terminal chain outcome.
