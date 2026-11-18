# M02.8 — Stateful upgrade backup and restore prerequisites

**Status:** offline backup package creation, content verification and empty-sandbox
restoration implemented in a draft PR. **Stateful upgrade execution stays blocked.**
This is not a validated live database backup system or a production runbook.

## Purpose

Before a stateful add-on (Convyx, Workspaces, Identity, Academy or any database-backed
extension) can receive an image upgrade, Yonder must establish that the **previous
release's data is recoverable**. Container rollback never reverses a database schema
migration, and retaining a Docker volume is not a backup.

The foundation includes offline export bundles and SQLite-native snapshots:

- a local administrator declares that a database export is already transactionally
  consistent or that the source has been stopped;
- a new private backup directory is created with a SHA-256 file manifest;
- the verifier checks the exact add-on ID, previous version, previous immutable
  image digest, UTC freshness and declared quiescence;
- file sizes and digests are validated; symlinks, unexpected files, duplicate/
  traversal paths and unapproved restore destinations are rejected;
- an operator can restore bytes into a **separate empty private directory** to
  prove the stored data is readable.

**Important:** The manifest's `source_quiesced` flag is an *operator statement*.
The controller cannot independently prove that the database transaction state was
consistent. The manifest and checksums are not cryptographically signed; someone
who can modify the bundle can alter data and manifest together. Keep backups in a
secure, independently controlled location and use signed provenance in a later phase.

## Offline workflow (development/lab only)

1. Stop or quiesce the add-on and create a database-native consistent export
   using that application's supported backup command. Do not point the helper
   at a live SQLite WAL database, PostgreSQL storage directory, or changing
   Docker volume.
2. Ensure the exported file is a regular file, not a symlink, and the backup
   destination path does not exist. Run:

```bash
cd apps/addon-controller
python create_backup.py \
  --source-file /secure/exports/addon-db.dump \
  --output-directory /secure/backups/demo-addon \
  --addon-id demo-addon \
  --version 1.0.0 \
  --image 'ghcr.io/example/demo@sha256:<64-hex-digest>' \
  --acknowledge-source-quiesced
```

3. To recheck the bundle:

```bash
python verify_backup.py \
  --source /secure/backups/demo-addon \
  --addon-id demo-addon \
  --version 1.0.0 \
  --image 'ghcr.io/example/demo@sha256:<64-hex-digest>'
```

4. Prepare a **different, empty, private (0700)** local directory on an
   isolated host or sandbox. Never use a live volume directory. Restore-test:

```bash
mkdir -m 700 /secure/restore-test
python verify_backup.py \
  --source /secure/backups/demo-addon \
  --addon-id demo-addon \
  --version 1.0.0 \
  --image 'ghcr.io/example/demo@sha256:<64-hex-digest>' \
  --restore-sandbox /secure/restore-test
```

5. Independently run the add-on's database restore and application-level
   integrity checks against the sandbox. Those application-specific checks
   are **not** implemented by this generic helper.

## SQLite-native consistent snapshot

SQLite-backed add-ons can now use a dedicated **SQLite online backup API**
instead of manually exporting a quiesced database. This method creates an
internally consistent copy of SQLite's pages (including a database that uses
WAL mode) using `sqlite3.Connection.backup`, then checks its SQLite structure
with `PRAGMA quick_check` and packages the snapshot in a private bundle.

The SQLite database is opened read-only. The resulting snapshot is an
independent file, so its bundle metadata records
`consistency_method: sqlite_online_backup` and
`source_quiesced: false` — it does **not** claim the original database or
application stopped running.

```bash
cd apps/addon-controller
python backup_sqlite.py \
  --source-database /secure/app-data/yonder-addon.db \
  --output-directory /secure/backups/sqlite-addon \
  --addon-id sqlite-addon \
  --version 1.0.0 \
  --image 'ghcr.io/example/sqlite-addon@sha256:<64-hex-digest>'
```

**Scope:** Only one SQLite database is made consistent by this method.
Unrelated application data, auxiliary stores, external attachments,
multiple SQLite databases, and in-flight application workflows are not
snapshotted atomically with that file. Use the add-on's documented
quiescence/backup procedure if its state spans multiple stores.

The generated bundle remains **plaintext on disk**. Private local file
permissions do not substitute for encrypted off-host backup storage,
independent key recovery, retention rules and access auditing.

The restore verifier still checks file bytes, not the add-on's business
logic. Opening the restored SQLite file and running `PRAGMA quick_check`
or `PRAGMA integrity_check` is a useful structural check, but does not
prove an upgraded application can read old records or safely roll back
schema migrations. Stateful upgrade execution remains blocked.

## Controller integration

The authenticated read-only upgrade assessment checks only a host-controlled
path: `YONDER_CONTROLLER_STATEFUL_BACKUP_ROOT/<addon-id>` (default:
`/var/lib/yonder/offline-backups/<addon-id>`). Browsers cannot select arbitrary
paths or ask the controller to restore files. No host source mount is added
automatically to the Compose controller profile.

For an add-on with a persistent data volume, the assessment reports:

- `offline_backup=blocked`, `backup_integrity=not_verified` when there is no
  fresh matching bundle, integrity fails or the previous release is unknown;
- `offline_backup=ready`, `backup_integrity=verified_contents_only` when the
  bundle's bytes and previous release match.

**Neither result permits a stateful upgrade.** The assessment's
`can_execute` remains `false` for data-volume add-ons. The read-only check
does not create, modify or restore backups.

The helper intentionally supports one regular export file in this phase.
Recursive filesystem snapshots, multi-volume consistency, PostgreSQL
backup tooling, SQLite online checkpointing, database migrations,
semantic restore validation, encryption-at-rest, remote object storage,
retention and automatic restore are separate engineering milestones.

## Additional acceptance gates before stateful upgrades

- [ ] Database-native, consistent backup adapters for supported stores and
      explicit maintenance windows.
- [ ] Independently signed/encrypted backup provenance, remote retention and
      restoration-tested snapshot identifiers.
- [ ] Immutable mapping from deployed version + database schema version to
      migration graph, with backward compatibility declared by add-on author.
- [ ] Sandbox migration dry-run with representative restored data and
      application-level validation, not only checksum verification.
- [ ] Durable, crash-safe migration/cutover state journal and verified
      recovery/roll-forward behavior.
- [ ] Fault injection: power loss, disk full, database outage, stale exports,
      malicious backup bundle, incompatible schema, and exhausted disk quota.
- [ ] Deployment on a dedicated lab Docker host and independent security review.

Never remove the old data volume or run irreversible schema changes merely
because an OCI upgrade passed health checks.
