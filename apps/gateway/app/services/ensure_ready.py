"""Ensure a target model/deployment is READY before serving work.

Auto-loads into free multi-lanes when measured VRAM fits. Prefer-evicts idle
Diffusers, protects the sole chat lane via demotion, and never invents free VRAM.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from yonder_configuration.models import CapacitySchedulerConfig
from yonder_configuration.worker_auth import worker_auth_headers
from yonder_contracts.errors import ErrorCode
from yonder_contracts.generation import GenerationMode
from yonder_contracts.placement import PlacementMode, VramFitStatus

from app.models.manifest import ModelManifest
from app.routing.models import (
    BUSY_LIFECYCLE_STATES,
    DeploymentRecord,
    DeploymentState,
    ModelDefinitionRecord,
)
from app.services.lifecycle import LifecycleError, LifecycleService
from app.services.placement import (
    build_placement,
    evaluate_placement,
    lane_counts_for_worker,
    worker_capacity_from_record,
)

ProgressCallback = Callable[[str, str, float | None], Awaitable[None] | None]

_BYTES_PER_GIB = 1024**3


@dataclass
class EnsureReadyResult:
    deployment_id: str
    model_definition_id: str
    actions: list[dict[str, Any]] = field(default_factory=list)
    progress: list[dict[str, Any]] = field(default_factory=list)


class EnsureReadyError(Exception):
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


class EnsureDeploymentReady:
    def __init__(
        self,
        *,
        lifecycle: LifecycleService,
        routes: Any,
        workers: dict[str, Any],
        definitions: dict[str, ModelDefinitionRecord],
        config: CapacitySchedulerConfig,
        capacity_events: Any | None = None,
    ) -> None:
        self.lifecycle = lifecycle
        self.routes = routes
        self.workers = workers
        self.definitions = definitions
        self.config = config
        self._events = capacity_events

    def _emit(self, event: dict[str, Any]) -> None:
        if self._events is not None and hasattr(self._events, "append"):
            self._events.append(event)

    async def _progress(
        self,
        sink: list[dict[str, Any]],
        *,
        step: str,
        message: str,
        percent: float | None,
        on_progress: ProgressCallback | None,
    ) -> None:
        sink.append(
            {
                "step": step,
                "message": message,
                "percent": percent,
            }
        )
        if on_progress is not None:
            maybe = on_progress(step, message, percent)
            if maybe is not None and hasattr(maybe, "__await__"):
                await maybe

    def _is_ready(self, deployment_id: str | None) -> DeploymentRecord | None:
        if not deployment_id:
            return None
        deployment = self.routes.deployments.get(deployment_id)
        if deployment is not None and deployment.status == DeploymentState.READY:
            return deployment
        return None

    def _worker_base_url(self, deployment: DeploymentRecord) -> str | None:
        runtime = deployment.runtime_configuration or {}
        url = runtime.get("base_url")
        if isinstance(url, str) and url.strip():
            return url.strip().rstrip("/")
        record = self.workers.get(deployment.worker_id)
        if isinstance(record, dict):
            endpoint = record.get("endpoint")
            if isinstance(endpoint, str) and endpoint.strip():
                return endpoint.strip().rstrip("/")
        return None

    def _mark_stale(
        self,
        deployment: DeploymentRecord,
        *,
        reason: str,
        progress: list[dict[str, Any]] | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        """Gateway said READY but the worker no longer has the runtime."""
        deployment.status = DeploymentState.STOPPED
        deployment.last_error = reason
        deployment.failure_reason = "stale_ready_missing_on_worker"
        self._emit(
            {
                "event_type": "stale_ready",
                "deployment_id": deployment.id,
                "model_definition_id": deployment.model_definition_id,
                "worker_id": deployment.worker_id,
                "gpu_indexes": list(deployment.gpu_assignments),
                "reason": reason,
                "details": {"previous_status": "ready"},
            }
        )

    async def _confirm_worker_ready(self, deployment: DeploymentRecord) -> bool:
        """Probe the worker lifecycle status. Never invent readiness.

        When no probe URL exists (typical unit fixtures), trust the Gateway
        registry. When a URL exists, require the worker to list this deployment
        as ready — covers worker restarts that leave stale Gateway READY rows.
        """
        base = self._worker_base_url(deployment)
        if not base:
            return True
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0, headers=worker_auth_headers()) as client:
                response = await client.get(f"{base}/v1/lifecycle/status")
                response.raise_for_status()
                payload = response.json()
        except Exception:  # noqa: BLE001
            return False
        if not isinstance(payload, dict):
            return False
        for item in payload.get("deployments") or []:
            if not isinstance(item, dict):
                continue
            item_id = item.get("deployment_id") or item.get("id")
            if item_id != deployment.id:
                continue
            status = str(item.get("status") or "").lower()
            return status in {"ready", "loaded"}
        return False

    async def _ready_if_live(
        self,
        deployment: DeploymentRecord | None,
        *,
        progress: list[dict[str, Any]],
        on_progress: ProgressCallback | None,
    ) -> DeploymentRecord | None:
        if deployment is None:
            return None
        if await self._confirm_worker_ready(deployment):
            return deployment
        await self._progress(
            progress,
            step="ensuring_capacity",
            message=(
                f"Deployment {deployment.id} is READY in Gateway but missing on "
                f"worker {deployment.worker_id} — will reload"
            ),
            percent=8.0,
            on_progress=on_progress,
        )
        self._mark_stale(
            deployment,
            reason=(
                f"Worker {deployment.worker_id} does not report ready runtime "
                f"for {deployment.id}"
            ),
            progress=progress,
            on_progress=on_progress,
        )
        return None

    def _wanted_profile(self, mode: GenerationMode | None, for_chat: bool) -> str | None:
        if for_chat:
            return "chat"
        if mode is None:
            return None
        if mode in {GenerationMode.TEXT_TO_VIDEO, GenerationMode.IMAGE_TO_VIDEO}:
            return "video"
        return "image"

    def _definition_matches(
        self,
        definition: ModelDefinitionRecord,
        *,
        provider: str | None,
        profile: str | None,
    ) -> bool:
        try:
            manifest = ModelManifest.model_validate(definition.manifest)
        except Exception:  # noqa: BLE001
            return False
        runtime_provider = str(getattr(manifest.runtime, "provider", "") or "")
        if provider and runtime_provider != provider:
            return False
        runtime_profile = str(getattr(manifest.runtime, "profile", "") or "").lower()
        if profile and runtime_profile != profile:
            return False
        return True

    def _pick_definition(
        self,
        *,
        model_alias: str | None,
        model_definition_id: str | None,
        mode: GenerationMode | None,
        for_chat: bool,
    ) -> ModelDefinitionRecord:
        if model_definition_id and model_definition_id in self.definitions:
            return self.definitions[model_definition_id]
        alias = self.routes.get_alias(model_alias) if model_alias else None
        if alias is not None:
            deployment = self.routes.deployments.get(alias.deployment_id)
            if deployment is not None:
                definition = self.definitions.get(deployment.model_definition_id)
                if definition is not None:
                    return definition
        if model_alias and model_alias in self.definitions:
            return self.definitions[model_alias]
        profile = self._wanted_profile(mode, for_chat)
        provider = "llama-cpp" if for_chat else "diffusers"
        # Profile fallback is only for requests that declare intent by mode
        # (Studio), alias-less requests, or the configured chat alias during
        # cold bring-up. A caller-supplied chat alias that matches no route
        # and no definition is UNKNOWN_MODEL — falling back here would
        # silently bind an arbitrary definition to a bogus alias via
        # set_alias.
        allow_profile_fallback = (
            mode is not None
            or not model_alias
            or model_alias == self.config.chat_alias
        )
        if allow_profile_fallback:
            for definition in self.definitions.values():
                if self._definition_matches(
                    definition, provider=provider, profile=profile
                ):
                    return definition
        raise EnsureReadyError(
            ErrorCode.UNKNOWN_MODEL,
            f"No model definition available for alias={model_alias!r} profile={profile!r}",
            details={"model_alias": model_alias, "profile": profile},
        )

    def _idle_long_enough(self, deployment: DeploymentRecord) -> bool:
        if deployment.active_requests > 0:
            return False
        if deployment.status in BUSY_LIFECYCLE_STATES:
            return False
        seconds = self.config.idle_evict_seconds
        if seconds <= 0:
            return True
        stamp = deployment.last_released_at or deployment.loaded_at
        if stamp is None:
            # Never served a request after load — treat as idle once loaded.
            return deployment.status == DeploymentState.READY
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - stamp.astimezone(UTC)).total_seconds()
        return age >= seconds

    def _aliases_for_deployment(self, deployment_id: str) -> list[str]:
        return [
            alias.alias
            for alias in self.routes.aliases.values()
            if alias.deployment_id == deployment_id
        ]

    def _is_sole_chat_lane(self, deployment: DeploymentRecord) -> bool:
        if not self.config.chat_protect_sole_lane:
            return False
        chat_alias = self.config.chat_alias
        route = self.routes.get_alias(chat_alias)
        if route is None or route.deployment_id != deployment.id:
            return False
        chat_ready = [
            item
            for item in self.routes.deployments.values()
            if item.status == DeploymentState.READY
            and item.provider in {"llama-cpp", "mock"}
            and str((item.runtime_configuration or {}).get("profile") or "chat").lower()
            in {"", "chat"}
        ]
        return len(chat_ready) <= 1

    def _is_protected(self, deployment: DeploymentRecord) -> bool:
        if deployment.locked:
            return True
        aliases = set(self._aliases_for_deployment(deployment.id))
        if aliases & set(self.config.never_evict_aliases):
            if self._is_sole_chat_lane(deployment):
                return True
            # never_evict without sole-chat still blocks bare unload; demote path handles chat.
            if self.config.chat_alias in aliases:
                return True
        return False

    def _eviction_rank(self, deployment: DeploymentRecord) -> tuple[int, float]:
        # Lower sorts first: Diffusers preferred, then other idle llama.
        provider = deployment.provider or ""
        if provider == "diffusers":
            family = 0
        elif provider.startswith("llama-cpp"):
            family = 1
        else:
            family = 2
        stamp = deployment.last_released_at or deployment.loaded_at
        age = 0.0
        if stamp is not None:
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            age = -stamp.timestamp()  # older idle first when equal family
        return (family, age)

    def _minimum_vram_bytes(self, deployment: DeploymentRecord) -> int | None:
        definition = self.definitions.get(deployment.model_definition_id)
        if definition is None:
            return None
        try:
            manifest = ModelManifest.model_validate(definition.manifest)
        except Exception:  # noqa: BLE001
            return None
        requirements = getattr(manifest, "requirements", None)
        minimum = getattr(requirements, "minimum_gpu_memory_gb", None)
        if minimum is None:
            return None
        try:
            gb = float(minimum)
        except (TypeError, ValueError):
            return None
        if gb <= 0:
            return None
        return int(gb * _BYTES_PER_GIB)

    def _credit_unloaded_vram(self, deployment: DeploymentRecord) -> None:
        """Add back measured minimum VRAM after unload until a fresh inventory arrives.

        Heartbeats often lag behind unload; without this credit, EnsureReady keeps
        seeing the pre-unload free-VRAM snapshot and refuses to load.
        """
        credit = self._minimum_vram_bytes(deployment)
        if credit is None:
            return
        record = self.workers.get(deployment.worker_id)
        if not isinstance(record, dict):
            return
        hardware = record.get("hardware_summary")
        if not isinstance(hardware, dict):
            return
        gpus = hardware.get("gpus")
        if not isinstance(gpus, list):
            return
        assigned = {int(index) for index in deployment.gpu_assignments}
        for gpu in gpus:
            if not isinstance(gpu, dict):
                continue
            raw_index = gpu.get("index")
            if isinstance(raw_index, dict):
                raw_index = raw_index.get("value")
            elif hasattr(raw_index, "value"):
                raw_index = raw_index.value
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if index not in assigned:
                continue
            available = gpu.get("memory_available_bytes")
            if not isinstance(available, dict):
                continue
            if available.get("availability") != "available":
                continue
            try:
                current = int(available.get("value") or 0)
            except (TypeError, ValueError):
                continue
            available["value"] = current + credit
            used = gpu.get("memory_used_bytes")
            if isinstance(used, dict) and used.get("availability") == "available":
                try:
                    used_val = int(used.get("value") or 0)
                except (TypeError, ValueError):
                    used_val = 0
                used["value"] = max(0, used_val - credit)

    async def _refresh_worker_hardware(self, worker_id: str) -> bool:
        """Pull live inventory from the worker agent. Never invents metrics."""
        record = self.workers.get(worker_id)
        if not isinstance(record, dict):
            return False
        endpoint = record.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint.strip():
            return False
        url = f"{endpoint.rstrip('/')}/v1/hardware"
        try:
            import httpx

            async with httpx.AsyncClient(timeout=15.0, headers=worker_auth_headers()) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
        except Exception:  # noqa: BLE001
            return False
        if not isinstance(payload, dict) or "gpus" not in payload:
            return False
        record["hardware_summary"] = payload
        return True

    async def _after_unload(self, deployment: DeploymentRecord) -> None:
        self._credit_unloaded_vram(deployment)
        await self._refresh_worker_hardware(deployment.worker_id)

    def _candidate_placements(
        self,
        *,
        definition: ModelDefinitionRecord,
    ) -> list[tuple[str, list[int]]]:
        """Enumerate single-GPU placements ordered by free lanes then free VRAM."""
        try:
            manifest = ModelManifest.model_validate(definition.manifest)
        except Exception as exc:  # noqa: BLE001
            raise EnsureReadyError(
                ErrorCode.INVALID_REQUEST,
                f"Invalid manifest for {definition.id}: {exc}",
            ) from exc
        options: list[tuple[int, int, str, list[int]]] = []
        for worker_id, record in self.workers.items():
            if not isinstance(record, dict):
                continue
            if record.get("maintenance_state") or record.get("maintenance"):
                continue
            capacity = worker_capacity_from_record(worker_id, record)
            if not capacity.gpus:
                continue
            counts = lane_counts_for_worker(
                worker_id=worker_id,
                deployments=self.routes.deployments,
            )
            for gpu in capacity.gpus:
                raw_index = gpu.index
                if hasattr(raw_index, "value"):
                    raw_index = raw_index.value
                try:
                    index = int(raw_index)
                except (TypeError, ValueError):
                    continue
                lanes = counts.get(index, 0)
                if lanes >= self.config.max_lanes_per_gpu:
                    continue
                placement = build_placement(
                    worker_id=worker_id,
                    placement_mode=PlacementMode.SINGLE_GPU,
                    gpu_indexes=[index],
                )
                fit, conflict = evaluate_placement(
                    placement=placement,
                    manifest=manifest,
                    workers=self.workers,
                    deployments=self.routes.deployments,
                    max_lanes_per_gpu=self.config.max_lanes_per_gpu,
                )
                if conflict is not None:
                    continue
                if fit.status != VramFitStatus.FITS:
                    continue
                free = 0
                for metric in fit.per_gpu_memory_available_bytes or ():
                    value = getattr(metric, "value", None)
                    if isinstance(value, (int, float)):
                        free += int(value)
                options.append((lanes, -free, worker_id, [index]))
        options.sort()
        return [(worker_id, indexes) for _, _, worker_id, indexes in options]

    async def _evict_victim(
        self,
        victim: DeploymentRecord,
        *,
        principal_subject: str,
        request_id: str,
        progress: list[dict[str, Any]],
        on_progress: ProgressCallback | None,
        actions: list[dict[str, Any]],
    ) -> None:
        await self._progress(
            progress,
            step="unloading",
            message=f"Unloading idle {victim.id} ({victim.provider})",
            percent=35.0,
            on_progress=on_progress,
        )
        if victim.status == DeploymentState.READY:
            try:
                await self.lifecycle.drain(
                    victim.id,
                    principal_subject=principal_subject,
                    request_id=request_id,
                )
            except LifecycleError:
                # Drain may refuse if already idle enough; continue to unload.
                pass
        await self.lifecycle.unload(
            victim.id,
            principal_subject=principal_subject,
            request_id=request_id,
        )
        await self._after_unload(victim)
        action = {
            "type": "evict",
            "deployment_id": victim.id,
            "provider": victim.provider,
            "model_definition_id": victim.model_definition_id,
        }
        actions.append(action)
        self._emit(
            {
                "event_type": "evict",
                "deployment_id": victim.id,
                "model_definition_id": victim.model_definition_id,
                "worker_id": victim.worker_id,
                "gpu_indexes": list(victim.gpu_assignments),
                "reason": "ensure_ready.idle_evict",
                "request_id": request_id,
                "principal_subject": principal_subject,
                "details": action,
            }
        )

    async def _demote_chat(
        self,
        victim: DeploymentRecord,
        *,
        principal_subject: str,
        request_id: str,
        progress: list[dict[str, Any]],
        on_progress: ProgressCallback | None,
        actions: list[dict[str, Any]],
    ) -> DeploymentRecord | None:
        ladder = list(self.config.chat_demotion_ladder)
        if not ladder:
            return None
        current = victim.model_definition_id
        try:
            idx = ladder.index(current)
            candidates = ladder[idx + 1 :]
        except ValueError:
            candidates = [item for item in ladder if item != current]
        await self._progress(
            progress,
            step="demote",
            message=f"Unloading chat {current} for demotion",
            percent=38.0,
            on_progress=on_progress,
        )
        await self.lifecycle.unload(
            victim.id,
            principal_subject=principal_subject,
            request_id=request_id,
        )
        await self._after_unload(victim)
        for model_id in candidates:
            definition = self.definitions.get(model_id)
            if definition is None:
                continue
            await self._progress(
                progress,
                step="demote",
                message=f"Demoting chat {current} → {model_id}",
                percent=40.0,
                on_progress=on_progress,
            )
            placements = self._candidate_placements(definition=definition)
            if not placements:
                continue
            # Prefer GPUs the victim previously held when still free.
            preferred = [
                item
                for item in placements
                if item[0] == victim.worker_id
                and set(item[1]) <= set(victim.gpu_assignments)
            ]
            worker_id, gpu_indexes = (preferred or placements)[0]
            loaded = await self.lifecycle.load(
                model_definition_id=model_id,
                worker_id=worker_id,
                placement_mode="single_gpu",
                gpu_indexes=gpu_indexes,
                principal_subject=principal_subject,
                request_id=request_id,
            )
            self.lifecycle.set_alias(
                self.config.chat_alias,
                loaded.id,
                principal_subject=principal_subject,
                request_id=request_id,
                enabled=True,
            )
            action = {
                "type": "demote",
                "from_deployment_id": victim.id,
                "from_model": current,
                "to_deployment_id": loaded.id,
                "to_model": model_id,
                "alias": self.config.chat_alias,
            }
            actions.append(action)
            self._emit(
                {
                    "event_type": "demote",
                    "deployment_id": loaded.id,
                    "model_definition_id": model_id,
                    "worker_id": loaded.worker_id,
                    "gpu_indexes": list(loaded.gpu_assignments),
                    "reason": "ensure_ready.chat_demote",
                    "request_id": request_id,
                    "principal_subject": principal_subject,
                    "details": action,
                }
            )
            return loaded
        return None

    async def ensure(
        self,
        *,
        model_alias: str | None = None,
        model_definition_id: str | None = None,
        mode: GenerationMode | None = None,
        for_chat: bool = False,
        principal_subject: str,
        request_id: str,
        on_progress: ProgressCallback | None = None,
    ) -> EnsureReadyResult:
        if not self.config.ensure_ready_enabled:
            raise EnsureReadyError(
                ErrorCode.MODEL_UNAVAILABLE,
                "EnsureReady is disabled in capacity config.",
            )

        progress: list[dict[str, Any]] = []
        actions: list[dict[str, Any]] = []
        self._emit(
            {
                "event_type": "ensure_start",
                "reason": "ensure_ready",
                "request_id": request_id,
                "principal_subject": principal_subject,
                "details": {
                    "model_alias": model_alias,
                    "model_definition_id": model_definition_id,
                    "mode": mode.value if mode else None,
                    "for_chat": for_chat,
                },
            }
        )
        await self._progress(
            progress,
            step="ensuring_capacity",
            message="Checking for a ready deployment",
            percent=5.0,
            on_progress=on_progress,
        )

        # Fast path: alias already READY (and still live on the worker).
        if model_alias:
            alias = self.routes.get_alias(model_alias)
            if alias is not None:
                ready = await self._ready_if_live(
                    self._is_ready(alias.deployment_id),
                    progress=progress,
                    on_progress=on_progress,
                )
                if ready is not None:
                    return EnsureReadyResult(
                        deployment_id=ready.id,
                        model_definition_id=ready.model_definition_id,
                        actions=actions,
                        progress=progress,
                    )

        # Ready Diffusers matching profile (Studio: any ready image/video is enough).
        # Skip when a concrete model_definition_id was requested.
        if not for_chat and mode is not None and not model_definition_id:
            profile = self._wanted_profile(mode, False)
            for deployment in list(self.routes.deployments.values()):
                if deployment.status != DeploymentState.READY:
                    continue
                if deployment.provider != "diffusers":
                    continue
                dep_profile = str(
                    (deployment.runtime_configuration or {}).get("profile") or ""
                ).lower()
                if profile and dep_profile != profile:
                    continue
                live = await self._ready_if_live(
                    deployment,
                    progress=progress,
                    on_progress=on_progress,
                )
                if live is None:
                    continue
                return EnsureReadyResult(
                    deployment_id=live.id,
                    model_definition_id=live.model_definition_id,
                    actions=actions,
                    progress=progress,
                )

        definition = self._pick_definition(
            model_alias=model_alias,
            model_definition_id=model_definition_id,
            mode=mode,
            for_chat=for_chat,
        )

        # Existing READY deployment for this definition (verify worker still has it).
        for deployment in list(self.routes.deployments.values()):
            if (
                deployment.model_definition_id == definition.id
                and deployment.status == DeploymentState.READY
            ):
                live = await self._ready_if_live(
                    deployment,
                    progress=progress,
                    on_progress=on_progress,
                )
                if live is None:
                    continue
                if model_alias:
                    self.lifecycle.set_alias(
                        model_alias,
                        live.id,
                        principal_subject=principal_subject,
                        request_id=request_id,
                        enabled=True,
                    )
                return EnsureReadyResult(
                    deployment_id=live.id,
                    model_definition_id=definition.id,
                    actions=actions,
                    progress=progress,
                )

        await self._progress(
            progress,
            step="ensuring_capacity",
            message=f"Finding capacity for {definition.id}",
            percent=15.0,
            on_progress=on_progress,
        )

        for attempt in range(4):
            placements = self._candidate_placements(definition=definition)
            if placements:
                worker_id, gpu_indexes = placements[0]
                await self._progress(
                    progress,
                    step="loading",
                    message=(
                        f"Loading {definition.id} on {worker_id} GPU "
                        f"{','.join(str(i) for i in gpu_indexes)}"
                    ),
                    percent=55.0,
                    on_progress=on_progress,
                )
                try:
                    loaded = await self.lifecycle.load(
                        model_definition_id=definition.id,
                        worker_id=worker_id,
                        placement_mode="single_gpu",
                        gpu_indexes=gpu_indexes,
                        principal_subject=principal_subject,
                        request_id=request_id,
                    )
                except LifecycleError as exc:
                    if attempt == 3:
                        self._emit(
                            {
                                "event_type": "ensure_fail",
                                "model_definition_id": definition.id,
                                "reason": str(exc),
                                "request_id": request_id,
                                "principal_subject": principal_subject,
                                "details": {"code": exc.code.value},
                            }
                        )
                        raise EnsureReadyError(
                            exc.code, exc.message, details=exc.details
                        ) from exc
                    continue
                if model_alias:
                    self.lifecycle.set_alias(
                        model_alias,
                        loaded.id,
                        principal_subject=principal_subject,
                        request_id=request_id,
                        enabled=True,
                    )
                await self._progress(
                    progress,
                    step="ready",
                    message=f"Deployment {loaded.id} is ready",
                    percent=90.0,
                    on_progress=on_progress,
                )
                return EnsureReadyResult(
                    deployment_id=loaded.id,
                    model_definition_id=definition.id,
                    actions=actions,
                    progress=progress,
                )

            # Need eviction / demotion.
            victims = [
                deployment
                for deployment in self.routes.deployments.values()
                if deployment.status == DeploymentState.READY
                and self._idle_long_enough(deployment)
            ]
            victims.sort(key=self._eviction_rank)
            evicted = False
            for victim in victims:
                if victim.locked:
                    continue
                if self._is_sole_chat_lane(victim) or (
                    self.config.chat_alias in self._aliases_for_deployment(victim.id)
                    and self.config.chat_protect_sole_lane
                ):
                    demoted = await self._demote_chat(
                        victim,
                        principal_subject=principal_subject,
                        request_id=request_id,
                        progress=progress,
                        on_progress=on_progress,
                        actions=actions,
                    )
                    if demoted is not None:
                        evicted = True
                        break
                    continue
                if self._is_protected(victim):
                    continue
                # Preference B: try Diffusers first; only then llama.
                await self._evict_victim(
                    victim,
                    principal_subject=principal_subject,
                    request_id=request_id,
                    progress=progress,
                    on_progress=on_progress,
                    actions=actions,
                )
                evicted = True
                break
            if not evicted:
                self._emit(
                    {
                        "event_type": "ensure_fail",
                        "model_definition_id": definition.id,
                        "reason": "no_free_lane_and_no_idle_victim",
                        "request_id": request_id,
                        "principal_subject": principal_subject,
                    }
                )
                raise EnsureReadyError(
                    ErrorCode.CAPACITY_EXCEEDED,
                    (
                        "No free GPU lane with measured VRAM headroom, and no idle "
                        "unloadable deployment (busy, locked, or sole chat protected)."
                    ),
                    details={"model_definition_id": definition.id},
                )

        self._emit(
            {
                "event_type": "ensure_fail",
                "model_definition_id": definition.id,
                "reason": "ensure_exhausted_attempts",
                "request_id": request_id,
                "principal_subject": principal_subject,
            }
        )
        raise EnsureReadyError(
            ErrorCode.CAPACITY_EXCEEDED,
            f"Could not ensure capacity for {definition.id} after eviction attempts.",
            details={"model_definition_id": definition.id},
        )
