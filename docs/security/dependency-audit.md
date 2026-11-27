# Dependency audit and release gate

The Dependency audit workflow audits the exact locked Core runtime Python dependencies, a resolved standalone controller environment, and the Console npm lock (runtime and build/test). It runs on pull requests, main and a weekly schedule. It creates machine-readable findings for review.

Python findings fail the audit; npm high and critical findings fail the audit. Lower-severity npm findings are still present in the JSON and require review before publication. Scanner errors, unavailable advisory services, skipped or unidentifiable packages are incomplete evidence, not a clean bill of health. The separate controller currently has version ranges rather than a reproducible lock: the report records what was resolved for that run, not every possible future build.

## Maintainer response

1. Inspect the exact commit, advisory identifiers, affected package path, severity, exploitability and fixed versions.
2. Update the relevant lock and run the full tests and fresh deployment again. Do not blindly force incompatible upgrades or suppress scanner failures.
3. Record unresolved findings with explicit owner, mitigation, expiration, and documented release decision. The automated publication gate requires a successful audit on the exact commit; no blanket bypass is provided.
4. Review container base image/OS packages, GPU runtimes, third-party models, offline vendor bundles and add-on images separately. These scans do not cover them or prove absence of vulnerabilities.
5. Generate and verify immutable image SBOMs and signatures as separate release evidence.

Do not send secrets or private institutional inventory to advisory services. The workflow only sends public package/version metadata and uses read-only repository permissions.
