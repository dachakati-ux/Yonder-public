"""Factories for gateway persistence backends."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from yonder_configuration.models import YonderSettings

from app.persistence.event_store import DurableRequestLedger, EventStore
from app.persistence.session import create_db_engine, create_session_factory, ensure_schema
from app.persistence.sql_challenge_store import SqlAlchemyChallengeStore
from app.persistence.sql_coder_store import SqlAlchemyCoderStore
from app.persistence.sql_conversation_store import SqlAlchemyConversationStore
from app.persistence.sql_corpus_quarantine_store import SqlAlchemyCorpusQuarantineStore
from app.persistence.sql_mission_progress_store import SqlAlchemyMissionProgressStore
from app.persistence.sql_progress_store import SqlAlchemyProgressStore
from app.persistence.sql_state_store import SqlAlchemyStateStore
from app.persistence.sql_workbench_store import SqlAlchemyWorkbenchStore
from app.persistence.state_store import JsonFileStateStore, StateStore
from app.services.coder_runs import InMemoryCoderRunStore
from app.services.cohorts import InMemoryChallengeStore
from app.services.instructor import CorpusQuarantineStore
from app.services.memory_store import InMemoryConversationStore
from app.services.mission_progress import InMemoryMissionProgressStore
from app.services.progress import InMemoryProgressStore
from app.services.workbench import InMemoryWorkbenchStore


class PersistenceBundle:
    def __init__(
        self,
        *,
        state_store: StateStore,
        conversations: Any,
        progress: Any,
        mission_progress: Any,
        challenges: Any,
        workbench: Any,
        corpus_quarantine: Any,
        backend: str,
        session_factory: sessionmaker[Session] | None = None,
        database_url: str | None = None,
        events: EventStore | None = None,
        request_ledger: DurableRequestLedger | None = None,
        coder_store: Any | None = None,
        coder_runs: Any | None = None,
    ) -> None:
        self.state_store = state_store
        self.conversations = conversations
        self.progress = progress
        self.mission_progress = mission_progress
        self.challenges = challenges
        self.workbench = workbench
        self.corpus_quarantine = corpus_quarantine
        self.backend = backend
        self.session_factory = session_factory
        self.database_url = database_url
        self.events = events or EventStore(limit=500)
        self.request_ledger = request_ledger or DurableRequestLedger(limit=200)
        self.coder_store = coder_store
        self.coder_runs = coder_runs or InMemoryCoderRunStore()


def build_persistence(settings: YonderSettings) -> PersistenceBundle:
    """Choose JSON or SQL backends based on database_url."""

    retention = settings.conversations
    if settings.database_url:
        engine = create_db_engine(settings.database_url)
        ensure_schema(engine)
        session_factory = create_session_factory(engine)
        state_store: StateStore = SqlAlchemyStateStore(session_factory)
        # Bootstrap from JSON state file when the DB is empty.
        if not state_store.has_persisted_state():
            json_store = JsonFileStateStore(Path(settings.state_file))
            if json_store.has_persisted_state():
                from app.routing.registry import RouteRegistry

                bootstrap = RouteRegistry()
                json_store.load_into(bootstrap)
                state_store.save_from(bootstrap)
        conversations = SqlAlchemyConversationStore(session_factory, retention)
        progress = SqlAlchemyProgressStore(session_factory)
        coder_store = SqlAlchemyCoderStore(session_factory)
        return PersistenceBundle(
            state_store=state_store,
            conversations=conversations,
            progress=progress,
            mission_progress=SqlAlchemyMissionProgressStore(session_factory),
            challenges=SqlAlchemyChallengeStore(session_factory),
            workbench=SqlAlchemyWorkbenchStore(session_factory),
            corpus_quarantine=SqlAlchemyCorpusQuarantineStore(session_factory),
            backend="sqlalchemy",
            session_factory=session_factory,
            database_url=settings.database_url,
            events=EventStore(
                limit=settings.admin_event_limit, session_factory=session_factory
            ),
            request_ledger=DurableRequestLedger(
                limit=settings.request_ledger_limit, session_factory=session_factory
            ),
            coder_store=coder_store,
            coder_runs=coder_store,
        )

    return PersistenceBundle(
        state_store=JsonFileStateStore(Path(settings.state_file)),
        conversations=InMemoryConversationStore(retention=retention),
        progress=InMemoryProgressStore(),
        mission_progress=InMemoryMissionProgressStore(),
        challenges=InMemoryChallengeStore(),
        workbench=InMemoryWorkbenchStore(),
        corpus_quarantine=CorpusQuarantineStore(),
        backend="json_file",
        events=EventStore(limit=settings.admin_event_limit),
        request_ledger=DurableRequestLedger(limit=settings.request_ledger_limit),
        coder_store=None,
        coder_runs=InMemoryCoderRunStore(),
    )
