"""Gateway placement validation against worker inventory."""

from __future__ import annotations

from typing import Any

from yonder_contracts.errors import ErrorCode
from yonder_contracts.hardware import HardwareInventory, MetricAvailability
from yonder_contracts.placement import (
    GpuPlacement,
    PlacementMode,
    UnloadConflict,
    UnloadConflictReason,
    VramFitAssessment,
    VramFitStatus,
    VramRequirement,
    WorkerGpuCapacity,
    assess_vram_fit,
)

from app.routing.models import DeploymentRecord, DeploymentState

OCCUPYING_STATES = frozenset(
    {
        DeploymentState.READY,
        DeploymentState.LOADING,
        DeploymentState.QUEUED,
        DeploymentState.VALIDATING,
        DeploymentState.DRAINING,
    }
)


def build_placement(
    *,
    worker_id: str,
    placement_mode: str | PlacementMode,
    gpu_indexes: list[int] | tuple[int, ...],
) -> GpuPlacement:
    mode = (
        placement_mode
        if isinstance(placement_mode, PlacementMode)
        else PlacementMode(placement_mode)
    )
    return GpuPlacement(
        mode=mode,
        worker_id=worker_id,
        gpu_indexes=tuple(int(index) for index in gpu_indexes),
    )


def worker_capacity_from_record(worker_id: str, record: dict[str, Any] | None) -> WorkerGpuCapacity:
    if record is None:
        return WorkerGpuCapacity(
            worker_id=worker_id,
            maintenance=False,
            gpu_discovery_status=MetricAvailability.NOT_REPORTED,
            gpu_discovery_message=f"Worker '{worker_id}' is not registered",
            gpus=[],
        )
    maintenance = bool(record.get("maintenance_state") or record.get("maintenance"))
    hardware = record.get("hardware_summary")
    inventory: HardwareInventory | None = None
    if isinstance(hardware, dict) and hardware:
        try:
            inventory = HardwareInventory.model_validate(hardware)
        except Exception:  # noqa: BLE001
            inventory = None
    return WorkerGpuCapacity.from_inventory(
        worker_id=worker_id,
        inventory=inventory,
        maintenance=maintenance,
    )


def requirement_from_manifest(
    manifest: Any,
    *,
    placement: GpuPlacement | None = None,
) -> VramRequirement:
    runtime = getattr(manifest, "runtime", None)
    requirements = getattr(manifest, "requirements", None)
    if placement is not None:
        tp = 2 if placement.mode == PlacementMode.TWO_GPU else 1
    else:
        tp = int(getattr(runtime, "tensor_parallel_size", 1) or 1)
    minimum = getattr(requirements, "minimum_gpu_memory_gb", None)
    mode_limit = mode_placement_limits(manifest, placement.mode) if placement else None
    if mode_limit is not None and getattr(mode_limit, "minimum_gpu_memory_gb", None):
        minimum = mode_limit.minimum_gpu_memory_gb
    families = tuple(getattr(requirements, "supported_gpu_families", None) or ())
    return VramRequirement.from_minimum_gb(
        minimum,
        tensor_parallel_size=tp,
        supported_gpu_families=families,
    )


def mode_placement_limits(manifest: Any, mode: PlacementMode | str) -> Any | None:
    requirements = getattr(manifest, "requirements", None)
    limits = getattr(requirements, "placement_limits", None)
    if limits is None:
        return None
    mode_value = mode.value if isinstance(mode, PlacementMode) else str(mode)
    if mode_value == PlacementMode.SINGLE_GPU.value:
        return getattr(limits, "single_gpu", None)
    if mode_value == PlacementMode.TWO_GPU.value:
        return getattr(limits, "two_gpu", None)
    return None


def resolve_context_length(
    *,
    manifest: Any,
    placement: GpuPlacement | None,
    requested: int | None,
) -> int:
    """Resolve and validate load-time context against placement hard caps.

    When placement_limits are absent, any positive requested/default context is
    accepted (legacy manifests). When limits exist, a missing or zero
    max_context_length for the selected mode forbids that mode.
    """
    default_ctx = int(getattr(getattr(manifest, "runtime", None), "context_length", 0) or 0)
    context_length = int(requested) if requested is not None else default_ctx
    if context_length < 1:
        raise ValueError("context_length must be >= 1")

    if placement is None:
        return context_length

    mode_limit = mode_placement_limits(manifest, placement.mode)
    limits = getattr(getattr(manifest, "requirements", None), "placement_limits", None)
    if limits is None:
        return context_length
    if mode_limit is None or int(getattr(mode_limit, "max_context_length", 0) or 0) <= 0:
        mode_name = (
            placement.mode.value
            if isinstance(placement.mode, PlacementMode)
            else str(placement.mode)
        )
        raise ValueError(
            f"Placement mode '{mode_name}' is not allowed for this model "
            "(no positive max_context_length in placement_limits)."
        )
    max_ctx = int(mode_limit.max_context_length)
    if context_length > max_ctx:
        raise ValueError(
            f"Requested context_length {context_length} exceeds "
            f"{placement.mode.value} hard limit of {max_ctx}."
        )
    return context_length


def lane_counts_for_worker(
    *,
    worker_id: str,
    deployments: dict[str, DeploymentRecord],
    exclude_deployment_id: str | None = None,
) -> dict[int, int]:
    """Count occupying deployments per GPU index on one worker."""
    counts: dict[int, int] = {}
    for deployment in deployments.values():
        if exclude_deployment_id and deployment.id == exclude_deployment_id:
            continue
        if deployment.worker_id != worker_id:
            continue
        if deployment.status not in OCCUPYING_STATES:
            continue
        for index in deployment.gpu_assignments:
            counts[int(index)] = counts.get(int(index), 0) + 1
    return counts


def find_gpu_overlap(
    *,
    placement: GpuPlacement,
    deployments: dict[str, DeploymentRecord],
    exclude_deployment_id: str | None = None,
    max_lanes_per_gpu: int = 3,
) -> UnloadConflict | None:
    """Conflict when any requested GPU already has max_lanes_per_gpu occupants.

    Sharing below the lane cap is allowed; measured VRAM fit is enforced
    separately by evaluate_placement / assess_vram_fit.
    """
    counts = lane_counts_for_worker(
        worker_id=placement.worker_id,
        deployments=deployments,
        exclude_deployment_id=exclude_deployment_id,
    )
    contested: set[int] = set()
    conflicting: list[str] = []
    loading = False
    for gpu_index in placement.gpu_indexes:
        if counts.get(int(gpu_index), 0) < max_lanes_per_gpu:
            continue
        contested.add(int(gpu_index))
    if not contested:
        return None
    for deployment in deployments.values():
        if exclude_deployment_id and deployment.id == exclude_deployment_id:
            continue
        if deployment.worker_id != placement.worker_id:
            continue
        if deployment.status not in OCCUPYING_STATES:
            continue
        overlap = sorted(set(deployment.gpu_assignments) & contested)
        if not overlap:
            continue
        conflicting.append(deployment.id)
        if deployment.status in {
            DeploymentState.LOADING,
            DeploymentState.QUEUED,
            DeploymentState.VALIDATING,
        }:
            loading = True
    if not conflicting:
        return None
    reason = (
        UnloadConflictReason.GPU_OVERLAP_WITH_LOADING
        if loading
        else UnloadConflictReason.GPU_OVERLAP_WITH_READY
    )
    return UnloadConflict(
        reason=reason,
        deployment_id=exclude_deployment_id or "",
        conflicting_deployment_ids=tuple(conflicting),
        contested_gpu_indexes=tuple(sorted(contested)),
        details={
            "worker_id": placement.worker_id,
            "max_lanes_per_gpu": max_lanes_per_gpu,
            "lane_counts": {str(k): v for k, v in sorted(counts.items())},
            "message": (
                f"Requested GPUs are at the lane cap ({max_lanes_per_gpu} per GPU). "
                "Unload an idle deployment or free a lane before loading."
            ),
        },
    )


def find_reported_runtime_overlap(
    *,
    placement: GpuPlacement,
    workers: dict[str, Any],
    known_deployment_ids: set[str],
    exclude_deployment_id: str | None = None,
    max_lanes_per_gpu: int = 3,
) -> UnloadConflict | None:
    record = workers.get(placement.worker_id)
    if record is None:
        return None
    runtime = (
        record.get("runtime_summary", {})
        if isinstance(record, dict)
        else getattr(record, "runtime_summary", {})
    )
    deployments = runtime.get("deployments", []) if isinstance(runtime, dict) else []
    counts: dict[int, int] = {}
    orphan_by_gpu: dict[int, list[str]] = {}
    for item in deployments:
        if not isinstance(item, dict):
            continue
        deployment_id = str(item.get("deployment_id") or item.get("id") or "")
        if (
            not deployment_id
            or deployment_id == exclude_deployment_id
            or deployment_id in known_deployment_ids
            or str(item.get("status")) not in {"ready", "loading", "draining"}
        ):
            continue
        indexes = {int(index) for index in item.get("gpu_assignments", [])}
        for index in indexes:
            counts[index] = counts.get(index, 0) + 1
            orphan_by_gpu.setdefault(index, []).append(deployment_id)
    contested: set[int] = set()
    conflicting: list[str] = []
    for gpu_index in placement.gpu_indexes:
        if counts.get(int(gpu_index), 0) < max_lanes_per_gpu:
            continue
        contested.add(int(gpu_index))
        conflicting.extend(orphan_by_gpu.get(int(gpu_index), []))
    if not contested:
        return None
    return UnloadConflict(
        reason=UnloadConflictReason.GPU_OVERLAP_WITH_READY,
        deployment_id=exclude_deployment_id or "",
        conflicting_deployment_ids=tuple(dict.fromkeys(conflicting)),
        contested_gpu_indexes=tuple(sorted(contested)),
        details={
            "worker_id": placement.worker_id,
            "occupancy_source": "worker_report",
            "max_lanes_per_gpu": max_lanes_per_gpu,
            "message": (
                f"Worker-reported runtimes fill the lane cap ({max_lanes_per_gpu}/GPU). "
                "Unload conflicting deployment(s) before loading."
            ),
        },
    )


def evaluate_placement(
    *,
    placement: GpuPlacement,
    manifest: Any,
    workers: dict[str, Any],
    deployments: dict[str, DeploymentRecord],
    exclude_deployment_id: str | None = None,
    max_lanes_per_gpu: int = 3,
) -> tuple[VramFitAssessment, UnloadConflict | None]:
    capacity = worker_capacity_from_record(placement.worker_id, workers.get(placement.worker_id))
    requirement = requirement_from_manifest(manifest, placement=placement)
    fit = assess_vram_fit(
        placement=placement,
        requirement=requirement,
        capacity=capacity,
    )
    conflict = find_gpu_overlap(
        placement=placement,
        deployments=deployments,
        exclude_deployment_id=exclude_deployment_id,
        max_lanes_per_gpu=max_lanes_per_gpu,
    )
    if conflict is None:
        conflict = find_reported_runtime_overlap(
            placement=placement,
            workers=workers,
            known_deployment_ids=set(deployments),
            exclude_deployment_id=exclude_deployment_id,
            max_lanes_per_gpu=max_lanes_per_gpu,
        )
    return fit, conflict


def placement_error_code(fit: VramFitAssessment) -> ErrorCode:
    if fit.status == VramFitStatus.WORKER_MAINTENANCE:
        return ErrorCode.WORKER_UNAVAILABLE
    if fit.status in {
        VramFitStatus.INVENTORY_UNAVAILABLE,
        VramFitStatus.GPU_INDEX_MISSING,
    }:
        return ErrorCode.WORKER_UNAVAILABLE
    if fit.status == VramFitStatus.DOES_NOT_FIT:
        return ErrorCode.INVALID_REQUEST
    return ErrorCode.INVALID_REQUEST


def deployment_id_for_placement(
    model_definition_id: str,
    placement: GpuPlacement | None,
) -> str:
    if placement is None:
        return f"{model_definition_id}-primary"
    indexes = "-".join(str(index) for index in placement.gpu_indexes)
    worker = placement.worker_id.replace("worker-", "w")
    return f"{model_definition_id}-{worker}-g{indexes}"
