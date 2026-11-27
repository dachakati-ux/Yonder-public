# Security policy

Yonder is under active development. No stable production release or security-support SLA has been established. The development Compose stack is intended for a trusted local machine, not an internet-facing, school, or multi-tenant installation.

## Reporting a suspected vulnerability

- Use [GitHub's private vulnerability reporting page](https://github.com/dachakati-ux/Yonder-public/security/advisories/new) if private reporting is available.
- If it is unavailable, ask for a private reporting channel without describing the vulnerability in a public issue. Do not post exploit details, tokens, private network addresses, student records, or real conversation content.
- Include the affected commit or image digest, component, prerequisites, a minimal synthetic reproduction, impact, and any mitigation you have tested.
- Coordinate disclosure with the maintainer. Do not test against systems you do not own or have permission to assess.

## Deployment boundary

Production requires independently approved identity and ingress controls. Yonder consumes normalized trusted context; it is not an identity provider. Do not expose the development principal to an untrusted network. Review the [security boundary](docs/security-boundary.md), [threat model](docs/security/threat-model.md), and [release gates](docs/release-readiness.md) before evaluating institutional use.

## Response and updates

The maintainer evaluates reproducibility, affected versions, scope, and mitigation. Fixes should be verified with regression tests and a fresh dependency audit; affected operators should receive clear upgrade instructions. A stable supported-version policy will be published with the first stable release. Until then, evaluate the exact source revision and do not assume ongoing support for old candidate builds.
