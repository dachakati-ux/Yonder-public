# Backup Guidance

## What to back up

| Asset | Why |
| --- | --- |
| `config/models/*.yaml` | Model definitions / manifests |
| `config/environments/*.yaml` | Non-secret environment configuration |
| `config/routes/*.yaml` | Alias examples / intended routes |
| `data/gateway-state.json` | Durable alias + deployment runtime state when no database URL |
| PostgreSQL / SQLite (`YONDER_DATABASE_URL`) | Live control-plane + conversations when configured |
| Alembic revision history | `database/migrations/` in git |

## What not to back up casually

* Secrets / `.env` files with credentials
* Prompt and response content (disabled in logs by default)
* Ephemeral request ledgers and in-memory conversation stores

## Restore (local state file)

1. Stop gateway.
2. Restore `data/gateway-state.json`.
3. Confirm manifests still exist under `config/models/`.
4. Start gateway; aliases/deployments reload from the state file.
5. Verify with `GET /api/admin/routes` and a test chat/completions call.

## Restore (Postgres)

1. Restore the database from dump/snapshot.
2. Run `make migrate` to ensure schema is current.
3. Start gateway with `YONDER_DATABASE_URL` set.
4. Validate model definitions and alias mappings before enabling traffic.

## SQLite verification tooling

Create and verify a consistent backup using SQLite's online backup API:

```bash
uv run python scripts/sqlite_backup.py backup data/yonder.db backups/yonder.db
uv run python scripts/sqlite_backup.py verify backups/yonder.db
```

Restore into a new path and verify required tables, integrity, and row counts:

```bash
uv run python scripts/sqlite_backup.py restore backups/yonder.db data/restored.db
```

Existing destinations are refused unless `--force` is explicitly supplied. Run
`make verify-migrations` to exercise an upgrade from the initial schema to the current
Alembic head in an isolated temporary database.

## Complete local-volume archives

The single-host updater stops all local Compose services before archiving the Gateway data volume. For an isolated restore rehearsal, use the staging command documented in [Updating Yonder](updating.md). Never point a staging command at an existing deployment's data directory. The archive may include private user content and models; keep it encrypted, restrict access, and account for temporary disk space.
