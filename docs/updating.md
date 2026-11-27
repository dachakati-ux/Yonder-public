# Updating Yonder

Yonder checks the current **main branch revision** against the revision
recorded by the single-host launcher. This is a preliminary update channel;
it is not semantic versioning, a signed release manifest, or a security advisory.

The Operations page reports when the revisions differ. A difference does not
always mean a newer *release*: main could have been force-rewritten or a
checkout may not be on the release channel. Administrators should review
changes before upgrading.

## Single-host Docker installations

From a clean checkout of main:

```bash
./start.sh --update
```

The command requires Git, Docker and Compose v2, verifies the expected
repository remote and fast-forward history, asks for confirmation, preserves
`.env`, stops the local stack, archives the quiesced gateway data volume with consolidated SQLite backups and integrity checks, verifies the archive listing, then pulls and rebuilds the stack. If any operation fails, the stack may remain stopped; inspect the backup and logs before attempting recovery. It does **not**
automatically update any non-main checkout or production cluster. The updater also checks the running Gateway environment/database and rejects additional Compose services; do not use it for external add-on data or coordinated deployments.

Backups are kept under `backups/update-YYYYMMDD-HHMMSS` on the deployment
host (mode 0700, `.env` mode 0600). Store an offline/encrypted copy as well.
The update is not automatic and intentionally interrupts service for a consistent snapshot. Ensure sufficient space for a temporary copy and compressed archive of the entire gateway data volume. An archive integrity check is not a restore rehearsal.

If the gateway is not already running, or its data cannot be backed up,
the updater intentionally stops. The update check is read-only.

## Production, school, multi-host and PostgreSQL installs

Do **not** use the single-host updater. Instead:

1. Schedule maintenance, review the release notes and database migration plans.
2. Confirm verified restorable backups of every database, model metadata,
   config, user workspace data and any external volumes.
3. Confirm compatibility of control plane, workers, runtimes and plugins.
4. Back up external PostgreSQL with approved database tools and retention.
5. Pull a reviewed release tag and pin a known tested revision.
6. Run database migrations in a coordinated window, deploy control plane,
   then roll workers separately after compatibility checks.
7. Verify health, worker registration, model loading and user access.
8. Have a rollback plan; database downgrade is not assumed safe.

## Restoring a failed single-host upgrade

Do not blindly reset the repository after schema migrations. First inspect
`docker compose logs` and the saved `previous-revision` file. For a
restorable SQLite backup, **stop the stack** and restore the saved gateway data
archive into its persistent volume using a controlled restoration procedure.
Do not restore while the database is open. Restore the saved `.env` as well,
and rebuild the previous reviewed commit. Always test restoration on a copy
before doing this on a school or production system. The former `--rollback` command is intentionally disabled until a stopped-volume restore and failure-injection procedure has been verified. Never restore a database over a running Gateway.

## Behavior and privacy

Update checks call the public GitHub API from the Gateway at most once per
hour per process, use no credentials and never execute downloaded code.
The UI must not automatically invoke a host-level update. Version checks may
fail on offline networks or due to GitHub rate limiting; that is not a
reason to assume an update exists.

**Future:** replace main-branch SHA comparison with signed, stable release
manifests, semantic versions, release channels, migration compatibility and
reliable administrator-targeted notifications.
