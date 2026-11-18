"""In-memory conversation store for local development.

Replaceable later without changing Chat UI or OpenAI routes.
Does not store hidden chain-of-thought or internal model reasoning.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from yonder_configuration.models import ConversationRetentionConfig

from app.services.conversations import Conversation, ConversationMessage, ConversationSummary


def _now() -> datetime:
    return datetime.now(UTC)


class InMemoryConversationStore:
    def __init__(self, retention: ConversationRetentionConfig | None = None) -> None:
        self._conversations: dict[str, Conversation] = {}
        self._retention = retention or ConversationRetentionConfig()

    async def list_conversations(self, principal_subject: str) -> list[ConversationSummary]:
        self._prune(principal_subject)
        items = [
            ConversationSummary(
                id=convo.id,
                title=convo.title,
                model_alias=convo.model_alias,
                created_at=convo.created_at,
                updated_at=convo.updated_at,
                message_count=len(convo.messages),
            )
            for convo in self._conversations.values()
            if convo.principal_subject == principal_subject
        ]
        return sorted(items, key=lambda item: item.updated_at, reverse=True)

    async def create_conversation(
        self,
        *,
        principal_subject: str,
        title: str,
        model_alias: str,
    ) -> Conversation:
        self._prune(principal_subject)
        now = _now()
        convo = Conversation(
            id=f"conv_{uuid.uuid4().hex[:16]}",
            principal_subject=principal_subject,
            title=title.strip() or "New conversation",
            model_alias=model_alias,
            created_at=now,
            updated_at=now,
            messages=[],
        )
        self._conversations[convo.id] = convo
        self._enforce_count_limit(principal_subject)
        return convo

    async def get_conversation(
        self, conversation_id: str, principal_subject: str
    ) -> Conversation | None:
        convo = self._conversations.get(conversation_id)
        if convo is None or convo.principal_subject != principal_subject:
            return None
        return convo

    async def rename_conversation(
        self, conversation_id: str, principal_subject: str, title: str
    ) -> Conversation | None:
        convo = await self.get_conversation(conversation_id, principal_subject)
        if convo is None:
            return None
        updated = convo.model_copy(
            update={"title": title.strip() or convo.title, "updated_at": _now()}
        )
        self._conversations[conversation_id] = updated
        return updated

    async def delete_conversation(
        self, conversation_id: str, principal_subject: str
    ) -> bool:
        convo = await self.get_conversation(conversation_id, principal_subject)
        if convo is None:
            return False
        del self._conversations[conversation_id]
        return True

    async def append_message(
        self,
        conversation_id: str,
        principal_subject: str,
        *,
        role: Literal["system", "user", "assistant"],
        content: str,
        request_id: str | None = None,
        metadata: dict | None = None,
    ) -> ConversationMessage | None:
        convo = await self.get_conversation(conversation_id, principal_subject)
        if convo is None:
            return None
        message = ConversationMessage(
            id=f"msg_{uuid.uuid4().hex[:16]}",
            conversation_id=conversation_id,
            role=role,
            content=content,
            request_id=request_id,
            metadata=metadata,
            created_at=_now(),
        )
        messages = list(convo.messages) + [message]
        limit = self._retention.max_messages_per_conversation
        if len(messages) > limit:
            messages = messages[-limit:]
        self._conversations[conversation_id] = convo.model_copy(
            update={"messages": messages, "updated_at": _now()}
        )
        return message

    async def replace_last_assistant_message(
        self,
        conversation_id: str,
        principal_subject: str,
        *,
        content: str,
        request_id: str | None = None,
        metadata: dict | None = None,
    ) -> ConversationMessage | None:
        convo = await self.get_conversation(conversation_id, principal_subject)
        if convo is None or not convo.messages:
            return None
        messages = list(convo.messages)
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].role == "assistant":
                previous_meta = messages[index].metadata
                replacement = ConversationMessage(
                    id=messages[index].id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=content,
                    request_id=request_id,
                    metadata=metadata if metadata is not None else previous_meta,
                    created_at=_now(),
                )
                messages[index] = replacement
                self._conversations[conversation_id] = convo.model_copy(
                    update={"messages": messages, "updated_at": _now()}
                )
                return replacement
        return None

    async def replace_messages(
        self,
        conversation_id: str,
        principal_subject: str,
        *,
        messages: list[dict],
    ) -> list[ConversationMessage] | None:
        convo = await self.get_conversation(conversation_id, principal_subject)
        if convo is None:
            return None
        now = _now()
        rebuilt: list[ConversationMessage] = []
        for item in messages:
            role = item.get("role")
            content = item.get("content")
            if role not in ("system", "user", "assistant"):
                continue
            if not isinstance(content, str) or not content:
                continue
            raw_meta = item.get("metadata")
            metadata = raw_meta if isinstance(raw_meta, dict) else None
            rebuilt.append(
                ConversationMessage(
                    id=f"msg_{uuid.uuid4().hex[:16]}",
                    conversation_id=conversation_id,
                    role=role,
                    content=content,
                    request_id=item.get("request_id"),
                    metadata=metadata,
                    created_at=now,
                )
            )
        limit = self._retention.max_messages_per_conversation
        if len(rebuilt) > limit:
            rebuilt = rebuilt[-limit:]
        self._conversations[conversation_id] = convo.model_copy(
            update={"messages": rebuilt, "updated_at": now}
        )
        return rebuilt

    def _prune(self, principal_subject: str) -> None:
        max_age = self._retention.max_age_days
        if max_age is None:
            return
        cutoff = _now() - timedelta(days=max_age)
        stale = [
            convo_id
            for convo_id, convo in self._conversations.items()
            if convo.principal_subject == principal_subject and convo.updated_at < cutoff
        ]
        for convo_id in stale:
            del self._conversations[convo_id]

    def _enforce_count_limit(self, principal_subject: str) -> None:
        owned = sorted(
            (
                convo
                for convo in self._conversations.values()
                if convo.principal_subject == principal_subject
            ),
            key=lambda item: item.updated_at,
            reverse=True,
        )
        for stale in owned[self._retention.max_conversations_per_principal :]:
            del self._conversations[stale.id]
