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

## Signed migration attestation and durable journal

The offline rehearsal command now requires a **detached Ed25519 attestation**
created with a publisher's protected private key. The attestation signs
the canonical migration contract SHA-256, exact prior-release backup manifest
SHA-256, both release versions/image digests, and publisher key ID. The
verifier uses only the local administrator-owned `publishers.json` trust
roots, which may be revoked or expired. The signer never uploads private keys
or exposes them through Gateway APIs.

Create a dedicated signing key in an administrator-approved private store.
The current CLI accepts a 0600 file containing a base64-encoded **raw
32-byte Ed25519 seed**. Protect and rotate it offline; do not commit it.

```bash
cd apps/addon-controller
python sign_migration.py \
  --contract /secure/policies/demo-addon.json \
  --backup-manifest /secure/backups/demo-addon/manifest.json \
  --private-key /secure/keys/migration-publisher.key \
  --publisher-key-id reviewed-migration \
  --output /secure/policies/demo-addon.attestation.json
```

The output is created exclusively and cannot overwrite an existing
attestation. It is not the private key. For rehearsal the verifier requires
the corresponding trusted public key to have been provisioned by a local
administrator in `publishers.json`.

A separate SQLite **rehearsal journal** records the add-on, old/new pinned
images, contract hash, backup hash, result and sandbox hash. Incomplete
`running` rehearsals become `interrupted` when the journal is reopened
after a restart. Interrupted records block fresh attempts until explicit
offline operator acknowledgment; they are **never automatically promoted
to passed or retried**. Keep the journal on private persistent storage.

Example:

```bash
python rehearse_migration.py \
  --contract /secure/policies/demo-addon.json \
  --backup /secure/backups/demo-addon \
  --sandbox /secure/yonder/rehearsal-demo \
  --attestation /secure/policies/demo-addon.attestation.json \
  --publisher-trust /secure/policies/publishers.json \
  --journal /secure/yonder/migration-rehearsals.db
```

A pass records the sandbox SQLite checksum and a durable local run ID.
Signing and journaling establish evidence for an **offline rehearsal only**;
the journal remains locally mutable by a privileged host administrator.
They do **not** authorize a production migration, prove application
compatibility, or reverse data changes.

### Reviewing interrupted rehearsal records

Use the returned `rehearsal_id` to inspect a journal record without
changing the state of an in-progress rehearsal:

```bash
python review_migration.py \
  --journal /secure/yonder/migration-rehearsals.db \
  --id '<rehearsal-id>'
```

If an offline rehearsal was interrupted by a process restart, inspect its
isolated sandbox and verify it is not being used. The operator can then
explicitly clear the **interrupted block**:

```bash
python review_migration.py \
  --journal /secure/yonder/migration-rehearsals.db \
  --id '<rehearsal-id>' \
  --acknowledge-interrupted
```

This records `acknowledged`, **not** `passed`. A new empty sandbox,
a new signed attestation and a fresh rehearsal attempt are needed to
produce new passing evidence. Journal records are local operational
metadata, not cryptographically immutable audit logs.

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
  --sandbox /secure/yonder/rehearsal-demo \
  --attestation /secure/policies/demo-addon.attestation.json \
  --publisher-trust /secure/policies/publishers.json \
  --journal /secure/yonder/migration-rehearsals.db
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

The result includes **unsigned local evidence**: SHA-256 of the canonical
reviewed migration contract, original backup manifest, and migrated sandbox
SQLite file. These can help operators compare exact rehearsals, but the
values do **not** prevent an administrator from forging a receipt.
`receipt_signed=false` is always returned. Signed release attestations and
trusted, auditable rehearsal evidence remain future requirements.

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
