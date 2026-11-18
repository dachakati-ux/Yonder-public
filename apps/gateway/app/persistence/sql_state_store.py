"""SQLAlchemy-backed control-plane state store for aliases and deployments."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.persistence.db_models import AliasRow, DeploymentRow
from app.routing.models import DeploymentRecord, DeploymentState, ModelAliasRoute, RouteState
from app.routing.registry import RouteRegistry


def _parse_dt(value: datetime | None) -> datetime | None:
    return value


class SqlAlchemyStateStore:
    """Persists alias and deployment rows. Definitions remain YAML-backed."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def has_persisted_state(self) -> bool:
        with self._session_factory() as session:
            has_deployments = session.query(DeploymentRow.id).limit(1).first() is not None
            has_aliases = session.query(AliasRow.alias).limit(1).first() is not None
            return has_deployments or has_aliases

    def load_into(self, routes: RouteRegistry) -> None:
        with self._session_factory() as session:
            for row in session.query(DeploymentRow).all():
                routes.upsert_deployment(
                    DeploymentRecord(
                        id=row.id,
                        model_definition_id=row.model_definition_id,
                        worker_id=row.worker_id,
                        provider=row.provider,
                        status=DeploymentState(row.status),
                        gpu_assignments=list(row.gpu_assignments or []),
                        runtime_configuration=dict(row.runtime_configuration or {}),
                        active_requests=0,
                        queue_depth=0,
                        load_progress_percent=row.load_progress_percent,
                        loaded_at=_parse_dt(row.loaded_at),
                        last_successful_load=_parse_dt(row.last_successful_load),
                        last_error=row.last_error,
                        failure_reason=row.failure_reason,
                        request_count=row.request_count or 0,
                        maintenance=bool(row.maintenance),
                        logs=list(row.logs or []),
                    )
                )
            for row in session.query(AliasRow).all():
                routes.upsert_alias(
                    ModelAliasRoute(
                        alias=row.alias,
                        deployment_id=row.deployment_id,
                        state=RouteState(row.state),
                        priority=row.priority,
                        experimental=bool(row.experimental),
                    )
                )

    def save_from(self, routes: RouteRegistry) -> None:
        with self._session_factory() as session:
            existing_deployments = {
                row.id: row for row in session.query(DeploymentRow).all()
            }
            seen_deployments: set[str] = set()
            for deployment in routes.deployments.values():
                seen_deployments.add(deployment.id)
                payload = _deployment_payload(deployment)
                row = existing_deployments.get(deployment.id)
                if row is None:
                    session.add(DeploymentRow(**payload))
                else:
                    for key, value in payload.items():
                        setattr(row, key, value)

            for deployment_id, row in existing_deployments.items():
                if deployment_id not in seen_deployments:
                    session.delete(row)

            existing_aliases = {row.alias: row for row in session.query(AliasRow).all()}
            seen_aliases: set[str] = set()
            for alias in routes.aliases.values():
                seen_aliases.add(alias.alias)
                payload = _alias_payload(alias)
                row = existing_aliases.get(alias.alias)
                if row is None:
                    session.add(AliasRow(**payload))
                else:
                    for key, value in payload.items():
                        setattr(row, key, value)

            for alias_name, row in existing_aliases.items():
                if alias_name not in seen_aliases:
                    session.delete(row)

            session.commit()


def _deployment_payload(deployment: DeploymentRecord) -> dict[str, Any]:
    data = deployment.model_dump(mode="python")
    data.pop("active_requests", None)
    data.pop("queue_depth", None)
    # In-memory idle clock — not durable on DeploymentRow yet.
    data.pop("last_released_at", None)
    data["status"] = deployment.status.value
    return data


def _alias_payload(alias: ModelAliasRoute) -> dict[str, Any]:
    return {
        "alias": alias.alias,
        "deployment_id": alias.deployment_id,
        "state": alias.state.value,
        "priority": alias.priority,
        "experimental": alias.experimental,
    }
