# Yonder Security & Privacy Threat Model (M03.6)

> **Important:** This document defines Yonder's threat model, trust boundaries, data retention, and deletion/export controls. It is **not** a legal or regulatory certification (such as FERPA or COPPA); deploying institutions must perform their own compliance review.

---

## 1. Trust Boundaries & Assets

| Asset | Sensitivity | Owner / Boundary |
| :--- | :--- | :--- |
| User identity & session cookies | High | Upstream IdP / OIDC (`yonder_session`, HMAC-SHA256, `HttpOnly`, `SameSite=Lax`, `Secure`) |
| Trusted-ingress headers | High | Reverse proxy / ingress (`trusted_ingress_shared_secret` + `trusted_proxy_cidrs`) |
| Worker service tokens | High | `YONDER_SECURITY__WORKER_SHARED_SECRET` (never exposed to browsers) |
| Add-on credentials & master key | Critical | `apps/addon-controller` AES-256-GCM vault (`0600`) + separate offline `credentials.key` (`0600`) |
| Student prompts, submissions & projects | High (PII / Educational) | Tenant + class + owner-scoped RBAC (`academy_org.py`, `apps/workspaces`) |

---

## 2. Mitigated Threats & Controls

1. **Direct-Gateway header spoofing (`M03.1`)**
   * *Threat:* Caller bypasses reverse proxy and sends `X-Yonder-Trusted-Subject: admin`.
   * *Control:* `TrustedIngressRequestContextProvider` verifies `trusted_proxy_cidrs` and `trusted_ingress_shared_secret` in constant time (`secrets.compare_digest`), failing closed with `401`.
2. **OIDC token / CSRF / replay attacks (`M03.2`, `M03.5`)**
   * *Threat:* Token forgery, `alg=none`, nonce replay, CSRF, or replaying logged-out cookies.
   * *Control:* PKCE S256 + `state` + `nonce`, pinned asymmetric JWK algorithms (`RS256`/`ES256`/`EdDSA`), `X-Yonder-CSRF-Token` on mutating requests, `SessionRevocationStore` JTI revocation on logout/admin revoke, and `require_mfa_for_admin` assurance gating.
3. **Cross-student / cross-classroom / cross-tenant data access (`M03.4`, `M05.2`, `M06.1–M06.2`)**
   * *Threat:* Student B reads Student A's workspace or submission; Teacher A reads Teacher B's class; Admin reads student documents without authorization.
   * *Control:* Server-enforced default-deny `authorize_resource_access()` and `can_access_project()` on every endpoint; admins inspect audit/policies without automatic read access to student project contents.
4. **SSRF & untrusted remote downloads (`M02`, `M04`, `M07`)**
   * *Threat:* Redirect or URL-userinfo smuggling to internal metadata services (`169.254.169.254`, `127.0.0.1`).
   * *Control:* `follow_redirects=False`, strict HTTPS + public-host checks (`validate_oidc_url`, `validate_convyx_url`, `trusted_url`), and pinned SHA-256 digest verification for GGUFs and OCI images.
5. **Untrusted code execution (`M05.3`)**
   * *Threat:* Container escape from multi-tenant student code execution.
   * *Control:* Workspaces execution defaults to `execution_enabled: false` and requires a VM/microVM provisioner (`ProxmoxAdapter` with scoped non-root `PVEAPIToken`, isolated VLAN bridge, and pinned `@sha256` templates).

---

## 3. Data Retention, Export & Deletion

* **Retention:** Configured per organization via `retention_days` (`k12_school`: 180d, `university`: 365d, `small_business`: 90d). Admins trigger retention purges via `POST /v1/academy/org/privacy/purge-expired`.
* **Export:** Students (self) or tenant admins can export enrollments, submissions, and privacy notices via `GET /v1/academy/org/privacy/export/{student_subject}` (audited as `student_data_exported`).
* **Deletion:** `DELETE /v1/academy/org/privacy/{student_subject}` deletes submissions, enrollments, and daily usage counters, leaving an immutable `student_data_deleted` audit record.
* **Workspaces:** Uninstall/disable never silently deletes project metadata; owners export/delete explicitly via `/workspaces/v1/projects/{id}/export` and `DELETE /workspaces/v1/projects/{id}`.
