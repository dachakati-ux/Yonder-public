# docs/security

Security- and compliance-facing documentation for Yonder: change audits, access
records, and tickets/requests submitted to security or network owners.

## Lab posture (explicit)

Several items in this tree and the related lab config are **lab only — pending
security review / revision** before any wider exposure:

- Development security mode with a non-production mock `RequestPrincipal`
- Application listen on all interfaces behind host firewall / approved ACLs
- Interim approved SSH local-forward access until firewall owners approve VPN
  ACLs (YON-7)
- Offline install helpers that touch **firewalld** / SELinux on hosts where
  those are the local mechanisms — **not** YAKL changes; do not use them to
  circumvent managed host firewalls

Production must use upstream trusted ingress and refuse development security
mode. See `security-boundary.md` and `../academy.md` (corporate auth cutover).

## Conventions

- **Redaction key** used across these docs:
  - `[PRIVATE]` — secret value intentionally omitted (lives on host / secret
    store, never in the repo).
  - `[INTERNAL]` — internal-only detail (e.g. lab IPs, CIDRs); fill in before
    sharing internally, keep out of external channels.
- **Never commit secrets** here (keys, passwords, tokens, BMC/IPMI creds).
- Keep these docs factual and evidence-based — no fabricated metrics or state.
- Prefer “interim approved access method” / “pending security review” over
  informal “workaround” language.

## Contents

- `security-change-audit.md` — review-ready record of the lab bring-up: every
  system change, the access model, Yonder's security posture, and secret
  handling.
- `jira-firewall-lab-console-access.md` — submission-ready firewall change
  request for team IP access to the lab console/gateway (VPN-scoped).
