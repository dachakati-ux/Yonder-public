"""Offline model-bundle manifest schema and verification."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BundleStatus(StrEnum):
    READY = "ready"
    NOT_BUNDLED = "not_bundled"
    INVALID = "invalid"
    INCOMPLETE = "incomplete"


class BundleFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str
    size_bytes: int = Field(ge=0)
    required: bool = True

    @field_validator("sha256")
    @classmethod
    def sha_hex(cls, value: str) -> str:
        text = value.strip().lower()
        if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return text


class BundleRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "not_selected"
    notes: str = ""


class BundleHardware(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_gpu_memory_gb: float | None = None
    supported_gpu_families: list[str] = Field(default_factory=list)
    status: str = "not_reported"


class ModelBundleManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    version: str
    modality: str
    aliases: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    license: str
    provenance: str
    runtime: BundleRuntime = Field(default_factory=BundleRuntime)
    hardware_constraints: BundleHardware = Field(default_factory=BundleHardware)
    files: list[BundleFile] = Field(default_factory=list)
    status: BundleStatus = BundleStatus.NOT_BUNDLED

    @field_validator("id", "modality")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()


class BundleIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    path: str | None = None


class BundleVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    status: BundleStatus
    issues: list[BundleIssue] = Field(default_factory=list)
    manifest: ModelBundleManifest | None = None


def load_bundle_manifest(path: Path) -> ModelBundleManifest:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Bundle manifest root must be a mapping: {path}")
    return ModelBundleManifest.model_validate(data)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_bundle_dir(bundle_dir: Path) -> BundleVerificationResult:
    manifest_path = bundle_dir / "bundle.yaml"
    if not manifest_path.exists():
        return BundleVerificationResult(
            valid=False,
            status=BundleStatus.INVALID,
            issues=[
                BundleIssue(
                    code="MISSING_MANIFEST",
                    message="bundle.yaml not found",
                    path=str(manifest_path),
                )
            ],
        )
    try:
        manifest = load_bundle_manifest(manifest_path)
    except Exception as exc:  # noqa: BLE001
        return BundleVerificationResult(
            valid=False,
            status=BundleStatus.INVALID,
            issues=[BundleIssue(code="INVALID_MANIFEST", message=str(exc))],
        )

    issues: list[BundleIssue] = []
    if manifest.runtime.provider in {"", "not_selected"}:
        issues.append(
            BundleIssue(
                code="RUNTIME_NOT_SELECTED",
                message=(
                    "runtime.provider is not_selected — complete V100 host evidence "
                    "before enabling a provider"
                ),
            )
        )

    for entry in manifest.files:
        file_path = bundle_dir / entry.path
        if not file_path.exists():
            if entry.required:
                issues.append(
                    BundleIssue(
                        code="MISSING_FILE",
                        message=f"Required file missing: {entry.path}",
                        path=entry.path,
                    )
                )
            continue
        size = file_path.stat().st_size
        if size != entry.size_bytes:
            issues.append(
                BundleIssue(
                    code="SIZE_MISMATCH",
                    message=f"size {size} != declared {entry.size_bytes}",
                    path=entry.path,
                )
            )
        digest = sha256_file(file_path)
        if digest != entry.sha256:
            issues.append(
                BundleIssue(
                    code="CHECKSUM_MISMATCH",
                    message="sha256 does not match manifest",
                    path=entry.path,
                )
            )

    file_errors = {"MISSING_FILE", "CHECKSUM_MISMATCH", "SIZE_MISMATCH"}
    if any(issue.code in file_errors for issue in issues):
        status = BundleStatus.INCOMPLETE
        valid = False
    elif any(issue.code == "RUNTIME_NOT_SELECTED" for issue in issues):
        status = BundleStatus.NOT_BUNDLED if not manifest.files else BundleStatus.INCOMPLETE
        # Runtime not selected is expected pre-V100; treat as non-fatal for packaging.
        valid = not any(
            issue.code in {"MISSING_FILE", "CHECKSUM_MISMATCH", "SIZE_MISMATCH", "INVALID_MANIFEST"}
            for issue in issues
        )
        # If files are placeholders (size 0), mark not_bundled.
        if all(entry.size_bytes == 0 for entry in manifest.files) or not manifest.files:
            status = BundleStatus.NOT_BUNDLED
            valid = True
    else:
        status = BundleStatus.READY
        valid = True

    return BundleVerificationResult(
        valid=valid,
        status=status,
        issues=issues,
        manifest=manifest,
    )


def list_bundle_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    dirs = []
    for path in root.iterdir():
        if path.is_dir() and (path / "bundle.yaml").exists():
            dirs.append(path)
    return sorted(dirs)


def public_bundle_summary(result: BundleVerificationResult) -> dict[str, Any]:
    manifest = result.manifest
    return {
        "id": manifest.id if manifest else None,
        "display_name": manifest.display_name if manifest else None,
        "modality": manifest.modality if manifest else None,
        "status": result.status.value,
        "valid": result.valid,
        "issues": [issue.model_dump(mode="json") for issue in result.issues],
        "runtime_provider": manifest.runtime.provider if manifest else None,
        "aliases": list(manifest.aliases) if manifest else [],
    }
