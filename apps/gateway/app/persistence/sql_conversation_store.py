"""SQLAlchemy-backed conversation store with retention controls."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload, sessionmaker
from yonder_configuration.models import ConversationRetentionConfig

from app.persistence.db_models import ConversationMessageRow, ConversationRow
from app.services.conversations import Conversation, ConversationMessage, ConversationSummary


def _now() -> datetime:
    return datetime.now(UTC)


class SqlAlchemyConversationStore:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        retention: ConversationRetentionConfig | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._retention = retention or ConversationRetentionConfig()

    async def list_conversations(self, principal_subject: str) -> list[ConversationSummary]:
        self._prune(principal_subject)
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(ConversationRow)
                    .where(ConversationRow.principal_subject == principal_subject)
                    .options(selectinload(ConversationRow.messages))
                    .order_by(ConversationRow.updated_at.desc())
                )
                .scalars()
                .all()
            )
            return [
                ConversationSummary(
                    id=row.id,
                    title=row.title,
                    model_alias=row.model_alias,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                    message_count=len(row.messages),
                )
                for row in rows
            ]

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
        with self._session_factory() as session:
            session.add(
                ConversationRow(
                    id=convo.id,
                    principal_subject=convo.principal_subject,
                    title=convo.title,
                    model_alias=convo.model_alias,
                    created_at=convo.created_at,
                    updated_at=convo.updated_at,
                )
            )
            session.commit()
        self._enforce_count_limit(principal_subject)
        return convo

    async def get_conversation(
        self, conversation_id: str, principal_subject: str
    ) -> Conversation | None:
        with self._session_factory() as session:
            row = self._get_row(session, conversation_id, principal_subject)
            if row is None:
                return None
            return self._to_conversation(row)

    async def rename_conversation(
        self, conversation_id: str, principal_subject: str, title: str
    ) -> Conversation | None:
        with self._session_factory() as session:
            row = self._get_row(session, conversation_id, principal_subject)
            if row is None:
                return None
            row.title = title.strip() or row.title
            row.updated_at = _now()
            session.commit()
            session.refresh(row)
            return self._to_conversation(row)

    async def delete_conversation(
        self, conversation_id: str, principal_subject: str
    ) -> bool:
        with self._session_factory() as session:
            row = self._get_row(session, conversation_id, principal_subject)
            if row is None:
                return False
            session.delete(row)
            session.commit()
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
        with self._session_factory() as session:
            row = self._get_row(session, conversation_id, principal_subject)
            if row is None:
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
            session.add(
                ConversationMessageRow(
                    id=message.id,
                    conversation_id=conversation_id,
                    role=role,
                    content=content,
                    request_id=request_id,
                    message_metadata=metadata,
                    created_at=message.created_at,
                )
            )
            row.updated_at = message.created_at
            session.flush()
            session.refresh(row)
            # Bound message growth.
            message_count = len(row.messages)
            if message_count > self._retention.max_messages_per_conversation:
                overflow = message_count - self._retention.max_messages_per_conversation
                for old in list(row.messages)[:overflow]:
                    session.delete(old)
            session.commit()
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
        with self._session_factory() as session:
            row = self._get_row(session, conversation_id, principal_subject)
            if row is None or not row.messages:
                return None
            for message_row in reversed(row.messages):
                if message_row.role == "assistant":
                    message_row.content = content
                    message_row.request_id = request_id
                    if metadata is not None:
                        message_row.message_metadata = metadata
                    message_row.created_at = _now()
                    row.updated_at = message_row.created_at
                    session.commit()
                    return ConversationMessage(
                        id=message_row.id,
                        conversation_id=conversation_id,
                        role="assistant",
                        content=content,
                        request_id=request_id,
                        metadata=message_row.message_metadata,
                        created_at=message_row.created_at,
                    )
            return None

    async def replace_messages(
        self,
        conversation_id: str,
        principal_subject: str,
        *,
        messages: list[dict],
    ) -> list[ConversationMessage] | None:
        with self._session_factory() as session:
            row = self._get_row(session, conversation_id, principal_subject)
            if row is None:
                return None
            for old in list(row.messages):
                session.delete(old)
            session.flush()
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
                message = ConversationMessage(
                    id=f"msg_{uuid.uuid4().hex[:16]}",
                    conversation_id=conversation_id,
                    role=role,
                    content=content,
                    request_id=item.get("request_id"),
                    metadata=metadata,
                    created_at=now,
                )
                session.add(
                    ConversationMessageRow(
                        id=message.id,
                        conversation_id=conversation_id,
                        role=role,
                        content=content,
                        request_id=message.request_id,
                        message_metadata=metadata,
                        created_at=now,
                    )
                )
                rebuilt.append(message)
            limit = self._retention.max_messages_per_conversation
            if len(rebuilt) > limit:
                overflow = len(rebuilt) - limit
                for old in rebuilt[:overflow]:
                    session.delete(
                        session.get(ConversationMessageRow, old.id)
                    )
                rebuilt = rebuilt[-limit:]
            row.updated_at = now
            session.commit()
            return rebuilt

    def _get_row(
        self, session: Session, conversation_id: str, principal_subject: str
    ) -> ConversationRow | None:
        row = session.execute(
            select(ConversationRow)
            .where(
                ConversationRow.id == conversation_id,
                ConversationRow.principal_subject == principal_subject,
            )
            .options(selectinload(ConversationRow.messages))
        ).scalar_one_or_none()
        return row

    def _to_conversation(self, row: ConversationRow) -> Conversation:
        return Conversation(
            id=row.id,
            principal_subject=row.principal_subject,
            title=row.title,
            model_alias=row.model_alias,
            created_at=row.created_at,
            updated_at=row.updated_at,
            messages=[
                ConversationMessage(
                    id=message.id,
                    conversation_id=message.conversation_id,
                    role=message.role,  # type: ignore[arg-type]
                    content=message.content,
                    request_id=message.request_id,
                    metadata=message.message_metadata,
                    created_at=message.created_at,
                )
                for message in row.messages
            ],
        )

    def _prune(self, principal_subject: str) -> None:
        max_age = self._retention.max_age_days
        if max_age is None:
            return
        cutoff = _now() - timedelta(days=max_age)
        with self._session_factory() as session:
            session.execute(
                delete(ConversationRow).where(
                    ConversationRow.principal_subject == principal_subject,
                    ConversationRow.updated_at < cutoff,
                )
            )
            session.commit()

    def _enforce_count_limit(self, principal_subject: str) -> None:
        limit = self._retention.max_conversations_per_principal
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(ConversationRow)
                    .where(ConversationRow.principal_subject == principal_subject)
                    .order_by(ConversationRow.updated_at.desc())
                )
                .scalars()
                .all()
            )
            for stale in rows[limit:]:
                session.delete(stale)
            session.commit()
