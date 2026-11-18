# Worker Registration

Every GPU server runs a Yonder Worker Agent.

## Responsibilities

* Register the node
* Report hardware inventory
* Send heartbeats
* Report runtime availability and loaded models (later phases / lifecycle stubs)
* Acknowledge lifecycle load/drain/unload stubs via `/v1/lifecycle/*`
* Start/stop approved deployments (Phase 3 real runtime)
* Collect NVIDIA and host metrics without fabricating values

## Registration modes

### Static registration

```yaml
workers:
  - id: v100-worker-01
    endpoint: http://yonder-worker-01.internal:8090
    enabled: true
```

### Controlled self-registration

Configured via `worker_registration`:

* `mode: allow_all` — accept every worker as trusted (default local)
* `mode: allowlist` — only `allowed_worker_ids` are trusted
  * `accept_untrusted: true` — unknown workers register but stay untrusted
  * `accept_untrusted: false` — unknown workers are rejected (403)

This is a stub until production trust (mTLS / attestation) is defined.

## Multi-host advertised endpoint

When the worker binds to `0.0.0.0`, set `worker_public_endpoint` (env
`YONDER_WORKER_PUBLIC_ENDPOINT`) to a Gateway-reachable URL such as
`http://192.168.10.21:8090`. Registration and heartbeats advertise that URL for
lifecycle callbacks. See [`multi-host-deployment.md`](multi-host-deployment.md).

## Local Mac behavior

The local worker registers as `local-dev-worker` against the gateway.
GPU discovery reports `not_supported` until `nvidia-smi` exists on the host.

Gateway endpoints:

* `POST /api/admin/workers/register`
* `POST /api/admin/workers/{worker_id}/heartbeat`
* `POST /api/admin/workers/{worker_id}/disconnect`
* `GET /api/admin/workers`

Worker mutation endpoints require the normalized `workers.report` capability.
Registration is the only operation that creates a dynamic worker record. Heartbeat
and disconnect requests reject unknown workers, and heartbeat payload IDs must match
the route ID.

## Worker service authentication

Gateway and workers authenticate each other with a shared service token
(`security.worker_shared_secret`, header `security.worker_token_header`,
default `X-Yonder-Worker-Token`). This is infrastructure trust between
Yonder services — corporate user authentication stays upstream.

* **Worker -> gateway**: registration, heartbeat, and disconnect calls carry
  the token. Under `security.mode=trusted_ingress` the gateway maps a valid
  token to a synthesized service principal holding only `workers.report`.
* **Gateway -> worker**: lifecycle, generation, speech, and runtime-proxy
  calls carry the token. When the secret is configured, the worker rejects
  any non-probe request without it (401); `/health`, `/ready`, `/version`,
  and `/metrics` stay open for probes.
* **Production fails closed**: configuration validation refuses
  `environment=production` without a non-empty `worker_shared_secret`.
  Set it via `YONDER_SECURITY__WORKER_SHARED_SECRET` from a managed secret
  and use the same value on the control plane and every worker.
