"""Bootstrap in-memory routes, providers, and model definitions for local development."""

from __future__ import annotations

from datetime import UTC, datetime

from yonder_configuration.models import YonderSettings

from app.models.manifest import load_manifests_from_dir, validate_manifest
from app.paths import resolve_repo_root
from app.providers import (
    DiffusersGenerationProvider,
    LlamaCppEmbeddingProvider,
    LlamaCppProvider,
    LlamaCppRerankProvider,
    MockEmbeddingProvider,
    MockGenerationProvider,
    MockStreamingProvider,
    ProviderRegistry,
)
from app.routing import (
    DeploymentRecord,
    DeploymentState,
    ModelAliasRoute,
    RouteRegistry,
    RouteState,
)
from app.routing.models import ModelDefinitionRecord


def build_provider_registry(settings: YonderSettings) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register("mock", MockStreamingProvider(chunk_delay_seconds=0.0))
    if settings.llama_cpp.enabled:
        registry.register("llama-cpp", LlamaCppProvider(settings.llama_cpp))
        registry.register_embedding(
            "llama-cpp-embeddings",
            LlamaCppEmbeddingProvider(
                request_timeout_seconds=settings.llama_cpp.request_timeout_seconds
            ),
        )
        registry.register_rerank(
            "llama-cpp-rerank",
            LlamaCppRerankProvider(
                request_timeout_seconds=settings.llama_cpp.request_timeout_seconds
            ),
        )
    # Mac-local / fallback mock lane. Lab can replace the alias with a real
    # llama-cpp-embeddings deployment after canary; API never silently falls back.
    registry.register_embedding("mock-embeddings", MockEmbeddingProvider())
    registry.register_generation("mock-generation", MockGenerationProvider())
    if settings.diffusers.enabled:
        registry.register_generation(
            "diffusers", DiffusersGenerationProvider(settings.diffusers)
        )
    return registry


def _external_attach_definition(
    settings: YonderSettings, alias: str, now: datetime
) -> ModelDefinitionRecord:
    """Synthesize a definition for an operator-attached external runtime.

    The runtime (llama.cpp server, LM Studio, …) is managed outside Yonder;
    the gateway verifies the served model against /v1/models at bind time,
    so nothing here asserts state Yonder cannot observe.
    """
    served = (settings.llama_cpp.served_model or alias).strip()
    manifest = {
        "id": f"external-{alias}",
        "display_name": f"External runtime ({alias})",
        "description": (
            "Operator-attached OpenAI-compatible endpoint at "
            f"{settings.llama_cpp.base_url} (llama_cpp.attach_aliases)."
        ),
        "source": {"type": "external", "path": None},
        "runtime": {
            "provider": "llama-cpp",
            "model_identifier": served,
            "context_length": int(settings.llama_cpp.attach_context_length),
        },
        "requirements": {"minimum_gpu_memory_gb": 1},
        "capabilities": ["chat", "streaming", "tools"],
    }
    return ModelDefinitionRecord(
        id=f"external-{alias}",
        display_name=f"External runtime ({alias})",
        description=str(manifest["description"]),
        manifest=manifest,
        capabilities=["chat", "streaming", "tools"],
        validation_status="valid",
        validation_issues=[],
        created_at=now,
        updated_at=now,
    )


def build_model_definitions(settings: YonderSettings) -> dict[str, ModelDefinitionRecord]:
    manifests = load_manifests_from_dir(resolve_repo_root() / "config" / "models")
    definitions: dict[str, ModelDefinitionRecord] = {}
    now = datetime.now(UTC)
    if settings.llama_cpp.enabled:
        for alias in settings.llama_cpp.attach_aliases:
            record = _external_attach_definition(settings, alias, now)
            definitions[record.id] = record
    for manifest in manifests:
        result = validate_manifest(
            manifest,
            known_providers={
                "mock",
                "vllm",
                "llama-cpp",
                "llama-cpp-embeddings",
                "llama-cpp-rerank",
                "mock-embeddings",
                "mock-generation",
                "diffusers",
            },
        )
        definitions[manifest.id] = ModelDefinitionRecord(
            id=manifest.id,
            display_name=manifest.display_name,
            description=manifest.description,
            manifest=manifest.model_dump(mode="json"),
            capabilities=list(manifest.capabilities),
            validation_status="valid" if result.valid else "invalid",
            validation_issues=[issue.model_dump(mode="json") for issue in result.issues],
            created_at=now,
            updated_at=now,
        )
    return definitions


def build_route_registry(
    settings: YonderSettings,
    definitions: dict[str, ModelDefinitionRecord] | None = None,
) -> RouteRegistry:
    routes = RouteRegistry()
    definitions = definitions or {}

    real_model_id = "yonder-qwen36-35b-a3b-q4"
    real_deployment_id = f"{real_model_id}-primary"
    use_real_chat = (
        settings.llama_cpp.enabled
        and real_model_id in (definitions or {})
    )

    if use_real_chat:
        manifest = definitions[real_model_id].manifest
        routes.upsert_deployment(
            DeploymentRecord(
                id=real_deployment_id,
                model_definition_id=real_model_id,
                worker_id=settings.llama_cpp.worker_id,
                provider="llama-cpp",
                status=DeploymentState.REGISTERED,
                gpu_assignments=[0],
                runtime_configuration=dict(manifest.get("runtime", {})),
                logs=[
                    f"{datetime.now(UTC).isoformat().replace('+00:00', 'Z')} "
                    "Registered llama.cpp deployment as primary chat lane"
                ],
            )
        )
        routes.upsert_alias(
            ModelAliasRoute(
                alias="yonder-chat",
                deployment_id=real_deployment_id,
                state=RouteState.UNAVAILABLE,
                priority=100,
                experimental=False,
            )
        )
    else:
        # Local / default: mock deployment starts ready so Chat works without a GPU.
        model_id = "yonder-chat-small"
        provider = "mock"
        if model_id in definitions:
            provider = str(
                definitions[model_id].manifest.get("runtime", {}).get("provider", "mock")
            )

        routes.upsert_deployment(
            DeploymentRecord(
                id="yonder-chat-mock-primary",
                model_definition_id=model_id,
                worker_id=settings.worker_id,
                provider=provider,
                status=DeploymentState.READY,
                gpu_assignments=[],
                runtime_configuration={"lane": "mock", "note": "Local foundation without GPU"},
                load_progress_percent=100,
                loaded_at=datetime.now(UTC),
                last_successful_load=datetime.now(UTC),
                logs=[
                    f"{datetime.now(UTC).isoformat().replace('+00:00', 'Z')} "
                    "Bootstrapped mock deployment as ready for local development"
                ],
            )
        )
        routes.upsert_alias(
            ModelAliasRoute(
                alias="yonder-chat",
                deployment_id="yonder-chat-mock-primary",
                state=RouteState.ENABLED,
                priority=100,
                experimental=True,
            )
        )
    # Operator-attached external OpenAI-compatible runtimes (llama.cpp server,
    # LM Studio, …). Registered READY here; the lifespan rehydrate step calls
    # provider.load(), which verifies the served model against /v1/models and
    # marks the deployment failed honestly when the runtime is unreachable.
    if settings.llama_cpp.enabled:
        for alias in settings.llama_cpp.attach_aliases:
            definition_id = f"external-{alias}"
            deployment_id = f"{definition_id}-attached"
            served = (settings.llama_cpp.served_model or alias).strip()
            routes.upsert_deployment(
                DeploymentRecord(
                    id=deployment_id,
                    model_definition_id=definition_id,
                    worker_id=settings.llama_cpp.worker_id,
                    provider="llama-cpp",
                    status=DeploymentState.READY,
                    gpu_assignments=[],
                    runtime_configuration={
                        "lane": "external_attach",
                        "model_identifier": served,
                        "base_url": settings.llama_cpp.base_url,
                        "context_length": int(
                            settings.llama_cpp.attach_context_length
                        ),
                    },
                    load_progress_percent=100,
                    loaded_at=datetime.now(UTC),
                    last_successful_load=datetime.now(UTC),
                    logs=[
                        f"{datetime.now(UTC).isoformat().replace('+00:00', 'Z')} "
                        f"Attached external runtime for alias {alias} at "
                        f"{settings.llama_cpp.base_url}"
                    ],
                )
            )
            routes.upsert_alias(
                ModelAliasRoute(
                    alias=alias,
                    deployment_id=deployment_id,
                    state=RouteState.ENABLED,
                    priority=100,
                    experimental=False,
                )
            )
    # Mock embedding alias — deterministic vectors, labeled mock.
    routes.upsert_deployment(
        DeploymentRecord(
            id="yonder-embed-mock-primary",
            model_definition_id="yonder-embed-small",
            worker_id=settings.worker_id,
            provider="mock-embeddings",
            status=DeploymentState.READY,
            gpu_assignments=[],
            runtime_configuration={"lane": "mock", "modality": "embeddings"},
            load_progress_percent=100,
            loaded_at=datetime.now(UTC),
            last_successful_load=datetime.now(UTC),
            logs=[
                f"{datetime.now(UTC).isoformat().replace('+00:00', 'Z')} "
                "Bootstrapped mock embedding deployment"
            ],
        )
    )
    routes.upsert_alias(
        ModelAliasRoute(
            alias="yonder-embed",
            deployment_id="yonder-embed-mock-primary",
            state=RouteState.ENABLED,
            priority=100,
            experimental=True,
        )
    )
    return routes
