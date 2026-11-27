# Yonder Identity & Security — modular integration design

**Status: integration specification.** OIDC bridge and security-context implementation primitives exist in the Gateway source, but the optional catalog add-on is not installable. No end-to-end approved IdP integration, production login flow or school-compliance certification is established by this document.

## Product rule

Yonder Core **always** enforces authentication and authorization whenever it serves multiple people or is reachable beyond a strictly local development environment. The **Identity & Security** add-on provides optional integrations, management UX, and advanced institutional controls. It is never a toggle to bypass the core security boundary.

Yonder's existing `RequestPrincipal` and capability checks remain the single contract for API authorization. The existing development mock principal is **not** a user-authentication method.

### Deployment experience

- **Personal local (explicit development):** loopback-only, prominent "unsecured local development" warning, no remote access, no automatic upgrade to shared mode.
- **Shared / school / business:** launch wizard requires an identity provider and verifies it before enabling access. Fail closed if authentication is absent, issuer keys cannot be validated, or the gateway is reachable in a prohibited configuration.
- **Institution-managed:** integrate with an existing enterprise IdP. No need to migrate institutional passwords, duplicate accounts or deploy Yonder's own directory.
- **Disconnected schools:** separate, reviewed identity profile with offline validation and lifecycle management; no anonymous fallback.

## Supported integration contract

**Initial recommended integration:** OpenID Connect (OIDC) with authorization-code + PKCE for browser login; standard discovery metadata and JWKS verification, using a vetted OIDC client library. Integrations should include:

1. **Authentik** — provider template + setup wizard, issuer/discovery validation.
2. **Keycloak** — OIDC template.
3. **Microsoft Entra ID** — OIDC template (including institutional tenants).
4. **Google Workspace** — OIDC template, with tenant/domain policy checks that do not rely on email domain alone.
5. **Generic OIDC** — advanced provider configuration.

**Later adapters:** SAML 2.0 via a reviewed identity bridge; LDAP/Active Directory through a gateway/IdP rather than raw passwords flowing into Yonder. Future OAuth-only integrations must not be mistaken for verified OIDC identity.

No provider is automatically trusted just because it exposes a URL or sends a username header.

## Core security architecture

```text
Browser -> hardened TLS ingress -> OIDC authentication / session bridge
                                      |
                                      v
                           signed / verified identity
                                      |
                          authorization policy engine
                                      |
                                      v
                             Yonder Gateway
                                      |
                         services and approved models
```

- Use Authorization Code + PKCE, exact registered redirect URIs, state and nonce.
- Validate issuer, audience, signature, nonce, expiry, token-use, key rotation and relevant authorization claims.
- Session cookie: HttpOnly, Secure, SameSite policy, short session life, CSRF protection for state-changing methods and secure logout.
- Browser tokens must not be written to localStorage or exposed in logs.
- Direct access to Gateway internal endpoints must not bypass identity verification.
- Existing trusted-header mode **must only be used behind an ingress that strips all incoming identity headers and injects newly verified ones**. Host port binding alone is not adequate security.
- Prefer signed identity assertions or internal authenticated transport from identity bridge to gateway; require server-side verification of peer/issuer and time-bound assertions.
- Service-to-service identity (workers, MCP, provisioning and Workspaces) must be **separate** from user identity. No shared admin credential used for all workers.
- Secrets live in a secret store or protected mounted secret files, never public `.env.example`, logs, browser bundles or checked-in manifests.

## Authorization and policy

Use server-enforced least privilege and deny-by-default RBAC plus attributes and resource scopes:

| Role | Example permitted responsibilities |
| --- | --- |
| Student | Approved chat, lessons and private projects; no model installation or host controls |
| Teacher | Class-scoped lessons, aggregate learning progress, explicitly granted project review |
| IT administrator | Integrations, node inventory, approved model installs, quotas and operational policy |
| Security administrator | Policy audit, identity-provider settings and controlled incident response |
| Workspace runner service | Short-lived, project-scoped compute authorization with no human admin powers |

Never authorize based on display names, raw email domains, or client-supplied roles. Map verified IdP groups to Yonder roles using **explicit administrator-approved mappings** with stable immutable identity identifiers and tenant scoping. Provide a test-user/mapping preview that cannot accidentally grant privileges.

Student privacy is default: admins manage resources and security events without automatically accessing student documents. Any exceptional content access must be policy-authorized, time-bound, recorded, and visible to appropriate oversight staff.

## Modular package boundaries

- `yonder-core` (mandatory): verifies trusted identity claims and capability authorization, rate limits, audit events, security configuration validation.
- `yonder-security` (optional add-on): OIDC integration adapters, guided provider onboarding, login/session bridge, group mapping UI, administrative policies and security dashboard.
- `yonder-workspaces` (optional add-on): consumes principal/scoped policy decisions; **never** bypasses identity checks and never inherits gateway root credentials.

The add-on should have a versioned API and independent migrations, and should be installable with a Compose profile or separate deployment. **Disabling/uninstalling it cannot silently expose routes**: if no alternate verified identity provider is configured for a shared deployment, fail closed.

## Setup wizard (suggested flow)

1. **Choose deployment mode** (personal local vs multi-user institutional).
2. **Select an existing IdP** (Authentik, Entra, Google Workspace, Keycloak or generic OIDC).
3. **Enter issuer URL and client ID**, then retrieve and validate OIDC discovery and JWKS from an explicitly configured trusted endpoint; outbound URL allowlist and SSRF defenses required.
4. **Store client secret securely**, when a confidential client is required, and register redirect/logout URLs.
5. **Test login and account mapping** with an explicitly designated bootstrap admin; bootstrap is local-console restricted and short lived, with no universal default password.
6. **Map groups to roles**, preview permissions, deny unmapped users by default.
7. **Verify network boundary and service identities**, session settings, audit output and recovery/lockout procedure.
8. **Enable institutional access** only after every security gate succeeds.

Wizard must show exact failures instead of claiming an installation is secure. Do not automatically alter host firewall rules.

## Requirements for school and small-business safety

- Administrative separation, group-specific policies, model/tool allowlists, rate limits and concurrency quotas.
- Audit: authentication failures, authorization decisions for privileged actions, policy changes, access grants, identity-provider changes, session revocation and provisioning actions. Do not log tokens, secrets or student prompt contents by default.
- Student records: configurable data retention, access controls, deletion/export paths, backups and an incident response process. School must evaluate FERPA, COPPA where applicable, district policy and vendor/data-processing obligations.
- Periodic revalidation of identities/groups; disable or suspend users promptly on deprovisioning.
- MFA and conditional access should be enforced at the institutional IdP; Yonder must display when assurance context is insufficient for elevated actions if configured.
- Secret rotation, TLS, safe reverse proxy examples, denial of identity-header spoofing, rate limiting, service-token separation, dependency scanning, and release security advisories.
- The local development mode must be explicitly unavailable to school/multiuser deployment profiles.

## Acceptance tests before shipping

- Unauthenticated browser cannot access any student, teacher, admin or API data.
- Spoofed `X-Yonder-Trusted-*` headers are rejected from untrusted clients.
- Wrong issuer, audience, expired token, invalid nonce, bad signature, missing tenant or rotated keys fail closed.
- One student cannot read another's chats, projects, assignments or agent artifacts through guessed IDs or API calls.
- Teacher cannot cross classroom scope without a recorded grant.
- Model management, downloads, worker enrollment and VM provisioning demand appropriate permissions.
- Group changes, revoked sessions and deleted accounts take effect within defined policy windows.
- Disabled add-on causes closed/protected deployment, not an anonymous user.
- Restore/rollback, disaster recovery, backup of IdP settings, admin lockout/recovery and update procedures tested.
- Independent security review and penetration testing required before presenting as school-ready or production-certified.

## Phases

1. Threat model + secure trusted-ingress hardening and production deployment gates.
2. OIDC bridge with generic provider and Authentik template, secure session and admin mapping.
3. School roles/scopes, auditing and policy UI.
4. Keycloak, Entra and Workspace integration templates + end-to-end tests.
5. Workspaces and model-manager policy integration and institutional security review.

### Non-goals

No Yonder-managed student password database in the initial release. No automatic claim of FERPA/COPPA compliance. No Proxmox, Kubernetes or mandatory rack dependency for the base application. No insecure "skip login" option for shared deployments.
