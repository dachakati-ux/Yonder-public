# Yonder Workspaces — optional module specification

**Status: proposed add-on, not implemented.** The core Yonder installation must
work unchanged when Workspaces is absent. Do not market current Coder cages or
Academy text sandboxes as isolated virtual machines.

## Boundary and packaging

- Deploy as an optional service and UI extension, **not** part of the core
  gateway or mandatory Compose stack.
- Its own API, durable workspace store/migrations, audit stream, provisioning
  adapters, capacity scheduler and scoped roles.
- Gateway integration only through authenticated, versioned APIs. Workspaces
  may consume approved model endpoints; it must not receive blanket GPU,
  filesystem or administrative privileges.
- Default disabled. Install with a documented profile and explicit initial
  administrator setup. Uninstall must not destroy project data automatically.

## Experience

Students create private projects, choose administrator-approved templates,
open an editor/terminal in an isolated session, and request approved AI agents.
Persist projects separately from short-lived compute sessions. Give teachers
class assignments and explicitly granted review access. Provide an admin
dashboard for quotas, policy, resource usage, session termination and audit.

## Security requirements before execution ships

1. Require real upstream school identity/SSO and verified teacher/student/admin
   roles. Never allow development/mock principals for multi-user workspaces.
2. Enforce tenant + project + class scope authorization **on every endpoint**
   and every storage/compute action, not only in the frontend.
3. Private by default, with policy-driven, logged access escalation for
   authorized investigations. Administrative oversight is not automatic
   unrestricted access to student documents.
4. Immutable audit events for session creation, privileged actions, sharing,
   approvals, termination and policy changes; documented retention/deletion.
5. Isolation suitable for untrusted student code: prefer dedicated VMs or
   microVMs for high-risk workloads. Ordinary privileged Docker containers
   are not an adequate hostile multi-tenant boundary.
6. No host Docker socket, privileged containers, host networking, arbitrary
   mounts, unrestricted internet, host secrets, or unrestricted agents.
7. Per-user and per-class CPU, RAM, disk, GPU, network, process-count and time
   quotas, with hard enforcement and safe cleanup.
8. Restricted images/templates with signature/digest pinning and scanning.
   Separate user storage from disposable session disks.
9. Protect data in transit and at rest; encrypted backups, restoration,
   key management, retention and FERPA/COPPA review by each deploying school.
10. Proxmox adapter uses tightly scoped API tokens, restricted networks and
    project-specific pools; never broad datacenter-admin credentials.

## Provisioning adapters

- **MicroVM/VM backend:** recommended for untrusted student execution.
- **Proxmox:** optional adapter for schools with an existing cluster.
- **Restricted container runner:** advanced opt-in only after documented
  isolation and policy review; never the default school threat model.
- **Additional providers:** versioned adapter contract, no core dependency.

## API design (proposed)

- `GET /workspaces/v1/capabilities`
- `GET|POST /workspaces/v1/projects`
- `GET|POST /workspaces/v1/projects/{id}/sessions`
- `POST /workspaces/v1/sessions/{id}/stop`
- `GET /workspaces/v1/admin/policies`
- `GET /workspaces/v1/admin/audit`

All create operations require a validated project owner, scope and quota;
all compute operations must be async jobs with idempotency and cancellation.

## Rollout

1. Identity and policy integration with durable project metadata (no execution).
2. Storage isolation and read/write project API with authorization tests.
3. One VM-based backend and bounded session lifecycle, then browser editor.
4. Proxmox adapter, teacher classroom controls, quotas, audit and reports.
5. Approved agents, templates and broader provider ecosystem.

Keep each phase feature-flagged. Do not claim school compliance or secure
isolation without an independent threat-model review and penetration testing.
