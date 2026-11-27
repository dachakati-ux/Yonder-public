# Feature availability and evidence

This is a conservative description of source capabilities, not a release or deployment certification. A default local install uses development security and can expose explicitly mock inference routes. A catalog entry or test file is not proof of a production-ready service.

| Surface | Current state | Evidence and remaining gate |
| --- | --- | --- |
| Chat and gateway | Core source implementation; development routes may be mock | [Architecture](architecture.md); verify real model completion and supported runtime |
| Local and external worker endpoints | Agent, registration, routing and lifecycle implementation | [Worker registration](worker-registration.md); validate actual hardware/runtime matrix |
| Model discovery and downloads | Source implementation in Models; checksum-verified GGUF workflow | [Model Library](huggingface-model-library.md); downloaded weights are not automatically loaded; optional catalog add-on remains unavailable |
| Image, video, speech | Gateway/worker contracts and runtime adapters; capabilities depend on configuration and actual model readiness | [Architecture](architecture.md); default local speech is disabled, real runtime canaries required |
| IDE MCP | Gateway-mediated implementation | [MCP](mcp.md); validate client interoperability and tool policy |
| Academy | Curriculum, missions, progress and teaching surfaces in source | [Academy](academy.md); multi-tenant institutional acceptance incomplete |
| Coder | Local coding pipeline and scoped workspace tools; restricted by default | [Coder](coder.md); write/exec unlock and remote integration have explicit proof gates |
| Security Insights | Bundled, available optional catalog entry | [Manifest](../addons/security-insights/addon.json); read-only guidance is not identity or compliance |
| Identity & SSO | Gateway identity primitives; catalog add-on not installable | [Identity module](security-identity-module.md); live IdP and tenant review required |
| Workspaces | Separate experimental service and tests; catalog add-on not installable | [Workspaces module](workspaces-module.md); isolated VM/microVM compute not established |
| Convyx | Gateway adapter primitives; catalog add-on not installable | [Manifest](../addons/convyx/addon.json); authenticated external integration acceptance required |
| Add-on controller | Separate experimental controller with tests | [Controller](addon-controller.md); hardened controller deployment and real-host lifecycle/recovery remain open |
| Distribution | Source checkout; versioned image candidate workflow | [Image distribution](release-images.md); published immutable releases and fresh-host verification outstanding |

## Audience boundaries

- **Individuals:** trusted local development only; real models require a configured runtime.
- **Developers:** evaluate the documented API, model and tool capabilities using non-sensitive data.
- **Schools and businesses:** evaluate on isolated test infrastructure; do not onboard students or confidential workloads until identity, privacy, resource isolation and recovery gates are independently verified.

The [master roadmap](yonder-master-roadmap.md) and [release readiness gates](release-readiness.md) track the remaining work. Keep catalog statuses conservative; do not advertise a planned add-on as installable because related source code exists.
