# Troubleshooting

## Configuration refused at startup

**Symptom:** `environment=production` with `security.mode=development`

**Fix:** Use `trusted_ingress` (or another approved provider) for production.
Development injection is intentionally impossible in production.

## GPU metrics show `not_supported`

On Mac development hosts this is expected. `nvidia-smi` is absent.
Do not patch in fake V100 values. Collect inventory on the GPU server.

## Worker cannot register with gateway

1. Confirm gateway is listening on `127.0.0.1:8080`
2. Confirm `gateway_url` in config points at that address
3. Check gateway logs for security-context failures
4. In development mode, registration should receive the mock principal automatically

## Metrics look empty or null

Check the `availability` field. Yonder intentionally returns `null` values with
states like `not_reported` or `collection_failed` instead of inventing numbers.

## Capacity / 429 responses

Admission enforces:

* max prompt size
* max output tokens
* max active requests per deployment
* bounded queue depth
* queue timeout → `504 QUEUE_TIMEOUT`
* inference timeout → `504 INFERENCE_TIMEOUT`

When active slots and queue are full, the gateway returns `429 CAPACITY_EXCEEDED`.

## Gateway restart lost my loaded model

Check `data/gateway-state.json`. Unload/drain/alias changes persist there across
restarts. Model *definitions* reload from `config/models/*.yaml`.

## Worker shows connected after shutdown

Workers now call `/api/admin/workers/{id}/disconnect` on shutdown. If that call
fails, the gateway falls back to heartbeat expiry
(`worker_heartbeat_timeout_seconds`, default 30s).

## Console cannot start

Node.js/npm are required for `apps/console`. Use `make console` after Node 22+
is available; the Python control plane does not depend on the console for API
validation.
