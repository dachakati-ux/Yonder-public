"""Platform APIs for tools, RAG, and structured schemas (Gateway-only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from yonder_contracts.errors import ErrorCode
from yonder_contracts.tools import ToolCall

from app.context import get_principal, get_request_id
from app.errors import error_response
from app.services.rag import build_rag_context

router = APIRouter(prefix="/v1/platform", tags=["platform"])


class ToolExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str | None = None


class RagIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source: str = "operator"
    document_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class RagRetrieveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    k: int = Field(default=3, ge=1, le=20)


@router.get("/tools")
async def list_tools(request: Request) -> dict[str, object]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not principal.has_capability("tools.use") and not principal.has_capability("lab.use"):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks tools.use capability.",
            request_id,
        )
    tools = request.app.state.tool_executor.list_definitions()
    return {
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters_schema,
            }
            for tool in tools
        ],
        "availability": "mock",
    }


@router.post("/tools/execute")
async def execute_tool(body: ToolExecuteRequest, request: Request) -> dict[str, object]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not principal.has_capability("tools.use") and not principal.has_capability("lab.use"):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks tools.use capability.",
            request_id,
        )
    result = request.app.state.tool_executor.execute(
        ToolCall(
            id=body.call_id or f"call_{request_id}",
            name=body.name,
            arguments=body.arguments,
        )
    )
    return {
        "tool_call_id": result.tool_call_id,
        "name": result.name,
        "content": result.content,
        "is_error": result.is_error,
        "availability": "mock",
    }


@router.get("/schemas")
async def list_schemas(request: Request) -> dict[str, object]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not principal.has_capability("lab.use") and not principal.has_capability("chat.use"):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks capability to list schemas.",
            request_id,
        )
    entries = request.app.state.schema_registry.list()
    return {
        "schemas": [
            {
                "schema_id": entry.schema_id,
                "version": entry.version,
                "title": entry.title,
                "description": entry.description,
                "json_schema": entry.json_schema,
            }
            for entry in entries
        ]
    }


@router.post("/rag/documents")
async def ingest_rag_document(body: RagIngestRequest, request: Request) -> dict[str, object]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not principal.has_capability("rag.manage") and not principal.has_capability("lab.use"):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks rag.manage capability.",
            request_id,
        )
    doc = request.app.state.rag_store.ingest(
        title=body.title,
        text=body.text,
        source=body.source,
        metadata=body.metadata,
        document_id=body.document_id,
    )
    return {
        "document": {
            "id": doc.id,
            "title": doc.title,
            "source": doc.source,
            "created_at": doc.created_at,
        }
    }


@router.get("/rag/documents")
async def list_rag_documents(request: Request) -> dict[str, object]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not principal.has_capability("rag.use") and not principal.has_capability("lab.use"):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks rag.use capability.",
            request_id,
        )
    docs = request.app.state.rag_store.list_documents()
    return {
        "documents": [
            {
                "id": doc.id,
                "title": doc.title,
                "source": doc.source,
                "created_at": doc.created_at,
            }
            for doc in docs
        ]
    }


@router.post("/rag/retrieve")
async def retrieve_rag(body: RagRetrieveRequest, request: Request) -> dict[str, object]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not principal.has_capability("rag.use") and not principal.has_capability("lab.use"):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks rag.use capability.",
            request_id,
        )
    hits = request.app.state.rag_retriever.retrieve(body.query, k=body.k)
    return {
        "query": body.query,
        "citations": [hit.citation for hit in hits],
        "context": build_rag_context(hits),
        "retriever": "lexical",
        "availability": "ready" if hits else "empty_corpus",
    }
