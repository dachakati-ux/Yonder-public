"""Safe model-folder discovery and offline bundle preparation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from yonder_contracts.model_bundles import (
    BundleFile,
    BundleHardware,
    BundleRuntime,
    BundleStatus,
    ModelBundleManifest,
    public_bundle_summary,
    sha256_file,
    verify_bundle_dir,
)

MODEL_SUFFIXES = {".bin", ".ckpt", ".gguf", ".onnx", ".pt", ".pth", ".safetensors"}
SKIP_NAMES = {".cache", "logs", "__pycache__"}


class ModelDiscoveryError(ValueError):
    """A model path is unsafe or cannot be prepared."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def resolved_model_roots(roots: list[Path]) -> list[Path]:
    return [root.expanduser().resolve() for root in roots]


def resolve_allowed_path(raw_path: str, roots: list[Path]) -> Path:
    if not raw_path.strip():
        raise ModelDiscoveryError("EMPTY_MODEL_PATH", "Model folder path is required.")
    path = Path(raw_path).expanduser().resolve()
    allowed = resolved_model_roots(roots)
    if not any(path == root or path.is_relative_to(root) for root in allowed):
        raise ModelDiscoveryError(
            "MODEL_PATH_NOT_ALLOWED",
            "Model folder must be inside a configured YONDER_MODEL_ROOTS directory.",
        )
    if not path.exists():
        raise ModelDiscoveryError("MODEL_PATH_NOT_FOUND", f"Model folder does not exist: {path}")
    if not path.is_dir():
        raise ModelDiscoveryError(
            "MODEL_PATH_NOT_DIRECTORY",
            f"Model path is not a directory: {path}",
        )
    return path


def _iter_files(path: Path):
    for item in path.rglob("*"):
        relative = item.relative_to(path)
        if any(part.startswith(".") or part in SKIP_NAMES for part in relative.parts):
            continue
        if item.is_symlink():
            continue
        if item.is_file() and item.name not in {"bundle.yaml"}:
            yield item, relative


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _model_class(path: Path) -> str:
    model_index = _read_json(path / "model_index.json")
    if model_index:
        return str(model_index.get("_class_name", ""))
    config = _read_json(path / "config.json")
    architectures = config.get("architectures")
    if isinstance(architectures, list) and architectures:
        return str(architectures[0])
    return str(config.get("model_type", ""))


def _detect(path: Path) -> dict[str, Any]:
    name = path.name
    lower = name.lower()
    model_class = _model_class(path)
    class_lower = model_class.lower()
    files = list(_iter_files(path))
    suffixes = {item.suffix.lower() for item, _ in files}

    if "reranker" in lower:
        modality = "reranking"
        capabilities = ["reranking"]
    elif "embed" in lower:
        modality = "embeddings"
        capabilities = ["embeddings"]
    elif "video" in lower or "wanpipeline" in class_lower or "cogvideo" in class_lower:
        modality = "video"
        capabilities = ["video"]
    elif (
        "image" in lower
        or "stable-diffusion" in lower
        or "sdxl" in lower
        or "zimage" in class_lower
        or "diffusion" in class_lower
    ):
        modality = "image"
        capabilities = ["image"]
    else:
        modality = "chat"
        capabilities = ["chat"]

    if ".gguf" in suffixes:
        model_format = "gguf"
        runtime_candidate = "llama.cpp"
    elif (path / "model_index.json").exists():
        model_format = "diffusers"
        runtime_candidate = "diffusers"
    elif ".safetensors" in suffixes:
        model_format = "safetensors"
        runtime_candidate = "vllm_or_transformers"
    else:
        model_format = "unknown"
        runtime_candidate = "not_detected"

    quantization = "not_reported"
    if any(token in lower for token in ("q4", "int4", "w4a16")):
        quantization = "int4"
    elif "q8" in lower or "int8" in lower:
        quantization = "int8"

    alias_prefix = {
        "chat": "yonder-chat",
        "embeddings": "yonder-embed",
        "reranking": "yonder-rerank",
        "image": "yonder-image",
        "video": "yonder-video",
    }[modality]
    slug = re.sub(r"[^a-z0-9]+", "-", lower).strip("-")
    top_level_files = [
        item for item in path.iterdir() if item.is_file() and not item.is_symlink()
    ]
    has_top_level_weight = any(
        item.suffix.lower() in MODEL_SUFFIXES for item in top_level_files
    )
    return {
        "id": slug,
        "display_name": name.replace("_", " "),
        "modality": modality,
        "capabilities": capabilities,
        "model_format": model_format,
        "quantization": quantization,
        "runtime_candidate": runtime_candidate,
        "suggested_aliases": [alias_prefix, f"{alias_prefix}-{slug}"],
        "model_class": model_class or None,
        "file_count": len(files),
        "size_bytes": sum(item.stat().st_size for item, _ in files),
        "recognized": bool(
            has_top_level_weight
            or (path / "config.json").exists()
            or (path / "model_index.json").exists()
        ),
    }


def inspect_model_folder(path: Path) -> dict[str, Any]:
    detected = _detect(path)
    manifest_path = path / "bundle.yaml"
    if manifest_path.exists():
        verification = public_bundle_summary(verify_bundle_dir(path))
        status = verification["status"]
        issues = verification["issues"]
    else:
        verification = None
        status = "discovered" if detected["recognized"] else "unrecognized"
        issues = []
    return {
        "path": str(path),
        "folder_name": path.name,
        "status": status,
        "issues": issues,
        "bundle": verification,
        **detected,
    }


def scan_model_path(path: Path) -> list[dict[str, Any]]:
    direct = inspect_model_folder(path)
    if direct["recognized"] or (path / "bundle.yaml").exists():
        return [direct]
    candidates = []
    for child in sorted(path.iterdir()):
        if (
            child.is_symlink()
            or not child.is_dir()
            or child.name.startswith(".")
            or child.name in SKIP_NAMES
        ):
            continue
        inspected = inspect_model_folder(child)
        if inspected["recognized"] or (child / "bundle.yaml").exists():
            candidates.append(inspected)
    return candidates


def _license_from_readme(path: Path) -> str:
    readme = path / "README.md"
    if not readme.exists():
        return "not_reported"
    try:
        text = readme.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "not_reported"
    if not text.startswith("---"):
        return "not_reported"
    _, frontmatter, *_ = text.split("---", 2)
    try:
        metadata = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError:
        return "not_reported"
    if isinstance(metadata, dict) and metadata.get("license"):
        return str(metadata["license"])
    return "not_reported"


def prepare_model_bundle(path: Path, *, force: bool = False) -> dict[str, Any]:
    detected = _detect(path)
    if not detected["recognized"]:
        raise ModelDiscoveryError(
            "MODEL_FOLDER_UNRECOGNIZED",
            "No supported model markers or weight files were found.",
        )
    manifest_path = path / "bundle.yaml"
    if manifest_path.exists() and not force:
        raise ModelDiscoveryError(
            "BUNDLE_ALREADY_EXISTS",
            "bundle.yaml already exists; pass force=true to regenerate it.",
        )

    entries = []
    for file_path, relative in _iter_files(path):
        entries.append(
            BundleFile(
                path=relative.as_posix(),
                sha256=sha256_file(file_path),
                size_bytes=file_path.stat().st_size,
            )
        )
    manifest = ModelBundleManifest(
        id=detected["id"],
        display_name=detected["display_name"],
        version="local",
        modality=detected["modality"],
        aliases=detected["suggested_aliases"],
        capabilities=detected["capabilities"],
        license=_license_from_readme(path),
        provenance=f"local-folder:{path.name}",
        runtime=BundleRuntime(
            provider="not_selected",
            notes=(
                f"Auto-discovered format={detected['model_format']}; "
                f"candidate={detected['runtime_candidate']}. Runtime remains disabled "
                "until host compatibility is validated."
            ),
        ),
        hardware_constraints=BundleHardware(status="not_reported"),
        files=entries,
        status=BundleStatus.INCOMPLETE,
    )
    manifest_path.write_text(
        yaml.safe_dump(manifest.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    return inspect_model_folder(path)
