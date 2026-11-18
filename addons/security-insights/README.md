# Security Insights

A bundled, read-only administrative security-posture view. The Add-ons page can install or remove this feature without running commands or downloading executable code.

When installed, the Gateway's `/api/admin/addons/security-insights/report` endpoint checks development mode, shared worker-secret configuration, and trusted-ingress assumptions. The feature **does not** authenticate users, change firewall settings, or certify an organization as compliant.

Requires `operations.view` to read and `operations.manage` to install. State persists in the Gateway data volume. **Never treat this as a substitute for Authentik or a verified identity provider.**
