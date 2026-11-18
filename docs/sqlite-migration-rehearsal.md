# M02.8 — Declarative SQLite Migration Contracts and Offline Rehearsal

**Status: experimental, sandbox-only. No live stateful migrations or upgrades are enabled.**

This builds on [Stateful Upgrade Backups](stateful-upgrade-backups.md).
Every migration must be tied to a previous and target add-on release, with
both immutable OCI digests and the expected SQLite database revision.

## Contract v1

The first contract supports only these **reviewed declarative** operations:

- `add_nullable_column` — add a nullable `TEXT`, `INTEGER`, `REAL`, or
  `BLOB` column to a previously existing table
- `create_index` — add an index on previously existing named columns

It cannot run publisher SQL, invoke a shell, drop tables, rewrite records,
change constraints, or access Docker. Invalid/unrecognized fields fail
validation. Every identifier uses a restricted alphanumeric/underscore
format. These limitations are deliberate, not a claim of universal migration
support.

Example local administrator-reviewed `/etc/yonder/migration-contracts/demo-addon.json`:

```json
{
  "schema_version": 1,
  "addon_id": "demo-addon",
  "source_version": "1.0.0",
  "source_image": "ghcr.io/example/demo@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "target_version": "2.0.0",
  "target_image": "ghcr.io/example/demo@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "source_db_revision": 0,
  "target_db_revision": 1,
  "steps": [
    {
      "kind": "add_nullable_column",
      "table": "documents",
      "column": "owner",
      "data_type": "TEXT"
    },
    {
      "kind": "create_index",
      "table": "documents",
      "index_name": "idx_document_owner",
      "columns": ["owner"]
    }
  ]
}
```

The operator controls this file and must restrict modifications. Its current
integrity check is file permissions + exact release identity, **not a
cryptographic publisher signature**. Signatures and immutable migration
artifact provenance remain release gates.

## Sandbox rehearsal

First generate or inspect a *SQLite-native* backup bundle with
`backup_sqlite.py`. Prepare an empty private sandbox directory (0700)
distinct from the backup and the live application:

```bash
mkdir -m 700 /secure/yonder/rehearsal-demo

cd apps/addon-controller
python rehearse_migration.py \
  --contract /etc/yonder/migration-contracts/demo-addon.json \
  --backup /secure/yonder/offline-backups/demo-addon \
  --sandbox /secure/yonder/rehearsal-demo
```

The runner:

1. Validates the reviewed migration contract and immutable prior release
   ID/version/digest against the offline backup.
2. Restores the backup bytes into the **empty sandbox only**.
3. Opens the restored SQLite file, confirms the declared source
   `PRAGMA user_version`, and runs `PRAGMA integrity_check`.
4. Checks foreign-key integrity and records row counts for all tables named
   by the contract, then executes allowed schema changes within one SQLite transaction.
   A failed statement rolls the transaction back in the sandbox.
5. Rechecks database integrity, foreign keys, and preserved row counts.
   Advances `PRAGMA user_version`, commits, and reports
   the resulting revision with explicit
   `production_migration_allowed=false`.

A passing rehearsal does **not** prove that an add-on version 2.0.0 can
successfully run on the migrated schema, that all business data is valid,
or that reversing a migration is safe.

The current rehearsal leaves the sandbox data in place for independent
application-level acceptance testing. It does not modify the source
backup bundle, the live database or any containers.

## Read-only controller integration

`GET /v1/upgrades/{addon_id}` checks a file under the operator-owned
`YONDER_CONTROLLER_MIGRATION_ROOT` directory (default
`/etc/yonder/migration-contracts`) when a persistent add-on declares
`upgrade_data_migration=requires_migration`.

It reports `migration_contract=identity_verified_only` when the contract
matches exactly both releases or `not_verified` otherwise. It never accepts
a browser-supplied path, executes the contract, changes schema or enables a
stateful upgrade. Independently running a rehearsal is the next prerequisite,
not automatic approval.

## Still required before stateful upgrade execution

- Signed publisher migration artifacts and configurable trust-root/key
  rotation and revocation.
- Application-level compatibility validation using the target app version,
  data semantics and representative fixtures.
- Database-specific migration planners (including Postgres), irreversible
  migration analysis, and schema-down/restore strategies.
- Recording verified backup, rehearsal and app-level results in a durable
  authorized release journal.
- Isolation from privileged host APIs; audit and key/backup lifecycle.
- Actual Docker host crash/power-loss testing, safe recovery and rollback.

**Do not run the migration runner against a live Yonder add-on database.**
