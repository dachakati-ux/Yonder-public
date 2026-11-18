"""OpenAI-compatible v1 API."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from yonder_contracts.errors import ErrorCode
from yonder_telemetry import log_event

from app.context import get_principal, get_request_id
from app.errors import error_response
from app.models.openai import (
    ChatCompletionRequest,
    EmbeddingData,
    EmbeddingsRequest,
    EmbeddingsResponse,
    EmbeddingsUsage,
    ModelCard,
    ModelListResponse,
)
from app.routing.models import RouteState
from app.routing.resolve import RouteResolutionError, resolve_route
from app.services.chat import ChatServiceError
from app.services.request_ledger import RequestRecord

router = APIRouter(prefix="/v1", tags=["v1"])


def _resolve_meta(request: Request, model: str) -> tuple[str | None, str | None]:
    resolved = resolve_route(request.app.state.routes, model)
    if isinstance(resolved, RouteResolutionError):
        return None, None
    return resolved.deployment.id, resolved.deployment.worker_id


@router.get("/models")
async def list_models(request: Request) -> ModelListResponse:
    principal = get_principal(request)
    routes = request.app.state.routes
    created = int(time.time())
    policy = request.app.state.settings.routing
    data = []
    for alias in routes.list_enabled_aliases():
        if alias.state == RouteState.DISABLED:
            continue
        if (
            alias.experimental
            and policy.require_capability_for_experimental
            and not principal.has_capability(policy.experimental_capability)
        ):
            continue
        data.append(ModelCard(id=alias.alias, created=created, owned_by="yonder"))
    return ModelListResponse(data=data)


@router.post("/chat/completions")
async def chat_completions(body: ChatCompletionRequest, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    chat_service = request.app.state.chat_service
    logger = request.app.state.logger
    ledger = request.app.state.request_ledger
    started = time.perf_counter()
    deployment_id, worker_id = _resolve_meta(request, body.model)

    if not principal.has_capability("chat.use"):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks chat.use capability.",
            request_id,
            details={"subject": principal.subject},
        )

    try:
        chat_service.enforce_lane_access(body.model, principal)
    except ChatServiceError as exc:
        return error_response(
            exc.code, exc.message, request_id, details=exc.details
        )

    ledger.add(
        RequestRecord(
            request_id=request_id,
            endpoint="/v1/chat/completions",
            model_alias=body.model,
            deployment_id=deployment_id,
            worker_id=worker_id,
            status="active",
        )
    )
    request.app.state.metrics.active_requests.labels(service="yonder-gateway").inc()
    log_event(
        logger,
        "chat.request.received",
        request_id=request_id,
        model_alias=body.model,
        status="received",
    )

    def finalize(status: str, *, error_code: str | None = None, message: str | None = None) -> None:
        duration_ms = (time.perf_counter() - started) * 1000
        ledger.add(
            RequestRecord(
                request_id=request_id,
                endpoint="/v1/chat/completions",
                model_alias=body.model,
                deployment_id=deployment_id,
                worker_id=worker_id,
                status=status,
                error_code=error_code,
                message=message,
                duration_ms=duration_ms,
            )
        )
        request.app.state.metrics.active_requests.labels(service="yonder-gateway").dec()
        request.app.state.metrics.requests_total.labels(
            service="yonder-gateway",
            endpoint="chat_completions",
            status=status,
        ).inc()

    try:
        if body.stream:
            async def event_stream():
                finished = False
                try:
                    async for line in chat_service.stream(
                        request_id=request_id,
                        body=body,
                        principal_subject=principal.subject,
                    ):
                        if await request.is_disconnected():
                            log_event(
                                logger,
                                "chat.request.cancelled",
                                request_id=request_id,
                                model_alias=body.model,
                                status="cancelled",
                            )
                            finalize("cancelled")
                            finished = True
                            break
                        yield line
                    if not finished:
                        log_event(
                            logger,
                            "chat.request.completed",
                            request_id=request_id,
                            model_alias=body.model,
                            status="success",
                        )
                        finalize("success")
                except ChatServiceError as exc:
                    payload = {
                        "error": {
                            "code": exc.code.value,
                            "message": exc.message,
                            "request_id": request_id,
                            "details": exc.details,
                        }
                    }
                    import json

                    yield f"data: {json.dumps(payload)}\n\n"
                    yield "data: [DONE]\n\n"
                    log_event(
                        logger,
                        "chat.request.failed",
                        request_id=request_id,
                        model_alias=body.model,
                        status="error",
                    )
                    finalize("error", error_code=exc.code.value, message=exc.message)

            return StreamingResponse(
                event_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Yonder-Request-Id": request_id,
                    "X-Accel-Buffering": "no",
                },
            )

        response = await chat_service.complete(
            request_id=request_id,
            body=body,
            principal_subject=principal.subject,
        )
        log_event(
            logger,
            "chat.request.completed",
            request_id=request_id,
            model_alias=body.model,
            status="success",
        )
        finalize("success")
        return JSONResponse(
            content=response.model_dump(),
            headers={"X-Yonder-Request-Id": request_id},
        )
    except ChatServiceError as exc:
        log_event(
            logger,
            "chat.request.failed",
            request_id=request_id,
            model_alias=body.model,
            status="error",
        )
        finalize("error", error_code=exc.code.value, message=exc.message)
        return error_response(
            exc.code,
            exc.message,
            request_id,
            details=exc.details,
        )


@router.post("/embeddings")
async def embeddings(body: EmbeddingsRequest, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not principal.has_capability("embeddings.use") and not principal.has_capability(
        "chat.use"
    ):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks embeddings.use capability.",
            request_id,
        )

    resolved = resolve_route(request.app.state.routes, body.model)
    if isinstance(resolved, RouteResolutionError):
            return error_response(
                resolved.code,
                resolved.message,
                request_id,
                status_code=404,
                details=resolved.details,
            )

    provider_name = resolved.deployment.provider
    provider = request.app.state.providers.get_embedding(provider_name)
    if provider is None:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "No embedding provider is configured for this alias.",
            request_id,
            status_code=501,
            details={"provider": provider_name, "availability": "not_configured"},
        )

    inputs = [body.input] if isinstance(body.input, str) else list(body.input)
    if not inputs:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "embeddings input must not be empty",
            request_id,
        )

    from yonder_contracts.embeddings import EmbeddingRequest

    result = await provider.embed(
        EmbeddingRequest(
            request_id=request_id,
            model_alias=body.model,
            deployment_id=resolved.deployment.id,
            inputs=tuple(inputs),
            dimensions=body.dimensions,
        )
    )
    usage = None
    if result.usage:
        usage = EmbeddingsUsage(
            prompt_tokens=int(result.usage.get("prompt_tokens", 0)),
            total_tokens=int(result.usage.get("total_tokens", 0)),
        )
    return EmbeddingsResponse(
        data=[
            EmbeddingData(index=item.index, embedding=list(item.embedding))
            for item in result.data
        ],
        model=body.model,
        usage=usage,
        yonder={
            "provider": result.provider,
            "availability": result.availability,
            "deployment_id": result.deployment_id,
        },
    )


@router.post("/rerank")
async def rerank(request: Request) -> JSONResponse:
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not principal.has_capability("rag.use") and not principal.has_capability(
        "embeddings.use"
    ):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks rag.use or embeddings.use capability.",
            request_id,
        )
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Invalid JSON body",
            request_id,
            status_code=400,
        )
    model = str(body.get("model") or "").strip()
    query = str(body.get("query") or "")
    documents = body.get("documents") or []
    top_n = body.get("top_n")
    if not model:
        return error_response(
            ErrorCode.INVALID_REQUEST, "model is required", request_id, status_code=400
        )
    if not isinstance(documents, list) or not documents:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "documents must be a non-empty list",
            request_id,
            status_code=400,
        )
    resolved = resolve_route(request.app.state.routes, model)
    if isinstance(resolved, RouteResolutionError):
        return error_response(
            resolved.code,
            resolved.message,
            request_id,
            status_code=404,
            details=resolved.details,
        )
    provider = request.app.state.providers.get_rerank(resolved.deployment.provider)
    if provider is None:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "No rerank provider is configured for this alias.",
            request_id,
            status_code=501,
            details={
                "provider": resolved.deployment.provider,
                "availability": "not_configured",
            },
        )
    from yonder_contracts.reranking import RerankRequest

    try:
        result = await provider.rerank(
            RerankRequest(
                request_id=request_id,
                model_alias=model,
                deployment_id=resolved.deployment.id,
                query=query,
                documents=tuple(str(item) for item in documents),
                top_n=int(top_n) if top_n is not None else None,
            )
        )
    except Exception as exc:  # noqa: BLE001
        return error_response(
            ErrorCode.PROVIDER_FAILURE,
            str(exc),
            request_id,
            status_code=502,
            details={"provider": resolved.deployment.provider},
        )
    return JSONResponse(
        {
            "object": "list",
            "model": model,
            "results": [
                {
                    "index": item.index,
                    "document": item.document,
                    "relevance_score": item.relevance_score,
                }
                for item in result.results
            ],
            "yonder": {
                "provider": result.provider,
                "availability": result.availability,
                "deployment_id": result.deployment_id,
            },
        }
    )


@router.post("/responses")
async def responses(request: Request) -> JSONResponse:
    request_id = get_request_id(request)
    get_principal(request)
    return error_response(
        ErrorCode.INVALID_REQUEST,
        "Responses API is prepared but not enabled in this milestone.",
        request_id,
        status_code=501,
        details={"endpoint": "/v1/responses"},
    )
