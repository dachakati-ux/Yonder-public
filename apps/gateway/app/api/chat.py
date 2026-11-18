"""Chat conversation management API (not OpenAI-compatible)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from yonder_contracts.errors import ErrorCode
from yonder_contracts.principal import RequestPrincipal

from app.context import get_principal, get_request_id
from app.errors import error_response

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _require_chat(principal: RequestPrincipal, request_id: str):
    if not principal.has_capability("chat.use"):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks chat.use capability.",
            request_id,
            details={"subject": principal.subject},
        )
    return None


class CreateConversationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = "New conversation"
    model_alias: str = "yonder-chat"


class RenameConversationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)


class AppendMessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)
    request_id: str | None = None
    metadata: dict[str, object] | None = None


class ReplaceAssistantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)
    request_id: str | None = None
    metadata: dict[str, object] | None = None


@router.get("/conversations")
async def list_conversations(request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_chat(principal, request_id)
    if denied:
        return denied
    store = request.app.state.conversations
    items = await store.list_conversations(principal.subject)
    return {
        "request_id": request_id,
        "conversations": [item.model_dump(mode="json") for item in items],
    }


@router.post("/conversations")
async def create_conversation(body: CreateConversationBody, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_chat(principal, request_id)
    if denied:
        return denied
    store = request.app.state.conversations
    convo = await store.create_conversation(
        principal_subject=principal.subject,
        title=body.title,
        model_alias=body.model_alias,
    )
    return {
        "request_id": request_id,
        "conversation": convo.model_dump(mode="json"),
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_chat(principal, request_id)
    if denied:
        return denied
    store = request.app.state.conversations
    convo = await store.get_conversation(conversation_id, principal.subject)
    if convo is None:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Conversation not found.",
            request_id,
            status_code=404,
            details={"conversation_id": conversation_id},
        )
    return {
        "request_id": request_id,
        "conversation": convo.model_dump(mode="json"),
    }


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(
    conversation_id: str, body: RenameConversationBody, request: Request
):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_chat(principal, request_id)
    if denied:
        return denied
    store = request.app.state.conversations
    convo = await store.rename_conversation(
        conversation_id, principal.subject, body.title
    )
    if convo is None:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Conversation not found.",
            request_id,
            status_code=404,
            details={"conversation_id": conversation_id},
        )
    return {
        "request_id": request_id,
        "conversation": convo.model_dump(mode="json"),
    }


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_chat(principal, request_id)
    if denied:
        return denied
    store = request.app.state.conversations
    deleted = await store.delete_conversation(conversation_id, principal.subject)
    if not deleted:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Conversation not found.",
            request_id,
            status_code=404,
            details={"conversation_id": conversation_id},
        )
    return {"request_id": request_id, "deleted": True}


@router.post("/conversations/{conversation_id}/messages")
async def append_message(
    conversation_id: str, body: AppendMessageBody, request: Request
):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_chat(principal, request_id)
    if denied:
        return denied
    store = request.app.state.conversations
    message = await store.append_message(
        conversation_id,
        principal.subject,
        role=body.role,
        content=body.content,
        request_id=body.request_id,
        metadata=body.metadata,
    )
    if message is None:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Conversation not found.",
            request_id,
            status_code=404,
            details={"conversation_id": conversation_id},
        )
    return {
        "request_id": request_id,
        "message": message.model_dump(mode="json"),
    }


@router.post("/conversations/{conversation_id}/messages/replace-assistant")
async def replace_assistant(
    conversation_id: str, body: ReplaceAssistantBody, request: Request
):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_chat(principal, request_id)
    if denied:
        return denied
    store = request.app.state.conversations
    message = await store.replace_last_assistant_message(
        conversation_id,
        principal.subject,
        content=body.content,
        request_id=body.request_id,
        metadata=body.metadata,
    )
    if message is None:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Conversation or assistant message not found.",
            request_id,
            status_code=404,
            details={"conversation_id": conversation_id},
        )
    return {
        "request_id": request_id,
        "message": message.model_dump(mode="json"),
    }
