"""Yonder Gateway application entrypoint."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from yonder_configuration import load_settings
from yonder_configuration.models import SecurityMode
from yonder_configuration.worker_auth import configure_worker_auth
from yonder_contracts.errors import ErrorCode
from yonder_contracts.tools import ToolDefinition
from yonder_telemetry import MetricsRegistry, configure_logging, log_event

from app.api import (
    academy_tier2,
    admin,
    chat,
    coder,
    generation,
    help,
    lab,
    mcp,
    ops,
    platform,
    speech,
    v1,
    workers,
)
from app.config.bootstrap import (
    build_model_definitions,
    build_provider_registry,
    build_route_registry,
)
from app.context import RequestContextMiddleware
from app.context.providers import build_request_context_provider
from app.errors import ERROR_STATUS, error_payload
from app.paths import resolve_repo_root
from app.persistence import build_persistence
from app.routing.gate import DeploymentAdmissionGate
from app.services.artifacts import LocalArtifactStore
from app.services.chat import ChatService
from app.services.coder_workspace import CoderWorkspaceService
from app.services.generation_jobs import GenerationJobService
from app.services.lifecycle import LifecycleService
from app.services.mcp_client import McpClientManager
from app.services.rag import LexicalRetriever, RagStore, seed_lab_corpus
from app.services.speech import SpeechService
from app.services.structured import build_default_schema_registry
from app.services.tools import build_default_tool_executor


def _seed_static_workers(app: FastAPI) -> None:
    settings = app.state.settings
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    for worker in settings.workers:
        if not worker.enabled:
            continue
        if worker.id in app.state.workers:
            continue
        app.state.workers[worker.id] = {
            "id": worker.id,
            "display_name": worker.display_name or worker.id,
            "endpoint": worker.endpoint,
            "status": "disconnected",
            "ready": False,
            "maintenance_state": False,
            "agent_version": "static-config",
            "hardware_summary": None,
            "runtime_summary": None,
            "last_heartbeat": None,
            "last_error": None,
            "registered_at": now,
            "updated_at": now,
            "registration_mode": "static_config",
            "trust_state": "trusted",
            "trusted": True,
            "connectivity_reason": "awaiting_heartbeat",
        }


def create_app() -> FastAPI:
    settings = load_settings()
    configure_worker_auth(settings.security)
    logger = configure_logging(
        service="yonder-gateway",
        level=settings.logging.level,
        json_logs=settings.logging.json_logs,
    )

    if settings.security.mode == SecurityMode.DEVELOPMENT:
        warning = (
            "DEVELOPMENT SECURITY MODE is active. "
            "A mock principal is injected. Never expose this process beyond "
            "localhost or an explicitly configured isolated lab network."
        )
        log_event(logger, "security.development_mode_warning", level=logging.WARNING)
        print(f"\n*** {warning} ***\n", file=sys.stderr)

    persistence = build_persistence(settings)
    state_store = persistence.state_store

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.accepting_requests = False
        log_event(logger, "gateway.lifespan.startup")
        # Persisted READY rows must be re-attached to in-memory providers before
        # traffic is accepted; otherwise chat fails with "not loaded in provider".
        try:
            outcomes = await app.state.lifecycle.rehydrate_ready_deployments()
            log_event(
                logger,
                "gateway.lifespan.rehydrate",
                outcomes=outcomes,
            )
        except Exception as exc:  # noqa: BLE001 — startup must continue fail-closed per deploy
            log_event(
                logger,
                "gateway.lifespan.rehydrate_error",
                level=logging.ERROR,
                error=str(exc),
            )
        app.state.accepting_requests = True
        executor = getattr(app.state, "coder_executor", None)
        if executor is not None:
            resumed = await executor.resume_active_runs()
            if resumed:
                log_event(logger, "gateway.lifespan.coder_runs_resumed", run_ids=resumed)
        try:
            yield
        finally:
            app.state.accepting_requests = False
            log_event(logger, "gateway.lifespan.shutdown_begin")
            if executor is not None:
                await executor.shutdown()
            # Close MCP client sessions so external stdio subprocesses do not
            # leak across gateway restarts.
            mcp_client = getattr(app.state, "mcp_client", None)
            if mcp_client is not None:
                await asyncio.to_thread(mcp_client.close)
            # Wait briefly for in-flight active requests to drain.
            for _ in range(50):
                active = sum(
                    deployment.active_requests
                    for deployment in app.state.routes.deployments.values()
                )
                if active <= 0:
                    break
                await asyncio.sleep(0.1)
            state_store.save_from(app.state.routes)
            log_event(logger, "gateway.lifespan.shutdown_complete")

    app = FastAPI(
        title="Yonder Gateway",
        version="0.1.0",
        description="OpenAI-compatible inference gateway and operator control plane.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.logger = logger
    app.state.metrics = MetricsRegistry(service="yonder-gateway")
    app.state.workers = {}
    app.state.event_store = persistence.events
    app.state.request_ledger = persistence.request_ledger
    app.state.accepting_requests = True
    app.state.persistence = persistence
    app.state.state_store = state_store
    app.state.providers = build_provider_registry(settings)
    app.state.model_definitions = build_model_definitions(settings)
    app.state.routes = build_route_registry(settings, app.state.model_definitions)
    # Overlay durable alias/deployment state from previous runs.
    state_store.load_into(app.state.routes)
    _seed_static_workers(app)

    def persist_state() -> None:
        state_store.save_from(app.state.routes)

    app.state.conversations = persistence.conversations
    app.state.progress = persistence.progress
    from app.services.curriculum import get_curriculum
    from app.services.sandbox import InMemorySandboxStore

    app.state.sandboxes = InMemorySandboxStore(quotas=get_curriculum().sandbox)
    app.state.mission_progress = persistence.mission_progress
    app.state.challenges = persistence.challenges
    app.state.workbench = persistence.workbench
    app.state.corpus_quarantine = persistence.corpus_quarantine
    app.state.chat_service = ChatService(
        routes=app.state.routes,
        providers=app.state.providers,
        admission=settings.admission,
        gate=DeploymentAdmissionGate(settings.admission),
        accepting_requests=lambda: bool(app.state.accepting_requests),
        on_state_changed=persist_state,
        routing_policy=settings.routing,
        context_window=settings.context_window,
        conversations=persistence.conversations,
    )

    def audit(event: dict) -> None:
        app.state.event_store.append(event)

    app.state.lifecycle = LifecycleService(
        routes=app.state.routes,
        providers=app.state.providers,
        definitions=app.state.model_definitions,
        audit=audit,
        on_state_changed=persist_state,
        workers=app.state.workers,
        metrics=app.state.metrics,
        max_lanes_per_gpu=settings.capacity.max_lanes_per_gpu,
        capacity_events=None,  # wired below after capacity store
    )
    app.state.tool_executor = build_default_tool_executor()
    app.state.mcp_client = McpClientManager(
        settings.mcp,
        audit=lambda event: app.state.event_store.append(event),
    )
    if settings.mcp.enabled and settings.mcp.bridge_into_tool_executor:
        for qualified, description, parameters, handler in app.state.mcp_client.bridge_handlers():
            app.state.tool_executor.register(
                ToolDefinition(
                    name=qualified,
                    description=description,
                    parameters_schema=parameters,
                ),
                handler,
            )
    app.state.schema_registry = build_default_schema_registry()
    rag_db = Path("data") / "rag.sqlite3"
    if settings.database_url and settings.database_url.startswith("sqlite:///"):
        # Keep RAG next to the main SQLite DB when one is configured.
        db_path = settings.database_url.removeprefix("sqlite:///")
        rag_db = Path(db_path).expanduser().resolve().parent / "rag.sqlite3"
    app.state.rag_store = RagStore(rag_db)
    seed_lab_corpus(
        app.state.rag_store,
        resolve_repo_root() / "config" / "lab" / "corpus.yaml",
    )
    seed_lab_corpus(
        app.state.rag_store,
        resolve_repo_root() / "config" / "lab" / "corpus-site-ops.yaml",
    )
    seed_lab_corpus(
        app.state.rag_store,
        resolve_repo_root() / "config" / "lab" / "corpus-support.yaml",
    )
    seed_lab_corpus(
        app.state.rag_store,
        resolve_repo_root() / "config" / "lab" / "corpus-ops.yaml",
    )
    seed_lab_corpus(
        app.state.rag_store,
        resolve_repo_root() / "config" / "lab" / "corpus-eng.yaml",
    )
    app.state.rag_retriever = LexicalRetriever(app.state.rag_store)
    artifact_root = Path("data") / "artifacts"
    if settings.database_url and settings.database_url.startswith("sqlite:///"):
        db_path = settings.database_url.removeprefix("sqlite:///")
        artifact_root = Path(db_path).expanduser().resolve().parent / "artifacts"
    app.state.artifact_store = LocalArtifactStore(artifact_root)
    generation_provider_name = (
        "diffusers" if settings.diffusers.enabled else "mock-generation"
    )
    gen_provider = app.state.providers.require_generation(generation_provider_name)
    generation_store = None
    if persistence.session_factory is not None:
        from app.persistence.sql_generation_store import SqlAlchemyGenerationStore

        generation_store = SqlAlchemyGenerationStore(persistence.session_factory)
    from app.persistence.sql_capacity_usage_store import (
        InMemoryCapacityUsageStore,
        SqlAlchemyCapacityUsageStore,
    )
    from app.services.ensure_ready import EnsureDeploymentReady

    if persistence.session_factory is not None:
        capacity_events = SqlAlchemyCapacityUsageStore(persistence.session_factory)
    else:
        capacity_events = InMemoryCapacityUsageStore()
    app.state.capacity_events = capacity_events
    app.state.lifecycle._capacity_events = capacity_events
    ensure_ready = EnsureDeploymentReady(
        lifecycle=app.state.lifecycle,
        routes=app.state.routes,
        workers=app.state.workers,
        definitions=app.state.model_definitions,
        config=settings.capacity,
        capacity_events=capacity_events,
    )
    app.state.ensure_ready = ensure_ready
    app.state.chat_service.ensure_ready = ensure_ready
    app.state.generation_jobs = GenerationJobService(
        provider=gen_provider,
        artifact_store=app.state.artifact_store,
        routes=app.state.routes,
        store=generation_store,
        ensure_ready=ensure_ready,
    )
    speech_cfg = settings.speech
    app.state.speech = SpeechService(
        enabled=bool(speech_cfg.enabled),
        worker_base_url=speech_cfg.worker_base_url,
        timeout_seconds=float(speech_cfg.request_timeout_seconds),
        artifact_store=app.state.artifact_store,
        stt_alias=speech_cfg.stt_alias,
        tts_alias=speech_cfg.tts_alias,
    )
    app.state.speech_audio_cache = {}
    coder_cfg = settings.coder

    # Docs corpus: a separate RAG store (Yonder docs + vendored library docs)
    # backing the read-only coder_docs_search tool. Seeded once if empty.
    app.state.docs_retriever = None
    if coder_cfg.enabled and coder_cfg.docs_search_enabled:
        from app.services.docs_corpus import seed_default_docs

        docs_db = rag_db.parent / "docs.sqlite3"
        docs_store = RagStore(docs_db)
        app.state.docs_store = docs_store
        if not docs_store.list_documents():
            seeded = seed_default_docs(docs_store, resolve_repo_root())
            log_event(logger, "gateway.docs_corpus.seeded", counts=seeded)
        # Phase 2: hybrid retrieval when an embedding alias is configured and
        # ready; otherwise lexical (build_docs_retriever falls back on failure).
        from app.services.docs_corpus import build_gateway_embed_fn
        from app.services.docs_retrieval import (
            DocsRetrieverConfig,
            build_docs_retriever,
        )

        embed_fn = None
        if coder_cfg.docs_embeddings_enabled:
            try:
                embed_fn = build_gateway_embed_fn(
                    app.state.routes, app.state.providers, coder_cfg.docs_embed_alias
                )
            except Exception as exc:  # noqa: BLE001 — embeddings optional, log honestly
                log_event(
                    logger,
                    "gateway.docs_corpus.embed_unavailable",
                    level=logging.WARNING,
                    error=str(exc),
                )
        app.state.docs_retriever = build_docs_retriever(
            docs_store,
            DocsRetrieverConfig(
                embeddings_enabled=coder_cfg.docs_embeddings_enabled,
                embed_texts=embed_fn,
                alpha=float(coder_cfg.docs_hybrid_alpha),
            ),
        )
    if coder_cfg.enabled:
        grantable = list(coder_cfg.grantable_roots)
        # Always allow the local eval fixture tree for proof packs.
        eval_root = resolve_repo_root() / "config" / "lab" / "coder-evals"
        if eval_root.exists():
            grantable.append(eval_root)
        coder_store = getattr(persistence, "coder_store", None)

        def on_workspace_changed(grant, deleted_id):
            if coder_store is None:
                return
            if deleted_id:
                coder_store.delete_workspace(deleted_id)
            elif grant is not None:
                coder_store.save_workspace(grant)

        app.state.coder = CoderWorkspaceService(
            cage_root=Path(coder_cfg.cage_root),
            grantable_roots=grantable,
            max_read_bytes=int(coder_cfg.max_read_bytes),
            max_list_entries=int(coder_cfg.max_list_entries),
            max_search_hits=int(coder_cfg.max_search_hits),
            write_unlock=bool(coder_cfg.write_unlock),
            check_allowlist=list(coder_cfg.check_allowlist),
            check_timeout_seconds=int(coder_cfg.check_timeout_seconds),
            check_isolation=coder_cfg.check_isolation,
            check_isolation_image=coder_cfg.check_isolation_image,
            audit=lambda event: app.state.event_store.append(event),
            on_workspace_changed=on_workspace_changed,
        )
        if coder_store is not None:
            for grant in coder_store.list_all_workspaces():
                if Path(grant.root).exists():
                    app.state.coder.restore_workspace(grant)
        from app.services.coder_executor import CoderRunExecutor
        from app.services.coder_runs import CoderRunOrchestrator, build_coder_llm

        # Live-only: the agent always calls the configured local model.
        app.state.coder_agent_mode = "live"
        llm_fn = build_coder_llm(
            app.state.chat_service,
            model_alias=coder_cfg.model_alias,
            max_output_tokens=int(coder_cfg.max_output_tokens),
        )
        verifier_fn = None
        if coder_cfg.verifier_model_alias.strip():
            verifier_fn = build_coder_llm(
                app.state.chat_service,
                model_alias=coder_cfg.verifier_model_alias.strip(),
                max_output_tokens=int(coder_cfg.max_output_tokens),
            )
        from app.services.git_adapter import ForgejoAdapter

        app.state.forgejo = ForgejoAdapter(
            base_url=coder_cfg.forgejo_base_url,
            token_ref=coder_cfg.forgejo_token_ref,
        )
        app.state.coder_runs = CoderRunOrchestrator(
            workspaces=app.state.coder,
            store=persistence.coder_runs,
            llm=llm_fn,
            verifier_llm=verifier_fn,
            max_agent_turns=int(coder_cfg.max_agent_turns),
            max_fix_iterations=int(coder_cfg.max_fix_iterations),
            max_model_calls=int(coder_cfg.max_model_calls),
            max_wall_clock_seconds=int(coder_cfg.max_wall_clock_seconds),
            context_budget_tokens=int(coder_cfg.context_budget_tokens),
            audit=lambda event: app.state.event_store.append(event),
            # Per-run, allowlisted MCP tools for the coder agent (fail closed).
            mcp_client=app.state.mcp_client,
            mcp_tools=list(coder_cfg.mcp_tools),
            # Gateway-native docs search over the RAG corpus (read-only).
            docs_retriever=app.state.docs_retriever,
            docs_max_results=int(coder_cfg.docs_max_results),
            # Phase L delivery: open a PR after local push when configured.
            forgejo=app.state.forgejo,
            forgejo_owner=coder_cfg.forgejo_owner,
            forgejo_base_branch=coder_cfg.forgejo_base_branch,
        )
        app.state.coder_executor = CoderRunExecutor(
            orchestrator=app.state.coder_runs,
            logger=logger,
            max_concurrent=int(coder_cfg.max_concurrent_runs),
        )
    else:
        app.state.coder = None
        app.state.coder_runs = None
        app.state.coder_executor = None
        app.state.forgejo = None
    # Persist bootstrapped defaults when no prior durable state existed.
    if not state_store.has_persisted_state():
        persist_state()

    provider = build_request_context_provider(settings.security)
    app.add_middleware(
        RequestContextMiddleware,
        provider=provider,
        service_name="yonder-gateway",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Yonder-Request-Id", "X-Request-Id"],
    )
    app.include_router(ops.router)
    app.include_router(admin.router)
    app.include_router(workers.router)
    app.include_router(chat.router)
    app.include_router(v1.router)
    app.include_router(lab.router)
    app.include_router(academy_tier2.router)
    app.include_router(help.router)
    app.include_router(platform.router)
    app.include_router(generation.router)
    app.include_router(speech.router)
    app.include_router(coder.router)
    app.include_router(mcp.router)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        request_id = getattr(request.state, "yonder_request_id", "unknown")
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(
                status_code=exc.status_code,
                content=exc.detail,
                headers={"X-Yonder-Request-Id": request_id},
            )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(
                ErrorCode.INTERNAL_ERROR,
                str(exc.detail),
                request_id,
            ),
            headers={"X-Yonder-Request-Id": request_id},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request_id = getattr(request.state, "yonder_request_id", "unknown")
        return JSONResponse(
            status_code=ERROR_STATUS[ErrorCode.INVALID_REQUEST],
            content=error_payload(
                ErrorCode.INVALID_REQUEST,
                "Request validation failed.",
                request_id,
                details={"errors": exc.errors()},
            ),
            headers={"X-Yonder-Request-Id": request_id},
        )

    log_event(
        logger,
        "gateway.startup",
        environment=settings.environment.value,
        security_mode=settings.security.mode.value,
        bind=f"{settings.gateway.host}:{settings.gateway.port}",
        providers=app.state.providers.names(),
        aliases=sorted(app.state.routes.aliases),
        persistence_backend=persistence.backend,
        state_file=str(settings.state_file),
        database_configured=bool(settings.database_url),
    )

    return app


def run() -> None:
    settings = load_settings()
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=settings.gateway.host,
        port=settings.gateway.port,
        log_level=settings.logging.level.lower(),
    )


app = create_app()
