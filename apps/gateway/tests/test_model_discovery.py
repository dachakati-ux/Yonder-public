"""Model-folder discovery and preparation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.services.model_discovery import (
    ModelDiscoveryError,
    prepare_model_bundle,
    resolve_allowed_path,
    scan_model_path,
)
from fastapi.testclient import TestClient
from yonder_contracts.model_bundles import load_bundle_manifest

ROOT = Path(__file__).resolve().parents[3]


def test_scan_detects_hf_chat_and_diffusers_video(tmp_path: Path) -> None:
    chat = tmp_path / "Qwen3.5-9B"
    chat.mkdir()
    (chat / "config.json").write_text(
        '{"architectures":["Qwen3_5ForConditionalGeneration"]}',
        encoding="utf-8",
    )
    (chat / "model.safetensors").write_bytes(b"weights")

    video = tmp_path / "Wan2.2-TI2V-5B-Diffusers"
    video.mkdir()
    (video / "model_index.json").write_text(
        '{"_class_name":"WanPipeline"}',
        encoding="utf-8",
    )
    (video / "transformer.safetensors").write_bytes(b"video")

    candidates = scan_model_path(tmp_path)
    by_name = {item["folder_name"]: item for item in candidates}
    assert by_name["Qwen3.5-9B"]["modality"] == "chat"
    assert by_name["Qwen3.5-9B"]["model_format"] == "safetensors"
    assert by_name["Wan2.2-TI2V-5B-Diffusers"]["modality"] == "video"
    assert by_name["Wan2.2-TI2V-5B-Diffusers"]["runtime_candidate"] == "diffusers"


def test_prepare_writes_checksummed_disabled_bundle(tmp_path: Path) -> None:
    model = tmp_path / "Qwen3-Embedding-4B"
    model.mkdir()
    (model / "config.json").write_text('{"model_type":"qwen3"}', encoding="utf-8")
    (model / "model.safetensors").write_bytes(b"embedding-weights")
    (model / "README.md").write_text("---\nlicense: apache-2.0\n---\n", encoding="utf-8")

    candidate = prepare_model_bundle(model)
    manifest = load_bundle_manifest(model / "bundle.yaml")

    assert candidate["status"] == "incomplete"
    assert manifest.modality == "embeddings"
    assert manifest.runtime.provider == "not_selected"
    assert manifest.hardware_constraints.status == "not_reported"
    assert manifest.license == "apache-2.0"
    assert {entry.path for entry in manifest.files} == {
        "README.md",
        "config.json",
        "model.safetensors",
    }


def test_resolve_allowed_path_rejects_escape(tmp_path: Path) -> None:
    allowed = tmp_path / "models"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    assert resolve_allowed_path(str(allowed), [allowed]) == allowed.resolve()
    with pytest.raises(ModelDiscoveryError, match="configured YONDER_MODEL_ROOTS"):
        resolve_allowed_path(str(outside), [allowed])


def test_admin_scan_and_prepare_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_root = tmp_path / "models"
    model = model_root / "sdxl-turbo-fp16"
    model.mkdir(parents=True)
    (model / "sd_xl_turbo_1.0_fp16.safetensors").write_bytes(b"image-weights")

    monkeypatch.setenv(
        "YONDER_CONFIG_FILE",
        str(ROOT / "config/environments/development.yaml"),
    )
    monkeypatch.setenv("YONDER_MODEL_ROOTS", str(model_root))
    from app.main import create_app

    client = TestClient(create_app())
    roots = client.get("/api/admin/model-folders/roots")
    assert roots.status_code == 200
    assert roots.json()["roots"] == [{"path": str(model_root), "exists": True}]

    scan = client.post("/api/admin/model-folders/scan", json={"path": str(model_root)})
    assert scan.status_code == 200
    candidate = scan.json()["candidates"][0]
    assert candidate["modality"] == "image"
    assert candidate["status"] == "discovered"

    prepared = client.post(
        "/api/admin/model-folders/prepare",
        json={"path": str(model)},
    )
    assert prepared.status_code == 200
    assert prepared.json()["candidate"]["status"] == "incomplete"
    assert (model / "bundle.yaml").exists()

    rejected = client.post(
        "/api/admin/model-folders/scan",
        json={"path": str(tmp_path)},
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["details"]["reason"] == "MODEL_PATH_NOT_ALLOWED"
