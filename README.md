# Yonder

**Intelligence beyond search.**

Yonder is an open-source, modular AI platform for running and operating local or self-hosted AI infrastructure behind a unified OpenAI-compatible gateway.

It provides:

1. **Web Console (`apps/console`)** — Conversational chat UI, model lab, recipes, helper glossary, and real-time operational diagnostics.
2. **OpenAI-Compatible Gateway (`apps/gateway`)** — Single control-plane API for chat, embeddings, reranking, image/video generation, and MCP tools.
3. **GPU Worker Agent (`apps/worker`)** — Hardware telemetry, model lifecycle management (`llama.cpp`, Diffusers), and runtime health reporting.
4. **MCP Server (`yonder-mcp`)** — Gateway-mediated Model Context Protocol server (`http://127.0.0.1:8095/mcp`) for IDEs like Cursor and Claude Desktop, plus an allowlisted Gateway MCP client (`/v1/mcp/*`).
5. **Model & Route Configuration (`config/`)** — Declarative model manifests, capacity/placement policies, and environment profiles.

---

## Quick Start (Local Development)

**Release status:** Yonder is under active development. The local stack is for a trusted single-user machine, not an internet-facing or school deployment. No stable release is published yet. Review the [release readiness checklist](docs/release-readiness.md) before wider use.

The easiest way to run Yonder locally is with the included launcher script. It checks for Docker, Docker Compose, and `curl`, offers to install any missing dependencies, creates `.env` from `.env.example`, and starts the full stack (**Gateway + Worker + Console + MCP Server**):

```bash
git clone https://github.com/dachakati-ux/Yonder-public.git
cd Yonder-public
./start.sh
```

Once the launcher finishes, open:

* **Web Console:** <http://127.0.0.1:3000>
* **Gateway API:** <http://127.0.0.1:8080>
* **Interactive API Docs (Swagger):** <http://127.0.0.1:8080/docs>
* **IDE MCP Server:** <http://127.0.0.1:8095/mcp>
* **Gateway MCP Status:** <http://127.0.0.1:8080/v1/mcp/status>

### Prebuilt image distribution

A versioned image candidate pipeline is available for review. Once tested release images are published, follow [the image deployment guide](docs/release-images.md). There is no stable image tag yet.

### Prefer raw Docker Compose?

```bash
cp .env.example .env
docker compose up --build
```

Once the Console opens, choose **Get started** for guided connection, hardware screening, and an explicit inference test.

New to Docker or setting up from scratch? Follow the **[Step-by-Step Deploy & Quick-Start Guide](docs/quickstart.md)**. A successful installation is not a real inference test: the development stack may start with mock routes. Follow the first real model steps before evaluating model quality.

### Useful Launcher Flags

```bash
./start.sh --yes        # Auto-confirm dependency installation prompts
./start.sh --postgres   # Use PostgreSQL instead of SQLite (local development)
./start.sh --logs       # Follow live container logs
./start.sh --stop       # Stop all running Yonder containers
```

---

## Ports & `.env` Configuration

All host ports are controlled from `.env` (copied automatically from `.env.example` on first run):

| Service | Default Port | `.env` Variable | Purpose |
| :--- | :---: | :--- | :--- |
| **Web Console** | `3000` | `YONDER_CONSOLE_PORT` | Next.js web UI (Chat, Model Lab, Operations, Academy) |
| **Gateway API** | `8080` | `YONDER_GATEWAY__PORT` | OpenAI-compatible inference API, `/v1/mcp/*`, `/docs` |
| **Worker Agent** | `8090` | `YONDER_WORKER__PORT` | GPU/hardware telemetry and runtime lifecycle API |
| **IDE MCP Server** | `8095` | `YONDER_MCP_PORT` | `yonder-mcp` Streamable HTTP endpoint for Cursor / Claude Desktop |
| **PostgreSQL** *(optional)* | `5432` | `YONDER_POSTGRES_PORT` | Optional Postgres database when launched with `--postgres` |

> **Changing ports:** Edit the port in `.env`, update `YONDER_CONSOLE_ORIGIN` / `NEXT_PUBLIC_YONDER_GATEWAY_URL` if you changed `3000` or `8080`, and re-run `./start.sh` (or `docker compose up --build -d`).

| Variable | Default | When to change |
| :--- | :--- | :--- |
| `YONDER_ENVIRONMENT` | `development` | Local quick start only; production requires a separately reviewed deployment. |
| `YONDER_SECURITY__MODE` | `development` | Production requires trusted ingress and a reviewed identity boundary. |
| `YONDER_SECURITY__WORKER_SHARED_SECRET` | *(unset)* | **Required in production.** Generate with `openssl rand -hex 32`. |
| `YONDER_CONSOLE_ORIGIN` | `http://127.0.0.1:3000` | Set to your public/LAN Console URL if not localhost. |
| `NEXT_PUBLIC_YONDER_GATEWAY_URL` | `http://127.0.0.1:8080` | Set to the Gateway URL reachable by your browser (rebuild console if changed). |
| `YONDER_MCP__ENABLED` | `true` | Enables Gateway MCP client (`/v1/mcp/*`). |
| `YONDER_MCP__SERVER_ENABLED` | `true` | Enables IDE MCP server status and `yonder-mcp` container on `:8095`. |
| `YONDER_LLAMA_CPP__BASE_URL` | *(unset)* | Point to a reachable OpenAI-compatible server; set enabled, served model and attach aliases as explained in the [first real model guide](docs/quickstart.md#c-first-real-model-response-lm-studio-or-another-openai-compatible-server). |
| `YONDER_DATABASE_URL` | `sqlite:///./data/yonder.db` | Uncomment the Postgres URL if using `./start.sh --postgres`. |

See [docs/quickstart.md](docs/quickstart.md) for the complete `.env` and MCP setup walkthrough.

---

## Architecture

All client applications and UIs talk exclusively to **Yonder Gateway**. Clients use stable model aliases rather than worker hostnames, GPU indexes, or filesystem paths.

```text
User / IDE (MCP :8095) / Application
          │
          ▼
   Upstream Auth / Ingress  (optional in dev; required in production)
          │
          ▼
    Yonder Gateway (:8080) ◄──► External MCP Servers (allowlisted)
          │
   ┌──────┼──────┐
   ▼      ▼      ▼
Worker  Worker  External
  #1      #2    OpenAI-compatible
(local) (GPU)   Endpoint
```

## Security Boundary

Yonder does **not** ship its own user/password database or login system. In production, authentication and authorization happen in an upstream ingress/IAM layer, and Yonder consumes normalized request context through a replaceable `RequestContextProvider` adapter.

For local development, `YONDER_SECURITY__MODE=development` provides a non-production mock principal. Production mode refuses to start with development security enabled.

See [docs/security-boundary.md](docs/security-boundary.md).

---

## Repository Layout

```text
apps/gateway   — OpenAI-compatible control plane, MCP client/server, and operator APIs (FastAPI)
apps/worker    — Worker agent for hardware metrics and runtime lifecycle
apps/console   — Web UI (Next.js) for chat, model management, and diagnostics
packages/      — Shared contracts, configuration, and telemetry libraries
config/        — Environment, model manifest, and worker configuration files
docs/          — Architecture, quickstart, MCP, and operations documentation
scripts/       — Helper scripts for benchmarking, model downloads, and ops
```

---

## Local Development (Without Docker)

Prerequisites: Python 3.12+, [uv](https://github.com/astral-sh/uv), Node.js 22+ with `npm`.

```bash
cp .env.example .env
make bootstrap
make validate-config
make test
make gateway   # http://127.0.0.1:8080
make worker    # http://127.0.0.1:8090
make console   # http://127.0.0.1:3000
```

## Project Goal & Milestone Tracker

**End goal:** Yonder 1.0 — an accessible, modular self-hosted AI platform for
individuals, developers, schools, and businesses.

The [Master Roadmap](docs/yonder-master-roadmap.md) is the planning source of
truth for milestone IDs (M01–M08), sub-goals, dependencies, acceptance criteria,
draft PR scope, and release gates. Each coding-agent handoff should reference a
specific sub-goal and provide test evidence. Draft implementation does not
mean released or production-ready.

---

## Documentation Index

* [Release readiness and validation](docs/release-readiness.md)
* [Versioned image distribution](docs/release-images.md)
* [Feature and add-on availability](docs/feature-status.md)
* [Step-by-Step Deploy & Quick-Start Guide](docs/quickstart.md)
* [MCP (Model Context Protocol) Guide](docs/mcp.md)
* [Multi-Host Deployment](docs/multi-host-deployment.md)
* [GPU Model Scheduler](docs/gpu-model-scheduler.md)
* [V100 / Linux Bootstrap](docs/v100-bootstrap.md)
* [Offline Bundle & Air-Gapped Install](docs/offline-usb-pack.md)
* [Architecture](docs/architecture.md)
* [Configuration](docs/configuration.md)
* [Model Manifests](docs/model-manifests.md)
* [Worker Registration](docs/worker-registration.md)
* [Security Boundary](docs/security-boundary.md)
* [Operations](docs/operations.md)
* [Troubleshooting](docs/troubleshooting.md)
* [Backup](docs/backup.md)

## Evaluate or contribute

* [Reproducible evaluation and demo](docs/evaluation.md) — local walkthrough and actual automated browser evidence.
* [Contributing](CONTRIBUTING.md) — development and review process.
* [Security policy](SECURITY.md) — responsible reporting and deployment limits.
* [Report a problem](https://github.com/dachakati-ux/Yonder-public/issues/new/choose) — use synthetic inputs and sanitized evidence.

## License

[MIT](LICENSE)
