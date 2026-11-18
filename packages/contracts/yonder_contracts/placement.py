"""GPU placement, VRAM fit, and unload-conflict contracts.

Capacity checks must use MetricAvailability. Never fabricate GPU totals.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from yonder_contracts.hardware import (
    GpuMetrics,
    HardwareInventory,
    MetricAvailability,
    MetricValue,
)

DEFAULT_VRAM_RESERVE_BYTES = 1024**3


class PlacementMode(StrEnum):
    SINGLE_GPU = "single_gpu"
    TWO_GPU = "two_gpu"


class GpuPlacement(BaseModel):
    """Explicit placement of one deployment onto a worker's GPU indexes."""

    model_config = ConfigDict(extra="forbid")

    mode: PlacementMode
    worker_id: str
    gpu_indexes: tuple[int, ...]
    gpu_uuids: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def indexes_match_mode(self) -> GpuPlacement:
        if self.mode == PlacementMode.SINGLE_GPU and len(self.gpu_indexes) != 1:
            raise ValueError("single_gpu placement requires exactly one gpu index")
        if self.mode == PlacementMode.TWO_GPU and len(self.gpu_indexes) != 2:
            raise ValueError("two_gpu placement requires exactly two gpu indexes")
        if len(set(self.gpu_indexes)) != len(self.gpu_indexes):
            raise ValueError("gpu_indexes must be unique")
        if any(index < 0 for index in self.gpu_indexes):
            raise ValueError("gpu_indexes must be non-negative")
        if self.gpu_uuids is not None and len(self.gpu_uuids) != len(self.gpu_indexes):
            raise ValueError("gpu_uuids length must match gpu_indexes")
        return self

    @property
    def gpu_assignments(self) -> list[int]:
        return list(self.gpu_indexes)


class VramRequirement(BaseModel):
    """Declared (not measured) VRAM needs from a model manifest."""

    model_config = ConfigDict(extra="forbid")

    minimum_bytes_per_gpu: int | None = None
    minimum_total_bytes: int | None = None
    supported_gpu_families: tuple[str, ...] = ()
    tensor_parallel_size: int = Field(default=1, ge=1)
    reserve_bytes_per_gpu: int = Field(
        default=DEFAULT_VRAM_RESERVE_BYTES,
        ge=0,
        description="VRAM kept free on every selected GPU for CUDA/runtime overhead.",
    )

    @classmethod
    def from_minimum_gb(
        cls,
        minimum_gpu_memory_gb: float | int | None,
        *,
        tensor_parallel_size: int = 1,
        supported_gpu_families: tuple[str, ...] | list[str] | None = None,
        reserve_bytes_per_gpu: int = DEFAULT_VRAM_RESERVE_BYTES,
    ) -> VramRequirement:
        per_gpu: int | None = None
        if minimum_gpu_memory_gb is not None:
            per_gpu = int(float(minimum_gpu_memory_gb) * (1024**3))
        total = per_gpu * tensor_parallel_size if per_gpu is not None else None
        return cls(
            minimum_bytes_per_gpu=per_gpu,
            minimum_total_bytes=total,
            supported_gpu_families=tuple(supported_gpu_families or ()),
            tensor_parallel_size=tensor_parallel_size,
            reserve_bytes_per_gpu=reserve_bytes_per_gpu,
        )


class VramFitStatus(StrEnum):
    FITS = "fits"
    DOES_NOT_FIT = "does_not_fit"
    INVENTORY_UNAVAILABLE = "inventory_unavailable"
    WORKER_MAINTENANCE = "worker_maintenance"
    GPU_INDEX_MISSING = "gpu_index_missing"
    FAMILY_UNSUPPORTED = "family_unsupported"
    NOT_EVALUATED = "not_evaluated"


class VramFitAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: VramFitStatus
    placement: GpuPlacement | None = None
    requirement: VramRequirement
    per_gpu_memory_total_bytes: tuple[MetricValue, ...] = ()
    per_gpu_memory_available_bytes: tuple[MetricValue, ...] = ()
    message: str | None = None


class UnloadConflictReason(StrEnum):
    DEPLOYMENT_BUSY = "deployment_busy"
    LOAD_IN_PROGRESS = "load_in_progress"
    UNLOAD_IN_PROGRESS = "unload_in_progress"
    GPU_OVERLAP_WITH_READY = "gpu_overlap_with_ready"
    GPU_OVERLAP_WITH_LOADING = "gpu_overlap_with_loading"
    SHARED_RUNTIME_OPERATOR_MANAGED = "shared_runtime_operator_managed"


class UnloadConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["CONFLICTING_OPERATION", "GPU_PLACEMENT_CONFLICT"] = (
        "GPU_PLACEMENT_CONFLICT"
    )
    reason: UnloadConflictReason
    deployment_id: str
    conflicting_deployment_ids: tuple[str, ...] = ()
    contested_gpu_indexes: tuple[int, ...] = ()
    details: dict[str, Any] = Field(default_factory=dict)


class WorkerGpuCapacity(BaseModel):
    """Honest capacity view — empty gpus is valid when discovery failed."""

    model_config = ConfigDict(extra="forbid")

    worker_id: str
    maintenance: bool = False
    gpu_discovery_status: MetricAvailability
    gpu_discovery_message: str | None = None
    gpus: list[GpuMetrics] = Field(default_factory=list)

    @classmethod
    def from_inventory(
        cls,
        *,
        worker_id: str,
        inventory: HardwareInventory | None,
        maintenance: bool = False,
    ) -> WorkerGpuCapacity:
        if inventory is None:
            return cls(
                worker_id=worker_id,
                maintenance=maintenance,
                gpu_discovery_status=MetricAvailability.NOT_REPORTED,
                gpu_discovery_message="No hardware inventory reported",
                gpus=[],
            )
        status = inventory.gpu_discovery_status
        gpus = list(inventory.gpus) if status == MetricAvailability.AVAILABLE else []
        return cls(
            worker_id=worker_id,
            maintenance=maintenance,
            gpu_discovery_status=status,
            gpu_discovery_message=inventory.gpu_discovery_message,
            gpus=gpus,
        )


def assess_vram_fit(
    *,
    placement: GpuPlacement,
    requirement: VramRequirement,
    capacity: WorkerGpuCapacity,
) -> VramFitAssessment:
    """Fail-closed VRAM fit check against measured worker inventory."""

    if capacity.maintenance:
        return VramFitAssessment(
            status=VramFitStatus.WORKER_MAINTENANCE,
            placement=placement,
            requirement=requirement,
            message=f"Worker '{capacity.worker_id}' is in maintenance",
        )
    if capacity.gpu_discovery_status != MetricAvailability.AVAILABLE or not capacity.gpus:
        return VramFitAssessment(
            status=VramFitStatus.INVENTORY_UNAVAILABLE,
            placement=placement,
            requirement=requirement,
            message=(
                capacity.gpu_discovery_message
                or f"GPU inventory unavailable for worker '{capacity.worker_id}' "
                f"({capacity.gpu_discovery_status.value})"
            ),
        )

    by_index: dict[int, GpuMetrics] = {}
    for gpu in capacity.gpus:
        if gpu.index.availability != MetricAvailability.AVAILABLE:
            continue
        if not isinstance(gpu.index.value, int):
            continue
        by_index[int(gpu.index.value)] = gpu

    totals: list[MetricValue] = []
    availables: list[MetricValue] = []
    for index in placement.gpu_indexes:
        gpu = by_index.get(index)
        if gpu is None:
            return VramFitAssessment(
                status=VramFitStatus.GPU_INDEX_MISSING,
                placement=placement,
                requirement=requirement,
                message=f"GPU index {index} not present on worker '{capacity.worker_id}'",
            )
        totals.append(gpu.memory_total_bytes)
        availables.append(gpu.memory_available_bytes)
        if gpu.memory_total_bytes.availability != MetricAvailability.AVAILABLE:
            return VramFitAssessment(
                status=VramFitStatus.INVENTORY_UNAVAILABLE,
                placement=placement,
                requirement=requirement,
                per_gpu_memory_total_bytes=tuple(totals),
                per_gpu_memory_available_bytes=tuple(availables),
                message=f"GPU {index} memory_total_bytes is not available",
            )
        if gpu.memory_available_bytes.availability != MetricAvailability.AVAILABLE:
            return VramFitAssessment(
                status=VramFitStatus.INVENTORY_UNAVAILABLE,
                placement=placement,
                requirement=requirement,
                per_gpu_memory_total_bytes=tuple(totals),
                per_gpu_memory_available_bytes=tuple(availables),
                message=f"GPU {index} memory_available_bytes is not available",
            )
        if requirement.minimum_bytes_per_gpu is not None:
            available = gpu.memory_available_bytes.value
            if not isinstance(available, (int, float)):
                return VramFitAssessment(
                    status=VramFitStatus.INVENTORY_UNAVAILABLE,
                    placement=placement,
                    requirement=requirement,
                    per_gpu_memory_total_bytes=tuple(totals),
                    per_gpu_memory_available_bytes=tuple(availables),
                    message=f"GPU {index} memory_available_bytes has no numeric value",
                )
            required_with_reserve = (
                requirement.minimum_bytes_per_gpu
                + requirement.reserve_bytes_per_gpu
            )
            if int(available) < required_with_reserve:
                return VramFitAssessment(
                    status=VramFitStatus.DOES_NOT_FIT,
                    placement=placement,
                    requirement=requirement,
                    per_gpu_memory_total_bytes=tuple(totals),
                    per_gpu_memory_available_bytes=tuple(availables),
                    message=(
                        f"GPU {index} has {int(available)} bytes available; requires "
                        f"{requirement.minimum_bytes_per_gpu} bytes plus "
                        f"{requirement.reserve_bytes_per_gpu} bytes reserved"
                    ),
                )

    if requirement.minimum_total_bytes is not None:
        aggregate = 0
        for metric in availables:
            if metric.availability != MetricAvailability.AVAILABLE or not isinstance(
                metric.value, (int, float)
            ):
                return VramFitAssessment(
                    status=VramFitStatus.INVENTORY_UNAVAILABLE,
                    placement=placement,
                    requirement=requirement,
                    per_gpu_memory_total_bytes=tuple(totals),
                    per_gpu_memory_available_bytes=tuple(availables),
                    message="Cannot aggregate available GPU memory",
                )
            aggregate += int(metric.value)
        required_total_with_reserve = (
            requirement.minimum_total_bytes
            + requirement.reserve_bytes_per_gpu * len(placement.gpu_indexes)
        )
        if aggregate < required_total_with_reserve:
            return VramFitAssessment(
                status=VramFitStatus.DOES_NOT_FIT,
                placement=placement,
                requirement=requirement,
                per_gpu_memory_total_bytes=tuple(totals),
                per_gpu_memory_available_bytes=tuple(availables),
                message=(
                    f"Placement has {aggregate} bytes available VRAM; requires "
                    f"{requirement.minimum_total_bytes} bytes plus "
                    f"{requirement.reserve_bytes_per_gpu} bytes reserved per GPU"
                ),
            )

    return VramFitAssessment(
        status=VramFitStatus.FITS,
        placement=placement,
        requirement=requirement,
        per_gpu_memory_total_bytes=tuple(totals),
        per_gpu_memory_available_bytes=tuple(availables),
        message="Placement fits measured GPU capacity",
    )
