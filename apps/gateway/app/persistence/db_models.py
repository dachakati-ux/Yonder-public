"""SQLAlchemy ORM models for durable Yonder control-plane state."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ModelDefinitionRow(Base):
    __tablename__ = "model_definitions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    validation_status: Mapped[str] = mapped_column(String(64), default="registered")
    validation_issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeploymentRow(Base):
    __tablename__ = "deployments"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    model_definition_id: Mapped[str] = mapped_column(String(128), index=True)
    worker_id: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(64))
    gpu_assignments: Mapped[list[int]] = mapped_column(JSON, default=list)
    runtime_configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    load_progress_percent: Mapped[float | None] = mapped_column(nullable=True)
    loaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_successful_load: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    maintenance: Mapped[bool] = mapped_column(Boolean, default=False)
    logs: Mapped[list[str]] = mapped_column(JSON, default=list)


class AliasRow(Base):
    __tablename__ = "model_aliases"

    alias: Mapped[str] = mapped_column(String(128), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(String(128), index=True)
    state: Mapped[str] = mapped_column(String(64), default="enabled")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    experimental: Mapped[bool] = mapped_column(Boolean, default=False)


class ConversationRow(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    principal_subject: Mapped[str] = mapped_column(String(256), index=True)
    title: Mapped[str] = mapped_column(String(256))
    model_alias: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    messages: Mapped[list[ConversationMessageRow]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessageRow.created_at",
    )


class ConversationMessageRow(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Optional structured payload (e.g. Chat generation job/artifact refs).
    # Named message_metadata in ORM to avoid clashing with Base.metadata.
    message_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    conversation: Mapped[ConversationRow] = relationship(back_populates="messages")


class AdminEventRow(Base):
    __tablename__ = "admin_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    principal_subject: Mapped[str | None] = mapped_column(String(256), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    result: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class RequestLedgerRow(Base):
    __tablename__ = "request_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(128), index=True)
    endpoint: Mapped[str] = mapped_column(String(128))
    model_alias: Mapped[str | None] = mapped_column(String(128), nullable=True)
    deployment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(nullable=True)
    ttft_ms: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class GenerationJobRow(Base):
    __tablename__ = "generation_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(128))
    principal_subject: Mapped[str] = mapped_column(String(256), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    mode: Mapped[str] = mapped_column(String(32))
    model_alias: Mapped[str] = mapped_column(String(128))
    deployment_id: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(64))
    availability: Mapped[str] = mapped_column(String(32))
    prompt: Mapped[str] = mapped_column(Text)
    negative_prompt: Mapped[str] = mapped_column(Text, default="")
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    progress: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GenerationArtifactRow(Base):
    __tablename__ = "generation_artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    principal_subject: Mapped[str] = mapped_column(String(256), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    mime_type: Mapped[str] = mapped_column(String(128))
    filename: Mapped[str] = mapped_column(String(256))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(512))
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LessonProgressRow(Base):
    """Academy lesson progress keyed by upstream RequestPrincipal.subject."""

    __tablename__ = "lesson_progress"

    principal_subject: Mapped[str] = mapped_column(String(256), primary_key=True)
    lesson_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="not_started")
    check_attempts: Mapped[int] = mapped_column(Integer, default=0)
    check_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    last_check_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_try_it_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    try_it_recorded: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class MissionProgressRow(Base):
    """Academy mission progress keyed by RequestPrincipal.subject."""

    __tablename__ = "mission_progress"

    principal_subject: Mapped[str] = mapped_column(String(256), primary_key=True)
    mission_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="not_started")
    completed_steps: Mapped[list[str]] = mapped_column(JSON, default=list)
    check_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[str] = mapped_column(String(64))


class ChallengeSubmissionRow(Base):
    """Academy challenge submissions — criteria met/missed, no vanity scores."""

    __tablename__ = "challenge_submission"

    principal_subject: Mapped[str] = mapped_column(String(256), primary_key=True)
    challenge_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    answer: Mapped[str] = mapped_column(Text)
    criteria_met: Mapped[list[str]] = mapped_column(JSON, default=list)
    criteria_missed: Mapped[list[str]] = mapped_column(JSON, default=list)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    submitted_at: Mapped[str] = mapped_column(String(64), index=True)


class WorkbenchNoteRow(Base):
    """Principal-scoped Academy workbench notes."""

    __tablename__ = "workbench_note"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    principal_subject: Mapped[str] = mapped_column(String(256), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(64), default="manual")
    created_at: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[str] = mapped_column(String(64), index=True)


class CorpusQuarantineRow(Base):
    """Global teaching corpus/preset quarantine flags."""

    __tablename__ = "corpus_quarantine"

    corpus_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    quarantined_at: Mapped[str] = mapped_column(String(64))


class CapacityUsageEventRow(Base):
    """EnsureReady / load / unload / evict / demote / lock telemetry."""

    __tablename__ = "capacity_usage_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    model_definition_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    deployment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    gpu_indexes: Mapped[list[int]] = mapped_column(JSON, default=list)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    principal_subject: Mapped[str | None] = mapped_column(String(256), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class CoderWorkspaceRow(Base):
    """Durable coder workspace metadata (files live under cage_root)."""

    __tablename__ = "coder_workspace"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    principal_subject: Mapped[str] = mapped_column(String(256), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    root: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(String(256), default="")
    source: Mapped[str] = mapped_column(Text, default="")
    git_head: Mapped[str] = mapped_column(String(128), default="")
    remote_url: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64), index=True)


class CoderRunRow(Base):
    """Durable coder pipeline runs with stage timeline + dual verdicts."""

    __tablename__ = "coder_run"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    principal_subject: Mapped[str] = mapped_column(String(256), index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    task: Mapped[str] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    check_command: Mapped[str] = mapped_column(String(256), default="python -m pytest")
    artifacts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    timeline: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    verdicts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    push_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[str] = mapped_column(String(64), index=True)
