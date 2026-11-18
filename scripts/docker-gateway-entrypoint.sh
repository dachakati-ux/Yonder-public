#!/usr/bin/env sh
# Gateway container entrypoint: apply migrations, then start the process.
set -eu

mkdir -p /app/data

if [ -n "${YONDER_DATABASE_URL:-}" ]; then
  echo "Applying Alembic migrations..."
  cd /app
  uv run alembic -c database/alembic.ini upgrade head
fi

exec "$@"
