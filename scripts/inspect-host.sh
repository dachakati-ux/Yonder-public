#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/yonder-uv-cache}" \
  uv run --package yonder-worker python scripts/inspect-host.py "$@"
