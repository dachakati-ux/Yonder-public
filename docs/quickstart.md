# Yonder Step-by-Step Deploy & Quick-Start Guide

This guide walks you through deploying **Yonder** from scratch—whether you are running it locally on your laptop for the first time or deploying across servers.

---

## Part 1: Prerequisites

The local quick start requires **Git** and a current **Docker Compose v2** (with `up --wait`, `config --environment`, and modern `env_file` support). Python, Node.js, FastAPI, Next.js, and all dependencies run inside containers automatically.

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
5. Uses Compose to bind the configured localhost ports; a binding conflict fails startup.
6. Builds and starts **Gateway**, **Worker**, **Console**, and **MCP Server**.
7. Waits for all configured service health checks and prints the actual published URLs. Service health does not guarantee a loaded inference model.

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

### C. First real model response (LM Studio or another OpenAI-compatible server)

The initial development chat route can use a **mock** model; service health and a mock answer do not establish real model readiness. To use a real model:

1. Start an OpenAI-compatible inference server on a trusted machine. In LM Studio, download a model suitable for your available RAM/VRAM, load it, and start its local server.
2. Query the inference server from that machine (adjust the port):

   ```bash
   curl --fail http://127.0.0.1:1234/v1/models
   ```

   Copy the exact model ID returned in `data[].id`. If this fails, resolve the model server issue first.
3. Choose the address that the Gateway container can reach. On Docker Desktop this is normally `host.docker.internal`; on Linux use a reachable host or private LAN IP. The model server must listen on an interface reachable by the container; restrict it with a firewall and never expose it publicly.
4. Add these settings to the local `.env`, substituting the actual endpoint and model identifier:

   ```dotenv
   YONDER_LLAMA_CPP__ENABLED=true
   YONDER_LLAMA_CPP__BASE_URL=http://host.docker.internal:1234
   YONDER_LLAMA_CPP__SERVED_MODEL=replace-with-exact-model-id
   YONDER_LLAMA_CPP__ATTACH_ALIASES=my-local-model
   ```

   Use a **server root URL**, not a URL ending in `/v1`. Use a fresh alias for the external attachment; do not remove an existing data volume to change models.
5. Run `./start.sh`, check Operations and Models for the external attachment, and select `my-local-model` in Chat. Test through the Gateway (not the inference server):

   ```bash
   curl --fail http://127.0.0.1:8080/v1/chat/completions \
     -H 'Content-Type: application/json' \
     -d '{"model":"my-local-model","messages":[{"role":"user","content":"Explain a CPU in one sentence."}],"max_tokens":128}'
   ```

   A real successful completion is your first inference acceptance test. If the alias is unavailable, check the exact ID, reachability from the container, model status and Operations logs. Existing persistent route state may require operator reconciliation; do not assume a changed environment variable replaces an existing deployment.

The default worker image is an agent/control-plane component, not a bundled GPU inference runtime. Speech is disabled in the local Compose stack until a real runtime is configured. Model downloads and GPU passthrough need runtime-specific setup; see [model library](huggingface-model-library.md), [worker registration](worker-registration.md) and [GPU scheduling](gpu-model-scheduler.md).

### D. Access from another machine

The default stack deliberately publishes only on loopback. Changing a browser URL does not change that security boundary. For a trusted single-user remote lab, use SSH local forwarding for both Console and Gateway; keep their browser URLs at localhost and use matching ports on the client. For multiple users, do not expose development mode. See [multi-host deployment](multi-host-deployment.md) and the [security boundary](security-boundary.md).

### E. Production readiness

The convenience launcher is **not a production installer**. A separate, reviewed deployment must enforce trusted ingress, worker service authentication, network isolation, data recovery, and tenant authorization. Setting environment variables or enabling PostgreSQL alone does not establish those controls. Follow [deployment](deployment.md), [security boundary](security-boundary.md), and [release readiness](release-readiness.md). Do not expose a mock principal or the development MCP endpoint to untrusted clients.

---

## Part 3: Verifying Your Deployment

Open **Get started** in the Console. The guide distinguishes Gateway readiness from model readiness, excludes mock routes from its test prompt, and screens hardware using eligible worker heartbeats. It does not install models or mutate deployments. Configure a runtime, refresh and send a test prompt.

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
| Permanently delete local data | Only after an independently verified backup: `docker compose down -v` deletes named volumes; this is not a routine troubleshooting step. |

| Problem | Fix |
| :--- | :--- |
| `Cannot connect to the Docker daemon` | Start Docker Desktop (macOS/Windows) or run `sudo systemctl start docker` (Linux). |
| `port is already allocated` (`3000`, `8080`, `8090`, `8095`) | Stop the conflicting app or inspect with `lsof -i :8080`. |
| `permission denied` on Linux Docker socket | Run `sudo usermod -aG docker "$USER"` and log out and back in. |
| Console cannot reach Gateway | Open `http://127.0.0.1:3000`, or update `NEXT_PUBLIC_YONDER_GATEWAY_URL` in `.env` and run `docker compose up --build -d`. |
