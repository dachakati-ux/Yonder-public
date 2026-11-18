# Convyx integration (planned)

This add-on is an optional adapter to a separately deployed Convyx instance. It will use a versioned authenticated API for agent coordination, task handoffs, scoped context indexing and auditable activity. No Convyx code is copied into Yonder Core.

Future installation will require a reviewed release artifact, service identity and scoped permissions. It must never grant agents access to unrelated students' workspaces or run arbitrary shell commands inside the Gateway.

This manifest is intentionally disabled until the integration can be verified end to end.
