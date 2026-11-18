# Identity & Security (planned)

Proposed external authentication bridge for OIDC: Authentik, Keycloak, Microsoft Entra ID, Google Workspace, and generic OIDC. The mandatory Core authorization boundary remains active regardless of whether this addon is installed.

See draft design PR #3. This catalog item intentionally cannot be installed until identity, session, CSRF, issuer/nonce/audience validation, trusted-header spoofing defenses, tenant group mapping, and school acceptance tests are implemented.
