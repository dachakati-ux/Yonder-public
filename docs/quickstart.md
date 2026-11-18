# Yonder Step-by-Step Deploy & Quick-Start Guide

This guide walks you through deploying **Yonder** from scratch—whether you are running it locally on your laptop for the first time or deploying across servers.

---

## Part 1: Prerequisites

You only need **Git** and **Docker** (with **Docker Compose**). Python, Node.js, FastAPI, Next.js, and all dependencies run inside containers automatically.

### Automated Install & Launch (Recommended)

Run the launcher script from the repository folder:

```bash
git clone https://github.com/dachakati-ux/Yonder-public.git
cd Yonder-public
./start.sh
```

What `./start.sh` does for you:
1. Checks for `curl`, `docker`, and `docker compose`.
2. Offers to install missing tools (using Homebrew on macOS or `apt`/`dnf` on Linux).
3. Starts Docker Desktop automatically if you are on macOS and Docker isn't running yet.
4. Copies `.env.example` to `.env` if you don't have one yet.
5. Checks that ports `3000`, `8080`, `8090`, and `8095` are free.
6. Builds and starts **Gateway**, **Worker**, **Console**, and **MCP Server**.
7. Waits until services pass health checks and prints the URLs to open.

### Manual Prerequisite Setup (If You Prefer)

* **macOS:** Install [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/), open it from Applications, and wait until the menu-bar icon stops animating.
* **Windows (WSL2):** Install [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) with the **WSL 2** backend enabled, then run commands inside your Ubuntu/WSL terminal.
* **Linux (Ubuntu / Debian):**
  ```bash
  sudo apt-get update
  sudo apt-get install -y git curl docker.io docker-compose-plugin
  sudo systemctl enable --now docker
  sudo usermod -aG docker "$USER"
  # Log out and back in (or run `newgrp docker`) to use docker without sudo
  ```

---

## Part 2: Step-by-Step `.env` Configuration

Before (or after) your first launch, review `.env` in the repository root:

```bash
cp .env.example .env
```

### A. Ports Reference & Customizing Ports

Every port Yonder exposes is controlled by `.env`:

| Service | Default Port | `.env` Variable | What It Is Used For |
| :--- | :---: | :--- | :--- |
| **Web Console** | `3000` | `YONDER_CONSOLE_PORT` | Browser UI (Chat, Model Lab, Operations, Recipes, Academy) |
| **Gateway API** | `8080` | `YONDER_GATEWAY__PORT` | OpenAI-compatible API, `/v1/mcp/*`, Swagger `/docs`, health `/health` |
| **Worker Agent** | `8090` | `YONDER_WORKER__PORT` | Hardware metrics and model runtime lifecycle (`llama.cpp`, Diffusers) |
| **IDE MCP Server** | `8095` | `YONDER_MCP_PORT` | `yonder-mcp` Streamable HTTP server for Cursor / Claude Desktop |
| **PostgreSQL** *(optional)* | `5432` | `YONDER_POSTGRES_PORT` | Optional Postgres DB (only started when using `./start.sh --postgres`) |

**Example: moving Yonder to custom ports** (e.g. if `3000` or `8080` is already in use):

```dotenv
YONDER_CONSOLE_PORT=3100
YONDER_GATEWAY__PORT=8180
YONDER_WORKER__PORT=8190
YONDER_MCP_PORT=8195

YONDER_CONSOLE_ORIGIN=http://127.0.0.1:3100
NEXT_PUBLIC_YONDER_GATEWAY_URL=http://127.0.0.1:8180
```

Then run `./start.sh` (or `docker compose up --build -d` so the Console picks up the new `NEXT_PUBLIC_YONDER_GATEWAY_URL`).

### B. Local Laptop / Single-Machine Defaults (No Edits Needed)

The default `.env.example` works out of the box for local use:
* `YONDER_ENVIRONMENT=development`
* `YONDER_SECURITY__MODE=development`
* `YONDER_CONSOLE_ORIGIN=http://127.0.0.1:3000`
* `NEXT_PUBLIC_YONDER_GATEWAY_URL=http://127.0.0.1:8080`
* `YONDER_MCP__ENABLED=true`
* `YONDER_MCP__SERVER_ENABLED=true`

### C. Connecting a Local LLM Runtime (Optional)

If you run `llama-server`, Ollama, or LM Studio on your host machine (e.g. on port `1234`), uncomment and set in `.env`:

```dotenv
YONDER_LLAMA_CPP__BASE_URL=http://host.docker.internal:1234
```
*(On Linux without Docker Desktop, use your host LAN IP or `172.17.0.1` instead of `host.docker.internal`.)*

### D. Deploying to a Remote Server / LAN Host

If users will open the Console from another machine (say `http://192.0.2.10:3000`), update `.env` **before** building so the Console browser bundle calls the reachable Gateway URL:

```dotenv
YONDER_CONSOLE_ORIGIN=http://192.0.2.10:3000
NEXT_PUBLIC_YONDER_GATEWAY_URL=http://192.0.2.10:8080
```

Then rebuild and restart:
```bash
docker compose up --build -d
```

### E. Production Checklist

When deploying to production behind your upstream auth/ingress:

1. Set `YONDER_ENVIRONMENT=production`
2. Set `YONDER_SECURITY__MODE=trusted_ingress`
3. Generate and set a shared secret for Gateway ↔ Worker calls:
   ```bash
   openssl rand -hex 32
   ```
   ```dotenv
   YONDER_SECURITY__WORKER_SHARED_SECRET=<paste-generated-secret>
   ```
4. Enable PostgreSQL persistence (`./start.sh --postgres` or set `YONDER_DATABASE_URL`).

---

## Part 3: Verifying Your Deployment

Once `./start.sh` (or `docker compose up --build -d`) finishes:

1. **Web Console:** Open <http://127.0.0.1:3000> in your browser.
2. **Gateway Health:**
   ```bash
   curl http://127.0.0.1:8080/health
   ```
3. **Interactive API Docs:** Open <http://127.0.0.1:8080/docs>.
4. **MCP Server Health:**
   ```bash
   curl http://127.0.0.1:8095/health
   ```
5. **Gateway MCP Client Status:**
   ```bash
   curl http://127.0.0.1:8080/v1/mcp/status
   ```

---

## Part 4: Using MCP Servers (Enabled by Default)

Yonder ships with **MCP enabled in both directions**:

### 1. Connect Your IDE (Cursor / Claude Desktop) to Yonder (`yonder-mcp`)

The Docker stack automatically runs `yonder-mcp` on `http://127.0.0.1:8095/mcp`. Point your MCP client configuration at:

```json
{
  "mcpServers": {
    "yonder": {
      "url": "http://127.0.0.1:8095/mcp"
    }
  }
}
```

This exposes Gateway-backed tools (`yonder_gateway_status`, `yonder_capacity_aliases`, `yonder_list_platform_tools`, `yonder_execute_platform_tool`).

### 2. Connect Yonder Gateway to External MCP Servers

Gateway's MCP client (`/v1/mcp/*`) is enabled (`YONDER_MCP__ENABLED=true`). To add or customize external MCP servers:

1. Edit `config/environments/development.yaml` under `mcp:`:
   ```yaml
   mcp:
     enabled: true
     server_enabled: true
     http_url_allowlist:
       - http://127.0.0.1:8900
       - http://host.docker.internal:8900
     servers:
       - name: serena
         transport: http
         url: http://host.docker.internal:8900/mcp   # if running on host; or http://127.0.0.1:8900/mcp for local non-Docker
         enabled: true
   ```
2. Restart Gateway:
   ```bash
   docker compose restart gateway
   ```
3. Inspect discovered tools:
   ```bash
   curl http://127.0.0.1:8080/v1/mcp/tools
   ```

See [docs/mcp.md](mcp.md) for security rules, allowlists, and stdio transport options.

---

## Part 5: Day-2 Commands & Troubleshooting

| Task | Command |
| :--- | :--- |
| View live logs | `./start.sh --logs` (or `docker compose logs -f`) |
| Stop all containers | `./start.sh --stop` (or `docker compose down`) |
| Enable PostgreSQL | `./start.sh --postgres` |
| Wipe local data & rebuild | `docker compose down -v && ./start.sh` |

| Problem | Fix |
| :--- | :--- |
| `Cannot connect to the Docker daemon` | Start Docker Desktop (macOS/Windows) or run `sudo systemctl start docker` (Linux). |
| `port is already allocated` (`3000`, `8080`, `8090`, `8095`) | Stop the conflicting app or inspect with `lsof -i :8080`. |
| `permission denied` on Linux Docker socket | Run `sudo usermod -aG docker "$USER"` and log out and back in. |
| Console cannot reach Gateway | Open `http://127.0.0.1:3000`, or update `NEXT_PUBLIC_YONDER_GATEWAY_URL` in `.env` and run `docker compose up --build -d`. |
