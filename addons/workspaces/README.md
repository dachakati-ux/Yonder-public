# Private Workspaces (planned)

Separate opt-in compute module with durable private projects, classroom-scoped permissions, audit and quotas, plus optional microVM/Proxmox backends. See draft design PR #2.

This catalog entry does **not** provide isolated execution. Student code must not run with the Gateway's filesystem privileges or Docker socket; activation is disabled pending secure implementation and testing.
