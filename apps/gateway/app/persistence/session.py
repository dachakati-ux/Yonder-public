"""SQLAlchemy engine/session helpers for gateway persistence."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.db_models import Base


def normalize_database_url(url: str) -> str:
    """Ensure SQLite URLs create parent directories when file-backed."""

    if url.startswith("sqlite:///"):
        path_part = url.removeprefix("sqlite:///")
        if path_part and path_part != ":memory:" and not path_part.startswith("/"):
            Path(path_part).parent.mkdir(parents=True, exist_ok=True)
        elif path_part.startswith("/") or (len(path_part) > 2 and path_part[1] == ":"):
            Path(path_part).parent.mkdir(parents=True, exist_ok=True)
    return url


def create_db_engine(database_url: str) -> Engine:
    url = normalize_database_url(database_url)
    connect_args: dict = {}
    if url.startswith("sqlite:"):
        connect_args["check_same_thread"] = False
    engine = create_engine(url, future=True, connect_args=connect_args)
    if url.startswith("sqlite:"):

        @event.listens_for(engine, "connect")
        def _sqlite_fk(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def ensure_schema(engine: Engine) -> None:
    """Create tables if missing (dev convenience; prefer Alembic in lab/prod)."""

    Base.metadata.create_all(engine)
