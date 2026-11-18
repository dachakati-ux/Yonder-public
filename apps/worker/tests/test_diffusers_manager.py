"""Diffusers worker tests use dry-run and require neither torch nor diffusers."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.runtime.diffusers_manager import (
    DiffusersRuntimeManager,
    resolve_cuda_device,
)
from fastapi.testclient import TestClient
from yonder_configuration.models import DiffusersWorkerRuntimeConfig


def _manager(root: Path) -> DiffusersRuntimeManager:
    return DiffusersRuntimeManager(
        DiffusersWorkerRuntimeConfig(
            enabled=False,
            dry_run=True,
            allowed_model_roots=[root],
        )
    )


def test_resolve_cuda_device_uses_physical_index() -> None:
    assert resolve_cuda_device([1]) == "cuda:1"
    assert resolve_cuda_device([0, 1]) == "cuda:0"
    with pytest.raises(RuntimeError, match="required"):
        resolve_cuda_device([])


def test_dry_run_load_reports_assigned_device(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    loaded = manager.load(
        deployment_id="video-g1",
        model_definition_id="wan-a",
        runtime_configuration={
            "profile": "video",
            "family": "wan",
            "model_path": str(tmp_path / "wan"),
        },
        gpu_assignments=[1],
    )
    assert loaded["status"] == "ready"
    assert loaded["device"] == "cuda:1"
    assert loaded["gpu_assignments"] == [1]


def test_diffusers_manager_refuses_maintenance(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    with pytest.raises(RuntimeError, match="maintenance"):
        manager.load(
            deployment_id="image-a",
            model_definition_id="sdxl-a",
            runtime_configuration={
                "profile": "image",
                "family": "sdxl",
                "model_path": str(tmp_path / "sdxl"),
            },
            gpu_assignments=[0],
            maintenance=True,
        )


def test_diffusers_manager_refuses_when_lane_cap_reached(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    for index in range(3):
        loaded = manager.load(
            deployment_id=f"image-{index}",
            model_definition_id=f"sdxl-{index}",
            runtime_configuration={
                "profile": "image",
                "family": "sdxl",
                "model_path": str(tmp_path / f"sdxl-{index}"),
            },
            gpu_assignments=[0],
        )
        assert loaded["status"] == "ready"

    with pytest.raises(RuntimeError, match="lane|occupied|capacity"):
        manager.load(
            deployment_id="video-a",
            model_definition_id="wan-a",
            runtime_configuration={
                "profile": "video",
                "family": "wan",
                "model_path": str(tmp_path / "wan"),
            },
            gpu_assignments=[0],
        )


def test_resolve_sdxl_weights_single_file(tmp_path: Path) -> None:
    weights = tmp_path / "sd_xl_turbo_1.0_fp16.safetensors"
    weights.write_bytes(b"fake")
    assert DiffusersRuntimeManager._resolve_sdxl_weights(weights) == weights.resolve()
    assert (
        DiffusersRuntimeManager._resolve_sdxl_weights(tmp_path) == weights.resolve()
    )


def test_resolve_sdxl_weights_prefers_diffusers_dir(tmp_path: Path) -> None:
    (tmp_path / "model_index.json").write_text("{}", encoding="utf-8")
    (tmp_path / "weights.safetensors").write_bytes(b"fake")
    assert DiffusersRuntimeManager._resolve_sdxl_weights(tmp_path) is None


def test_generation_api_accepts_diffusers_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setenv(
        "YONDER_CONFIG_FILE",
        str(root / "config/environments/development.yaml"),
    )
    monkeypatch.setenv("YONDER_DIFFUSERS_RUNTIME__DRY_RUN", "true")

    from app.main import create_app

    client = TestClient(create_app())
    response = client.post(
        "/v1/generation/jobs",
        json={
            "job_id": "dry-job-1",
            "deployment_id": "image-a",
            "mode": "txt2img",
            "prompt": "test prompt",
            "params": {"steps": 1},
        },
    )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["accepted"] is True
    assert job["status"] == "queued"
    assert job["availability"] == "dry_run"

    fetched = client.get("/v1/generation/jobs/dry-job-1")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "queued"
