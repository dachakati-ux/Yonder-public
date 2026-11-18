"""SQLAlchemy durability for Studio generation jobs and artifact metadata."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.db_models import GenerationArtifactRow, GenerationJobRow


def _parse_dt(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not value:
        return datetime.now(UTC)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _iso(value: str | datetime | None) -> str:
    return _parse_dt(value).astimezone(UTC).isoformat().replace("+00:00", "Z")


class SqlAlchemyGenerationStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def upsert_job(self, job: dict[str, Any]) -> None:
        with self._session_factory() as session:
            row = session.get(GenerationJobRow, job["id"])
            if row is None:
                row = GenerationJobRow(id=job["id"])
                session.add(row)
            row.request_id = str(job.get("request_id") or "")
            row.principal_subject = str(job["principal_subject"])
            row.status = str(job["status"])
            row.mode = str(job["mode"])
            row.model_alias = str(job.get("model_alias") or "")
            row.deployment_id = str(job.get("deployment_id") or "")
            row.provider = str(job.get("provider") or "")
            row.availability = str(job.get("availability") or "")
            row.prompt = str(job.get("prompt") or "")
            row.negative_prompt = str(job.get("negative_prompt") or "")
            row.params = dict(job.get("params") or {})
            row.progress = list(job.get("progress") or [])
            row.error = job.get("error")
            row.created_at = _parse_dt(job.get("created_at"))
            row.updated_at = _parse_dt(job.get("updated_at"))
            session.commit()

    def upsert_artifact(self, meta: dict[str, Any]) -> None:
        with self._session_factory() as session:
            artifact_id = str(meta["id"])
            row = session.get(GenerationArtifactRow, artifact_id)
            if row is None:
                row = GenerationArtifactRow(id=artifact_id)
                session.add(row)
            row.job_id = meta.get("job_id")
            row.principal_subject = str(meta["principal_subject"])
            row.kind = str(meta.get("kind") or "reference")
            row.mime_type = str(meta.get("mime_type") or "application/octet-stream")
            row.filename = str(meta.get("filename") or artifact_id)
            row.byte_size = int(meta.get("byte_size") or 0)
            row.sha256 = str(meta.get("sha256") or "")
            row.storage_key = str(meta.get("storage_key") or "")
            row.width = meta.get("width")
            row.height = meta.get("height")
            row.duration_seconds = meta.get("duration_seconds")
            row.created_at = _parse_dt(meta.get("created_at"))
            session.commit()

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(GenerationArtifactRow, artifact_id)
            if row is None:
                return None
            return self._artifact_meta(row)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(GenerationJobRow, job_id)
            if row is None:
                return None
            assets = session.scalars(
                select(GenerationArtifactRow).where(GenerationArtifactRow.job_id == job_id)
            ).all()
            return self._job_record(row, list(assets))

    def list_jobs(self, *, principal_subject: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(GenerationJobRow)
                .where(GenerationJobRow.principal_subject == principal_subject)
                .order_by(GenerationJobRow.created_at.desc())
                .limit(limit)
            ).all()
            job_ids = [row.id for row in rows]
            assets_by_job: dict[str, list[GenerationArtifactRow]] = {
                job_id: [] for job_id in job_ids
            }
            if job_ids:
                for asset in session.scalars(
                    select(GenerationArtifactRow).where(
                        GenerationArtifactRow.job_id.in_(job_ids)
                    )
                ).all():
                    if asset.job_id:
                        assets_by_job.setdefault(asset.job_id, []).append(asset)
            return [
                self._job_record(row, assets_by_job.get(row.id, []))
                for row in rows
            ]

    def delete_job(self, job_id: str) -> None:
        with self._session_factory() as session:
            for asset in session.scalars(
                select(GenerationArtifactRow).where(GenerationArtifactRow.job_id == job_id)
            ).all():
                session.delete(asset)
            row = session.get(GenerationJobRow, job_id)
            if row is not None:
                session.delete(row)
            session.commit()

    def delete_artifact(self, artifact_id: str) -> None:
        with self._session_factory() as session:
            row = session.get(GenerationArtifactRow, artifact_id)
            if row is not None:
                session.delete(row)
                session.commit()

    def _job_record(
        self,
        row: GenerationJobRow,
        assets: list[GenerationArtifactRow],
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "request_id": row.request_id,
            "principal_subject": row.principal_subject,
            "status": row.status,
            "mode": row.mode,
            "model_alias": row.model_alias,
            "deployment_id": row.deployment_id,
            "provider": row.provider,
            "availability": row.availability,
            "prompt": row.prompt,
            "negative_prompt": row.negative_prompt,
            "params": dict(row.params or {}),
            "progress": list(row.progress or []),
            "assets": [
                {
                    "artifact_id": asset.id,
                    "kind": asset.kind,
                    "mime_type": asset.mime_type,
                    "url": f"/v1/generation/artifacts/{asset.id}",
                }
                for asset in assets
            ],
            "error": row.error,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }

    def _artifact_meta(self, row: GenerationArtifactRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "job_id": row.job_id,
            "principal_subject": row.principal_subject,
            "kind": row.kind,
            "mime_type": row.mime_type,
            "filename": row.filename,
            "byte_size": row.byte_size,
            "sha256": row.sha256,
            "storage_key": row.storage_key,
            "width": row.width,
            "height": row.height,
            "duration_seconds": row.duration_seconds,
            "url": f"/v1/generation/artifacts/{row.id}",
            "created_at": _iso(row.created_at),
        }
