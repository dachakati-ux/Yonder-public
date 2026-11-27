# Yonder add-on SDK (contract v1)

**Status:** Bundled activation, catalog discovery, compatible-version checks and permission-gated management are present in the source tree. The standalone experimental controller contains additional release trust, credential and migration primitives. Arbitrary remote package execution and a production-ready dynamic marketplace are not supported. This document defines the v1 manifest contract; see [feature status](feature-status.md) and [controller limitations](addon-controller.md).

## File layout

```text
addons/
  catalog.json
  addon-id/
    addon.json
    README.md
    config.example.json       # optional in future
    schemas/                  # optional schemas for service requests
```

Each add-on must have its own directory, a complete `addon.json` and a
`README.md`. The JSON manifest is **declarative**. It cannot execute a script,
install arbitrary Python modules or ask the Gateway to run Docker.

### Manifest v1 fields

| Field | Meaning |
| --- | --- |
| `schema_version` | Manifest syntax revision; currently 1 |
| `api_version` | Yonder add-on API contract; currently 1 |
| `id`, `name`, `version` | Stable add-on ID and SemVer package version |
| `min_core_version` | Oldest supported Yonder Core release |
| `max_core_version_exclusive` | Optional exclusive upper compatibility bound |
| `category`, `summary`, `docs` | Catalog presentation and bundled README path |
| `status` | `available` or `planned` |
| `install_mode` | `bundled` or `not_available`; remote exec prohibited |
| `permissions` | Requested **existing** Core capabilities for operator review |
| `dependencies` | Other add-on IDs that must already be installed |
| `config_schema_version` | Future migration compatibility marker |
| `requires_restart` | Whether activation eventually needs a service restart |
| `service_id` | Reserved for a separately managed service adapter |
| `warning` | Prominent limitations and safety caveats |

Core validates the manifest (unknown keys fail), compares Core version ranges,
requires dependencies, and refuses activation of unimplemented add-ons.

**Important:** Listing `operations.manage` in the manifest does not grant
that privilege to the add-on. Runtime service identities must be separately
authorized. Future extensions must request narrowly scoped access tokens.

## Stable boundaries for upcoming modules

Core owns these mandatory facilities:

- Human authentication and authorization, identity normalization and audit;
  an external SSO adapter must not bypass or replace Core enforcement.
- The model gateway, capabilities and routing policies.
- Bounded service identity, health/status, configuration validation and
  add-on lifecycle coordination.
- Version negotiation and maintenance-mode update safety.

Upcoming add-ons implement these interfaces **outside the Gateway process**:

| Capability | Intended integration |
| --- | --- |
| `identity_provider` | Authentik/OIDC session bridge with verified assertions |
| `workspace_backend` | Isolated student projects and VM/microVM compute |
| `model_provider` | Catalog/runtime adapters and approved model installation |
| `agent_orchestrator` | Convyx task bus and scoped agent coordination |
| `admin_panel` | Safe user-facing configuration and health surfaces |

These are **planned contracts**, not currently executable hooks. The first
runtime integration should use a mutually authenticated HTTPS service adapter
with a versioned capability handshake. No module gets direct database,
Docker socket or host filesystem access by default.

### Proposed v1 adapter handshake

`GET /addon/v1/capabilities` responds with stable fields:

```json
{
  "addon_id": "example-addon",
  "api_version": 1,
  "version": "0.1.0",
  "capabilities": ["agent_orchestrator"],
  "health": "ready"
}
```

The Core adapter must verify the service identity **before** trusting the
response. This response does not grant privileges; it only advertises
supported operations.

Future task calls must carry a trace ID, principal/project scope, short-lived
delegation token, idempotency key and bounded resource policy. User-scoped
resources must be checked at each service boundary.

### Lifecycle

`discovered → compatible → installable → installed → enabled`

`installed → upgrade_pending → migrating → enabled` (future)

`installed → disabled → uninstall_pending` (future)

Current bundled activation only persists `installed` and `not_installed`
and exposes whether the manifest is compatible and dependencies are met.
No services are started/stopped and no migrations are executed by the
current Add-ons manager. If a version changes, the manager flags review
rather than silently mutating schema or running code.

## GitHub catalog, safety and supply chain

The Gateway performs a read-only HTTPS check against the official
`addons/catalog.json`. This catalog is for **discovery only** and its content
cannot introduce or activate arbitrary downloaded code.

The actual installable add-on must already be included in the reviewed,
running Yonder release. The add-on manager does not execute downloaded
scripts, pip-install packages, clone repositories or reconfigure firewalls.

Eventually, remote installation can require cryptographically signed immutable
release artifacts, release identity verification, hashes, exact pinned
versions, a trusted publisher allowlist, configuration previews, migration
planning, rollback and a separate least-privilege installer service. Until
then, installing a new module means updating to a reviewed Yonder release
that bundles it.

## Development and test gates

Before proposing a new `available` add-on:

1. Add a strict manifest, README, example configuration and compatibility range.
2. Keep auth, policy and audit checks in Core and the add-on service.
3. Provide installation, disabling, restart and migration tests.
4. Verify school/enterprise isolation and delegated-identity behavior where relevant.
5. Ensure GitHub catalog is unavailable without affecting installed add-ons.
6. Test that invalid paths, arbitrary entrypoints, unknown permissions and
   unsupported Core versions fail closed.
7. Provide an end-to-end installation and restoration procedure for release.
8. Never advertise a planned module as working or compliant.

Run targeted development checks:

```bash
uv run ruff check apps/gateway/app/services/addon_contract.py apps/gateway/app/services/addons.py apps/gateway/app/api/addons.py
cd apps/gateway
uv run pytest -q tests/test_addon_contract.py tests/test_addons.py
```

A separate, fully reviewed school-ready release will require real login/SSO,
class/student authorization, retention/deletion policies, and adversarial
security testing.


## Separate-service adapter foundation (implemented, not a deployer)

The Gateway now includes a restricted, admin-configured connection registry for
future independently deployed add-ons:

- `GET /api/admin/addons/{addon_id}/service/health` requires
  `operations.view` and returns `not_installed`, `not_configured`,
  `verified`, or `unavailable`. Unknown add-ons are rejected.
- `POST /api/admin/addons/services/reload` requires `operations.manage`.
  Only the server's preconfigured file is read—no browser-supplied URL.
- `/addon/v1/capabilities` is the proposed service-side handshake, checked
  with a bounded HTTPS request, strict response schema, expected add-on ID,
  expected service ID and API version. Redirects and environment HTTP proxies
  are disabled. Unverifiable responses fail closed.
- The optional configuration file is
  `/etc/yonder/addon-services.json` and must be **operator mounted** as a
  private, nonsymlink file (0600 on POSIX). The registry never returns its
  tokens in an API response.
- A missing configuration file is normal on a basic Yonder installation:
  Core runs without any external add-on services.

Example **template only** (do not put real tokens in Git):

```json
{
  "schema_version": 1,
  "endpoints": [
    {
      "addon_id": "convyx",
      "expected_service_id": "convyx-service",
      "base_url": "https://convyx.example.org",
      "bearer_token": "REPLACE-WITH-A-LONG-RANDOM-SECRET"
    }
  ]
}
```

The optional file must be created by a trusted administrator and made
accessible only to the Gateway process. This is the foundation for connection
checks, **not a completed mTLS/service mesh integration**. The current
client uses certificate-validated HTTPS and a bearer token. Operators must
restrict outbound traffic with firewall/network allowlists and protect DNS:
DNS rebinding and a compromised configured domain can still threaten
internal resources without additional egress enforcement.

A future installer/controller must implement signed artifact verification,
isolated runtime provisioning, migrations, snapshots, rollback, service
credentials and restart/health monitoring **outside the Gateway**. The current
manager does not start a remote container or a VM. For school deployments,
external agent/compute modules are not considered production-ready without
resource-scoped identity, isolation and audit.
