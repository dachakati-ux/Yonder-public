# Yonder add-ons

Add-ons live under `addons/<addon-id>/` and are declared in
`addons/catalog.json`. See [Add-on SDK & versioning](../docs/addons-framework.md)
for the v1 manifest contract, compatibility rules, security restrictions,
testing requirements and future service adapter boundaries.

**Current behavior:** Yonder's UI checks the official GitHub catalog for
discovery and offers one-click activation **only** for implemented modules
already bundled and reviewed in the running Yonder image. Nothing from GitHub
is downloaded and executed when an operator clicks Install.

Available now: Security Insights, an opt-in read-only configuration check.

Reserved for future integration: Identity/SSO, Workspaces, Model Library and
Convyx. Their manifests are disabled until each implementation can pass
security, compatibility and functional review.

Adding a new independently deployed add-on currently requires releasing an
updated reviewed Yonder package, not simply pushing a folder to GitHub.
Remote signed packages, migrations and lifecycle orchestration are separate
planned enhancements.
