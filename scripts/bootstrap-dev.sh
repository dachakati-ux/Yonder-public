#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Yonder bootstrap (Mac / local development)"
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://github.com/astral-sh/uv"
  exit 1
fi

uv sync --all-packages --group dev
cp -n .env.example .env || true
export YONDER_CONFIG_FILE="${YONDER_CONFIG_FILE:-config/environments/development.yaml}"
uv run python scripts/validate-config.py
echo "Bootstrap OK. Start with: make gateway  and  make worker"
