"""SQLAlchemy-backed durable coder workspaces and runs."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.db_models import CoderRunRow, CoderWorkspaceRow
from app.services.coder_runs import CoderRun
from app.services.coder_workspace import WorkspaceGrant


class SqlAlchemyCoderStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    # --- workspaces ---

    def list_workspace_rows(self, principal_subject: str) -> list[WorkspaceGrant]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(CoderWorkspaceRow).where(
                    CoderWorkspaceRow.principal_subject == principal_subject
                )
            ).all()
        return [_workspace_from_row(r) for r in rows]

    def list_all_workspaces(self) -> list[WorkspaceGrant]:
        with self._session_factory() as session:
            rows = session.scalars(select(CoderWorkspaceRow)).all()
        return [_workspace_from_row(r) for r in rows]

    def save_workspace(self, grant: WorkspaceGrant) -> None:
        with self._session_factory() as session:
            row = session.get(CoderWorkspaceRow, grant.id)
            if row is None:
                row = CoderWorkspaceRow(id=grant.id)
                session.add(row)
            row.principal_subject = grant.subject
            row.kind = grant.kind
            row.root = str(grant.root)
            row.label = grant.label
            row.source = grant.source
            row.git_head = grant.git_head
            row.remote_url = grant.remote_url
            row.created_at = grant.created_at
            session.commit()

    def delete_workspace(self, workspace_id: str) -> None:
        with self._session_factory() as session:
            row = session.get(CoderWorkspaceRow, workspace_id)
            if row is not None:
                session.delete(row)
                session.commit()

    # --- runs ---

    def list_runs(self, principal_subject: str) -> list[CoderRun]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(CoderRunRow).where(CoderRunRow.principal_subject == principal_subject)
            ).all()
        items = [_run_from_row(r) for r in rows]
        return sorted(items, key=lambda r: r.updated_at, reverse=True)

    def list_active_runs(self) -> list[CoderRun]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(CoderRunRow).where(CoderRunRow.status == "active")
            ).all()
        return [_run_from_row(r) for r in rows]

    def get(self, run_id: str, principal_subject: str) -> CoderRun | None:
        with self._session_factory() as session:
            row = session.get(CoderRunRow, run_id)
            if row is None or row.principal_subject != principal_subject:
                return None
            return _run_from_row(row)

    def save(self, run: CoderRun) -> CoderRun:
        with self._session_factory() as session:
            row = session.get(CoderRunRow, run.id)
            if row is None:
                row = CoderRunRow(id=run.id)
                session.add(row)
            row.principal_subject = run.principal_subject
            row.workspace_id = run.workspace_id
            row.task = run.task
            row.stage = run.stage
            row.status = run.status
            row.check_command = run.check_command
            row.artifacts = dict(run.artifacts or {})
            row.timeline = list(run.timeline or [])
            row.verdicts = list(run.verdicts or [])
            row.push_result = run.push_result
            row.error = run.error
            row.created_at = run.created_at
            row.updated_at = run.updated_at
            session.commit()
        return run


def _workspace_from_row(row: CoderWorkspaceRow) -> WorkspaceGrant:
    return WorkspaceGrant(
        id=row.id,
        subject=row.principal_subject,
        kind=row.kind,
        root=Path(row.root),
        created_at=row.created_at,
        label=row.label or "",
        source=row.source or "",
        git_head=row.git_head or "",
        remote_url=row.remote_url or "",
    )


def _run_from_row(row: CoderRunRow) -> CoderRun:
    return CoderRun(
        id=row.id,
        principal_subject=row.principal_subject,
        workspace_id=row.workspace_id,
        task=row.task,
        stage=row.stage,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
        check_command=row.check_command or "python -m pytest",
        artifacts=dict(row.artifacts or {}),
        timeline=list(row.timeline or []),
        verdicts=list(row.verdicts or []),
        push_result=row.push_result,
        error=row.error,
    )
