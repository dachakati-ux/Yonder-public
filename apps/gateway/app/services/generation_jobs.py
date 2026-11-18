"""Generation policy gate and durable job orchestration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from yonder_contracts.generation import (
    GenerationJob,
    GenerationJobStatus,
    GenerationMode,
    GenerationRequest,
)

from app.services.artifacts import LocalArtifactStore

DISALLOWED_TOKENS = ("child sexual", "csam", "make malware")


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    checks: tuple[dict[str, Any], ...]
    reason: str | None = None


def evaluate_generation_policy(
    *,
    prompt: str,
    negative_prompt: str = "",
    mode: str,
) -> PolicyDecision:
    checks: list[dict[str, Any]] = []
    text = f"{prompt}\n{negative_prompt}".lower()
    length_ok = 1 <= len(prompt.strip()) <= 4000
    checks.append({"name": "prompt_length", "passed": length_ok})
    banned = [token for token in DISALLOWED_TOKENS if token in text]
    checks.append(
        {
            "name": "disallowed_token_stub",
            "passed": not banned,
            "matched": banned,
        }
    )
    checks.append(
        {
            "name": "worker_route_gateway_only",
            "passed": True,
            "detail": "Clients may only call Gateway generation APIs",
        }
    )
    checks.append({"name": "mode_known", "passed": mode in {m.value for m in GenerationMode}})
    if not all(bool(item["passed"]) for item in checks):
        return PolicyDecision(allowed=False, checks=tuple(checks), reason="policy_rejected")
    return PolicyDecision(allowed=True, checks=tuple(checks))


class GenerationJobService:
    """Provider-backed job service with optional SQL durability for Studio history."""

    def __init__(
        self,
        *,
        provider: Any,
        artifact_store: LocalArtifactStore,
        artifact_meta: dict[str, dict[str, Any]] | None = None,
        routes: Any | None = None,
        store: Any | None = None,
        ensure_ready: Any | None = None,
    ) -> None:
        self.provider = provider
        self.artifact_store = artifact_store
        self.routes = routes
        self._store = store
        self.ensure_ready = ensure_ready
        self._jobs: dict[str, dict[str, Any]] = {}
        self._artifacts: dict[str, dict[str, Any]] = artifact_meta or {}

    def register_artifact_meta(self, meta: dict[str, Any]) -> None:
        self._artifacts[meta["id"]] = meta
        if self._store is not None:
            payload = dict(meta)
            payload.setdefault("created_at", datetime.now(UTC).isoformat().replace("+00:00", "Z"))
            self._store.upsert_artifact(payload)

    def get_artifact_meta(self, artifact_id: str) -> dict[str, Any] | None:
        cached = self._artifacts.get(artifact_id)
        if cached is not None:
            return cached
        if self._store is None:
            return None
        loaded = self._store.get_artifact(artifact_id)
        if loaded is not None:
            self._artifacts[artifact_id] = loaded
        return loaded

    def _persist_job(self, record: dict[str, Any]) -> None:
        self._jobs[record["id"]] = record
        if self._store is None:
            return
        self._store.upsert_job(record)
        for asset in record.get("assets") or []:
            artifact_id = str(asset.get("artifact_id") or "")
            if not artifact_id:
                continue
            meta = self._artifacts.get(artifact_id)
            if meta is None:
                continue
            linked = dict(meta)
            linked["job_id"] = record["id"]
            self._artifacts[artifact_id] = linked
            self._store.upsert_artifact(linked)

    def _resolve_deployment_id(
        self,
        *,
        model_alias: str,
        deployment_id: str | None,
        mode: GenerationMode,
    ) -> str:
        """Map Studio/Lab aliases onto a ready Diffusers (or mock) deployment."""
        if deployment_id and deployment_id != "yonder-gen-mock":
            return deployment_id
        routes = self.routes
        if routes is not None:
            alias = routes.get_alias(model_alias)
            if alias is not None and alias.deployment_id:
                return alias.deployment_id
            wanted_profile = (
                "video"
                if mode
                in {GenerationMode.TEXT_TO_VIDEO, GenerationMode.IMAGE_TO_VIDEO}
                else "image"
            )
            for deployment in routes.deployments.values():
                if getattr(deployment, "provider", None) != "diffusers":
                    continue
                status = getattr(deployment.status, "value", deployment.status)
                if status not in {"ready", "loaded"}:
                    continue
                runtime = deployment.runtime_configuration or {}
                profile = str(runtime.get("profile") or "").lower()
                if profile == wanted_profile:
                    return deployment.id
        return deployment_id or "yonder-gen-mock"

    def _provider_job_id(self, job: dict[str, Any]) -> str | None:
        params = job.get("params") or {}
        if isinstance(params, dict):
            value = params.get("provider_job_id")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _touch_job(
        self,
        job: dict[str, Any],
        *,
        status: str | None = None,
        progress: list[dict[str, Any]] | None = None,
        error: str | None = None,
        availability: str | None = None,
        deployment_id: str | None = None,
        append_progress: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status is not None:
            job["status"] = status
        if availability is not None:
            job["availability"] = availability
        if deployment_id is not None:
            job["deployment_id"] = deployment_id
        if error is not None:
            job["error"] = error
        if progress is not None:
            job["progress"] = progress
        elif append_progress is not None:
            existing = list(job.get("progress") or [])
            existing.append(append_progress)
            job["progress"] = existing
        job["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self._persist_job(job)
        return job

    async def create_job(
        self,
        *,
        principal_subject: str,
        request_id: str,
        mode: GenerationMode,
        prompt: str,
        negative_prompt: str = "",
        aspect_ratio: str = "1:1",
        style_preset: str | None = None,
        steps: int = 20,
        guidance: float = 7.5,
        strength: float | None = None,
        seed: int | None = None,
        reference_artifact_ids: list[str] | None = None,
        model_alias: str = "yonder-image",
        deployment_id: str = "yonder-gen-mock",
    ) -> dict[str, Any]:
        """Accept a job immediately; EnsureReady + provider run in the background.

        Studio/Chat poll ``get_job`` for ensuring_capacity / load / denoise progress.
        Blocking EnsureReady inside the POST previously hung the UI for minutes
        with no job id to poll.
        """
        deployment_id = self._resolve_deployment_id(
            model_alias=model_alias,
            deployment_id=deployment_id,
            mode=mode,
        )
        policy = evaluate_generation_policy(
            prompt=prompt, negative_prompt=negative_prompt, mode=mode.value
        )
        now = datetime.now(UTC)
        job_id = f"gen-{uuid.uuid4().hex[:12]}"
        stamp = now.isoformat().replace("+00:00", "Z")
        base_params: dict[str, Any] = {
            "aspect_ratio": aspect_ratio,
            "style_preset": style_preset,
            "steps": steps,
            "guidance": guidance,
            "strength": strength,
            "seed": seed,
            "reference_artifact_ids": reference_artifact_ids or [],
            "policy_checks": list(policy.checks),
        }
        if not policy.allowed:
            record = {
                "id": job_id,
                "request_id": request_id,
                "principal_subject": principal_subject,
                "status": GenerationJobStatus.REJECTED.value,
                "mode": mode.value,
                "model_alias": model_alias,
                "deployment_id": deployment_id,
                "provider": getattr(self.provider, "name", "unknown"),
                "availability": "rejected",
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "params": base_params,
                "progress": [
                    {
                        "job_id": job_id,
                        "step": "policy",
                        "message": policy.reason or "rejected",
                        "percent": 100.0,
                    }
                ],
                "assets": [],
                "error": policy.reason,
                "created_at": stamp,
                "updated_at": stamp,
            }
            self._persist_job(record)
            return record

        for artifact_id in reference_artifact_ids or []:
            meta = self.get_artifact_meta(artifact_id)
            if meta is None or meta.get("principal_subject") != principal_subject:
                raise PermissionError(f"reference_artifact_forbidden:{artifact_id}")

        record = {
            "id": job_id,
            "request_id": request_id,
            "principal_subject": principal_subject,
            "status": GenerationJobStatus.QUEUED.value,
            "mode": mode.value,
            "model_alias": model_alias,
            "deployment_id": deployment_id,
            "provider": getattr(self.provider, "name", "unknown"),
            "availability": "queued",
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "params": base_params,
            "progress": [
                {
                    "job_id": job_id,
                    "step": "queued",
                    "message": "Accepted — ensuring capacity then generating",
                    "percent": 1.0,
                }
            ],
            "assets": [],
            "error": None,
            "created_at": stamp,
            "updated_at": stamp,
        }
        self._persist_job(record)
        return record

    async def run_accepted_job(self, job_id: str) -> None:
        """Background continuation after ``create_job`` accepts a queued record."""
        job = self._jobs.get(job_id)
        if job is None and self._store is not None:
            job = self._store.get_job(job_id)
            if job is not None:
                self._jobs[job_id] = job
        if job is None:
            return
        if job.get("status") not in {
            GenerationJobStatus.QUEUED.value,
            GenerationJobStatus.RUNNING.value,
        }:
            return
        params = dict(job.get("params") or {})
        mode = GenerationMode(job["mode"])
        await self._run_job(
            job_id=job_id,
            principal_subject=str(job["principal_subject"]),
            request_id=str(job.get("request_id") or job_id),
            mode=mode,
            prompt=str(job.get("prompt") or ""),
            negative_prompt=str(job.get("negative_prompt") or ""),
            aspect_ratio=str(params.get("aspect_ratio") or "1:1"),
            style_preset=params.get("style_preset"),
            steps=int(params.get("steps") or 20),
            guidance=float(params.get("guidance") or 7.5),
            strength=params.get("strength"),
            seed=params.get("seed"),
            reference_artifact_ids=list(params.get("reference_artifact_ids") or []),
            model_alias=str(job.get("model_alias") or "yonder-image"),
            deployment_id=str(job.get("deployment_id") or "yonder-gen-mock"),
        )

    async def _run_job(
        self,
        *,
        job_id: str,
        principal_subject: str,
        request_id: str,
        mode: GenerationMode,
        prompt: str,
        negative_prompt: str,
        aspect_ratio: str,
        style_preset: str | None,
        steps: int,
        guidance: float,
        strength: float | None,
        seed: int | None,
        reference_artifact_ids: list[str],
        model_alias: str,
        deployment_id: str,
    ) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return

        async def on_ensure_progress(
            step: str, message: str, percent: float | None
        ) -> None:
            current = self._jobs.get(job_id)
            if current is None:
                return
            self._touch_job(
                current,
                status=GenerationJobStatus.RUNNING.value,
                availability="ensuring",
                append_progress={
                    "job_id": job_id,
                    "step": step,
                    "message": message,
                    "percent": percent,
                },
            )

        provider_name = str(getattr(self.provider, "name", "unknown"))
        if self.ensure_ready is not None and provider_name == "diffusers":
            from app.services.ensure_ready import EnsureReadyError

            try:
                ensured = await self.ensure_ready.ensure(
                    model_alias=model_alias,
                    mode=mode,
                    principal_subject=principal_subject,
                    request_id=request_id,
                    on_progress=on_ensure_progress,
                )
                deployment_id = ensured.deployment_id
                current = self._jobs.get(job_id)
                if current is not None:
                    self._touch_job(current, deployment_id=deployment_id)
            except EnsureReadyError as exc:
                current = self._jobs.get(job_id)
                if current is None:
                    return
                params = dict(current.get("params") or {})
                params["ensure_error_code"] = exc.code.value
                current["params"] = params
                self._touch_job(
                    current,
                    status=GenerationJobStatus.FAILED.value,
                    availability="collection_failed",
                    error=exc.message,
                    append_progress={
                        "job_id": job_id,
                        "step": "ensuring_capacity",
                        "message": exc.message,
                        "percent": 100.0,
                    },
                )
                return

        deployment_id = self._resolve_deployment_id(
            model_alias=model_alias,
            deployment_id=deployment_id,
            mode=mode,
        )
        current = self._jobs.get(job_id)
        if current is not None:
            self._touch_job(
                current,
                status=GenerationJobStatus.RUNNING.value,
                availability="running",
                deployment_id=deployment_id,
                append_progress={
                    "job_id": job_id,
                    "step": "submitting",
                    "message": f"Submitting to provider on {deployment_id}",
                    "percent": 50.0,
                },
            )

        gen_request = GenerationRequest(
            request_id=request_id,
            model_alias=model_alias,
            deployment_id=deployment_id,
            mode=mode,
            prompt=prompt,
            negative_prompt=negative_prompt,
            aspect_ratio=aspect_ratio,
            style_preset=style_preset,
            steps=steps,
            guidance=guidance,
            strength=strength,
            seed=seed,
            reference_artifact_ids=tuple(reference_artifact_ids),
        )
        self._bind_provider_route("", deployment_id)
        try:
            provider_job: GenerationJob = await self.provider.create_job(gen_request)
            self._bind_provider_route(provider_job.job_id, deployment_id)
        except Exception as exc:  # noqa: BLE001
            error_message = str(exc)
            response = getattr(exc, "response", None)
            if response is not None:
                try:
                    detail = response.json()
                    if isinstance(detail, dict) and detail.get("detail") is not None:
                        error_message = str(detail["detail"])
                    else:
                        error_message = f"{response.status_code}: {response.text[:300]}"
                except Exception:  # noqa: BLE001
                    error_message = f"{getattr(response, 'status_code', '?')}: {exc}"
            current = self._jobs.get(job_id)
            if current is None:
                return
            self._touch_job(
                current,
                status=GenerationJobStatus.FAILED.value,
                availability="collection_failed",
                error=error_message,
                append_progress={
                    "job_id": job_id,
                    "step": "provider",
                    "message": error_message,
                    "percent": 100.0,
                },
            )
            return

        current = self._jobs.get(job_id)
        if current is None:
            return
        params = dict(current.get("params") or {})
        params["provider_job_id"] = provider_job.job_id
        ensure_progress = [
            item
            for item in (current.get("progress") or [])
            if item.get("step")
            not in {"submitting"}
        ]
        current["params"] = params
        current["provider"] = provider_job.provider
        current["deployment_id"] = deployment_id
        current["status"] = provider_job.status.value
        current["availability"] = provider_job.availability
        current["progress"] = [
            *ensure_progress,
            *[
                {
                    "job_id": job_id,
                    "step": item.step,
                    "message": item.message,
                    "percent": item.percent,
                }
                for item in provider_job.progress
            ],
        ]
        current["assets"] = [
            {
                "artifact_id": asset.artifact_id,
                "kind": asset.kind.value,
                "mime_type": asset.mime_type,
                "url": f"/v1/generation/artifacts/{asset.artifact_id}",
            }
            for asset in provider_job.assets
        ]
        current["error"] = provider_job.error
        current["updated_at"] = provider_job.updated_at or datetime.now(UTC).isoformat().replace(
            "+00:00", "Z"
        )
        self._persist_job(current)

    async def _ingest_worker_assets(
        self,
        *,
        job: dict[str, Any],
        provider_job: GenerationJob,
        principal_subject: str,
    ) -> list[dict[str, Any]]:
        """Fetch worker artifacts into Gateway storage; clients never see worker paths."""
        fetch = getattr(self.provider, "fetch_artifact_bytes", None)
        assets: list[dict[str, Any]] = []
        for asset in provider_job.assets:
            gateway_artifact_id = asset.artifact_id
            mime_type = asset.mime_type
            if callable(fetch) and not str(asset.artifact_id).startswith("art-"):
                data, mime_type, filename = await fetch(
                    deployment_id=job["deployment_id"],
                    artifact_id=asset.artifact_id,
                )
                stored = self.artifact_store.put_stream(
                    chunks=[data],
                    filename=filename or f"{asset.artifact_id}.bin",
                    mime_type=mime_type or asset.mime_type,
                    kind="generated",
                )
                gateway_artifact_id = stored.artifact_id
                self.register_artifact_meta(
                    {
                        "id": stored.artifact_id,
                        "job_id": job["id"],
                        "filename": stored.filename,
                        "mime_type": stored.mime_type,
                        "byte_size": stored.byte_size,
                        "sha256": stored.sha256,
                        "storage_key": stored.storage_key,
                        "kind": "generated",
                        "principal_subject": principal_subject,
                    }
                )
            assets.append(
                {
                    "artifact_id": gateway_artifact_id,
                    "kind": asset.kind.value,
                    "mime_type": mime_type,
                    "url": f"/v1/generation/artifacts/{gateway_artifact_id}",
                }
            )
        return assets

    def _worker_base_url_for_deployment(self, deployment_id: str) -> str | None:
        if self.routes is None:
            return None
        deployment = self.routes.deployments.get(deployment_id)
        if deployment is None:
            return None
        runtime = deployment.runtime_configuration or {}
        url = runtime.get("base_url")
        if isinstance(url, str) and url.strip():
            return url.strip()
        return None

    def _bind_provider_route(self, job_id: str, deployment_id: str) -> None:
        bind = getattr(self.provider, "bind_job_route", None)
        if not callable(bind) or not deployment_id:
            return
        bind(
            job_id,
            deployment_id=deployment_id,
            worker_base_url=self._worker_base_url_for_deployment(deployment_id),
        )

    async def get_job(
        self, job_id: str, *, principal_subject: str
    ) -> dict[str, Any] | None:
        job = self._jobs.get(job_id)
        if job is None and self._store is not None:
            loaded = self._store.get_job(job_id)
            if loaded is not None:
                self._jobs[job_id] = loaded
                job = loaded
        if job is None:
            return None
        if job["principal_subject"] != principal_subject:
            raise PermissionError("job_forbidden")
        if job["status"] in {
            GenerationJobStatus.QUEUED.value,
            GenerationJobStatus.RUNNING.value,
        }:
            provider_job_id = self._provider_job_id(job)
            if not provider_job_id:
                # Still ensuring capacity / waiting for provider accept — no poll yet.
                return job
            self._bind_provider_route(
                provider_job_id, str(job.get("deployment_id") or "")
            )
            try:
                provider_job: GenerationJob = await self.provider.get_job(
                    provider_job_id
                )
            except KeyError:
                stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                job["status"] = GenerationJobStatus.FAILED.value
                job["availability"] = "collection_failed"
                job["error"] = (
                    "Worker no longer has this generation job "
                    "(worker restart or wrong worker endpoint)."
                )
                job["progress"] = [
                    {
                        "job_id": job_id,
                        "step": "lost",
                        "message": job["error"],
                        "percent": 100.0,
                    }
                ]
                job["updated_at"] = stamp
                self._persist_job(job)
                return job
            except Exception as exc:  # noqa: BLE001
                stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                job["status"] = GenerationJobStatus.FAILED.value
                job["availability"] = "collection_failed"
                job["error"] = f"{type(exc).__name__}: {exc}"
                job["progress"] = [
                    {
                        "job_id": job_id,
                        "step": "provider",
                        "message": job["error"],
                        "percent": 100.0,
                    }
                ]
                job["updated_at"] = stamp
                self._persist_job(job)
                return job
            ensure_progress = [
                item
                for item in (job.get("progress") or [])
                if item.get("step")
                in {
                    "queued",
                    "ensuring_capacity",
                    "unloading",
                    "demote",
                    "loading",
                    "ready",
                    "submitting",
                }
            ]
            job["status"] = provider_job.status.value
            job["availability"] = provider_job.availability
            job["progress"] = [
                *ensure_progress,
                *[
                    {
                        "job_id": job_id,
                        "step": item.step,
                        "message": item.message,
                        "percent": item.percent,
                    }
                    for item in provider_job.progress
                ],
            ]
            if provider_job.status == GenerationJobStatus.SUCCEEDED:
                job["assets"] = await self._ingest_worker_assets(
                    job=job,
                    provider_job=provider_job,
                    principal_subject=principal_subject,
                )
            else:
                job["assets"] = [
                    {
                        "artifact_id": asset.artifact_id,
                        "kind": asset.kind.value,
                        "mime_type": asset.mime_type,
                        "url": f"/v1/generation/artifacts/{asset.artifact_id}",
                    }
                    for asset in provider_job.assets
                ]
            job["error"] = provider_job.error
            job["updated_at"] = provider_job.updated_at or datetime.now(UTC).isoformat().replace(
                "+00:00", "Z"
            )
            self._persist_job(job)
        return job

    async def cancel_job(self, job_id: str, *, principal_subject: str) -> dict[str, Any]:
        job = await self.get_job(job_id, principal_subject=principal_subject)
        if job is None:
            raise KeyError(job_id)
        provider_job_id = self._provider_job_id(job)
        if provider_job_id:
            try:
                cancelled = await self.provider.cancel_job(provider_job_id)
                job["status"] = cancelled.status.value
                job["updated_at"] = cancelled.updated_at or datetime.now(
                    UTC
                ).isoformat().replace("+00:00", "Z")
            except Exception:  # noqa: BLE001
                job["status"] = GenerationJobStatus.CANCELLED.value
                job["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        else:
            job["status"] = GenerationJobStatus.CANCELLED.value
            job["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            job["error"] = job.get("error") or "Cancelled before provider submit"
        self._persist_job(job)
        return job

    async def delete_job(self, job_id: str, *, principal_subject: str) -> None:
        job = self._jobs.get(job_id)
        if job is None and self._store is not None:
            job = self._store.get_job(job_id)
            if job is not None:
                self._jobs[job_id] = job
        if job is None:
            raise KeyError(job_id)
        if job["principal_subject"] != principal_subject:
            raise PermissionError("job_forbidden")
        # Best-effort cancel if still open.
        if job.get("status") in {
            GenerationJobStatus.QUEUED.value,
            GenerationJobStatus.RUNNING.value,
        }:
            provider_job_id = self._provider_job_id(job)
            if provider_job_id:
                try:
                    await self.provider.cancel_job(provider_job_id)
                except Exception:  # noqa: BLE001
                    pass
        artifact_ids = [
            str(asset.get("artifact_id") or "")
            for asset in (job.get("assets") or [])
            if asset.get("artifact_id")
        ]
        artifact_metas: list[dict[str, Any]] = []
        for artifact_id in artifact_ids:
            meta = self._artifacts.get(artifact_id)
            if meta is None and self._store is not None:
                meta = self._store.get_artifact(artifact_id)
            if meta is not None:
                artifact_metas.append(meta)
        self._jobs.pop(job_id, None)
        if self._store is not None and hasattr(self._store, "delete_job"):
            self._store.delete_job(job_id)
        for meta in artifact_metas:
            artifact_id = str(meta.get("id") or "")
            self._artifacts.pop(artifact_id, None)
            storage_key = meta.get("storage_key")
            if storage_key:
                try:
                    self.artifact_store.delete(storage_key)
                except Exception:  # noqa: BLE001
                    pass
            if self._store is not None and hasattr(self._store, "delete_artifact"):
                self._store.delete_artifact(artifact_id)

    async def list_jobs(
        self, *, principal_subject: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        if self._store is not None:
            items = self._store.list_jobs(principal_subject=principal_subject, limit=limit)
            for job in items:
                self._jobs[job["id"]] = job
        else:
            items = [
                job
                for job in self._jobs.values()
                if job["principal_subject"] == principal_subject
            ]
            items.sort(key=lambda item: item["created_at"], reverse=True)
            items = items[:limit]
        refreshed: list[dict[str, Any]] = []
        for job in items:
            if job.get("status") in {
                GenerationJobStatus.QUEUED.value,
                GenerationJobStatus.RUNNING.value,
            }:
                try:
                    latest = await self.get_job(
                        job["id"], principal_subject=principal_subject
                    )
                    refreshed.append(latest or job)
                except PermissionError:
                    continue
            else:
                refreshed.append(job)
        return refreshed
