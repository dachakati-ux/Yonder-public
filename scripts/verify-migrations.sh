#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
migration_dir="$(mktemp -d "${TMPDIR:-/tmp}/yonder-migrations.XXXXXX")"
trap 'rm -rf "$migration_dir"' EXIT

cd "$repo_root"
export YONDER_DATABASE_URL="sqlite:///$migration_dir/migrations.db"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/yonder-uv-cache}"

uv run alembic -c database/alembic.ini upgrade 0001_initial
uv run alembic -c database/alembic.ini upgrade head
uv run alembic -c database/alembic.ini current
uv run python scripts/sqlite_backup.py verify "$migration_dir/migrations.db"
