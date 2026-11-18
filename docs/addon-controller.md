# Yonder Add-on Controller — first deployable service

**Status: experimental opt-in controller, draft PR #5.** This is a
**separate service** that performs explicitly approved, restricted Docker
operations. Core and its bundled add-ons continue working without it.

## What exists

- Authenticated controller API, with controller secret held server-side.
- Operator-maintained deployment allowlist containing full OCI image digests.
- Deterministic SHA-256 plan previews that change with version or resource
  limits; browser asks an administrator to approve the exact plan.
- Durable SQLite installation/removal jobs, with per-add-on active-operation
  locks and request idempotency.
- Reboot recovery: unfinished jobs become **interrupted**, never silently
  rerun. An operator must inspect host state and reconcile manually.
- Restricted Docker adapter. It only runs locally present digest-pinned
  images from an administrator-authored allowlist with read-only filesystem,
  dropped Linux capabilities, no-new-privileges, CPU/RAM/PID limits, no host
  mounts, no host ports, and initially **no network**.
- Gateway proxies plan/job operations and enforces `operations.manage`.
  The browser never sees the controller's secret or Docker socket.
- Unit tests use a fake Docker adapter, so CI cannot accidentally deploy.

## Dev/test setup (trusted operator only)

1. Copy `config/addon-deployments.example.json` to
   `config/addon-deployments.json` and restrict write permissions.
2. Generate a cryptographically strong secret, e.g.
   `openssl rand -hex 32`; set `YONDER_CONTROLLER_TOKEN` in a private
   local environment, not in Git.
3. To approve a **reviewed** container, add a deployment entry with
   `addon_id`, `version`, a full pinned
   `registry.example.org/path@sha256:<64 hex>` reference, resource limits,
   and `network: "none"`. Pull that **exact digest** to the Docker host
   separately after verifying the publisher and provenance. No image is
   auto-fetched from GitHub, and no example add-on has a default executable
   digest. The add-on must also be marked implemented and installed in Yonder.
4. Explicitly start the optional profile from the checkout:

   ```bash
   docker compose -f compose.yaml -f compose.addon-controller.yaml \
     --profile addon-controller up -d --build
   ```

5. Visit Add-ons in the browser as an authorized operator. Select an
   installed, controller-approved add-on, review its plan and approve. Monitor
   the durable job history. A preflight failure will not be called success.

**Do not enable the Docker socket mount on an untrusted or multi-user host.**
Access to `/var/run/docker.sock` is effectively host-administrator access,
even when the controller container has `cap_drop: ALL`. The optional Compose
profile provides a **developer/homelab prototype**, not a school-ready
security boundary. For production, move the controller to a separately
hardened machine or tightly constrained daemon API with mutually authenticated
transport and dedicated isolation.

## API contracts

Only the Gateway should call these authenticated controller operations:

- `GET /v1/approved` — approved deployment IDs and versions
- `GET /v1/plans/{id}?action=install|remove` — plan with approval hash
- `POST /v1/jobs` — exact plan SHA, action and idempotency key
- `GET /v1/jobs` / `GET /v1/jobs/{id}` — durable status

Gateway endpoints under `/api/admin/addons/controller/*` and
`/api/admin/addons/{id}/controller/*` enforce existing operator permissions.

The daemon requires a secret of at least 32 characters and a valid, restricted
deployment config on startup. It binds only inside the Compose network and
has no published host port. Do **not** expose port 8110 on the internet.

## Guarantees and known limitations

- Deployment plans cannot specify arbitrary commands, code, host mounts,
  ports, privileged mode or Docker flags.
- Unknown image tags, unapproved names and unpinned digests are rejected.
- Container name collisions are checked against owner labels; unowned
  containers cannot be removed.
- Image verification checks the local Docker image's referenced digest.
  **This is digest pinning, not cryptographic publisher signature verification.**
- The controller does not yet download images, create networks, configure
  credentials for add-on services, or manage their databases. Initial
  workloads have no network, so they are suitable only for self-contained
  pilot containers—not a functioning Convyx or Authentik deployment.
- A running container is not equivalent to application readiness; future
  adapters need protocol health checks, signed provenance and rollback.
- No automatic app migration, multi-host orchestration, service account
  delegation, user-managed policy changes or durable scheduler reconciliation.
- Docker administrator access remains a major threat; never advertise this
  prototype as ready for school student workloads or public deployments.

## Next release gates

1. Signed release bundle verification with immutable provenance and a
   publisher trust policy (not just a GitHub commit).
2. External installer/controller isolation and mutual authentication.
3. Config schemas, scoped secrets and resource/network policies.
4. Application-level health checks with safe rollout and compensating rollback.
5. Durable reconciliation of desired/observed state without repeat actions.
6. Typed service adapter, lifecycle migrations, backups and restoration.
7. School and small-business threat-model review, penetration test and
   deployment guidance.

## Tests

```bash
cd apps/addon-controller
uv run pytest -q tests/
uv run ruff check controller/ tests/
```

No Docker daemon is needed for the tests.


## Recovery after an interrupted operation

An interrupted job is a **blocking state**, not a warning. The controller
will reject another operation for the same add-on until review.

1. Open Yonder Add-ons and locate the `interrupted` job.
2. Click **Review interrupted job**. The Gateway asks the controller to inspect
   the actual Docker container on its host. A failed Docker connection or
   unowned container is not treated as an absent deployment.
3. Inspect the actual service state and its data using approved host tools.
4. Confirm the exact observed state: `running`, `stopped`, or
   `not_deployed`. The controller rechecks immediately before acknowledging.
5. The job becomes `acknowledged`; a **new** reviewed plan and idempotency
   key are required to retry. Acknowledgment never silently replays the job.

This is a first recovery barrier, **not** transactional rollback. If a
migration changed persistent data, recover it from a tested backup before
performing further operations. A collision with an unowned container must be
resolved by an administrator outside the controller.
