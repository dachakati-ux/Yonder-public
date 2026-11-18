"""Gateway-only reverse proxy to worker-local inference runtimes.

Managed llama.cpp processes remain bound to worker loopback. The Gateway calls
this deployment-scoped worker endpoint, so browsers and internal applications
never need a direct runtime address and multi-host workers do not expose a
dynamic port range.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/v1/runtime", tags=["runtime-proxy"])

_ALLOWED_ROUTES = frozenset(
    {
        ("GET", "health"),
        ("GET", "v1/models"),
        ("POST", "v1/chat/completions"),
        ("POST", "v1/embeddings"),
        ("POST", "v1/rerank"),
    }
)
_FORWARDED_REQUEST_HEADERS = frozenset({"accept", "content-type"})
_FORWARDED_RESPONSE_HEADERS = frozenset(
    {"content-type", "cache-control", "x-request-id"}
)


def _runtime_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=None, write=300.0, pool=5.0)
    )


@router.api_route(
    "/{deployment_id}/{runtime_path:path}",
    methods=["GET", "POST"],
)
async def proxy_runtime(
    deployment_id: str,
    runtime_path: str,
    request: Request,
) -> StreamingResponse:
    route = (request.method.upper(), runtime_path.strip("/"))
    if route not in _ALLOWED_ROUTES:
        raise HTTPException(status_code=404, detail="Unsupported runtime proxy route")

    manager = getattr(request.app.state, "llama_runtime", None)
    endpoint = manager.endpoint_for(deployment_id) if manager is not None else None
    if not endpoint:
        raise HTTPException(
            status_code=404,
            detail=f"Runtime deployment is not active: {deployment_id}",
        )

    client = _runtime_client()
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() in _FORWARDED_REQUEST_HEADERS
    }
    try:
        upstream_request = client.build_request(
            request.method,
            f"{endpoint.rstrip('/')}/{route[1]}",
            params=request.query_params,
            headers=headers,
            content=await request.body(),
        )
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(
            status_code=502,
            detail=f"Worker-local runtime proxy failed: {exc}",
        ) from exc

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    response_headers = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() in _FORWARDED_RESPONSE_HEADERS
    }
    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=response_headers,
    )
