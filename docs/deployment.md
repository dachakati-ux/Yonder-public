# Deployment

## Full stack with Docker (recommended)

Starts gateway, worker, and console together:

```bash
cp .env.example .env
docker compose up --build
# or
make compose-up
```

Then open:

* Console: http://127.0.0.1:3000
* Gateway: http://127.0.0.1:8080
* Worker: http://127.0.0.1:8090

Inside containers the gateway/worker bind `0.0.0.0` (with
`allow_non_localhost_bind_in_development=true`). Host ports stay on `127.0.0.1`.
The browser talks to the gateway at `http://127.0.0.1:8080`.

Stop with `docker compose down` / `make compose-down`.

### Optional Postgres

```bash
docker compose -f compose.yaml -f compose.persistence.yaml --profile persistence up --build
# or
make compose-persistence
```

Gateway migrations run automatically on container start when
`YONDER_DATABASE_URL` is set.

## Local Mac development (without Docker)

```bash
make bootstrap
make gateway
make worker
make console
```

Services bind to `127.0.0.1` under development security mode.

## Production prerequisites

1. Upstream corporate security integration (`security.mode=trusted_ingress` or approved provider)
2. `environment=production` (development security mode is refused)
3. Gateway reachable only from approved ingress — not ordinary clients
4. External persistence for control-plane state and future conversation retention policy
5. Health probes: `GET /health`, readiness: `GET /ready`
6. Graceful shutdown support (gateway drains in-flight requests; worker sends disconnect)

## Migrations

```bash
make migrate
# or
uv run alembic -c database/alembic.ini upgrade head
```

Compose runs migrations in the gateway entrypoint. Tables include model
definitions/deployments/aliases, conversations, admin events, and the request
ledger.

## Backup guidance

Back up:

* `data/gateway-state.json` (alias + deployment runtime state when no database URL)
* Docker volume `yonder_data` / `yonder_pg`
* Model manifests under `config/models/`
* Environment configuration under `config/environments/` (without secrets)

Do not back up or retain prompt/response content unless an approved retention
policy explicitly requires it. Prompt logging remains disabled by default.
