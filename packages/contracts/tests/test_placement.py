"""Tests for GPU placement and VRAM fit contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from yonder_contracts.hardware import (
    GpuMetrics,
    HardwareInventory,
    HostMetrics,
    MetricAvailability,
    MetricValue,
)
from yonder_contracts.placement import (
    GpuPlacement,
    PlacementMode,
    VramFitStatus,
    VramRequirement,
    WorkerGpuCapacity,
    assess_vram_fit,
)


def _metric(value: int | str, *, unit: str | None = None) -> MetricValue:
    return MetricValue.available(value, unit=unit)


def _unavailable() -> MetricValue:
    return MetricValue.unavailable(MetricAvailability.NOT_REPORTED)


def _gpu(index: int, *, total_bytes: int, available_bytes: int) -> GpuMetrics:
    return GpuMetrics(
        index=_metric(index),
        uuid=_metric(f"GPU-{index}"),
        name=_metric("Tesla V100-PCIE-32GB"),
        utilization_percent=_metric(0, unit="percent"),
        memory_used_bytes=_metric(total_bytes - available_bytes, unit="bytes"),
        memory_total_bytes=_metric(total_bytes, unit="bytes"),
        memory_available_bytes=_metric(available_bytes, unit="bytes"),
        temperature_c=_unavailable(),
        power_draw_watts=_unavailable(),
        power_limit_watts=_unavailable(),
        clock_graphics_mhz=_unavailable(),
        clock_memory_mhz=_unavailable(),
        ecc_status=_unavailable(),
        nvlink_state=_unavailable(),
        active_processes=_unavailable(),
    )


def _host() -> HostMetrics:
    return HostMetrics(
        hostname=_metric("ai1"),
        operating_system=_metric("rhel"),
        kernel_version=_metric("4.18"),
        architecture=_metric("x86_64"),
        cpu_model=_metric("xeon"),
        cpu_count=_metric(72),
        cpu_utilization_percent=_unavailable(),
        memory_total_bytes=_unavailable(),
        memory_used_bytes=_unavailable(),
        memory_available_bytes=_unavailable(),
        uptime_seconds=_unavailable(),
        nvidia_driver_version=_metric("580.126.20"),
        cuda_version=_metric("13.0"),
    )


def test_single_gpu_placement_requires_one_index() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        GpuPlacement(
            mode=PlacementMode.SINGLE_GPU,
            worker_id="worker-ai1",
            gpu_indexes=(0, 1),
        )


def test_two_gpu_placement_requires_two_indexes() -> None:
    with pytest.raises(ValueError, match="exactly two"):
        GpuPlacement(
            mode=PlacementMode.TWO_GPU,
            worker_id="worker-ai1",
            gpu_indexes=(0,),
        )


def test_vram_fit_single_gpu() -> None:
    capacity = WorkerGpuCapacity(
        worker_id="worker-ai1",
        gpu_discovery_status=MetricAvailability.AVAILABLE,
        gpus=[
            _gpu(0, total_bytes=32 * 1024**3, available_bytes=30 * 1024**3),
            _gpu(1, total_bytes=32 * 1024**3, available_bytes=30 * 1024**3),
        ],
    )
    placement = GpuPlacement(
        mode=PlacementMode.SINGLE_GPU,
        worker_id="worker-ai1",
        gpu_indexes=(0,),
    )
    assessment = assess_vram_fit(
        placement=placement,
        requirement=VramRequirement.from_minimum_gb(24),
        capacity=capacity,
    )
    assert assessment.status == VramFitStatus.FITS


def test_vram_does_not_fit() -> None:
    capacity = WorkerGpuCapacity(
        worker_id="worker-ai1",
        gpu_discovery_status=MetricAvailability.AVAILABLE,
        gpus=[_gpu(0, total_bytes=16 * 1024**3, available_bytes=14 * 1024**3)],
    )
    placement = GpuPlacement(
        mode=PlacementMode.SINGLE_GPU,
        worker_id="worker-ai1",
        gpu_indexes=(0,),
    )
    assessment = assess_vram_fit(
        placement=placement,
        requirement=VramRequirement.from_minimum_gb(24),
        capacity=capacity,
    )
    assert assessment.status == VramFitStatus.DOES_NOT_FIT


def test_vram_fit_uses_available_not_installed_capacity() -> None:
    capacity = WorkerGpuCapacity(
        worker_id="worker-ai1",
        gpu_discovery_status=MetricAvailability.AVAILABLE,
        gpus=[
            _gpu(
                0,
                total_bytes=32 * 1024**3,
                available_bytes=20 * 1024**3,
            )
        ],
    )
    placement = GpuPlacement(
        mode=PlacementMode.SINGLE_GPU,
        worker_id="worker-ai1",
        gpu_indexes=(0,),
    )

    assessment = assess_vram_fit(
        placement=placement,
        requirement=VramRequirement.from_minimum_gb(24),
        capacity=capacity,
    )

    assert assessment.status == VramFitStatus.DOES_NOT_FIT
    assert "available" in (assessment.message or "")
    assert "reserved" in (assessment.message or "")


def test_vram_fit_fails_closed_when_available_memory_is_unreported() -> None:
    gpu = _gpu(
        0,
        total_bytes=32 * 1024**3,
        available_bytes=30 * 1024**3,
    )
    gpu.memory_available_bytes = MetricValue.unavailable(
        MetricAvailability.NOT_REPORTED,
        message="driver did not report free memory",
    )
    capacity = WorkerGpuCapacity(
        worker_id="worker-ai1",
        gpu_discovery_status=MetricAvailability.AVAILABLE,
        gpus=[gpu],
    )
    placement = GpuPlacement(
        mode=PlacementMode.SINGLE_GPU,
        worker_id="worker-ai1",
        gpu_indexes=(0,),
    )

    assessment = assess_vram_fit(
        placement=placement,
        requirement=VramRequirement.from_minimum_gb(1),
        capacity=capacity,
    )

    assert assessment.status == VramFitStatus.INVENTORY_UNAVAILABLE


def test_ai5_shaped_inventory_unavailable() -> None:
    inventory = HardwareInventory(
        collected_at=datetime.now(UTC),
        host=_host(),
        gpus=[],
        gpu_discovery_status=MetricAvailability.COLLECTION_FAILED,
        gpu_discovery_message="GPUs not enumerating",
    )
    capacity = WorkerGpuCapacity.from_inventory(
        worker_id="worker-ai5",
        inventory=inventory,
        maintenance=True,
    )
    assert capacity.gpus == []
    placement = GpuPlacement(
        mode=PlacementMode.SINGLE_GPU,
        worker_id="worker-ai5",
        gpu_indexes=(0,),
    )
    assessment = assess_vram_fit(
        placement=placement,
        requirement=VramRequirement.from_minimum_gb(24),
        capacity=capacity,
    )
    assert assessment.status == VramFitStatus.WORKER_MAINTENANCE


def test_missing_gpu_index() -> None:
    capacity = WorkerGpuCapacity(
        worker_id="worker-ai1",
        gpu_discovery_status=MetricAvailability.AVAILABLE,
        gpus=[_gpu(0, total_bytes=32 * 1024**3, available_bytes=30 * 1024**3)],
    )
    placement = GpuPlacement(
        mode=PlacementMode.SINGLE_GPU,
        worker_id="worker-ai1",
        gpu_indexes=(1,),
    )
    assessment = assess_vram_fit(
        placement=placement,
        requirement=VramRequirement.from_minimum_gb(1),
        capacity=capacity,
    )
    assert assessment.status == VramFitStatus.GPU_INDEX_MISSING
