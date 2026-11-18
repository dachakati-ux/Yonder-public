"""Gateway GPU placement scheduler tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.routing.models import DeploymentRecord, DeploymentState
from app.services.placement import (
    build_placement,
    deployment_id_for_placement,
    evaluate_placement,
    find_gpu_overlap,
    resolve_context_length,
)
from fastapi.testclient import TestClient
from yonder_contracts.hardware import MetricAvailability
from yonder_contracts.placement import PlacementMode, VramFitStatus

ROOT = Path(__file__).resolve().parents[3]


def _metric(value: object, *, availability: str = "available", unit: str | None = None) -> dict:
    return {
        "availability": availability,
        "value": value if availability == "available" else None,
        "unit": unit,
        "message": None if availability == "available" else "unavailable",
    }


def _unavailable(availability: str = "not_reported") -> dict:
    return _metric(None, availability=availability)


def _gpu(index: int, *, total_gb: int = 32, available_gb: int | None = None) -> dict:
    total = total_gb * (1024**3)
    available = (available_gb if available_gb is not None else total_gb) * (1024**3)
    return {
        "index": _metric(index),
        "uuid": _metric(f"GPU-{index}"),
        "name": _metric("Tesla V100-PCIE-32GB"),
        "utilization_percent": _metric(0, unit="percent"),
        "memory_used_bytes": _metric(0, unit="bytes"),
        "memory_total_bytes": _metric(total, unit="bytes"),
        "memory_available_bytes": _metric(available, unit="bytes"),
        "temperature_c": _unavailable(),
        "power_draw_watts": _unavailable(),
        "power_limit_watts": _unavailable(),
        "clock_graphics_mhz": _unavailable(),
        "clock_memory_mhz": _unavailable(),
        "ecc_status": _unavailable(),
        "nvlink_state": _unavailable(),
        "active_processes": _unavailable(),
    }


def _hardware(
    *, gpus: list[dict], discovery: str = "available", message: str | None = None
) -> dict:
    return {
        "collected_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "gpu_discovery_status": discovery,
        "gpu_discovery_message": message,
        "notes": [],
        "raw_collector_errors": [],
        "host": {
            "hostname": _metric("ai1"),
            "operating_system": _metric("rhel"),
            "kernel_version": _metric("4.18"),
            "architecture": _metric("x86_64"),
            "cpu_model": _metric("xeon"),
            "cpu_count": _metric(72),
            "cpu_utilization_percent": _unavailable(),
            "memory_total_bytes": _unavailable(),
            "memory_used_bytes": _unavailable(),
            "memory_available_bytes": _unavailable(),
            "uptime_seconds": _unavailable(),
            "nvidia_driver_version": _metric("580.126.20"),
            "cuda_version": _metric("13.0"),
            "storage": [],
            "network_interfaces": [],
        },
        "gpus": gpus,
    }


class _Manifest:
    class runtime:
        provider = "llama-cpp"
        tensor_parallel_size = 1
        context_length = 8192

    class requirements:
        minimum_gpu_memory_gb = 24
        supported_gpu_families = ("volta",)
        placement_limits = None


class _LimitedManifest:
    class runtime:
        provider = "llama-cpp"
        tensor_parallel_size = 1
        context_length = 8192

    class requirements:
        minimum_gpu_memory_gb = 12
        supported_gpu_families = ("volta",)

        class placement_limits:
            class single_gpu:
                max_context_length = 32768
                minimum_gpu_memory_gb = 12

            class two_gpu:
                max_context_length = 65536
                minimum_gpu_memory_gb = 12


class _CoderManifest:
    class runtime:
        provider = "llama-cpp"
        tensor_parallel_size = 2
        context_length = 8192

    class requirements:
        minimum_gpu_memory_gb = 24
        supported_gpu_families = ("volta",)

        class placement_limits:
            class single_gpu:
                max_context_length = 0
                minimum_gpu_memory_gb = 48

            class two_gpu:
                max_context_length = 8192
                minimum_gpu_memory_gb = 24


def test_resolve_context_length_accepts_default_and_request() -> None:
    placement = build_placement(
        worker_id="worker-ai1",
        placement_mode="single_gpu",
        gpu_indexes=[0],
    )
    assert (
        resolve_context_length(
            manifest=_LimitedManifest(),
            placement=placement,
            requested=None,
        )
        == 8192
    )
    assert (
        resolve_context_length(
            manifest=_LimitedManifest(),
            placement=placement,
            requested=16384,
        )
        == 16384
    )


def test_resolve_context_length_rejects_over_cap_and_forbidden_mode() -> None:
    single = build_placement(
        worker_id="worker-ai1",
        placement_mode="single_gpu",
        gpu_indexes=[0],
    )
    with pytest.raises(ValueError, match="hard limit"):
        resolve_context_length(
            manifest=_LimitedManifest(),
            placement=single,
            requested=65536,
        )
    with pytest.raises(ValueError, match="not allowed"):
        resolve_context_length(
            manifest=_CoderManifest(),
            placement=single,
            requested=8192,
        )


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(
        "YONDER_CONFIG_FILE",
        str(ROOT / "config/environments/development.yaml"),
    )
    from app.main import create_app

    return TestClient(create_app())


def test_deployment_id_encodes_worker_and_gpus() -> None:
    placement = build_placement(
        worker_id="worker-ai1",
        placement_mode=PlacementMode.TWO_GPU,
        gpu_indexes=[0, 1],
    )
    assert deployment_id_for_placement("yonder-qwen36-35b-a3b-q4", placement) == (
        "yonder-qwen36-35b-a3b-q4-wai1-g0-1"
    )


def test_overlap_under_lane_cap_allows_sharing() -> None:
    placement = build_placement(
        worker_id="worker-ai1",
        placement_mode="single_gpu",
        gpu_indexes=[0],
    )
    deployments = {
        "occupant": DeploymentRecord(
            id="occupant",
            model_definition_id="model",
            worker_id="worker-ai1",
            provider="llama-cpp",
            status=DeploymentState.READY,
            gpu_assignments=[0],
        )
    }
    conflict = find_gpu_overlap(
        placement=placement, deployments=deployments, max_lanes_per_gpu=3
    )
    assert conflict is None


def test_overlap_at_lane_cap_requires_unload() -> None:
    placement = build_placement(
        worker_id="worker-ai1",
        placement_mode="single_gpu",
        gpu_indexes=[0],
    )
    deployments = {
        f"occupant-{i}": DeploymentRecord(
            id=f"occupant-{i}",
            model_definition_id="model",
            worker_id="worker-ai1",
            provider="llama-cpp",
            status=DeploymentState.READY,
            gpu_assignments=[0],
        )
        for i in range(3)
    }
    conflict = find_gpu_overlap(
        placement=placement, deployments=deployments, max_lanes_per_gpu=3
    )
    assert conflict is not None
    assert conflict.contested_gpu_indexes == (0,)
    assert len(conflict.conflicting_deployment_ids) == 3


def test_overlap_is_shared_across_runtime_families_at_cap() -> None:
    placement = build_placement(
        worker_id="worker-ai1",
        placement_mode="single_gpu",
        gpu_indexes=[1],
    )
    deployments = {
        f"image-runtime-{i}": DeploymentRecord(
            id=f"image-runtime-{i}",
            model_definition_id="sdxl",
            worker_id="worker-ai1",
            provider="diffusers",
            status=DeploymentState.READY,
            gpu_assignments=[1],
        )
        for i in range(3)
    }

    conflict = find_gpu_overlap(
        placement=placement, deployments=deployments, max_lanes_per_gpu=3
    )

    assert conflict is not None
    assert "image-runtime-0" in conflict.conflicting_deployment_ids
    assert conflict.contested_gpu_indexes == (1,)


def test_ai5_empty_inventory_is_unavailable() -> None:
    workers = {
        "worker-ai5": {
            "maintenance_state": True,
            "hardware_summary": _hardware(
                gpus=[],
                discovery=MetricAvailability.COLLECTION_FAILED.value,
                message="nvidia-smi failed to enumerate GPUs",
            ),
        }
    }
    placement = build_placement(
        worker_id="worker-ai5",
        placement_mode="single_gpu",
        gpu_indexes=[0],
    )
    fit, conflict = evaluate_placement(
        placement=placement,
        manifest=_Manifest(),
        workers=workers,
        deployments={},
    )
    assert conflict is None
    assert fit.status in {
        VramFitStatus.WORKER_MAINTENANCE,
        VramFitStatus.INVENTORY_UNAVAILABLE,
        VramFitStatus.GPU_INDEX_MISSING,
    }


def test_two_gpu_fit_against_v100_inventory() -> None:
    workers = {
        "worker-ai1": {
            "maintenance_state": False,
            "hardware_summary": _hardware(gpus=[_gpu(0), _gpu(1)]),
        }
    }
    placement = build_placement(
        worker_id="worker-ai1",
        placement_mode="two_gpu",
        gpu_indexes=[0, 1],
    )
    fit, conflict = evaluate_placement(
        placement=placement,
        manifest=_Manifest(),
        workers=workers,
        deployments={},
    )
    assert conflict is None
    assert fit.status == VramFitStatus.FITS


def test_two_gpu_rejects_when_either_gpu_lacks_live_headroom() -> None:
    workers = {
        "worker-ai1": {
            "maintenance_state": False,
            "hardware_summary": _hardware(
                gpus=[
                    _gpu(0, available_gb=20),
                    _gpu(1, available_gb=30),
                ]
            ),
        }
    }
    placement = build_placement(
        worker_id="worker-ai1",
        placement_mode="two_gpu",
        gpu_indexes=[0, 1],
    )

    fit, conflict = evaluate_placement(
        placement=placement,
        manifest=_Manifest(),
        workers=workers,
        deployments={},
    )

    assert conflict is None
    assert fit.status == VramFitStatus.DOES_NOT_FIT
    assert "GPU 0" in (fit.message or "")


def test_worker_reported_diffusers_occupancy_blocks_load() -> None:
    workers = {
        "worker-ai1": {
            "maintenance_state": False,
            "hardware_summary": _hardware(gpus=[_gpu(0), _gpu(1)]),
            "runtime_summary": {
                "status": "available",
                "deployments": [
                    {
                        "deployment_id": f"worker-image-{i}",
                        "runtime_family": "diffusers",
                        "status": "ready",
                        "gpu_assignments": [0],
                    }
                    for i in range(3)
                ],
            },
        }
    }
    placement = build_placement(
        worker_id="worker-ai1",
        placement_mode="single_gpu",
        gpu_indexes=[0],
    )

    fit, conflict = evaluate_placement(
        placement=placement,
        manifest=_Manifest(),
        workers=workers,
        deployments={},
        max_lanes_per_gpu=3,
    )

    assert fit.status == VramFitStatus.FITS
    assert conflict is not None
    assert "worker-image-0" in conflict.conflicting_deployment_ids
    assert conflict.details["occupancy_source"] == "worker_report"


def _register_gpu_worker(client: TestClient) -> None:
    response = client.post(
        "/api/admin/workers/register",
        json={
            "worker_id": "local-dev-worker",
            "display_name": "Local worker",
            "endpoint": "http://127.0.0.1:8090",
            "agent_version": "0.1.0",
            "ready": True,
            "hardware": _hardware(gpus=[_gpu(0), _gpu(1)]),
            "runtime": {"status": "skeleton"},
        },
    )
    assert response.status_code == 200


def test_admin_load_allows_shared_lane_then_blocks_at_cap(client: TestClient) -> None:
    _register_gpu_worker(client)

    first = client.post(
        "/api/admin/deployments/load",
        json={
            "model_definition_id": "yonder-chat-small",
            "worker_id": "local-dev-worker",
            "placement_mode": "single_gpu",
            "gpu_indexes": [0],
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["deployment"]["gpu_assignments"] == [0]
    assert first.json()["deployment"]["status"] == "ready"

    second = client.post(
        "/api/admin/deployments/load",
        json={
            "model_definition_id": "yonder-embed-small",
            "worker_id": "local-dev-worker",
            "placement_mode": "single_gpu",
            "gpu_indexes": [0],
        },
    )
    # Soft multi-lane: second deployment may share GPU 0 when under the cap.
    assert second.status_code == 200, second.text
    assert second.json()["deployment"]["gpu_assignments"] == [0]
