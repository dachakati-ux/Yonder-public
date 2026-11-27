# Security Boundary

> Yonder does not authenticate corporate users. Authentication and authorization occur in an approved upstream corporate security layer. Yonder accepts only trusted, normalized request context through a replaceable adapter and must not be directly exposed to ordinary clients in production.

## Expected production flow

```text
Employee or internal service
            │
            ▼
Corporate authentication and authorization
            │
            ▼
upstream IAM and required security controls
            │
            ▼
Approved ingress, proxy, or service layer
            │
            ▼
         Yonder
```

The exact production mechanism is not yet known. Yonder must not guess upstream IAM
policies, headers, or token formats.

## What Yonder must not implement

* Login page
* Password handling
* User registration
* Local production user database
* Corporate token issuance
* Permanent API-key bypass
* Hidden administrator credential
* Fallback production administrator
* Hardcoded employee usernames or groups
* Guessed upstream IAM policies
* Route-specific ad-hoc header parsing
* Direct internet exposure

## Normalized context

All application logic depends on `RequestPrincipal` from `yonder_contracts`.

Providers:

* `DevelopmentRequestContextProvider` — local mock principal only
* `TrustedIngressRequestContextProvider` — fails closed without trusted headers

Every API request (except health/ready/version/metrics) passes through central
request-context middleware. Routes must not read raw identity headers.

## Development mode

Configured explicitly via `security.mode: development`.

**Lab / local only — pending corporate auth review/revision.** Supplies a
non-production mock `RequestPrincipal` for bring-up. Production must use
upstream trusted ingress only.

Requirements:

* Persistent `DEVELOPMENT SECURITY MODE` warning in UI and startup logs
* Bind to localhost unless an isolated lab bind is explicitly allowed
* Never activate automatically
* Never default in production configuration
* Never include a hidden fallback password
* Refused when `environment=production`

## Production readiness

Yonder is not production-ready until the approved upstream security integration is
supplied and tested. If trusted context cannot be established:

* Reject the request
* Do not assume an anonymous user
* Do not silently use a development principal
* Do not bypass the security boundary

## Network trust

The production gateway must not be directly reachable by ordinary clients.
Only the approved upstream ingress or service layer should reach it.

Production trusted-header mode requires a separate managed `YONDER_SECURITY__TRUSTED_INGRESS_SHARED_SECRET` (at least 32 characters). Generate independently from the worker token, configure it in the approved reverse proxy, and have the proxy strip all client-supplied identity, capability and ingress-authenticator headers before injecting its own. Prefer an additional private-network peer allowlist and TLS. An IP allowlist alone does not authorize production startup. Never put this secret into browser configuration, logs or Git. Production also requires the worker secret to be at least 32 characters and refuses untrusted worker registration. Existing insecure production configurations must be corrected before upgrading.

## Worker service authentication

Gateway <-> worker calls are authenticated with a shared service token
(`security.worker_shared_secret`, sent in `security.worker_token_header`).
This is infrastructure trust between Yonder services and is **not** a
substitute for the upstream corporate security layer:

* Workers reject non-probe requests without a valid token when the secret is
  configured (constant-time comparison).
* The gateway maps a valid worker token to a service principal scoped to
  `workers.report` only — it grants no operator, admin, or chat capability.
* Production configuration fails closed: startup is refused when
  `environment=production` and `worker_shared_secret` is unset.

See [worker-registration.md](worker-registration.md) for wiring details.
