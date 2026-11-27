# Contributing to Yonder

Yonder aims to make local AI infrastructure approachable while preserving a modular, replaceable architecture. Contributions should follow the [master roadmap](docs/yonder-master-roadmap.md) and [development rules](AGENTS.md).

## Development and review

1. Open an issue describing the use case or reproducible defect. Link the relevant milestone when known.
2. Use a focused branch. Keep optional services independently deployable, use typed contracts, and route inference through Gateway.
3. Run `make bootstrap`, `make validate-config`, and `make verify` from a development environment.
4. Add regression tests for changed behavior, update documentation and `WORKLOG.md`, and describe limitations and test evidence in the pull request.
5. For UI changes, exercise the browser workflow and verify on the authorized lab deployment before claiming lab readiness.
6. Never invent hardware readings, model responses, benchmarks, deployment readiness or compliance results. Label mock and synthetic fixtures explicitly.

Do not include private infrastructure identifiers, employee or student data, credentials, production logs or model artifacts in a contribution. For security concerns follow [SECURITY.md](SECURITY.md), not a public issue.
