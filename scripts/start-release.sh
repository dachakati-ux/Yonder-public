#!/usr/bin/env bash
# Run a reviewed prebuilt local stack; never install or publish an image automatically.
set -euo pipefail
cd "$(dirname "$0")/.."
fail() { echo "Yonder release: $*" >&2; exit 1; }
[[ "$#" -ge 1 && "$#" -le 2 ]] || fail "Usage: scripts/start-release.sh vVERSION [up|down|logs|check]"
tag="$1"
action="${2:-up}"
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?$ ]] || fail "Use an explicit semantic version, not latest"
export YONDER_IMAGE_TAG="$tag"
command -v docker >/dev/null || fail "Install Docker and current Compose v2 first"
docker compose version >/dev/null || fail "Compose v2 required"
if [[ ! -f .env ]]; then
  (umask 077; cp .env.example .env)
fi
compose=(docker compose -f compose.yaml -f compose.release.yaml)
case "$action" in
  down) "${compose[@]}" down; exit ;;
  logs) "${compose[@]}" logs -f; exit ;;
  check|up) ;;
  *) fail "Unknown action $action" ;;
esac
resolved="$("${compose[@]}" config --environment)"
if printf '%s\n' "$resolved" | grep -Eq '^YONDER_ENVIRONMENT=production$|^YONDER_SECURITY__MODE=trusted_ingress$'; then
  fail "This release launcher is for local development only"
fi
# The Console bundle was compiled for the default browser endpoint.
if printf '%s\n' "$resolved" | grep -E '^YONDER_GATEWAY__PORT=' | grep -qv '^YONDER_GATEWAY__PORT=8080$'; then
  fail "Prebuilt Console requires Gateway port 8080; use the source launcher for custom ports"
fi
if printf '%s\n' "$resolved" | grep -E '^NEXT_PUBLIC_YONDER_GATEWAY_URL=' | grep -qv '^NEXT_PUBLIC_YONDER_GATEWAY_URL=http://127.0.0.1:8080$'; then
  fail "Prebuilt Console requires NEXT_PUBLIC_YONDER_GATEWAY_URL=http://127.0.0.1:8080"
fi
unset resolved
"${compose[@]}" config -q
if [[ "$action" == check ]]; then
  echo "Release configuration validated; no images pulled or containers started"
  exit 0
fi
"${compose[@]}" pull
"${compose[@]}" up --no-build --pull never --wait --wait-timeout 180
address="$("${compose[@]}" port console 3000)"
echo "Local stack healthy at http://$address (real model readiness is separate)"
