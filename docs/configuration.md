# Configuration

Configuration layers, applied in order:

```text
Application defaults
        ↓
Environment configuration (YAML)
        ↓
Node configuration (documented examples under config/nodes/; apply via env today)
        ↓
Environment variables and secret references
```

For a 1× control-plane + 2× worker lab rack, see
[`multi-host-deployment.md`](multi-host-deployment.md).

## Loading

```bash
export YONDER_CONFIG_FILE=config/environments/development.yaml
make validate-config
```

`yonder_configuration.load_settings()` validates typed settings during startup.

## Important keys

| Key | Meaning |
| --- | --- |
| `environment` | `development`, `lab`, or `production` |
| `security.mode` | `development` or `trusted_ingress` |
| `gateway.host` / `gateway.port` | Gateway bind address |
| `worker.host` / `worker.port` | Worker agent bind address |
| `gateway_url` | Worker → gateway registration target |
| `worker_public_endpoint` | Gateway-reachable worker URL (required when bind host is `0.0.0.0`) |
| `admission.*` | Request size, concurrency, queue, timeout limits |
| `worker_registration.*` | Dynamic worker allowlist and untrusted-worker policy |
| `model_roots` / `YONDER_MODEL_ROOTS` | Comma-separated server-side roots the Models UI may scan |

## Automatic model-folder setup

The Console Models page can inspect a folder only when it is under one of the
configured `model_roots`. The browser sends the path to Yonder Gateway; it never
contacts workers or inference runtimes directly.

```bash
export YONDER_MODEL_ROOTS=/srv/yonder/models
```

Folder setup detects Hugging Face safetensors, Diffusers layouts, and GGUF
files; proposes modality aliases; hashes the files; and writes `bundle.yaml`.
It does **not** select or enable a runtime. Runtime activation remains a separate
operator action after measured host compatibility is recorded.

## Production refusal

Startup fails when:

```text
environment = production
security.mode = development
```

Production also refuses `worker_registration.mode=allow_all`. Configure an explicit
allowlist and keep `accept_untrusted=false` unless an approved operational process
requires otherwise.

## Diagnostics

`settings.export_public()` returns a redacted dictionary suitable for diagnostics.
Secret-like keys are replaced with `***REDACTED***`.

## Mac local defaults

`config/environments/development.yaml` binds services to `127.0.0.1` and enables
development security mode for local Mac work until the GPU server is online.
