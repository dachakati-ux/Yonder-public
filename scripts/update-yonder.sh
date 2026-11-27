#!/usr/bin/env bash
# User-approved single-host update with a verified data backup.
set -euo pipefail
cd "$(dirname "$0")/.."
fail() { echo "Yonder update: $*" >&2; exit 1; }

if [[ "${1:-}" == "--rollback" ]]; then
  fail "Automatic rollback is disabled: restore only into a stopped, isolated stack after verifying the backup. See docs/updating.md"
fi
[[ "$#" -eq 0 ]] || fail "Unknown arguments; see docs/updating.md"

command -v git >/dev/null || fail "git missing"
command -v docker >/dev/null || fail "Docker missing"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 required"
docker info >/dev/null 2>&1 || fail "Docker daemon unavailable"
[[ "$(git branch --show-current)" == "main" ]] || fail "Only main may auto-update"
[[ -z "$(git status --porcelain --untracked-files=no)" ]] ||
  fail "Commit or stash tracked changes first"
case "$(git remote get-url origin)" in
  https://github.com/dachakati-ux/Yonder-public.git|git@github.com:dachakati-ux/Yonder-public.git) ;;
  *) fail "Unexpected origin; update manually after verification" ;;
esac
[[ ! -f .env.control-plane && ! -f .env.worker ]] ||
  fail "Multi-host setup requires coordinated update; see docs/updating.md"
if [[ -f .env ]] && grep -Eq '^YONDER_DATABASE_URL=.*postgresql' .env; then
  fail "External Postgres needs a coordinated backup; see docs/updating.md"
fi
# Check effective settings, not just .env: the persistence overlay can
# select PostgreSQL without changing .env. This helper is local SQLite only.
docker compose exec -T gateway python -c '
import os
import sys
url = os.environ.get("YONDER_DATABASE_URL", "")
mode = os.environ.get("YONDER_SECURITY__MODE", "")
env = os.environ.get("YONDER_ENVIRONMENT", "")
sys.exit(0 if mode == "development" and env == "development" and
         url in ("sqlite:///./data/yonder.db", "sqlite:////app/data/yonder.db") else 1)
' >/dev/null 2>&1 || fail "Only a running local development SQLite stack is supported; see docs/updating.md"
# Inspect all containers in this Compose project, including stopped add-ons.
extra_services="$(docker compose ps --all --services)"
while IFS= read -r service; do
  case "$service" in
    gateway|worker|console|mcp|"") ;;
    *) fail "Additional service $service exists; use coordinated backup and update" ;;
  esac
done <<< "$extra_services"
previous="$(git rev-parse HEAD)"
git fetch origin main
latest="$(git rev-parse origin/main)"
[[ "$previous" != "$latest" ]] || { echo "Already current"; exit 0; }
git merge-base --is-ancestor "$previous" "$latest" ||
  fail "Not a fast-forward; review changes manually"
git --no-pager log -n 20 --oneline "$previous..$latest"
read -r -p "Create backup and upgrade this instance? [y/N] " answer
[[ "$answer" == "y" || "$answer" == "Y" ]] || exit 0
backup_dir="backups/update-$(date -u +%Y%m%d-%H%M%S)"
(umask 077; mkdir -p backups; mkdir "$backup_dir")
chmod 700 "$backup_dir"
umask 077
echo "$previous" > "$backup_dir/previous-revision"
if [[ -f .env ]]; then
  cp -p .env "$backup_dir/.env"
  chmod 600 "$backup_dir/.env"
fi
# Stop the stack so the snapshot includes a consistent database and other
# state, not a mix of live files from different points in time.
echo "Stopping the local stack for a consistent backup"
docker compose stop
echo "Backing up data at $backup_dir"
# Use the existing image and bypass its migration/startup entrypoint.
# Read the helper from this checkout, not from an untrusted container.
docker compose run --rm --no-deps --pull never -T --entrypoint python gateway - \
  < scripts/archive-local-data.py > "$backup_dir/gateway-data.tar.gz.partial"
[[ -s "$backup_dir/gateway-data.tar.gz.partial" ]] || fail "Backup empty"
tar -tzf "$backup_dir/gateway-data.tar.gz.partial" >/dev/null || fail "Backup invalid"
mv "$backup_dir/gateway-data.tar.gz.partial" "$backup_dir/gateway-data.tar.gz"
echo "Backup created: $backup_dir (independent restore test still required)"
git merge --ff-only origin/main
export YONDER_BUILD_SHA="$(git rev-parse HEAD)"
docker compose config -q || fail "Compose configuration invalid"
if docker compose up --build -d --wait --wait-timeout 180; then
  echo "Update succeeded; backup: $backup_dir"
else
  fail "Stack did not become healthy. See docs/updating.md; backup: $backup_dir"
fi
