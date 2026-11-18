"""Conversation persistence contracts — modular for future retention policy."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    conversation_id: str
    role: Literal["system", "user", "assistant"]
    content: str
    request_id: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime


class Conversation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    principal_subject: str
    title: str
    model_alias: str
    created_at: datetime
    updated_at: datetime
    messages: list[ConversationMessage] = Field(default_factory=list)


class ConversationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    model_alias: str
    created_at: datetime
    updated_at: datetime
    message_count: int


class ConversationStore(Protocol):
    async def list_conversations(self, principal_subject: str) -> list[ConversationSummary]: ...

    async def create_conversation(
        self,
        *,
        principal_subject: str,
        title: str,
        model_alias: str,
    ) -> Conversation: ...

    async def get_conversation(
        self, conversation_id: str, principal_subject: str
    ) -> Conversation | None: ...

    async def rename_conversation(
        self, conversation_id: str, principal_subject: str, title: str
    ) -> Conversation | None: ...

    async def delete_conversation(
        self, conversation_id: str, principal_subject: str
    ) -> bool: ...

    async def append_message(
        self,
        conversation_id: str,
        principal_subject: str,
        *,
        role: Literal["system", "user", "assistant"],
        content: str,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ConversationMessage | None: ...

    async def replace_last_assistant_message(
        self,
        conversation_id: str,
        principal_subject: str,
        *,
        content: str,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ConversationMessage | None: ...

    async def replace_messages(
        self,
        conversation_id: str,
        principal_subject: str,
        *,
        messages: list[dict[str, Any]],
    ) -> list[ConversationMessage] | None: ...
