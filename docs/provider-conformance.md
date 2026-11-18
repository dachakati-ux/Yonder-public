# Provider Conformance

Every `InferenceProvider` adapter must satisfy the shared normalized contract before
it can be registered with Yonder Gateway. This allows Phase 3 runtime selection to
remain independent of API, routing, and console behavior.

The reusable checks live in `apps/gateway/tests/provider_conformance.py` and cover:

* normalized health and model discovery
* load and unload lifecycle calls
* request and deployment identity preservation across chat chunks
* an explicit terminal `finish_reason`
* cooperative cancellation

Provider-specific tests must also cover timeout, malformed-response, runtime-error,
and usage-reporting behavior. Usage values must come from authoritative provider
tokenizer output. Providers without that information leave OpenAI response `usage`
as null rather than estimating it.

The mock adapter is the first conforming implementation. It is an explicit test and
development provider and does not claim GPU or production runtime readiness.
