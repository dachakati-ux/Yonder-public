#!/usr/bin/env bash
set -euo pipefail

# Yonder — One-command Docker launcher
# Checks dependencies, installs missing tools (with confirmation),
# sets up .env, builds & starts the stack, and waits until healthy.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

AUTO_YES=0
WITH_POSTGRES=0
ACTION="up"

for arg in "$@"; do
  case "$arg" in
    -y|--yes)       AUTO_YES=1 ;;
    --postgres)     WITH_POSTGRES=1 ;;
    --stop|--down)  ACTION="down" ;;
    --logs)         ACTION="logs" ;;
    --update)       ACTION="update" ;;
    -h|--help)      ACTION="help" ;;
    *)
      echo "Unknown option: $arg"
      echo "Run ./start.sh --help for usage."
      exit 1
      ;;
  esac
done

# Colors (disabled if not a TTY)
if [[ -t 1 ]]; then
  BOLD="\033[1m"
  GREEN="\033[0;32m"
  YELLOW="\033[0;33m"
  RED="\033[0;31m"
  BLUE="\033[0;34m"
  RESET="\033[0m"
else
  BOLD="" GREEN="" YELLOW="" RED="" BLUE="" RESET=""
fi

info()  { echo -e "${BLUE}[yonder]${RESET} $*"; }
ok()    { echo -e "${GREEN}[  ok  ]${RESET} $*"; }
warn()  { echo -e "${YELLOW}[ warn ]${RESET} $*"; }
err()   { echo -e "${RED}[error ]${RESET} $*" >&2; }

confirm() {
  local prompt="$1"
  if [[ "$AUTO_YES" -eq 1 ]]; then
    return 0
  fi
  read -r -p "$prompt [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]]
}

print_help() {
  cat <<'EOF'
Yonder — One-Command Launcher

Usage:
  ./start.sh [OPTIONS]

Options:
  -y, --yes       Automatically confirm dependency installation prompts
      --postgres  Start with PostgreSQL persistence enabled (in addition to SQLite)
      --stop      Stop all running Yonder containers
      --logs      Follow container logs
      --update    Back up local data, pull main, and rebuild
  -h, --help      Show this help message

Examples:
  ./start.sh                 # Check deps, build, and start Gateway + Worker + Console
  ./start.sh --yes           # Same, without interactive prompts
  ./start.sh --postgres      # Start with PostgreSQL database
  ./start.sh --stop          # Stop all Yonder containers
EOF
}

if [[ "$ACTION" == "help" ]]; then
  print_help
  exit 0
fi

if [[ "$ACTION" == "update" ]]; then
  exec bash "$ROOT_DIR/scripts/update-yonder.sh"
fi

# Require Compose v2: health-aware startup and modern env_file syntax.
detect_compose() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD=(docker compose)
    return 0
  fi
  return 1
}

if [[ "$ACTION" == "down" ]]; then
  if ! command -v docker >/dev/null 2>&1 || ! detect_compose; then
    err "Docker / Docker Compose not found."
    exit 1
  fi
  info "Stopping Yonder containers..."
  "${COMPOSE_CMD[@]}" -f compose.yaml -f compose.persistence.yaml --profile persistence down
  ok "Yonder stopped."
  exit 0
fi

if [[ "$ACTION" == "logs" ]]; then
  if ! command -v docker >/dev/null 2>&1 || ! detect_compose; then
    err "Docker / Docker Compose not found."
    exit 1
  fi
  exec "${COMPOSE_CMD[@]}" logs -f
fi

echo -e "${BOLD}=== Yonder Quick-Start Launcher ===${RESET}"

OS="$(uname -s)"

# 1. Check / install curl
if ! command -v curl >/dev/null 2>&1; then
  warn "curl is not installed."
  if [[ "$OS" == "Linux" ]] && command -v apt-get >/dev/null 2>&1; then
    if confirm "Install curl using apt-get?"; then
      sudo apt-get update && sudo apt-get install -y curl
    else
      err "curl is required for health checks. Please install curl and re-run."
      exit 1
    fi
  elif [[ "$OS" == "Linux" ]] && command -v dnf >/dev/null 2>&1; then
    if confirm "Install curl using dnf?"; then
      sudo dnf install -y curl
    else
      err "curl is required. Please install curl and re-run."
      exit 1
    fi
  else
    err "Please install curl and re-run ./start.sh."
    exit 1
  fi
fi
ok "curl is available"

# 2. Check / install Docker
install_docker_linux() {
  if command -v apt-get >/dev/null 2>&1; then
    info "Installing Docker and Docker Compose plugin via apt-get..."
    sudo apt-get update
    sudo apt-get install -y docker.io docker-compose-plugin || {
      # Fallback to official Docker convenience script if distro package lacks compose plugin
      curl -fsSL https://get.docker.com | sudo sh
    }
    sudo systemctl enable --now docker || true
    sudo usermod -aG docker "$USER" || true
  elif command -v dnf >/dev/null 2>&1; then
    info "Installing Docker via dnf..."
    curl -fsSL https://get.docker.com | sudo sh
    sudo systemctl enable --now docker || true
    sudo usermod -aG docker "$USER" || true
  else
    err "Unsupported Linux package manager. Please install Docker from https://docs.docker.com/engine/install/"
    exit 1
  fi
}

install_docker_macos() {
  if ! command -v brew >/dev/null 2>&1; then
    warn "Homebrew is not installed."
    if confirm "Install Homebrew first so we can install Docker Desktop?"; then
      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
      if [[ -x /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
      fi
    else
      err "Please install Docker Desktop from https://www.docker.com/products/docker-desktop/ and re-run ./start.sh."
      exit 1
    fi
  fi
  info "Installing Docker Desktop via Homebrew..."
  brew install --cask docker
}

if ! command -v docker >/dev/null 2>&1; then
  warn "Docker is not installed."
  if confirm "Would you like to install Docker now?"; then
    if [[ "$OS" == "Darwin" ]]; then
      install_docker_macos
    elif [[ "$OS" == "Linux" ]]; then
      install_docker_linux
    else
      err "Unsupported OS ($OS). Please install Docker Desktop and re-run ./start.sh."
      exit 1
    fi
  else
    err "Docker is required. Install it and re-run ./start.sh."
    exit 1
  fi
fi
ok "docker CLI is installed ($(docker --version))"

# 3. Ensure Docker daemon is running
if ! docker info >/dev/null 2>&1; then
  warn "Docker daemon is not running."
  if [[ "$OS" == "Darwin" ]]; then
    if [[ -d "/Applications/Docker.app" ]]; then
      info "Starting Docker Desktop..."
      open -a Docker
      info "Waiting for Docker daemon to become ready (up to 90s)..."
      for _ in $(seq 1 45); do
        if docker info >/dev/null 2>&1; then
          break
        fi
        sleep 2
      done
    fi
  elif [[ "$OS" == "Linux" ]] && command -v systemctl >/dev/null 2>&1; then
    if confirm "Start the Docker service with sudo systemctl start docker?"; then
      sudo systemctl start docker
    fi
  fi

  if ! docker info >/dev/null 2>&1; then
    err "Docker daemon is still not reachable. Please start Docker and re-run ./start.sh."
    exit 1
  fi
fi
ok "Docker daemon is running"

# 4. Check Docker Compose
if ! detect_compose; then
  warn "Docker Compose plugin is missing."
  if [[ "$OS" == "Linux" ]] && command -v apt-get >/dev/null 2>&1; then
    if confirm "Install docker-compose-plugin via apt-get?"; then
      sudo apt-get update && sudo apt-get install -y docker-compose-plugin
    fi
  fi
  if ! detect_compose; then
    err "Docker Compose is required. Please install Docker Desktop or docker-compose-plugin."
    exit 1
  fi
fi
ok "Docker Compose is available (${COMPOSE_CMD[*]})"

# 5. Ensure .env exists; Compose handles dotenv parsing.
if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    (umask 077; cp .env.example .env)
    ok "Created .env from .env.example"
  else
    warn ".env.example not found; continuing with Compose defaults"
  fi
else
  ok ".env already exists"
fi

# Compose reads dotenv itself. Never execute .env as a shell script.
# Record the checked-out revision; unknown revisions never trigger a false update alert.
export YONDER_BUILD_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"

# 7. Build and start the stack
COMPOSE_ARGS=(-f compose.yaml)
if [[ "$WITH_POSTGRES" -eq 1 ]]; then
  COMPOSE_ARGS+=(-f compose.persistence.yaml --profile persistence)
  info "PostgreSQL persistence profile enabled."
fi

# Fail before creating containers if this checkout was configured for production.
# The convenience launcher is intentionally a localhost development installer.
COMPOSE_ENV="$("${COMPOSE_CMD[@]}" "${COMPOSE_ARGS[@]}" config --environment)"
if printf '%s\n' "$COMPOSE_ENV" | grep -Eq '^YONDER_ENVIRONMENT=production$|^YONDER_SECURITY__MODE=trusted_ingress$'; then
  err "The local launcher is not a production installer. See docs/security-boundary.md and docs/deployment.md."
  exit 1
fi
unset COMPOSE_ENV

info "Building and starting Yonder containers (this may take a few minutes on first run)..."
if ! "${COMPOSE_CMD[@]}" "${COMPOSE_ARGS[@]}" up --build -d --wait --wait-timeout 180; then
  err "The stack did not become healthy. Inspect it with ./start.sh --logs"
  "${COMPOSE_CMD[@]}" "${COMPOSE_ARGS[@]}" ps || true
  exit 1
fi

# Read actual published ports from this Compose project, not shell-evaluated dotenv.
published_port() {
  local address
  address="$("${COMPOSE_CMD[@]}" "${COMPOSE_ARGS[@]}" port "$1" "$2")" || return 1
  [[ "$address" =~ ^127\.0\.0\.1:([0-9]+)$ ]] || return 1
  printf '%s' "${BASH_REMATCH[1]}"
}
CONSOLE_PORT="$(published_port console 3000)" || { err "Console loopback port unavailable"; exit 1; }
GATEWAY_PORT="$(published_port gateway 8080)" || { err "Gateway loopback port unavailable"; exit 1; }
MCP_PORT="$(published_port mcp 8095)" || { err "MCP loopback port unavailable"; exit 1; }
ok "All configured services are healthy (model readiness is separate)"

echo ""
echo -e "${GREEN}${BOLD}Yonder is running!${RESET}"
echo ""
echo -e "  ${BOLD}Web Console:${RESET}  http://127.0.0.1:${CONSOLE_PORT}"
echo -e "  ${BOLD}Gateway API:${RESET}  http://127.0.0.1:${GATEWAY_PORT}"
echo -e "  ${BOLD}API Docs:${RESET}     http://127.0.0.1:${GATEWAY_PORT}/docs"
echo -e "  ${BOLD}MCP Server:${RESET}   http://127.0.0.1:${MCP_PORT}/mcp"
echo -e "  ${BOLD}MCP Status:${RESET}   http://127.0.0.1:${GATEWAY_PORT}/v1/mcp/status"
echo ""
echo "Useful commands:"
echo "  ./start.sh --logs    Follow live logs"
echo "  ./start.sh --stop    Stop all containers"
echo ""
echo "Next steps: see docs/quickstart.md to connect a local LLM runtime or explore the Console."
