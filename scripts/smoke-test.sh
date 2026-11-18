#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export YONDER_CONFIG_FILE="${YONDER_CONFIG_FILE:-config/environments/development.yaml}"

echo "==> validate config"
uv run python scripts/validate-config.py

echo "==> unit tests"
uv run pytest -q

echo "==> hardware endpoint (if worker is running)"
if curl -sf http://127.0.0.1:8090/health >/dev/null 2>&1; then
  curl -sf http://127.0.0.1:8090/v1/hardware | head -c 400
  echo
else
  echo "worker not running; skipped live hardware check"
fi

echo "==> gateway health (if gateway is running)"
if curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1; then
  curl -sf http://127.0.0.1:8080/ready
  echo
else
  echo "gateway not running; skipped live gateway check"
fi

echo "Smoke checks completed."
