"""Model manifest validation tests."""

from __future__ import annotations

from pathlib import Path

from app.models.manifest import (
    ModelManifest,
    load_manifest_file,
    validate_manifest,
)

ROOT = Path(__file__).resolve().parents[3]


def test_mock_manifest_loads_and_validates() -> None:
    manifest = load_manifest_file(ROOT / "config/models/yonder-chat-small.yaml")
    result = validate_manifest(manifest, known_providers={"mock", "vllm"})
    assert result.valid is True
    assert manifest.runtime.provider == "mock"


def test_unknown_provider_is_invalid() -> None:
    manifest = ModelManifest.model_validate(
        {
            "id": "x",
            "display_name": "X",
            "source": {"type": "local", "path": "/tmp/x"},
            "runtime": {
                "provider": "not-a-provider",
                "model_identifier": "x",
                "context_length": 1024,
                "tensor_parallel_size": 1,
            },
            "requirements": {"minimum_gpu_memory_gb": 8, "supported_gpu_families": ["volta"]},
            "capabilities": ["chat"],
        }
    )
    result = validate_manifest(manifest, known_providers={"mock"})
    assert result.valid is False
    assert any(issue.code == "UNKNOWN_PROVIDER" for issue in result.errors)


def test_missing_files_for_non_mock_provider() -> None:
    manifest = ModelManifest.model_validate(
        {
            "id": "x",
            "display_name": "X",
            "source": {"type": "local", "path": "/definitely/missing/model-weights"},
            "runtime": {
                "provider": "vllm",
                "model_identifier": "/definitely/missing/model-weights",
                "context_length": 2048,
                "tensor_parallel_size": 1,
            },
            "requirements": {"minimum_gpu_memory_gb": 20, "supported_gpu_families": ["volta"]},
            "capabilities": ["chat", "streaming"],
        }
    )
    result = validate_manifest(manifest, known_providers={"mock", "vllm"})
    assert result.valid is False
    assert any(issue.code == "MISSING_MODEL_FILES" for issue in result.errors)


def test_binary_outside_runtime_root_is_invalid() -> None:
    manifest = ModelManifest.model_validate(
        {
            "id": "x",
            "display_name": "X",
            "source": {"type": "local", "path": "/tmp/x"},
            "runtime": {
                "provider": "llama-cpp",
                "model_identifier": "x",
                "context_length": 2048,
                "tensor_parallel_size": 1,
                "binary_path": "/tmp/not-allowed/llama-server",
            },
            "requirements": {
                "minimum_gpu_memory_gb": 8,
                "supported_gpu_families": ["volta"],
            },
            "capabilities": ["chat"],
        }
    )
    result = validate_manifest(
        manifest, known_providers={"llama-cpp"}, check_files=False
    )
    assert result.valid is False
    assert any(issue.code == "BINARY_OUTSIDE_RUNTIME_ROOT" for issue in result.errors)


def test_duplicate_model_id() -> None:
    manifest = load_manifest_file(ROOT / "config/models/yonder-chat-small.yaml")
    result = validate_manifest(
        manifest,
        known_providers={"mock"},
        existing_ids={"yonder-chat-small"},
    )
    assert result.valid is False
    assert any(issue.code == "DUPLICATE_MODEL_ID" for issue in result.errors)
