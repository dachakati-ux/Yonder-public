"""Model manifest schema and validation."""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from yaml import YAMLError

_LOG = logging.getLogger(__name__)


class ManifestValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class RuntimeProfile(StrEnum):
    """Typed runtime use-case for scheduling and process flags."""

    CHAT = "chat"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    IMAGE = "image"
    VIDEO = "video"
    SPEECH = "speech"


class ManifestIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: ManifestValidationSeverity
    code: str
    message: str
    field: str | None = None


class ManifestValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    issues: list[ManifestIssue] = Field(default_factory=list)

    @property
    def errors(self) -> list[ManifestIssue]:
        return [
            issue
            for issue in self.issues
            if issue.severity == ManifestValidationSeverity.ERROR
        ]


class ModelSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = "local"
    path: str | None = None


class ModelRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model_identifier: str
    profile: RuntimeProfile = RuntimeProfile.CHAT
    dtype: str = "auto"
    quantization: str = "auto"
    context_length: int = Field(gt=0)
    tensor_parallel_size: int = Field(default=1, ge=1)
    mmproj_path: str | None = None
    # Optional per-model llama-server binary (e.g. PrismML fork). When unset,
    # the worker uses LlamaCppWorkerRuntimeConfig.binary_path.
    binary_path: str | None = None
    pooling: str | None = None
    embedding: bool | None = None
    reranking: bool | None = None
    # Diffusers pipeline family (sdxl / z-image / cogvideox / wan).
    family: str | None = None
    # Speech runtime kind when provider=speech (stt | tts).
    speech_kind: str | None = None


class GpuModePlacementLimits(BaseModel):
    """Hard caps for one placement mode on lab V100 workers.

    ``max_context_length`` of 0 means the mode is forbidden (e.g. model weights
    cannot fit on a single 32 GB GPU). ``evidence`` records whether the cap is
    a published research estimate or host-measured.
    """

    model_config = ConfigDict(extra="forbid")

    max_context_length: int = Field(ge=0)
    minimum_gpu_memory_gb: float | None = Field(default=None, gt=0)
    evidence: str = "research_estimate"


class PlacementLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    single_gpu: GpuModePlacementLimits | None = None
    two_gpu: GpuModePlacementLimits | None = None


class ModelRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_gpu_memory_gb: float = Field(gt=0)
    supported_gpu_families: list[str] = Field(default_factory=list)
    placement_limits: PlacementLimits | None = None


class ModelDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float = 0.6
    top_p: float = 0.9
    maximum_output_tokens: int = Field(default=2048, ge=1)


class ModelManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    description: str = ""
    source: ModelSource
    runtime: ModelRuntime
    requirements: ModelRequirements
    defaults: ModelDefaults = Field(default_factory=ModelDefaults)
    capabilities: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def id_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("id must not be empty")
        return value.strip()


KNOWN_PROVIDERS = {
    "mock",
    "vllm",
    "llama-cpp",
    "llama-cpp-embeddings",
    "llama-cpp-rerank",
    "mock-embeddings",
    "mock-generation",
    "diffusers",
}
KNOWN_QUANTIZATION = {
    "auto",
    "none",
    "awq",
    "gptq",
    "fp8",
    "int2",
    "int4",
    "int8",
    "q2_0",
    "q4_0",
    "q4_k_m",
    "q4_k_xl",
    "q8_0",
}
KNOWN_CAPABILITIES = {
    "chat",
    "streaming",
    "tools",
    "structured",
    "embeddings",
    "reranking",
    "image",
    "video",
    "rag",
}
KNOWN_POOLING = {"none", "mean", "cls", "last", "rank"}
KNOWN_DIFFUSERS_FAMILIES = {
    RuntimeProfile.IMAGE: {"sdxl", "z-image"},
    RuntimeProfile.VIDEO: {"cogvideox", "wan"},
}


def validate_manifest(
    manifest: ModelManifest,
    *,
    known_providers: set[str] | None = None,
    existing_ids: set[str] | None = None,
    check_files: bool = True,
) -> ManifestValidationResult:
    providers = known_providers or KNOWN_PROVIDERS
    issues: list[ManifestIssue] = []

    if existing_ids and manifest.id in existing_ids:
        issues.append(
            ManifestIssue(
                severity=ManifestValidationSeverity.ERROR,
                code="DUPLICATE_MODEL_ID",
                message=f"Duplicate model id: {manifest.id}",
                field="id",
            )
        )

    if manifest.runtime.provider not in providers:
        issues.append(
            ManifestIssue(
                severity=ManifestValidationSeverity.ERROR,
                code="UNKNOWN_PROVIDER",
                message=f"Unknown provider: {manifest.runtime.provider}",
                field="runtime.provider",
            )
        )

    if manifest.runtime.context_length < 1:
        issues.append(
            ManifestIssue(
                severity=ManifestValidationSeverity.ERROR,
                code="INVALID_CONTEXT_LENGTH",
                message="context_length must be >= 1",
                field="runtime.context_length",
            )
        )

    if manifest.runtime.tensor_parallel_size < 1:
        issues.append(
            ManifestIssue(
                severity=ManifestValidationSeverity.ERROR,
                code="IMPOSSIBLE_GPU_ALLOCATION",
                message="tensor_parallel_size must be >= 1",
                field="runtime.tensor_parallel_size",
            )
        )

    if (
        manifest.runtime.quantization not in KNOWN_QUANTIZATION
        and manifest.runtime.quantization != "auto"
    ):
        issues.append(
            ManifestIssue(
                severity=ManifestValidationSeverity.ERROR,
                code="INVALID_QUANTIZATION",
                message=f"Unsupported quantization: {manifest.runtime.quantization}",
                field="runtime.quantization",
            )
        )

    if (
        manifest.runtime.pooling is not None
        and manifest.runtime.pooling not in KNOWN_POOLING
    ):
        issues.append(
            ManifestIssue(
                severity=ManifestValidationSeverity.ERROR,
                code="INVALID_POOLING",
                message=f"Unsupported pooling: {manifest.runtime.pooling}",
                field="runtime.pooling",
            )
        )

    if manifest.runtime.profile == RuntimeProfile.RERANK:
        if manifest.runtime.pooling not in {None, "rank"}:
            issues.append(
                ManifestIssue(
                    severity=ManifestValidationSeverity.ERROR,
                    code="INVALID_RERANK_POOLING",
                    message="rerank profile requires pooling=rank",
                    field="runtime.pooling",
                )
            )

    if manifest.runtime.provider == "diffusers":
        allowed = KNOWN_DIFFUSERS_FAMILIES.get(manifest.runtime.profile)
        family = (manifest.runtime.family or "").strip().lower()
        if allowed is None:
            issues.append(
                ManifestIssue(
                    severity=ManifestValidationSeverity.ERROR,
                    code="INVALID_DIFFUSERS_PROFILE",
                    message="diffusers provider requires profile image or video",
                    field="runtime.profile",
                )
            )
        elif family not in allowed:
            issues.append(
                ManifestIssue(
                    severity=ManifestValidationSeverity.ERROR,
                    code="INVALID_DIFFUSERS_FAMILY",
                    message=(
                        f"Unsupported diffusers family for {manifest.runtime.profile}: "
                        f"{manifest.runtime.family!r} "
                        f"(supported={sorted(allowed)})"
                    ),
                    field="runtime.family",
                )
            )

    if manifest.requirements.minimum_gpu_memory_gb <= 0:
        issues.append(
            ManifestIssue(
                severity=ManifestValidationSeverity.ERROR,
                code="INVALID_MEMORY_REQUIREMENT",
                message="minimum_gpu_memory_gb must be > 0",
                field="requirements.minimum_gpu_memory_gb",
            )
        )

    limits = manifest.requirements.placement_limits
    if limits is not None:
        mode_limits = (
            (limits.single_gpu, "single_gpu"),
            (limits.two_gpu, "two_gpu"),
        )
        if limits.single_gpu is None and limits.two_gpu is None:
            issues.append(
                ManifestIssue(
                    severity=ManifestValidationSeverity.ERROR,
                    code="EMPTY_PLACEMENT_LIMITS",
                    message="placement_limits must declare single_gpu and/or two_gpu",
                    field="requirements.placement_limits",
                )
            )
        for mode_limit, mode_name in mode_limits:
            if mode_limit is None:
                continue
            if mode_limit.max_context_length < 0:
                issues.append(
                    ManifestIssue(
                        severity=ManifestValidationSeverity.ERROR,
                        code="INVALID_PLACEMENT_CONTEXT",
                        message=f"{mode_name}.max_context_length must be >= 0",
                        field=f"requirements.placement_limits.{mode_name}",
                    )
                )
            elif (
                mode_limit.max_context_length > 0
                and manifest.runtime.context_length > mode_limit.max_context_length
            ):
                # Default ctx must fit the mode that matches tensor_parallel_size.
                default_mode = (
                    "two_gpu"
                    if manifest.runtime.tensor_parallel_size >= 2
                    else "single_gpu"
                )
                if mode_name == default_mode:
                    issues.append(
                        ManifestIssue(
                            severity=ManifestValidationSeverity.ERROR,
                            code="DEFAULT_CONTEXT_EXCEEDS_LIMIT",
                            message=(
                                "runtime.context_length exceeds "
                                f"{mode_name}.max_context_length"
                            ),
                            field="runtime.context_length",
                        )
                    )

    if check_files and manifest.source.type == "local":
        if not manifest.source.path:
            issues.append(
                ManifestIssue(
                    severity=ManifestValidationSeverity.ERROR,
                    code="MISSING_MODEL_FILES",
                    message="Local source requires a path",
                    field="source.path",
                )
            )
        elif manifest.runtime.provider != "mock":
            path = Path(manifest.source.path)
            if not path.exists():
                issues.append(
                    ManifestIssue(
                        severity=ManifestValidationSeverity.ERROR,
                        code="MISSING_MODEL_FILES",
                        message=f"Model path does not exist: {manifest.source.path}",
                        field="source.path",
                    )
                )
            if manifest.runtime.mmproj_path:
                mmproj = Path(manifest.runtime.mmproj_path)
                if not mmproj.exists():
                    issues.append(
                        ManifestIssue(
                            severity=ManifestValidationSeverity.ERROR,
                            code="MISSING_MMPROJ",
                            message=(
                                "mmproj_path does not exist: "
                                f"{manifest.runtime.mmproj_path}"
                            ),
                            field="runtime.mmproj_path",
                        )
                    )
            if (
                manifest.runtime.binary_path
                and check_files
                and Path(manifest.runtime.binary_path).expanduser().is_absolute()
            ):
                binary = Path(manifest.runtime.binary_path).expanduser()
                try:
                    binary.resolve().relative_to(Path("/opt/yonder/runtimes"))
                except ValueError:
                    pass
                else:
                    if not binary.exists():
                        issues.append(
                            ManifestIssue(
                                severity=ManifestValidationSeverity.ERROR,
                                code="MISSING_RUNTIME_BINARY",
                                message=(
                                    "binary_path does not exist: "
                                    f"{manifest.runtime.binary_path}"
                                ),
                                field="runtime.binary_path",
                            )
                        )
        else:
            # Mock provider intentionally skips filesystem presence.
            issues.append(
                ManifestIssue(
                    severity=ManifestValidationSeverity.WARNING,
                    code="MOCK_SOURCE_SKIPPED",
                    message="Filesystem check skipped for mock provider",
                    field="source.path",
                )
            )

    if manifest.runtime.binary_path:
        binary = Path(manifest.runtime.binary_path).expanduser()
        under_root = False
        for root in (Path("/opt/yonder/runtimes"),):
            try:
                # Avoid requiring the root to exist on the validating host.
                binary_abs = binary if binary.is_absolute() else binary.resolve()
                root_abs = root if root.is_absolute() else root.resolve()
                binary_abs.relative_to(root_abs)
                under_root = True
                break
            except ValueError:
                continue
        if not under_root:
            issues.append(
                ManifestIssue(
                    severity=ManifestValidationSeverity.ERROR,
                    code="BINARY_OUTSIDE_RUNTIME_ROOT",
                    message=(
                        "binary_path must be under /opt/yonder/runtimes: "
                        f"{manifest.runtime.binary_path}"
                    ),
                    field="runtime.binary_path",
                )
            )

    if not manifest.capabilities:
        issues.append(
            ManifestIssue(
                severity=ManifestValidationSeverity.WARNING,
                code="MISSING_CAPABILITIES",
                message="Manifest declares no modality capabilities",
                field="capabilities",
            )
        )
    else:
        unknown_caps = [c for c in manifest.capabilities if c not in KNOWN_CAPABILITIES]
        for cap in unknown_caps:
            issues.append(
                ManifestIssue(
                    severity=ManifestValidationSeverity.WARNING,
                    code="UNKNOWN_CAPABILITY",
                    message=f"Unknown capability label: {cap}",
                    field="capabilities",
                )
            )

    errors = [issue for issue in issues if issue.severity == ManifestValidationSeverity.ERROR]
    return ManifestValidationResult(valid=len(errors) == 0, issues=issues)


def load_manifest_file(path: Path) -> ModelManifest:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Manifest root must be a mapping: {path}")
    return ModelManifest.model_validate(data)


def load_manifests_from_dir(directory: Path) -> list[ModelManifest]:
    """Load manifests from a directory.

    A single invalid YAML must not prevent Gateway startup. Bad files are
    skipped so the control plane stays up; fix or remove the file and reload.
    """
    if not directory.exists():
        return []
    manifests: list[ModelManifest] = []
    for path in sorted(directory.glob("*.yaml")):
        if path.name.endswith(".example.yaml"):
            continue
        try:
            manifests.append(load_manifest_file(path))
        except (OSError, ValueError, ValidationError, YAMLError) as exc:
            _LOG.error("Skipping invalid model manifest %s: %s", path, exc)
    return manifests


def manifest_public_dict(manifest: ModelManifest) -> dict[str, Any]:
    return manifest.model_dump(mode="json")
