"""Deployment lifecycle orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yonder_contracts.errors import ErrorCode
from yonder_contracts.inference import DeploymentSpec
from yonder_contracts.placement import PlacementMode, VramFitStatus
from yonder_telemetry import log_event

from app.models.manifest import ModelManifest, validate_manifest
from app.routing.models import (
    BUSY_LIFECYCLE_STATES,
    DeploymentRecord,
    DeploymentState,
    ModelAliasRoute,
    ModelDefinitionRecord,
    RouteState,
)
from app.routing.registry import RouteRegistry
from app.services.placement import (
    build_placement,
    deployment_id_for_placement,
    evaluate_placement,
    placement_error_code,
    resolve_context_length,
)

logger = logging.getLogger("yonder.gateway.lifecycle")


class LifecycleError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _now() -> datetime:
    return datetime.now(UTC)


def _worker_notify_detail(result: dict[str, Any]) -> str:
    """Surface worker rejection text so operators see path/binary failures."""
    body = result.get("body")
    if isinstance(body, str) and body.strip():
        text = body.strip()
        if text.startswith("{") and '"detail"' in text:
            try:
                parsed = json.loads(text)
                detail = parsed.get("detail")
                if detail is not None:
                    return str(detail)
            except Exception:  # noqa: BLE001
                pass
        return text[:300]
    error = result.get("error")
    if error:
        return str(error)[:300]
    reason = result.get("reason")
    return str(reason) if reason else ""


def _artifact_bytes(path: str | None) -> int | None:
    if not path:
        return None
    try:
        return int(Path(path).stat().st_size)
    except OSError:
        return None


class LifecycleService:
    def __init__(
        self,
        *,
        routes: RouteRegistry,
        providers: Any,
        definitions: dict[str, ModelDefinitionRecord],
        audit: Callable[[dict[str, Any]], None] | None = None,
        on_state_changed: Callable[[], None] | None = None,
        workers: dict[str, Any] | None = None,
        metrics: Any | None = None,
        max_lanes_per_gpu: int = 3,
        capacity_events: Any | None = None,
    ) -> None:
        self.routes = routes
        self.providers = providers
        self.definitions = definitions
        self._audit = audit or (lambda _event: None)
        self._on_state_changed = on_state_changed
        self._workers = workers if workers is not None else {}
        self._metrics = metrics
        self._max_lanes_per_gpu = max_lanes_per_gpu
        self._capacity_events = capacity_events
        self._locks: dict[str, asyncio.Lock] = {}
        self._cancel_queued: set[str] = set()

    def _emit_capacity(
        self,
        *,
        event_type: str,
        deployment: DeploymentRecord | None = None,
        reason: str | None = None,
        request_id: str | None = None,
        principal_subject: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        store = self._capacity_events
        if store is None or not hasattr(store, "append"):
            return
        store.append(
            {
                "event_type": event_type,
                "model_definition_id": (
                    deployment.model_definition_id if deployment else None
                ),
                "deployment_id": deployment.id if deployment else None,
                "worker_id": deployment.worker_id if deployment else None,
                "gpu_indexes": list(deployment.gpu_assignments) if deployment else [],
                "reason": reason,
                "request_id": request_id,
                "principal_subject": principal_subject,
                "details": details or {},
            }
        )
    def _record_load_telemetry(
        self,
        *,
        deployment: DeploymentRecord,
        outcome: str,
        duration_ms: int,
        started_at: str,
        finished_at: str,
        model_path: str | None,
        artifact_bytes: int | None,
        worker_duration_ms: int | None,
        request_id: str | None,
    ) -> None:
        worker_id = deployment.worker_id or "unknown"
        provider = deployment.provider or "unknown"
        self._append_log(
            deployment,
            (
                f"Load timing: outcome={outcome} duration_ms={duration_ms}"
                f" started_at={started_at} finished_at={finished_at}"
                f" worker_id={worker_id}"
                + (
                    f" worker_duration_ms={worker_duration_ms}"
                    if worker_duration_ms is not None
                    else ""
                )
                + (
                    f" artifact_bytes={artifact_bytes}"
                    if artifact_bytes is not None
                    else " artifact_bytes=not_reported"
                )
                + (f" model_path={model_path}" if model_path else "")
            ),
        )
        log_event(
            logger,
            "deployment.load.completed",
            level=logging.INFO if outcome == "success" else logging.WARNING,
            request_id=request_id,
            deployment_id=deployment.id,
            model_definition_id=deployment.model_definition_id,
            worker_id=worker_id,
            provider=provider,
            duration_ms=duration_ms,
            artifact_bytes=artifact_bytes,
            model_path=model_path,
            outcome=outcome,
            status=outcome,
            gpu_assignments=list(deployment.gpu_assignments or []),
            worker_duration_ms=worker_duration_ms,
        )
        observe = getattr(self._metrics, "observe_model_load", None)
        if callable(observe):
            observe(
                worker_id=worker_id,
                provider=provider,
                outcome=outcome,
                duration_seconds=duration_ms / 1000.0,
            )

    def _persist(self) -> None:
        if self._on_state_changed:
            self._on_state_changed()

    async def rehydrate_ready_deployments(self) -> dict[str, str]:
        """Re-attach persisted READY deployments into in-memory providers.

        Durable state survives gateway restart, but provider adapters do not.
        Without this step a deployment can remain READY in the route store while
        chat/embed/rerank fail with "Deployment is not loaded in … provider".
        This only re-binds existing runtimes (static sidecar or recorded
        worker base_url); it never asks a worker to spawn a new process.
        """
        outcomes: dict[str, str] = {}
        for deployment in list(self.routes.deployments.values()):
            if deployment.status != DeploymentState.READY:
                continue
            definition = self.definitions.get(deployment.model_definition_id)
            if definition is None:
                self._mark_rehydrate_failed(
                    deployment,
                    "Missing model definition during provider rehydrate",
                )
                outcomes[deployment.id] = "failed_missing_definition"
                continue
            try:
                manifest = ModelManifest.model_validate(definition.manifest)
                runtime_cfg = dict(deployment.runtime_configuration or {})
                # Prefer a previously recorded worker endpoint; otherwise the
                # provider falls back to its static operator-managed base_url.
                worker_endpoint = runtime_cfg.get("base_url")
                if isinstance(worker_endpoint, str):
                    worker_endpoint = worker_endpoint.strip() or None
                else:
                    worker_endpoint = None
                provider = self.providers.require_any(deployment.provider)
                load = getattr(provider, "load", None)
                if not callable(load):
                    # Some mock lanes serve without an explicit load() registry.
                    outcomes[deployment.id] = "skipped_no_load"
                    continue
                await load(
                    DeploymentSpec(
                        deployment_id=deployment.id,
                        model_definition_id=deployment.model_definition_id,
                        model_identifier=manifest.runtime.model_identifier,
                        runtime_configuration=runtime_cfg,
                        gpu_assignments=list(deployment.gpu_assignments or []),
                        placement=runtime_cfg.get("placement"),
                        worker_endpoint=worker_endpoint,
                    )
                )
                self._append_log(
                    deployment,
                    "Rehydrated provider attachment after gateway restart",
                )
                self.routes.upsert_deployment(deployment)
                outcomes[deployment.id] = "rehydrated"
            except Exception as exc:  # noqa: BLE001 — fail closed per deployment
                self._mark_rehydrate_failed(
                    deployment,
                    f"Provider rehydrate failed: {exc}",
                )
                outcomes[deployment.id] = "failed"
        if outcomes:
            self._persist()
            self._audit(
                {
                    "event_type": "deployment.rehydrate",
                    "resource_type": "gateway",
                    "resource_id": "startup",
                    "result": "completed",
                    "metadata": {"outcomes": outcomes},
                }
            )
        return outcomes

    def _mark_rehydrate_failed(
        self, deployment: DeploymentRecord, reason: str
    ) -> None:
        deployment.status = DeploymentState.UNAVAILABLE
        deployment.failure_reason = reason
        deployment.last_error = reason
        deployment.load_progress_percent = None
        self._append_log(deployment, reason)
        for alias in self.routes.aliases.values():
            if (
                alias.deployment_id == deployment.id
                and alias.state == RouteState.ENABLED
            ):
                alias.state = RouteState.UNAVAILABLE
        self.routes.upsert_deployment(deployment)

    async def _notify_worker(
        self,
        *,
        operation: str,
        deployment: DeploymentRecord,
        authoritative: bool = False,
    ) -> dict[str, Any]:
        from app.services.worker_lifecycle import notify_worker_lifecycle

        result = await notify_worker_lifecycle(
            workers=self._workers,
            worker_id=deployment.worker_id,
            operation=operation,
            deployment_id=deployment.id,
            model_definition_id=deployment.model_definition_id,
            runtime_configuration=dict(deployment.runtime_configuration or {}),
            gpu_assignments=list(deployment.gpu_assignments or []),
        )
        if result.get("status") == "acknowledged":
            self._append_log(
                deployment, f"Worker '{deployment.worker_id}' acknowledged {operation}"
            )
        elif result.get("status") == "skipped":
            self._append_log(
                deployment,
                f"Worker {operation} skipped ({result.get('reason')})",
            )
            if authoritative:
                raise LifecycleError(
                    ErrorCode.WORKER_UNAVAILABLE,
                    f"Authoritative worker {operation} skipped.",
                    details=result,
                )
        else:
            self._append_log(
                deployment,
                f"Worker {operation} notify failed ({result.get('reason')})",
            )
            if authoritative:
                detail = _worker_notify_detail(result)
                raise LifecycleError(
                    ErrorCode.WORKER_UNAVAILABLE,
                    (
                        f"Authoritative worker {operation} failed"
                        f"{f': {detail}' if detail else ''}."
                    ),
                    details=result,
                )
        return result

    def _lock_for(self, deployment_id: str) -> asyncio.Lock:
        if deployment_id not in self._locks:
            self._locks[deployment_id] = asyncio.Lock()
        return self._locks[deployment_id]

    def _append_log(self, deployment: DeploymentRecord, message: str) -> None:
        timestamp = _now().isoformat().replace("+00:00", "Z")
        deployment.logs.append(f"{timestamp} {message}")
        # Bound log growth for in-memory Phase 5.
        if len(deployment.logs) > 200:
            deployment.logs = deployment.logs[-200:]

    def _require_deployment(self, deployment_id: str) -> DeploymentRecord:
        deployment = self.routes.get_deployment(deployment_id)
        if deployment is None:
            raise LifecycleError(
                ErrorCode.INVALID_REQUEST,
                f"Unknown deployment: {deployment_id}",
                details={"deployment_id": deployment_id},
            )
        return deployment

    def _ensure_idle(self, deployment: DeploymentRecord, operation: str) -> None:
        if deployment.status in BUSY_LIFECYCLE_STATES:
            raise LifecycleError(
                ErrorCode.CONFLICTING_OPERATION,
                (
                    f"Cannot {operation} deployment '{deployment.id}' while status "
                    f"is '{deployment.status.value}'."
                ),
                details={
                    "deployment_id": deployment.id,
                    "status": deployment.status.value,
                    "operation": operation,
                },
            )

    def validate_definition(self, model_id: str) -> dict[str, Any]:
        definition = self.definitions.get(model_id)
        if definition is None:
            raise LifecycleError(
                ErrorCode.UNKNOWN_MODEL,
                f"Unknown model definition: {model_id}",
                details={"model_id": model_id},
            )
        manifest = ModelManifest.model_validate(definition.manifest)
        result = validate_manifest(
            manifest,
            known_providers=self.providers.all_names(),
            existing_ids=None,
            check_files=True,
        )
        definition.validation_status = "valid" if result.valid else "invalid"
        definition.validation_issues = [issue.model_dump(mode="json") for issue in result.issues]
        definition.updated_at = _now()
        self._audit(
            {
                "event_type": "model.validated",
                "resource_type": "model_definition",
                "resource_id": model_id,
                "result": "success" if result.valid else "failed",
                "metadata": {"valid": result.valid, "issue_count": len(result.issues)},
            }
        )
        return {
            "model_id": model_id,
            "valid": result.valid,
            "issues": definition.validation_issues,
        }

    async def load(
        self,
        *,
        deployment_id: str | None = None,
        model_definition_id: str | None = None,
        worker_id: str = "local-dev-worker",
        placement_mode: str | None = None,
        gpu_indexes: list[int] | None = None,
        context_length: int | None = None,
        principal_subject: str,
        request_id: str,
    ) -> DeploymentRecord:
        placement = None
        if placement_mode is not None or gpu_indexes is not None:
            if placement_mode is None or not gpu_indexes:
                raise LifecycleError(
                    ErrorCode.INVALID_REQUEST,
                    "placement_mode and gpu_indexes must be provided together",
                )
            placement = build_placement(
                worker_id=worker_id,
                placement_mode=placement_mode,
                gpu_indexes=gpu_indexes,
            )

        if deployment_id:
            deployment = self._require_deployment(deployment_id)
            if placement is not None:
                worker_id = placement.worker_id
        else:
            if not model_definition_id:
                raise LifecycleError(
                    ErrorCode.INVALID_REQUEST,
                    "load requires deployment_id or model_definition_id",
                )
            definition = self.definitions.get(model_definition_id)
            if definition is None:
                raise LifecycleError(
                    ErrorCode.UNKNOWN_MODEL,
                    f"Unknown model definition: {model_definition_id}",
                )
            deployment_id = deployment_id_for_placement(model_definition_id, placement)
            existing = self.routes.get_deployment(deployment_id)
            if existing is None:
                manifest = ModelManifest.model_validate(definition.manifest)
                try:
                    resolved_ctx = resolve_context_length(
                        manifest=manifest,
                        placement=placement,
                        requested=context_length,
                    )
                except ValueError as exc:
                    raise LifecycleError(
                        ErrorCode.INVALID_REQUEST,
                        str(exc),
                        details={
                            "context_length": context_length,
                            "placement_mode": (
                                placement.mode.value if placement is not None else None
                            ),
                        },
                    ) from exc
                if placement is not None:
                    fit, conflict = evaluate_placement(
                        placement=placement,
                        manifest=manifest,
                        workers=self._workers,
                        deployments=self.routes.deployments,
                        max_lanes_per_gpu=self._max_lanes_per_gpu,
                    )
                    if conflict is not None:
                        raise LifecycleError(
                            ErrorCode.GPU_PLACEMENT_CONFLICT,
                            conflict.details.get(
                                "message",
                                "Requested GPUs are occupied; unload explicitly first.",
                            ),
                            details=conflict.model_dump(mode="json"),
                        )
                    if fit.status != VramFitStatus.FITS:
                        # Local/dev without worker inventory: allow mock only.
                        if (
                            manifest.runtime.provider == "mock"
                            and fit.status
                            in {
                                VramFitStatus.INVENTORY_UNAVAILABLE,
                                VramFitStatus.NOT_EVALUATED,
                            }
                        ):
                            pass
                        else:
                            raise LifecycleError(
                                placement_error_code(fit),
                                fit.message or f"Placement rejected: {fit.status.value}",
                                details=fit.model_dump(mode="json"),
                            )
                runtime_cfg = manifest.runtime.model_dump(mode="json")
                runtime_cfg["context_length"] = resolved_ctx
                if placement is not None:
                    runtime_cfg["placement"] = placement.model_dump(mode="json")
                    if placement.mode == PlacementMode.TWO_GPU:
                        runtime_cfg["tensor_parallel_size"] = 2
                deployment = DeploymentRecord(
                    id=deployment_id,
                    model_definition_id=model_definition_id,
                    worker_id=worker_id,
                    provider=manifest.runtime.provider,
                    status=DeploymentState.REGISTERED,
                    gpu_assignments=(
                        list(placement.gpu_indexes) if placement is not None else []
                    ),
                    runtime_configuration=runtime_cfg,
                )
                self.routes.upsert_deployment(deployment)
            else:
                deployment = existing

        async with self._lock_for(deployment.id):
            self._ensure_idle(deployment, "load")
            if deployment.status == DeploymentState.READY:
                raise LifecycleError(
                    ErrorCode.CONFLICTING_OPERATION,
                    "Deployment is already ready.",
                    details={"deployment_id": deployment.id, "status": deployment.status.value},
                )

            definition = self.definitions.get(deployment.model_definition_id)
            if definition is None:
                raise LifecycleError(
                    ErrorCode.UNKNOWN_MODEL,
                    f"Missing model definition: {deployment.model_definition_id}",
                )
            manifest = ModelManifest.model_validate(definition.manifest)

            try:
                resolved_ctx = resolve_context_length(
                    manifest=manifest,
                    placement=placement,
                    requested=context_length,
                )
            except ValueError as exc:
                raise LifecycleError(
                    ErrorCode.INVALID_REQUEST,
                    str(exc),
                    details={
                        "context_length": context_length,
                        "placement_mode": (
                            placement.mode.value if placement is not None else None
                        ),
                    },
                ) from exc

            if placement is not None:
                fit, conflict = evaluate_placement(
                    placement=placement,
                    manifest=manifest,
                    workers=self._workers,
                    deployments=self.routes.deployments,
                    exclude_deployment_id=deployment.id,
                    max_lanes_per_gpu=self._max_lanes_per_gpu,
                )
                if conflict is not None:
                    raise LifecycleError(
                        ErrorCode.GPU_PLACEMENT_CONFLICT,
                        conflict.details.get(
                            "message",
                            "Requested GPUs are occupied; unload explicitly first.",
                        ),
                        details=conflict.model_dump(mode="json"),
                    )
                if fit.status != VramFitStatus.FITS and manifest.runtime.provider != "mock":
                    if not (
                        manifest.runtime.provider == "mock"
                        and fit.status == VramFitStatus.INVENTORY_UNAVAILABLE
                    ):
                        raise LifecycleError(
                            placement_error_code(fit),
                            fit.message or f"Placement rejected: {fit.status.value}",
                            details=fit.model_dump(mode="json"),
                        )
                deployment.worker_id = placement.worker_id
                deployment.gpu_assignments = list(placement.gpu_indexes)
                runtime_cfg = dict(deployment.runtime_configuration or {})
                runtime_cfg["placement"] = placement.model_dump(mode="json")
                if placement.mode == PlacementMode.TWO_GPU:
                    runtime_cfg["tensor_parallel_size"] = 2
                runtime_cfg["context_length"] = resolved_ctx
                deployment.runtime_configuration = runtime_cfg
            else:
                runtime_cfg = dict(deployment.runtime_configuration or {})
                runtime_cfg["context_length"] = resolved_ctx
                deployment.runtime_configuration = runtime_cfg

            validation = validate_manifest(
                manifest,
                known_providers=self.providers.all_names(),
                check_files=True,
            )
            deployment.status = DeploymentState.VALIDATING
            deployment.load_progress_percent = 5
            deployment.failure_reason = None
            deployment.last_error = None
            self._append_log(deployment, "Validating model definition")
            if not validation.valid:
                deployment.status = DeploymentState.FAILED
                deployment.failure_reason = "; ".join(
                    issue.message for issue in validation.errors
                )
                deployment.last_error = deployment.failure_reason
                deployment.load_progress_percent = None
                self._append_log(deployment, f"Validation failed: {deployment.failure_reason}")
                self._audit(
                    {
                        "event_type": "deployment.load_failed",
                        "resource_type": "deployment",
                        "resource_id": deployment.id,
                        "principal_subject": principal_subject,
                        "request_id": request_id,
                        "result": "failed",
                        "metadata": {"reason": deployment.failure_reason},
                    }
                )
                self.routes.upsert_deployment(deployment)
                self._persist()
                raise LifecycleError(
                    ErrorCode.INVALID_REQUEST,
                    "Model definition failed validation.",
                    details={"issues": [i.model_dump() for i in validation.issues]},
                )

            if deployment.id in self._cancel_queued:
                self._cancel_queued.discard(deployment.id)
                deployment.status = DeploymentState.STOPPED
                deployment.load_progress_percent = None
                self._append_log(deployment, "Queued load cancelled before start")
                self.routes.upsert_deployment(deployment)
                self._persist()
                return deployment

            deployment.status = DeploymentState.QUEUED
            deployment.load_progress_percent = 15
            self._append_log(deployment, "Load queued")
            await asyncio.sleep(0)  # yield

            if deployment.id in self._cancel_queued:
                self._cancel_queued.discard(deployment.id)
                deployment.status = DeploymentState.STOPPED
                deployment.load_progress_percent = None
                self._append_log(deployment, "Queued load cancelled")
                self._audit(
                    {
                        "event_type": "deployment.load_cancelled",
                        "resource_type": "deployment",
                        "resource_id": deployment.id,
                        "principal_subject": principal_subject,
                        "request_id": request_id,
                        "result": "cancelled",
                    }
                )
                self.routes.upsert_deployment(deployment)
                self._persist()
                return deployment

            deployment.status = DeploymentState.LOADING
            deployment.load_progress_percent = 40
            self._append_log(deployment, f"Loading via provider '{deployment.provider}'")
            worker_endpoint: str | None = None
            load_started_at = _now().isoformat().replace("+00:00", "Z")
            load_started_perf = time.perf_counter()
            model_path = str(getattr(manifest.source, "path", "") or "") or None
            artifact_bytes = _artifact_bytes(model_path)
            worker_duration_ms: int | None = None
            try:
                worker_managed = deployment.provider in {
                    "llama-cpp",
                    "llama-cpp-embeddings",
                    "llama-cpp-rerank",
                    "diffusers",
                }
                authoritative = worker_managed and placement is not None
                if authoritative or (
                    worker_managed and deployment.gpu_assignments
                ):
                    notify_cfg = dict(deployment.runtime_configuration or {})
                    notify_cfg["model_path"] = str(manifest.source.path)
                    notify_cfg["model_identifier"] = manifest.runtime.model_identifier
                    notify_cfg["context_length"] = int(
                        notify_cfg.get("context_length")
                        or manifest.runtime.context_length
                    )
                    min_vram = manifest.requirements.minimum_gpu_memory_gb
                    placement_cfg = notify_cfg.get("placement") or {}
                    mode_name = (
                        placement_cfg.get("mode")
                        if isinstance(placement_cfg, dict)
                        else None
                    )
                    limits = manifest.requirements.placement_limits
                    if limits is not None and mode_name:
                        mode_limit = (
                            limits.two_gpu
                            if mode_name == "two_gpu"
                            else limits.single_gpu
                        )
                        if (
                            mode_limit is not None
                            and mode_limit.minimum_gpu_memory_gb is not None
                        ):
                            min_vram = mode_limit.minimum_gpu_memory_gb
                    notify_cfg["minimum_gpu_memory_gb"] = min_vram
                    notify_cfg["profile"] = str(
                        getattr(manifest.runtime, "profile", "chat")
                    )
                    if getattr(manifest.runtime, "family", None):
                        notify_cfg["family"] = str(manifest.runtime.family)
                    if getattr(manifest.runtime, "mmproj_path", None):
                        notify_cfg["mmproj_path"] = str(manifest.runtime.mmproj_path)
                    if getattr(manifest.runtime, "binary_path", None):
                        notify_cfg["binary_path"] = str(manifest.runtime.binary_path)
                    if getattr(manifest.runtime, "pooling", None):
                        notify_cfg["pooling"] = str(manifest.runtime.pooling)
                    if getattr(manifest.runtime, "embedding", None) is not None:
                        notify_cfg["embedding"] = bool(manifest.runtime.embedding)
                    if getattr(manifest.runtime, "reranking", None) is not None:
                        notify_cfg["reranking"] = bool(manifest.runtime.reranking)
                    deployment.runtime_configuration = notify_cfg
                    notify = await self._notify_worker(
                        operation="load",
                        deployment=deployment,
                        authoritative=authoritative,
                    )
                    worker_response = notify.get("worker_response") or {}
                    worker_deployment = worker_response.get("deployment") or {}
                    raw_worker_ms = worker_deployment.get("load_duration_ms")
                    if raw_worker_ms is None:
                        raw_worker_ms = worker_response.get("load_duration_ms")
                    if raw_worker_ms is not None:
                        try:
                            worker_duration_ms = int(raw_worker_ms)
                        except (TypeError, ValueError):
                            worker_duration_ms = None
                    worker_artifact = worker_deployment.get("artifact_bytes")
                    if worker_artifact is None:
                        worker_artifact = worker_response.get("artifact_bytes")
                    if isinstance(worker_artifact, int):
                        artifact_bytes = worker_artifact
                    worker_endpoint = (
                        worker_response.get("base_url")
                        or worker_deployment.get("base_url")
                    )
                    if authoritative and not worker_endpoint:
                        raise LifecycleError(
                            ErrorCode.PROVIDER_FAILURE,
                            "Worker load did not return a runtime base_url.",
                            details=notify,
                        )

                provider = self.providers.require_any(deployment.provider)
                await provider.load(
                    DeploymentSpec(
                        deployment_id=deployment.id,
                        model_definition_id=deployment.model_definition_id,
                        model_identifier=manifest.runtime.model_identifier,
                        runtime_configuration=dict(deployment.runtime_configuration or {}),
                        gpu_assignments=list(deployment.gpu_assignments or []),
                        placement=(
                            (deployment.runtime_configuration or {}).get("placement")
                        ),
                        worker_endpoint=worker_endpoint,
                    )
                )
                deployment.status = DeploymentState.READY
                deployment.load_progress_percent = 100
                deployment.loaded_at = _now()
                deployment.last_successful_load = deployment.loaded_at
                deployment.failure_reason = None
                deployment.last_error = None
                self._emit_capacity(
                    event_type="load",
                    deployment=deployment,
                    reason="lifecycle.load",
                    request_id=request_id,
                    principal_subject=principal_subject,
                )
                if worker_endpoint:
                    runtime_cfg = dict(deployment.runtime_configuration or {})
                    runtime_cfg["base_url"] = worker_endpoint
                    deployment.runtime_configuration = runtime_cfg
                for alias in self.routes.aliases.values():
                    if alias.deployment_id == deployment.id and alias.state != RouteState.DISABLED:
                        alias.state = RouteState.ENABLED
                # Bind stable Studio aliases when a Diffusers image/video load
                # succeeds so /v1/generation/jobs can resolve yonder-image /
                # yonder-video without a separate operator repoint.
                profile = str(getattr(manifest.runtime, "profile", "") or "").lower()
                if deployment.provider == "diffusers" and profile in {"image", "video"}:
                    studio_alias = (
                        "yonder-image" if profile == "image" else "yonder-video"
                    )
                    previous = self.routes.get_alias(studio_alias)
                    self.routes.upsert_alias(
                        ModelAliasRoute(
                            alias=studio_alias,
                            deployment_id=deployment.id,
                            state=RouteState.ENABLED,
                            priority=previous.priority if previous else 100,
                            experimental=previous.experimental if previous else False,
                        )
                    )
                    self._append_log(
                        deployment,
                        f"Bound studio alias '{studio_alias}' → {deployment.id}",
                    )
                load_finished_at = _now().isoformat().replace("+00:00", "Z")
                duration_ms = int((time.perf_counter() - load_started_perf) * 1000)
                self._append_log(deployment, "Load completed — ready")
                self._record_load_telemetry(
                    deployment=deployment,
                    outcome="success",
                    duration_ms=duration_ms,
                    started_at=load_started_at,
                    finished_at=load_finished_at,
                    model_path=model_path,
                    artifact_bytes=artifact_bytes,
                    worker_duration_ms=worker_duration_ms,
                    request_id=request_id,
                )
                if not authoritative:
                    await self._notify_worker(operation="load", deployment=deployment)
                self._audit(
                    {
                        "event_type": "deployment.loaded",
                        "resource_type": "deployment",
                        "resource_id": deployment.id,
                        "principal_subject": principal_subject,
                        "request_id": request_id,
                        "result": "success",
                        "metadata": {
                            "previous_state": "loading",
                            "new_state": "ready",
                            "gpu_assignments": list(deployment.gpu_assignments or []),
                            "duration_ms": duration_ms,
                            "worker_duration_ms": worker_duration_ms,
                            "artifact_bytes": artifact_bytes,
                            "model_path": model_path,
                            "worker_id": deployment.worker_id,
                            "started_at": load_started_at,
                            "finished_at": load_finished_at,
                        },
                    }
                )
            except LifecycleError as exc:
                load_finished_at = _now().isoformat().replace("+00:00", "Z")
                duration_ms = int((time.perf_counter() - load_started_perf) * 1000)
                deployment.status = DeploymentState.FAILED
                deployment.failure_reason = str(exc)
                deployment.last_error = str(exc)
                deployment.load_progress_percent = None
                self._append_log(deployment, f"Load failed: {exc}")
                self._record_load_telemetry(
                    deployment=deployment,
                    outcome="failed",
                    duration_ms=duration_ms,
                    started_at=load_started_at,
                    finished_at=load_finished_at,
                    model_path=model_path,
                    artifact_bytes=artifact_bytes,
                    worker_duration_ms=worker_duration_ms,
                    request_id=request_id,
                )
                self._audit(
                    {
                        "event_type": "deployment.load_failed",
                        "resource_type": "deployment",
                        "resource_id": deployment.id,
                        "principal_subject": principal_subject,
                        "request_id": request_id,
                        "result": "failed",
                        "metadata": {
                            "reason": str(exc),
                            "duration_ms": duration_ms,
                            "worker_duration_ms": worker_duration_ms,
                            "artifact_bytes": artifact_bytes,
                            "model_path": model_path,
                            "worker_id": deployment.worker_id,
                            "started_at": load_started_at,
                            "finished_at": load_finished_at,
                        },
                    }
                )
                raise
            except Exception as exc:  # noqa: BLE001
                load_finished_at = _now().isoformat().replace("+00:00", "Z")
                duration_ms = int((time.perf_counter() - load_started_perf) * 1000)
                deployment.status = DeploymentState.FAILED
                deployment.failure_reason = str(exc)
                deployment.last_error = str(exc)
                deployment.load_progress_percent = None
                self._append_log(deployment, f"Load failed: {exc}")
                self._record_load_telemetry(
                    deployment=deployment,
                    outcome="failed",
                    duration_ms=duration_ms,
                    started_at=load_started_at,
                    finished_at=load_finished_at,
                    model_path=model_path,
                    artifact_bytes=artifact_bytes,
                    worker_duration_ms=worker_duration_ms,
                    request_id=request_id,
                )
                self._audit(
                    {
                        "event_type": "deployment.load_failed",
                        "resource_type": "deployment",
                        "resource_id": deployment.id,
                        "principal_subject": principal_subject,
                        "request_id": request_id,
                        "result": "failed",
                        "metadata": {
                            "reason": str(exc),
                            "duration_ms": duration_ms,
                            "worker_duration_ms": worker_duration_ms,
                            "artifact_bytes": artifact_bytes,
                            "model_path": model_path,
                            "worker_id": deployment.worker_id,
                            "started_at": load_started_at,
                            "finished_at": load_finished_at,
                        },
                    }
                )
                raise LifecycleError(
                    ErrorCode.PROVIDER_FAILURE,
                    "Provider failed to load deployment.",
                    details={"reason": str(exc), "provider": deployment.provider},
                ) from exc
            finally:
                self.routes.upsert_deployment(deployment)
                self._persist()
            return deployment

    async def drain(
        self,
        deployment_id: str,
        *,
        principal_subject: str,
        request_id: str,
    ) -> DeploymentRecord:
        deployment = self._require_deployment(deployment_id)
        async with self._lock_for(deployment.id):
            self._ensure_idle(deployment, "drain")
            if deployment.status != DeploymentState.READY:
                raise LifecycleError(
                    ErrorCode.INVALID_REQUEST,
                    "Only ready deployments can be drained.",
                    details={"status": deployment.status.value},
                )
            previous = deployment.status.value
            deployment.status = DeploymentState.DRAINING
            self._append_log(deployment, "Drain started — rejecting new requests")
            for alias in self.routes.aliases.values():
                if alias.deployment_id == deployment.id and alias.state == RouteState.ENABLED:
                    alias.state = RouteState.DRAINING
            for _ in range(20):
                if deployment.active_requests <= 0:
                    break
                await asyncio.sleep(0.05)
            self._append_log(deployment, "Drain complete — awaiting unload or reload")
            await self._notify_worker(operation="drain", deployment=deployment)
            self._audit(
                {
                    "event_type": "deployment.drained",
                    "resource_type": "deployment",
                    "resource_id": deployment.id,
                    "principal_subject": principal_subject,
                    "request_id": request_id,
                    "result": "success",
                    "metadata": {"previous_state": previous, "new_state": "draining"},
                }
            )
            self.routes.upsert_deployment(deployment)
            self._persist()
            return deployment

    async def unload(
        self,
        deployment_id: str,
        *,
        principal_subject: str,
        request_id: str,
    ) -> DeploymentRecord:
        deployment = self._require_deployment(deployment_id)
        async with self._lock_for(deployment.id):
            if deployment.status in {
                DeploymentState.LOADING,
                DeploymentState.QUEUED,
                DeploymentState.VALIDATING,
            }:
                raise LifecycleError(
                    ErrorCode.CONFLICTING_OPERATION,
                    "Cannot unload while a load is in progress.",
                    details={"status": deployment.status.value},
                )
            if deployment.status == DeploymentState.UNLOADING:
                raise LifecycleError(
                    ErrorCode.CONFLICTING_OPERATION,
                    "Unload already in progress.",
                    details={"status": deployment.status.value},
                )
            if deployment.status == DeploymentState.STOPPED:
                return deployment

            previous = deployment.status.value
            deployment.status = DeploymentState.UNLOADING
            deployment.load_progress_percent = None
            self._append_log(deployment, "Unload started")
            try:
                authoritative = (
                    deployment.provider
                    in {
                        "llama-cpp",
                        "llama-cpp-embeddings",
                        "llama-cpp-rerank",
                        "diffusers",
                    }
                    and bool(deployment.gpu_assignments)
                    and bool((deployment.runtime_configuration or {}).get("placement"))
                )
                if authoritative:
                    await self._notify_worker(
                        operation="unload",
                        deployment=deployment,
                        authoritative=True,
                    )
                provider = self.providers.require_any(deployment.provider)
                await provider.unload(deployment.id)
                deployment.status = DeploymentState.STOPPED
                deployment.loaded_at = None
                deployment.failure_reason = None
                deployment.last_error = None
                self._emit_capacity(
                    event_type="unload",
                    deployment=deployment,
                    reason="lifecycle.unload",
                    request_id=request_id,
                    principal_subject=principal_subject,
                    details={"previous_status": previous},
                )
                self._append_log(deployment, "Unload completed — stopped")
                if not authoritative:
                    await self._notify_worker(operation="unload", deployment=deployment)
                for alias in self.routes.aliases.values():
                    if alias.deployment_id == deployment.id:
                        alias.state = RouteState.UNAVAILABLE
                self._audit(
                    {
                        "event_type": "deployment.unloaded",
                        "resource_type": "deployment",
                        "resource_id": deployment.id,
                        "principal_subject": principal_subject,
                        "request_id": request_id,
                        "result": "success",
                        "metadata": {
                            "previous_state": previous,
                            "new_state": "stopped",
                        },
                    }
                )
            except LifecycleError:
                raise
            except Exception as exc:  # noqa: BLE001
                deployment.status = DeploymentState.FAILED
                deployment.failure_reason = str(exc)
                deployment.last_error = str(exc)
                self._append_log(deployment, f"Unload failed: {exc}")
                raise LifecycleError(
                    ErrorCode.PROVIDER_FAILURE,
                    "Provider failed to unload deployment.",
                    details={"reason": str(exc)},
                ) from exc
            finally:
                self.routes.upsert_deployment(deployment)
                self._persist()
            return deployment

    def cancel_queued_load(
        self,
        deployment_id: str,
        *,
        principal_subject: str,
        request_id: str,
    ) -> DeploymentRecord:
        deployment = self._require_deployment(deployment_id)
        if deployment.status not in {DeploymentState.QUEUED, DeploymentState.VALIDATING}:
            raise LifecycleError(
                ErrorCode.INVALID_REQUEST,
                "Only queued or validating loads can be cancelled.",
                details={"status": deployment.status.value},
            )
        self._cancel_queued.add(deployment_id)
        self._append_log(deployment, "Cancel requested for queued load")
        self._audit(
            {
                "event_type": "deployment.load_cancel_requested",
                "resource_type": "deployment",
                "resource_id": deployment.id,
                "principal_subject": principal_subject,
                "request_id": request_id,
                "result": "accepted",
            }
        )
        self.routes.upsert_deployment(deployment)
        self._persist()
        return deployment

    async def retry_failed_load(
        self,
        deployment_id: str,
        *,
        principal_subject: str,
        request_id: str,
    ) -> DeploymentRecord:
        deployment = self._require_deployment(deployment_id)
        if deployment.status != DeploymentState.FAILED:
            raise LifecycleError(
                ErrorCode.INVALID_REQUEST,
                "Only failed deployments can be retried.",
                details={"status": deployment.status.value},
            )
        deployment.status = DeploymentState.STOPPED
        return await self.load(
            deployment_id=deployment_id,
            principal_subject=principal_subject,
            request_id=request_id,
        )

    def set_deployment_lock(
        self,
        deployment_id: str,
        *,
        locked: bool,
        principal_subject: str,
        request_id: str,
    ) -> DeploymentRecord:
        deployment = self._require_deployment(deployment_id)
        runtime = dict(deployment.runtime_configuration or {})
        runtime["locked"] = bool(locked)
        deployment.runtime_configuration = runtime
        self._persist()
        self._emit_capacity(
            event_type="lock" if locked else "unlock",
            deployment=deployment,
            reason="operator.lock" if locked else "operator.unlock",
            request_id=request_id,
            principal_subject=principal_subject,
        )
        self._audit(
            {
                "event_type": "deployment.lock_updated",
                "resource_type": "deployment",
                "resource_id": deployment.id,
                "principal_subject": principal_subject,
                "request_id": request_id,
                "result": "success",
                "metadata": {"locked": locked},
            }
        )
        return deployment

    def set_alias(
        self,
        alias: str,
        deployment_id: str,
        *,
        principal_subject: str,
        request_id: str,
        enabled: bool = True,
    ) -> ModelAliasRoute:
        deployment = self._require_deployment(deployment_id)
        previous = self.routes.get_alias(alias)
        route = ModelAliasRoute(
            alias=alias,
            deployment_id=deployment_id,
            state=RouteState.ENABLED if enabled else RouteState.DISABLED,
            priority=previous.priority if previous else 100,
            experimental=previous.experimental if previous else False,
        )
        if deployment.status == DeploymentState.DRAINING:
            route.state = RouteState.DRAINING
        elif deployment.status != DeploymentState.READY and enabled:
            route.state = RouteState.UNAVAILABLE
        self.routes.upsert_alias(route)
        self._persist()
        self._audit(
            {
                "event_type": "route.alias_updated",
                "resource_type": "alias",
                "resource_id": alias,
                "principal_subject": principal_subject,
                "request_id": request_id,
                "result": "success",
                "metadata": {
                    "previous_deployment_id": previous.deployment_id if previous else None,
                    "new_deployment_id": deployment_id,
                },
            }
        )
        return route
