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


## Verified publishers and signed releases

**New release-trust gate:** the opt-in Compose controller now sets
`YONDER_CONTROLLER_REQUIRE_SIGNATURES=true` by default. This means an
operator-approved OCI image digest **is not enough** to install it: the
controller must also verify an Ed25519 signature from an independently
approved publisher public key.

Each deployed add-on requires three administrator-provisioned, reviewable
inputs:

1. `config/addon-deployments.json`: exact add-on ID, version, OCI digest and
   resource/readiness policy. Its contents determine the approval-plan hash.
2. `config/addon-trust/publishers.json`: publisher IDs and trusted Ed25519
   public keys. No private signing keys belong on the controller or in Yonder.
3. `config/addon-releases/<addon-id>.json`: the detached signed descriptor
   binding that add-on ID, version and OCI image digest to a trusted publisher.

Example trusted key format (the public key string is a placeholder):

```json
{
  "schema_version": 1,
  "publishers": [
    {
      "key_id": "yonder-official",
      "public_key_ed25519_base64": "REPLACE_WITH_REAL_32_BYTE_BASE64_PUBLIC_KEY"
    }
  ]
}
```

The publisher signs a canonical descriptor consisting of the schema version,
add-on ID, version, and the exact OCI digest. Use the offline signing utility
from `apps/addon-controller` on a **trusted release-signing workstation**:

```bash
python sign_release.py \
  --addon-id example-addon --version 0.1.0 \
  --image 'registry.example.org/example@sha256:<full digest>' \
  --key-id yonder-official \
  --private-key /secure/offline-ed25519-key.pem \
  --out /tmp/example-addon.json
```

Only the **public key** and signed descriptor are distributed to controller
hosts. The private signing key is kept offline or in a dedicated release
signing service. Key creation, fingerprint verification, rotation, revocation,
and publisher trust bootstrapping remain explicit operator responsibilities.
Never auto-trust a key merely because it came from GitHub.

For local development/tests, signed-release enforcement can be disabled
explicitly, but that mode must not be used for school or business deployment.
Approval plans explicitly identify whether publisher verification was enforced.

The signature validates **who approved the precise image digest**. The Docker
adapter separately checks that the locally available image's `RepoDigests`
contains that approved reference. The controller currently does **not**
automatically fetch the OCI image from a registry. This is not yet a complete
transparency-log-backed software supply-chain or remote update process.

## Readiness and limited rollback

Administrators can set these additional fields in their approved deployment
record:

```json
{
  "readiness": "docker_healthy",
  "readiness_timeout_seconds": 60,
  "rollback_on_failed_readiness": true
}
```

`docker_healthy` uses the image-defined Docker HEALTHCHECK, not a
user-provided shell command or URL. The controller requires Docker to report
`healthy` before marking installation successful; a missing HEALTHCHECK is a
failure. The simpler `running` mode confirms only that the process is still
running. Both modes have a bounded readiness timeout.

If a newly installed container fails readiness and rollback is enabled, the
controller attempts to remove **only the container it just deployed**, after
checking its Yonder ownership labels. If removal fails, the job remains
`interrupted` and further operations are blocked until an administrator
reviews the observed state.

This is **not** database rollback or image-upgrade rollback. Those require
snapshots, migration guards, backup verification and a stronger deployment
adapter. No student workloads or multi-tenant infrastructure should be run
under the current prototype.


## Opt-in immutable registry acquisition (experimental)

A signed, approved release may now use `"image_acquisition": "signed_pull"`
instead of the default `"preloaded"`. The controller:

1. Requires `YONDER_CONTROLLER_REQUIRE_SIGNATURES=true` and verifies the
   Ed25519 descriptor against operator-provisioned public keys.
2. Checks that the full OCI digest is listed in the approved deployment plan.
3. Restricts downloads to exact registry hostnames in
   `YONDER_CONTROLLER_ALLOWED_REGISTRIES` (default: `ghcr.io`).
4. Pulls **only** the exact image `repository@sha256:<digest>`; never a
   mutable tag or a browser-supplied URL.
5. Rechecks Docker's locally reported repository digest before deployment.
6. Performs the approved readiness check and limited new-container rollback.

Example reviewed deployment entry:

```json
{
  "addon_id": "example-addon",
  "version": "0.1.0",
  "image": "ghcr.io/example/reviewed-addon@sha256:<64 hex characters>",
  "image_acquisition": "signed_pull",
  "network": "none",
  "readiness": "docker_healthy",
  "readiness_timeout_seconds": 60,
  "rollback_on_failed_readiness": true
}
```

The host administrator must still approve the publisher key, immutable OCI
digest, release descriptor, and requested resource limits. Merely appearing in
GitHub's Add-ons catalog **never** grants installation rights.

This requires the controller host's Docker daemon to reach the approved
registry. Networked application containers remain disabled by default:
`network: none` applies to the resulting add-on container, not image pulls
by the Docker daemon. Private registry authentication and credential
distribution are **not** implemented. Error messages are intentionally
sanitized rather than exposing registry responses.

**Limitations:** This is a pinned OCI artifact fetcher, not a general-purpose
GitHub archive installer or complete software supply chain. Release-signing
key provisioning is manual, registry/network egress control must be enforced
outside the process, and deployments still require independent security
review and real-host testing.


## Declarative configuration, secrets and approved internal networking

A deployment plan can now declare **non-secret** application settings,
pre-provisioned Docker named volumes, and a dedicated internal network. Each
choice is incorporated into its SHA-256 approval hash.

Example:

```json
{
  "addon_id": "example-addon",
  "version": "0.1.0",
  "image": "ghcr.io/example/reviewed@sha256:<64-hex-digest>",
  "network": "yonder-addon-net-example-addon",
  "public_settings": {"CLASSROOM_MODE": "enabled", "MAX_USERS": "30"},
  "secret_volume": true,
  "data_volume": true,
  "readiness": "docker_healthy"
}
```

### Protection model

- Public settings must use approved uppercase environment variable names.
  Names resembling `TOKEN`, `SECRET`, `PASSWORD`, `PRIVATE`,
  `CREDENTIAL` or `API_KEY` are rejected. Values cannot contain control
  characters and are capped at 256 characters; they may be shown in the
  approval preview, logs or Docker inspect. **Never put credentials here.**
- `secret_volume: true` mounts the administrator-prepared Docker named volume
  `yonder-addon-secrets-<addon-id>` **read-only** under
  `/run/yonder-secrets`. The controller does not generate, read, send, or
  expose secret contents. The application must support reading credential
  files from that path.
- `data_volume: true` mounts the distinct existing volume
  `yonder-addon-data-<addon-id>` read/write at
  `/var/lib/yonder-addon`. Disabling/removing the service does **not** delete
  the data volume; database upgrades still require backups.
- Only a Docker network named `yonder-addon-net-<addon-id>` is allowed.
  It must already exist, use Docker `bridge` and have `Internal=true`,
  together with labels `io.yonder.approved=true` and
  `io.yonder.addon=<addon-id>`. The controller does not create networks.
  This allows tightly scoped inter-container communication but no direct
  outbound internet from the add-on's network. No host ports are published.
- Both named volumes must already exist and have labels
  `io.yonder.approved=true`, `io.yonder.addon=<addon-id>`, and
  `io.yonder.purpose=secrets` or `data` respectively. Unlabeled or missing
  volumes are rejected **before** Docker run, preventing silent empty volume
  creation. No host bind mount paths or arbitrary Docker arguments are
  permitted.

Example manual preparation on an isolated **trusted** controller host:

```bash
docker network create --internal --driver bridge \
  --label io.yonder.approved=true \
  --label io.yonder.addon=example-addon \
  yonder-addon-net-example-addon

docker volume create \
  --label io.yonder.approved=true \
  --label io.yonder.addon=example-addon \
  --label io.yonder.purpose=data \
  yonder-addon-data-example-addon

docker volume create \
  --label io.yonder.approved=true \
  --label io.yonder.addon=example-addon \
  --label io.yonder.purpose=secrets \
  yonder-addon-secrets-example-addon
```

**Populate secrets separately using institution-approved privileged tooling.**
Do not paste credentials into Yonder's UI or a GitHub issue. Labeling a
Docker volume as a secrets volume does not encrypt it, restrict access by the
host administrator, or turn it into Docker Swarm Secrets. This is an
incremental no-secret-in-API design rather than a full Vault/secret-manager
integration. Institutions need encrypted storage, scoped identities, rotation,
audit and incident response before production use.

An internal network also does not by itself guarantee container isolation
against a malicious administrator, compromised Docker daemon or kernel
vulnerability. Network/DNS and host access controls remain the operator's
responsibility.


### Optional automatic preparation of managed resources

For a simpler first-time installation, an administrator may add:

```json
{
  "provision_managed_resources": true,
  "data_volume": true,
  "network": "yonder-addon-net-example-addon"
}
```

The controller will then create **only** the missing dedicated internal bridge
network and persistent data volume, using the exact preapproved names and
Yonder ownership/policy labels. It subsequently inspects those resources
and refuses a name collision with unexpected labels. These operations are
covered by the administrator-reviewed plan SHA-256.

The controller **never creates a secrets volume** automatically. If
`secret_volume` is requested, a properly labeled credential volume must
already exist, and its contents must have been prepared by approved
administrator tooling. No arbitrary host paths, public ports, external
network access or new Docker privileges become available.

Data volumes and networks are **not automatically deleted on uninstall or
failed readiness**. Existing projects, database files and administrator-created
secrets remain in place. Removal of those resources is a separate,
administrator-controlled retention and backup decision.

The default remains `provision_managed_resources: false`, which requires all
referenced resources to already exist. Production/school environments should
not use the Docker-socket controller prototype without a hardened deployment
and security review.


## Desired versus observed installation state

After a successful, readiness-verified installation, the controller records
`desired_state=running` alongside the exact approved add-on version and
OCI digest in its SQLite database. After a successful removal, it records
`desired_state=absent`. These records survive controller restarts.

The new authenticated `GET /v1/inventory` API (also proxied through
`GET /api/admin/addons/controller/inventory`) compares these desired
records with the actual state of the dedicated Docker container. The Add-ons
page displays **In sync** or **Review drift** for each known installation.

**Drift detection is read-only.** If a running add-on disappears or stops,
Yonder does not automatically reinstall, restart, or destroy resources.
Investigate the discrepancy and explicitly approve a new plan or recovery.
This preserves a foundation for a later reconciler without introducing
unreviewed automatic infrastructure changes today.

The inventory currently tracks deployments controlled by Yonder, not arbitrary
Docker resources and not stateful database migration versions. It is not a
substitute for independent backups or service monitoring.
