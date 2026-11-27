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
# Inspect the live configuration: --postgres can enable PostgreSQL without
# changing .env. Never copy a live external database as if it were SQLite.
docker compose exec -T gateway python -c '
import os
import sys
url = os.environ.get("YONDER_DATABASE_URL", "")
mode = os.environ.get("YONDER_SECURITY__MODE", "")
env = os.environ.get("YONDER_ENVIRONMENT", "")
sys.exit(0 if mode == "development" and env == "development" and
         url in ("sqlite:///./data/yonder.db", "sqlite:////app/data/yonder.db") else 1)
' >/dev/null 2>&1 || fail "Only a running local development SQLite stack is supported; see docs/updating.md"
# An independently deployed controller or workspace may have state outside
# the gateway volume. Require an explicit coordinated update instead.
extra_services="$(docker compose ps --services --status running)"
if printf '%s\n' "$extra_services" | grep -Ev '^(gateway|worker|console|mcp)
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
echo "$previous" > "$backup_dir/previous-revision"
if [[ -f .env ]]; then
  cp -p .env "$backup_dir/.env"
  chmod 600 "$backup_dir/.env"
fi
gateway="$(docker compose ps -q gateway)"
[[ -n "$gateway" ]] || fail "Gateway not running; back up manually first"
echo "Backing up data at $backup_dir"
docker exec "$gateway" python -c '
import pathlib, sqlite3, tempfile, shutil, tarfile, sys
root=pathlib.Path("/app/data")
with tempfile.TemporaryDirectory() as temp:
    target=pathlib.Path(temp)/"data"
    target.mkdir()
    if root.is_dir():
        for file in root.rglob("*"):
            if not file.is_file() or file.is_symlink(): continue
            out=target/file.relative_to(root)
            out.parent.mkdir(parents=True, exist_ok=True)
            if file.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
                src=sqlite3.connect(f"file:{file}?mode=ro", uri=True)
                dst=sqlite3.connect(out)
                try: src.backup(dst)
                finally: src.close(); dst.close()
            else: shutil.copy2(file,out)
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as tar:
        tar.add(target,arcname="data")
' > "$backup_dir/gateway-data.tar.gz"
[[ -s "$backup_dir/gateway-data.tar.gz" ]] || fail "Backup empty"
tar -tzf "$backup_dir/gateway-data.tar.gz" >/dev/null || fail "Backup invalid"
git merge --ff-only origin/main
export YONDER_BUILD_SHA="$(git rev-parse HEAD)"
docker compose config -q || fail "Compose configuration invalid"
if docker compose up --build -d --wait --wait-timeout 180; then
  echo "Update succeeded; backup: $backup_dir"
else
  fail "Stack did not become healthy. See docs/updating.md; backup: $backup_dir"
fi
 | grep -q .; then
  fail "Additional services are running; use coordinated backup and update"
fi
previous="$(git rev-parse HEAD)"
git fetch origin main
latest="$(git rev-parse origin/main)"
[[ "$previous" != "$latest" ]] || { echo "Already current"; exit 0; }
git merge-base --is-ancestor "$previous" "$latest" ||
  fail "Not a fast-forward; review changes manually"
git --no-pager log --oneline "$previous..$latest" | head -n 20
read -r -p "Create backup and upgrade this instance? [y/N] " answer
[[ "$answer" == "y" || "$answer" == "Y" ]] || exit 0
backup_dir="backups/update-$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
echo "$previous" > "$backup_dir/previous-revision"
if [[ -f .env ]]; then
  cp -p .env "$backup_dir/.env"
  chmod 600 "$backup_dir/.env"
fi
gateway="$(docker compose ps -q gateway)"
[[ -n "$gateway" ]] || fail "Gateway not running; back up manually first"
echo "Backing up data at $backup_dir"
docker exec "$gateway" python -c '
import pathlib, sqlite3, tempfile, shutil, tarfile, sys
root=pathlib.Path("/app/data")
with tempfile.TemporaryDirectory() as temp:
    target=pathlib.Path(temp)/"data"
    target.mkdir()
    if root.is_dir():
        for file in root.rglob("*"):
            if not file.is_file() or file.is_symlink(): continue
            out=target/file.relative_to(root)
            out.parent.mkdir(parents=True, exist_ok=True)
            if file.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
                src=sqlite3.connect(f"file:{file}?mode=ro", uri=True)
                dst=sqlite3.connect(out)
                try: src.backup(dst)
                finally: src.close(); dst.close()
            else: shutil.copy2(file,out)
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as tar:
        tar.add(target,arcname="data")
' > "$backup_dir/gateway-data.tar.gz"
[[ -s "$backup_dir/gateway-data.tar.gz" ]] || fail "Backup empty"
tar -tzf "$backup_dir/gateway-data.tar.gz" >/dev/null || fail "Backup invalid"
git merge --ff-only origin/main
export YONDER_BUILD_SHA="$(git rev-parse HEAD)"
docker compose config -q || fail "Compose configuration invalid"
docker compose up --build -d
for _ in $(seq 1 30); do
  if docker compose exec -T gateway python -c '
import urllib.request
urllib.request.urlopen("http://127.0.0.1:8080/health",timeout=3).read()
' >/dev/null 2>&1; then
    echo "Update succeeded; backup: $backup_dir"
    exit 0
  fi
  sleep 2
done
fail "Gateway did not become healthy. See docs/updating.md; backup: $backup_dir"
