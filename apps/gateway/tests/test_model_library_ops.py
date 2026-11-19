"""Tests for M04 model library ops: bundle registration, placement, benchmarks, and gating."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import yaml
from app.api import model_catalog as catalog_api
from app.context import RequestContextMiddleware
from fastapi import FastAPI
from fastapi.testclient import TestClient
from yonder_contracts.principal import RequestPrincipal


class _StaticProvider:
    def __init__(self, principal: RequestPrincipal) -> None:
        self._p = principal

    async def resolve(self, _request) -> RequestPrincipal:
        return self._p


def _principal(*, capabilities: tuple[str, ...]) -> RequestPrincipal:
    return RequestPrincipal(
        subject="admin-1",
        principal_type="user",
        capabilities=capabilities,
        attributes=MappingProxyType({}),
    )


def _make_client(
    *,
    model_roots: list[Path] | None = None,
    workers: dict | None = None,
    capabilities: tuple[str, ...] = ("models.view", "models.manage"),
) -> TestClient:
    app = FastAPI()
    app.add_middleware(
        RequestContextMiddleware,
        provider=_StaticProvider(_principal(capabilities=capabilities)),
        service_name="yonder-gateway",
    )
    app.include_router(catalog_api.router)
    app.state.settings = SimpleNamespace(model_roots=model_roots or [])
    app.state.workers = workers or {}
    return TestClient(app)


def _create_valid_bundle(bundle_dir: Path) -> Path:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    weights = bundle_dir / "model.gguf"
    weights.write_bytes(b"GGUF-TEST-WEIGHTS")
    digest = hashlib.sha256(b"GGUF-TEST-WEIGHTS").hexdigest()
    manifest = {
        "id": bundle_dir.name,
        "display_name": bundle_dir.name,
        "version": "1.0.0",
        "modality": "chat",
        "capabilities": ["chat"],
        "license": "apache-2.0",
        "provenance": "local-test",
        "runtime": {"provider": "llama_cpp"},
        "hardware_constraints": {"min_gpu_memory_gb": 4.0},
        "files": [
            {"path": "model.gguf", "sha256": digest, "size_bytes": len(b"GGUF-TEST-WEIGHTS")}
        ],
        "status": "ready",
    }
    (bundle_dir / "bundle.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return bundle_dir


def test_bundle_registration_and_rollback_on_failure(tmp_path: Path) -> None:
    root = tmp_path / "models"
    bundle_v1 = _create_valid_bundle(root / "qwen-v1")
    bundle_v2 = _create_valid_bundle(root / "qwen-v2")
    client = _make_client(model_roots=[root])

    # Register v1
    r1 = client.post(
        "/api/admin/catalog/bundles/register",
        json={"model_id": "qwen-local", "path": str(bundle_v1), "runtime_id": "llama_cpp"},
    )
    assert r1.status_code == 200
    assert r1.json()["registration"]["runtime_id"] == "llama_cpp"

    # Attempt v2 with simulated load failure -> rolls back cleanly to v1
    r2 = client.post(
        "/api/admin/catalog/bundles/register",
        json={
            "model_id": "qwen-local",
            "path": str(bundle_v2),
            "runtime_id": "vllm",
            "simulate_load_failure": True,
        },
    )
    assert r2.status_code == 400
    assert r2.json()["error"]["details"]["reason"] == "RUNTIME_LOAD_FAILED_ROLLED_BACK"

    regs = client.get("/api/admin/catalog/bundles/registrations").json()["registrations"]
    assert len(regs) == 1
    assert regs[0]["runtime_id"] == "llama_cpp"
    assert regs[0]["bundle_path"] == str(bundle_v1.resolve())

    # Reject outside model_roots
    outside = _create_valid_bundle(tmp_path / "outside" / "evil")
    r3 = client.post(
        "/api/admin/catalog/bundles/register",
        json={"model_id": "evil", "path": str(outside), "runtime_id": "llama_cpp"},
    )
    assert r3.status_code == 400
    assert r3.json()["error"]["details"]["reason"] == "MODEL_PATH_NOT_ALLOWED"


def test_heterogeneous_placement_and_reschedule() -> None:
    workers = {
        "worker-a": {
            "status": "healthy",
            "maintenance": False,
            "hardware_summary": {"gpus": [{"memory_free_mb": 24576}]},
        },
        "worker-b": {
            "status": "healthy",
            "maintenance": False,
            "hardware_summary": {"gpus": [{"memory_free_mb": 16384}]},
        },
        "worker-maint": {
            "status": "healthy",
            "maintenance": True,
            "hardware_summary": {"gpus": [{"memory_free_mb": 32768}]},
        },
        "worker-small": {
            "status": "healthy",
            "maintenance": False,
            "hardware_summary": {"gpus": [{"memory_free_mb": 4096}]},
        },
    }
    client = _make_client(workers=workers)

    rec = client.post(
        "/api/admin/catalog/placement/recommend",
        json={"required_vram_gb": 12.0},
    )
    assert rec.status_code == 200
    data = rec.json()
    assert data["status"] == "scheduled"
    assert data["selected_worker_id"] == "worker-a"
    assert data["fallback_worker_ids"] == ["worker-b"]

    resched = client.post(
        "/api/admin/catalog/placement/reschedule",
        json={"failed_worker_id": "worker-a", "required_vram_gb": 12.0},
    )
    assert resched.status_code == 200
    rdata = resched.json()
    assert rdata["rescheduled"] is True
    assert rdata["selected_worker_id"] == "worker-b"


def test_compatibility_matrix_and_reproducible_benchmarks() -> None:
    workers = {
        "w-24g": {"hardware_summary": {"gpus": [{"memory_free_mb": 24576}]}},
        "w-8g": {"hardware_summary": {"gpus": [{"memory_free_mb": 8192}]}},
        "w-unknown": {},
    }
    client = _make_client(workers=workers)

    matrix = client.get("/api/admin/catalog/compatibility-matrix")
    assert matrix.status_code == 200
    rows = matrix.json()["matrix"]
    assert any(r["worker_id"] == "w-24g" and r["compatibility"] == "compatible" for r in rows)
    assert any(
        r["worker_id"] == "w-8g" and r["compatibility"] == "compatible_quantized" for r in rows
    )
    assert any(r["worker_id"] == "w-unknown" and r["compatibility"] == "not_reported" for r in rows)

    bench = client.post(
        "/api/admin/catalog/benchmarks",
        json={
            "model_id": "qwen-local",
            "runtime_id": "llama_cpp",
            "worker_id": "w-24g",
            "prompt_tokens": 128,
            "completion_tokens": 100,
            "latency_ms": 2000.0,
            "seed": 42,
        },
    )
    assert bench.status_code == 200
    run = bench.json()["run"]
    assert run["tokens_per_sec"] == 50.0
    assert len(run["reproducible_config_hash"]) == 16

    listed = client.get("/api/admin/catalog/benchmarks?model_id=qwen-local")
    assert listed.status_code == 200
    assert len(listed.json()["runs"]) == 1


def test_optional_model_library_gating_keeps_core_connections(monkeypatch) -> None:
    monkeypatch.setenv("YONDER_MODEL_LIBRARY_ENABLED", "false")
    client = _make_client()

    status = client.get("/api/admin/catalog/status")
    assert status.status_code == 200
    assert status.json()["model_library_enabled"] is False
    assert status.json()["core_connections_enabled"] is True

    presets = client.get("/api/admin/catalog/runtime-presets")
    assert presets.status_code == 200
    assert "ollama" in presets.json()["presets"]

    search = client.get("/api/admin/catalog/search")
    assert search.status_code == 403
    assert search.json()["error"]["details"]["reason"] == "MODEL_LIBRARY_DISABLED"
