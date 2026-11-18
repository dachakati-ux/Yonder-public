# Yonder — Master End Goal & Milestone Roadmap

**Canonical planning document** · Updated 2026-11-18 · Planning baseline for coding agents

> This file is a roadmap, not a claim that the described features exist.
> Implementation status must be verified against merged code, tests and live
> deployment. A draft PR is not a released feature.

## End Goal: Yonder 1.0

Deliver a **modular, self-hosted AI platform** that is easy for individual
users to start, yet powerful and secure enough for schools, developers and
small businesses. Yonder Core must remain lightweight and independently
useful; advanced features are installed as optional, versioned add-ons.

A user should be able to:

1. Install Yonder via a documented, reliable single-host or multi-host setup,
   connect available local/external models, and chat without learning
   infrastructure first.
2. Explore models, download verified artifacts, check device compatibility,
   schedule or connect workers, and safely update Yonder.
3. Discover add-ons within Yonder; review version compatibility, permissions,
   settings, resource requirements, provenance and risk; install, upgrade,
   disable and uninstall through a monitored, recoverable controller.
4. Optionally connect existing school/business identity (Authentik, generic
   OIDC, Entra, Google Workspace, etc.) without compromising Core's required
   authentication/authorization boundary.
5. Optionally create private, **actually isolated** coding/agent workspaces
   with quotas, audit, and institution-scoped permissions.
6. Use Academy features appropriate to students, teachers and organization
   admins while respecting student privacy, retention and data ownership.
7. Optionally connect Convyx as an **independent** agent collaboration
   platform: shared context, orchestration, delegated tasks, scoped memory.
8. Keep each add-on and Yonder Core independently deployable, supportable and
   testable, with explicit release health, recovery, migration and audit.

### Non-negotiable principles

- **Core-first:** Core is functional with every add-on disabled. No add-on may
  bypass authentication or authorization or require a privileged Docker socket
  in the Gateway.
- **Trust-first:** Read-only GitHub catalog discovery is not permission to
  execute code. Pinned artifacts, publisher signatures, approvals and
  least-privilege services are required for deployable add-ons.
- **No misleading status:** `planned`, `draft implemented`, `verified`,
  `released` and `institution-ready` mean different things.
- **Use existing identity and infrastructure:** Connect first rather than
  forcing administrators to replace their current IdP, model endpoint or
  virtualization platform.
- **Data ownership:** Preserved data and migrations/backups are explicit.
  Removal must not silently erase projects, models or secrets.
- **Affordable and accessible:** A small single-host deployment must remain
  practical; GPU clusters, VMs and multi-agent systems are opt-in.
- **Replaceable integrations:** Convyx, Proxmox and institutional IdPs remain
  separate products/services, linked by versioned, authenticated adapters.
- **Institutional privacy:** No claim of compliance until reviewed against
  applicable rules and signed-off controls. Isolation and school role
  authorization require adversarial tests, not just UI hiding.

## Live milestone issues

Milestone checklists are tracked in GitHub Issues; the master roadmap retains
the architecture, dependencies and acceptance criteria. Keep the issue and
this document synchronized when evidence changes.

| Milestone | Tracking issue |
| --- | --- |
| M01 Core | [#6](https://github.com/dachakati-ux/Yonder-public/issues/6) |
| M02 Add-ons | [#7](https://github.com/dachakati-ux/Yonder-public/issues/7) |
| M03 Identity & SSO | [#8](https://github.com/dachakati-ux/Yonder-public/issues/8) |
| M04 Model Library | [#9](https://github.com/dachakati-ux/Yonder-public/issues/9) |
| M05 Workspaces | [#10](https://github.com/dachakati-ux/Yonder-public/issues/10) |
| M06 Academy | [#11](https://github.com/dachakati-ux/Yonder-public/issues/11) |
| M07 Convyx | [#12](https://github.com/dachakati-ux/Yonder-public/issues/12) |
| M08 Release Validation | [#13](https://github.com/dachakati-ux/Yonder-public/issues/13) |

## Tracking semantics

- **Released** — merged, packaged, supported and actually deployment-tested.
- **Implemented in draft** — code exists in an unmerged PR; focused tests may
  pass, but deployment/release remains unverified.
- **Design in draft** — specification exists, no production implementation.
- **Planned** — accepted goal or contract, implementation not present.
- **Blocked** — prerequisite or failing release gate prevents delivery.

Each milestone is complete only when **all** its acceptance checks pass and
the milestone has a reproducible test report, migration notes if applicable,
and a reviewable merged PR. A green targeted test is not equivalent to a green
entire release.

## Milestone 01 — Stable Core and onboarding

**Outcome:** Yonder is useful out of the box with one host and degrades
gracefully without optional services. **Status:** Existing foundation;
completion and release validation pending.

Sub-goals:

- [ ] **M01.1** Stabilize chat, OpenAI-compatible gateway, model aliases,
  hardware/worker registration, MCP, generation, Academy entry points.
  Maintain backward-compatible contracts and realistic error states.
- [ ] **M01.2** Validate the `start.sh` / Docker Compose quick start,
  dependency bootstrap, configuration checks and offline guide on clean hosts.
- [ ] **M01.3** Define lightweight single-host, external model endpoint,
  multi-host worker and optional PostgreSQL installation modes.
- [ ] **M01.4** Complete dependable backups, restoration, migration
  preparation, state retention and health checks.
- [ ] **M01.5** Finish safe Yonder update discovery and human-approved
  single-host update/rollback; clearly distinguish Git revision checks from
  trusted signed releases.
- [ ] **M01.6** Resolve existing repository-wide Python lint, TypeScript and
  dependency audit baseline; make full CI green on a fresh checkout.

Acceptance: clean install + basic inference/chat + worker reconnect + restart
and restoration + full CI pass on supported host(s), with all optional add-ons
disabled.

## Milestone 02 — Add-on platform and controller

**Outcome:** A predictable one-click experience for *reviewed* add-ons with
stable contracts and controlled lifecycle. **Status:** Framework and
controller implemented in draft PRs; not released.

Sub-goals:

- [x] **M02.1 — Draft implementation:** Repository
  `/addons/<id>/addon.json` convention, official discovery index and Add-ons
  UI; reviewed bundled-only activation (PR #4).
- [x] **M02.2 — Draft implementation:** Manifest schema, Core/API compatibility,
  declared permissions, dependency checks, versioned service capabilities
  handshake, admin/audit boundary (PR #4).
- [x] **M02.3 — Draft implementation:** Separate controller, durable idempotent
  jobs, reviewed deployment SHA, Docker adapter, resource limits and read-only
  desired-vs-observed inventory (PR #5).
- [x] **M02.4 — Draft implementation:** Immutable OCI digest approval,
  offline Ed25519 publisher verification, optional allowlisted registry pull,
  operator-approved internal network/data volumes (PR #5).
- [x] **M02.5 — Draft implementation:** Generic schema-driven *non-secret*
  setup wizard and read-only preflight; scoped credential inventory,
  offline credential volume delivery and metadata audit (PR #5).
- [ ] **M02.6** Harden controller host privileges; replace direct Docker
  socket exposure for multi-user/institutional deployment with an isolated,
  strongly authenticated deployment service. Validate outbound policies.
- [ ] **M02.7** Implement add-on release update workflow, deprecation,
  compatibility pinning, signature key rotation/revocation and supported
  package delivery.
- [ ] **M02.8** Add transactional rollout orchestration, stateful migration
  contract, verified backups/restores, rollback/roll-forward and crash
  recovery; never assume container rollback reverses a database migration.
- [ ] **M02.9** Complete secret backend lifecycle: supported KMS/Vault
  adapter, scoped service identities, encrypted backup recovery, provider
  revocation, synchronization acknowledgment and restart policies.
- [ ] **M02.10** True independently deployable optional services and
  configuration UI mounting, without modifying Yonder Gateway per add-on.
- [ ] **M02.11** Test install/upgrade/uninstall under network outage, full disk,
  stopped Docker daemon, failed migrations, malicious packages and restart;
  collect real-host test reports.

Acceptance: a reviewed signed add-on is discoverable, can be installed,
configured, upgraded, disabled and removed through a common UI; all changes
are authorized, audited, recoverable and comprehensively tested. Basic Core
continues working when the controller is absent or down.

## Milestone 03 — Identity, security and institutional roles

**Outcome:** Secure shared deployment with plug-and-play existing identity.
**Status:** Design in PR #3; production implementation pending.

Sub-goals:

- [ ] **M03.1** Preserve mandatory Core auth/authz boundary and strip untrusted
  ingress headers; test direct-Gateway identity-header spoofing.
- [ ] **M03.2** Build generic OIDC Authorization Code + PKCE bridge with
  validated issuer/audience/nonce/JWKS, secure sessions and CSRF protection.
- [ ] **M03.3** Authentik guided template, followed by Keycloak, Entra,
  Google Workspace and generic OIDC; LDAP/SAML via trusted IdP bridges.
- [ ] **M03.4** Tenant/institution/class scoped roles, teacher/student/admin,
  provisioning/deprovisioning and default-deny resource-level permissions.
- [ ] **M03.5** MFA/conditional access delegated to IdP, service delegation,
  session expiration, audit, safe error handling and recovery flows.
- [ ] **M03.6** Security and privacy threat model, documented retention,
  deletion/export, parental/age constraints as applicable, and school-specific
  external review. Do not claim FERPA/COPPA certification without review.

Acceptance: end-to-end SSO, tenant separation and role denial tests,
malicious-header tests, session/logout/revocation tests, fail-closed identity
provider outage behavior, and institutional admin review.

**Dependency:** M01 security baseline + M02 installer/secret policy; Core
authorization enforcement is not an optional plug-in.

## Milestone 04 — Model Library and inference orchestration

**Outcome:** Users discover compatible models and run them with minimal
manual knowledge. **Status:** Working slice in draft PR #1; complete add-on
packaging, runtime binding and release checks pending.

Sub-goals:

- [ ] **M04.1** Hugging Face model search, metadata, licensing, revision
  inspection and compatible artifact filtering.
- [ ] **M04.2** Verified managed downloads and cancellation/retry/cleanup,
  persistent download jobs and disk quota/provenance policy.
- [ ] **M04.3** Model installation, bundle validation, runtime registration,
  loading/unloading, explicit permissions and safe rollback after failure.
- [ ] **M04.4** LM Studio external-worker and model endpoint integration,
  `llama.cpp`, MLX/Mac and supported GPU runtimes without unnecessary
  dependencies; hardware-aware placement and memory-fit estimates.
- [ ] **M04.5** Hardware/worker discovery across heterogeneous hosts, tested
  routing, safe defaults, health checks and rescheduling on failures.
- [ ] **M04.6** Model evaluation, compatibility matrix, reproducible
  benchmarks, generation workflows and clear manual loader controls.
- [ ] **M04.7** Optional Model Library packaging while keeping basic model
  connections in Core.

Acceptance: select a compatible model, verify license and source, download
and validate it, load it on a supported endpoint, run inference, restart and
recover without corrupting assets.

## Milestone 05 — Private Workspaces

**Outcome:** Private, reliable, isolated projects and execution sessions.
**Status:** Design in PR #2; implementation pending.

Sub-goals:

- [ ] **M05.1** Dedicated workspace API/service and persistent project
  metadata; Core only knows versioned provider contracts.
- [ ] **M05.2** Per-user and per-class ownership, scoped file access,
  quotas, expiration, retention, export and deletion.
- [ ] **M05.3** Secure untrusted-code isolation in VM/microVM boundaries,
  ephemeral compute and durable files. Treat ordinary containers as
  insufficient isolation for arbitrary student code.
- [ ] **M05.4** Optional Proxmox provisioning adapter and other supported
  backends, never requiring Proxmox for a single-host Yonder.
- [ ] **M05.5** IDE/editor, terminal execution, project snapshots, session
  cost/resource controls and full audit.
- [ ] **M05.6** Concurrency, sandbox escape, cross-student access and resource
  exhaustion testing.

Acceptance: two students cannot read, write, execute or discover each
other's private resources; admins have explicitly defined oversight; a
compromised workspace cannot reach Core secrets, identity or Docker control.

**Dependency:** M03 tenancy/auth; M02 service lifecycle and resource
controller isolation.

## Milestone 06 — Academy and organization features

**Outcome:** A school or organization can administer safe AI learning
without becoming an infrastructure engineering team. **Status:** Academy
foundation exists in Core; organization workflows not complete.

Sub-goals:

- [ ] **M06.1** Student/teacher/institution roles, classes, enrollment,
  course policies and organization resource settings.
- [ ] **M06.2** Curriculum/lesson tools, model usage guidance, assignment
  workflows and appropriate progress visibility.
- [ ] **M06.3** Controlled student Workspaces and age/institution-specific
  agent permissions, spending/compute quotas and acceptable-use controls.
- [ ] **M06.4** Privacy notices, exports/deletion, audit and configurable
  retention with reviewed institution-specific requirements.
- [ ] **M06.5** Small-business setup templates, approved external IdP
  connections, admin diagnostics, billing/usage reporting only if needed.

Acceptance: complete pilot flows for teacher, student and admin with tenant
isolation, supportability and approved privacy policies.

**Dependency:** M03 + M05 (for execution features).

## Milestone 07 — Optional agents and Convyx

**Outcome:** Coordinated, auditable multi-agent collaboration without
copying Convyx into Yonder Core. **Status:** Add-on manifest planned,
integration not implemented.

Sub-goals:

- [ ] **M07.1** Independently deployed Convyx service and versioned
  authenticated Gateway adapter.
- [ ] **M07.2** Scoped agent identities, tool permissions, resource
  budgets, per-workspace/class access and auditable delegation.
- [ ] **M07.3** Shared context indexing, project memory, task planning,
  agent team coordination and resumable handoffs.
- [ ] **M07.4** Model-aware routing and observability: token/compute usage,
  outcomes, failure reasons, and explainable actions.
- [ ] **M07.5** Optional Academy Agent Collaboration Lab for teaching
  agent roles, prompts, review, testing and accountability.
- [ ] **M07.6** Prevent cross-tenant context leakage, unauthorized
  tool execution and unsafe autonomous resource provisioning.

Acceptance: coding/review/test agents collaborate on a private project,
restart safely, honor delegated scopes and provide a replayable audit trail.
Convyx operates independently if Yonder is removed.

**Dependency:** M02 + M03 for institutional use; M05 for executable workspaces.

## Milestone 08 — Verification and release readiness

**Outcome:** Evidence-based, supportable Yonder 1.0 release, not only a
large collection of draft branches. **Status:** Not met.

Sub-goals:

- [ ] **M08.1** Merge milestone PRs in dependency order with repeatable
  targeted and full CI; resolve integration conflicts deliberately.
- [ ] **M08.2** End-to-end integration matrix: Linux/macOS single-host,
  heterogenous local/remote worker, optional database, offline install,
  disconnected GitHub, existing institutional IdPs.
- [ ] **M08.3** Security assessment: threat model, privilege boundaries,
  penetration tests, secrets recovery, malicious artifacts, sandbox
  isolation, cross-tenant access, operator approvals.
- [ ] **M08.4** Data retention and migration test matrix: install,
  clean upgrades, failed upgrades, backups, restore, rollback/roll-forward
  and remove/reinstall without data loss.
- [ ] **M08.5** Usability tests with novice home users, developer/maintainers,
  school admins and teachers, including a setup without console commands.
- [ ] **M08.6** Documentation, support/runbooks, release notes,
  supported upgrade versions, licensing, accessibility and disaster recovery.
- [ ] **M08.7** Produce signed/tagged release with published reproducible
  build, compatibility matrix, explicit limitations and rollback guidance.

Acceptance: full CI is green, release artifacts are verified, reproducible
real-host smoke tests pass, institutional cases get independent review,
and no blocking security/data-loss issues remain.

## Dependency sequence and parallel work

```text
M01 Core stability ──────────────┬── M04 Model Library
                                 │
M02 Add-on platform ─────────────┼── M03 Identity & Security ──┐
                                 ├── M04 Model Library         │
                                 ├── M07 Convyx adapter        │
                                 └── M05 Workspaces <──────────┤
                                             │                 │
                                             └── M06 Academy <─┘
                                                       │
             All accepted release candidates ──────── M08
```

These are *logical gates*. M01/M02/M04 design and development may progress in
parallel. Institutional activation of M05/M06/M07 must wait for verified
identity, isolation and delegation boundaries.

**Recommended active order** (avoid excessive simultaneous PRs):

1. Finish current M02 controller lifecycle work and the M01 CI baseline.
2. Integrate M04 model discovery/runtime completion while packaging as an
   add-on without breaking Core model loading.
3. Implement M03 identity security foundation early enough that workspace
   and Academy APIs can build against real principals.
4. Build M05 isolation; then M06 organizational flows.
5. Integrate M07 Convyx as a separate service; finish M08 release gates.

## Existing draft PRs and work inventory

| PR | Scope | Current interpretation |
| --- | --- | --- |
| [#1](https://github.com/dachakati-ux/Yonder-public/pull/1) | Model Library, verified downloads, Yonder update notices | Draft implementation; partial, not released |
| [#2](https://github.com/dachakati-ux/Yonder-public/pull/2) | Private Workspaces | Design only, not implemented |
| [#3](https://github.com/dachakati-ux/Yonder-public/pull/3) | Identity & SSO | Design only, not implemented |
| [#4](https://github.com/dachakati-ux/Yonder-public/pull/4) | Add-on registry, manifest contract, UI, service foundation | Draft implementation; not released |
| [#5](https://github.com/dachakati-ux/Yonder-public/pull/5) | Standalone controller, signed OCI installs, configuration, encrypted credential inventory and offline delivery | Draft implementation **stacked on #4**; not released |

**Important:** PR #1, #2 and #3 were created separately from the add-on
framework branches. They must be rebased or integrated intentionally rather
than assuming their files already exist in PR #5. The latest focused add-on
and controller CI jobs passed, but the full repository validation job
continues to fail on existing lint debt. No institutional acceptance or
real-host controller deployment has been verified.

## Agent work breakdown contract

Every coding-agent handoff should use this template:

```text
Milestone: M02
Sub-goal: M02.9
Objective: [one measurable outcome]
Prerequisites: [IDs and interface contracts]
Scope: [exact services/files owned]
Out of scope: [what must not be changed]
Security/privacy boundary: [roles, secrets, trust, data handling]
Implementation steps: [small commits]
Tests: [unit, integration, adversarial, host]
Definition of done: [observable expected results]
Evidence: [PR, CI run, reproducible test commands]
Status: planned | in_progress | draft_implemented | verified | released
```

**Agent rules:**
- Pick one sub-goal and complete its tests before starting another.
- Don't claim a milestone complete merely because its UI exists or unit tests
  pass; deployment and security validation still apply.
- Never copy Convyx implementations into Yonder Core or silently promote
  design-only add-ons to `available`.
- Never put credentials, private keys, student data or host secrets in Git.
- Keep backwards compatibility and fail-closed authorization; require
  explicit approval for destructive infrastructure/data operations.
- Update this roadmap when the scope or accepted test evidence changes.

## Progress evidence — 2026-11-18

- **M01.6 (partial):** all 25 reported Python Ruff lint findings were
  corrected on `feature/addon-controller` without suppressing checks. The
  Gateway/worker and Console tests, Python lint, and production build
  subsequently passed in full CI run
  [44393875389](https://github.com/dachakati-ux/Yonder-public/actions/runs/44393875389).
  Dependency vulnerability review and real-host testing remain release gates.
- **M02.9 (draft):** encrypted credential inventory and scoped offline
  delivery now have recorded delivery revisions; the UI distinguishes
  `never_delivered`, `delivered_unverified`, `stale_delivery`,
  `revoked_cleanup_required`, and `revoked_removed`. These receipts
  describe local file operations, not confirmation that the add-on process
  loaded the secret or that its provider accepted the credential.
- **Tracking:** [M01 #6](https://github.com/dachakati-ux/Yonder-public/issues/6)
  and [M02 #7](https://github.com/dachakati-ux/Yonder-public/issues/7).
- **Remaining:** dependency auditing, a host-level Docker
  smoke test, and security review before either sub-goal is released.

## M02.7/M02.8 implementation evidence — 2026-11-18

- **M02.7 draft:** trusted Ed25519 publisher keys can now be revoked or
  expired via administrator-owned trust policy. Revoked/expired keys fail
  release verification; tests cover normal validity and failure cases.
  Fleet-wide propagation, key rotation procedures, artifact transparency and
  incident recovery are still incomplete.
- **M02.8 draft:** a read-only upgrade assessment reports source/target
  digests, migration and backup requirements and all safety blockers.
  Stateless-only staged upgrades have an **experimental opt-in** controller
  implementation: signed release plans, candidate readiness, previous image
  identity checks, Docker rename-based cutover, preserved stopped old
  containers, and bounded restoration on failed readiness. Restart/cutover
  uncertainty marks the job interrupted and blocks retries.
- **Recovery gate:** an interrupted upgrade can be acknowledged only after
  the controller verifies the previous image is running under its canonical
  name, with no job-specific staging/rollback artifacts.
- **Restrictions:** feature flag defaults off; no persistent data volumes,
  secret volumes, external networking, or declared data migrations may use
  this stateless path. No live-host smoke test has run. State-aware backups,
  transactional database migrations, reversible upgrades for stateful add-ons,
  and production-grade controller isolation remain open.
- **Evidence:** [PR #5](https://github.com/dachakati-ux/Yonder-public/pull/5)
  and [controller guide](docs/addon-controller.md). Automated fake-Docker
  tests are not a substitute for crash/power-loss and real daemon testing.

### Offline backup verification foundation — 2026-11-18

- [x] **M02.8 draft sub-slice:** offline one-file backup package creation,
  strict previous-release manifest validation, SHA-256 checksums, path
  restrictions, freshness and operator quiescence declaration; an explicit
  private empty-directory restore test is available.
- [x] Read-only upgrade preview reports matching offline backup content proof,
  without marking live database consistency or stateful migration safe.
- [x] Draft SQLite online backup adapter and CLI with WAL-consistent snapshot,
  database structural verification and offline restore test. Not yet
  released or tested against real add-on databases.
- [ ] Other database-native backup adapters, trusted/signed backup provenance,
  application-level restore acceptance, migration recovery, durable cutover,
  and real-host failure testing remain open.
- [Guide](stateful-upgrade-backups.md) and [M02 tracking issue](https://github.com/dachakati-ux/Yonder-public/issues/7).

### M02.8 SQLite migration contract and rehearsal foundation — 2026-11-18

- [x] Draft **versioned declarative** SQLite migration contracts bind an add-on, old/new version and pinned image digest, and old/new database revision. Unsupported/raw SQL and shell commands are rejected.
- [x] Offline sandbox runner restores a previously verified SQLite snapshot, validates the starting schema revision and integrity, and rehearses nullable-column and index additions within one transactional migration.
- [x] Controller read-only upgrade assessment checks administrator-owned migration contract identity and reports it separately from backup integrity. It never runs a live migration or unlocks a stateful cutover.
- [ ] Publisher-signed contract provenance, application-level schema/data validation, reversible/destructive migration planning, durable migration receipts, multi-store workflows and real-host fault injection remain release gates.
- [Guide](sqlite-migration-rehearsal.md) · [M02 issue #7](https://github.com/dachakati-ux/Yonder-public/issues/7).

### M02.8 signed migration and durable rehearsal journal — 2026-11-18

- [x] Draft detached Ed25519 migration attestation binds the reviewed
  canonical contract, old/new immutable image/version and exact source
  backup manifest digest. Revoked, expired or untrusted publisher keys
  fail verification. Signing uses offline admin-held private keys.
- [x] Offline rehearsal CLI now **requires** the trusted signature and
  a durable SQLite journal. Completed runs record the contract, backup,
  target image and sandbox checksum. Restart-interrupted runs cannot
  auto-resume or be recorded as passed.
- [x] Operator CLI can inspect rehearsal metadata without altering active
  runs, or explicitly acknowledge an interrupted record without
  upgrading its result to success.
- [x] Upgrade readiness now reports **signed migration verification**
  separately from unsigned identity checks and content-valid backups.
- [ ] Tamper-resistant centralized audit records, application compatibility,
  live production data migrations, database-specific recovery, multi-host
  orchestration and real-host fault tests remain incomplete. Stateful
  `can_execute` stays false.
- [Guide](sqlite-migration-rehearsal.md) · [M02 issue #7](https://github.com/dachakati-ux/Yonder-public/issues/7).

## Immediate next sub-goal

**M02.9 — Credential lifecycle hardening:** finalize scoped offline credential
file delivery, receipt/version tracking, stale-delivery detection, explicit
revocation audit and reliable backup; then design authenticated runtime
delivery and master-key rotation as separate security-reviewed increments.
Do **not** assume a `configured` credential is installed or active.

### M02.9 offline master-key rotation, status surfacing & preflight gate — 2026-11-18

- [x] Added atomic offline `CredentialVault.rotate_master_key()` and
  `credentials_cli.py rotate-key` (generates a 0600 key without overwriting,
  re-encrypts all records in one SQLite transaction, records
  `master_key_rotated` audit events, and cleans up on failure).
- [x] Surfaced `delivery=<status>` and `delivered_revision` in
  `credentials_cli.py status`, added `audit_events` / `stale_count` /
  `cleanup_required_count` to `Controller.credential_status()`, and blocked
  `preflight` `secrets` check when `revoked_cleanup_required` is present.
### M02.6 / M02.9 controller isolation, token rotation & sync ack — 2026-11-18

- [x] `DockerAdapter` now supports validated `YONDER_CONTROLLER_DOCKER_HOST`
  (`unix:///`, loopback `tcp://`, or `https://` TLS-verified socket proxies)
  and strict `validate_registry_allowlist()` (rejecting wildcards, schemes,
  and userinfo).
- [x] Controller API now supports zero-downtime `YONDER_CONTROLLER_PREVIOUS_TOKEN`
  rotation and optional `YONDER_CONTROLLER_REQUIRE_MTLS` client-cert header
  enforcement.
- [x] Added `CredentialVault.record_sync_ack()` and `credentials_cli.py ack-sync`
  to transition `delivered_unverified` → `synced` with `sync_acknowledged`
  audit logging, invalidated on rotation.
- [x] **M02.7 release lifecycle & compatibility pinning:** `ReleaseDescriptor`
  now supports `deprecated` / `deprecation_reason` and `min_core_version` /
  `max_core_version` semver pinning enforced by `verify_release()`; added
  `rotate_trust_key()` / `revoke_trust_key()` trust-store helpers and semver
  downgrade blocking in `assess_upgrade()`.
- [ ] Full external KMS/Vault provider adapters and institutional multi-host
  deployment service packaging remain pending.

**M01.6 — CI baseline cleanup** should be maintained as a parallel
release-enabling workstream so the final green signal is meaningful.
