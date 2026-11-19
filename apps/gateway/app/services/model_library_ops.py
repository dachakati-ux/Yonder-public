"""Model library operations: bundle registration/rollback, placement, and benchmarks."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yonder_contracts.model_bundles import verify_bundle_dir

from app.services.model_discovery import ModelDiscoveryError, resolve_allowed_path

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def is_model_library_enabled() -> bool:
    """Return whether the optional Model Library catalog/install module is enabled."""
    raw = os.getenv("YONDER_MODEL_LIBRARY_ENABLED", "true").strip().lower()
    if raw in _FALSE_VALUES:
        return False
    if raw in _TRUE_VALUES:
        return True
    return True


@dataclass
class ModelRegistration:
    """Validated model bundle registered for a specific runtime."""

    model_id: str
    bundle_path: str
    runtime_id: str
    modality: str
    verified_files: int
    total_bytes: int
    status: str = "registered"
    registered_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    previous_runtime_id: str | None = None
    previous_bundle_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "bundle_path": self.bundle_path,
            "runtime_id": self.runtime_id,
            "modality": self.modality,
            "verified_files": self.verified_files,
            "total_bytes": self.total_bytes,
            "status": self.status,
            "registered_at": self.registered_at,
            "previous_runtime_id": self.previous_runtime_id,
            "previous_bundle_path": self.previous_bundle_path,
        }


class ModelRegistrationStore:
    """Thread-safe in-memory registry of validated bundles with safe rollback."""

    def __init__(self) -> None:
        self._records: dict[str, ModelRegistration] = {}
        self._history: dict[str, ModelRegistration] = {}

    def validate_and_register(
        self,
        *,
        model_id: str,
        raw_path: str,
        model_roots: list[Path],
        runtime_id: str,
        modality: str = "chat",
        simulate_load_failure: bool = False,
    ) -> ModelRegistration:
        """Validate bundle checksums/path and register it, rolling back on load failure."""
        clean_id = model_id.strip()
        if not clean_id:
            raise ModelDiscoveryError("INVALID_MODEL_ID", "model_id is required.")
        if not runtime_id.strip():
            raise ModelDiscoveryError("INVALID_RUNTIME_ID", "runtime_id is required.")

        bundle_dir = resolve_allowed_path(raw_path, model_roots)
        manifest_path = bundle_dir / "bundle.yaml"
        if not manifest_path.is_file():
            raise ModelDiscoveryError(
                "BUNDLE_MANIFEST_MISSING",
                f"Expected bundle.yaml inside {bundle_dir}",
            )

        report = verify_bundle_dir(bundle_dir)
        if not report.valid or report.manifest is None:
            messages = "; ".join(issue.message for issue in report.issues)
            raise ModelDiscoveryError(
                "BUNDLE_VERIFICATION_FAILED",
                messages or "Bundle verification failed.",
            )

        previous = self._records.get(clean_id)
        if previous is not None:
            self._history[clean_id] = previous

        files = report.manifest.files
        record = ModelRegistration(
            model_id=clean_id,
            bundle_path=str(bundle_dir),
            runtime_id=runtime_id.strip(),
            modality=modality.strip() or "chat",
            verified_files=len(files),
            total_bytes=sum(int(item.size_bytes or 0) for item in files),
            status="registered",
            previous_runtime_id=previous.runtime_id if previous else None,
            previous_bundle_path=previous.bundle_path if previous else None,
        )
        self._records[clean_id] = record

        if simulate_load_failure:
            self.rollback(clean_id, reason="Simulated runtime load failure")
            raise ModelDiscoveryError(
                "RUNTIME_LOAD_FAILED_ROLLED_BACK",
                f"Runtime load failed for '{clean_id}'; registration safely rolled back.",
            )

        return record

    def rollback(self, model_id: str, *, reason: str = "operator_rollback") -> dict[str, Any]:
        """Restore the prior registration if present, or remove the failed registration."""
        clean_id = model_id.strip()
        current = self._records.get(clean_id)
        if current is None:
            raise ModelDiscoveryError(
                "REGISTRATION_NOT_FOUND",
                f"No registration found for '{clean_id}'.",
            )
        prior = self._history.pop(clean_id, None)
        if prior is not None:
            self._records[clean_id] = prior
            return {
                "model_id": clean_id,
                "rolled_back": True,
                "restored_previous": True,
                "reason": reason,
                "active_registration": prior.to_dict(),
            }
        self._records.pop(clean_id, None)
        return {
            "model_id": clean_id,
            "rolled_back": True,
            "restored_previous": False,
            "reason": reason,
            "active_registration": None,
        }

    def list_registrations(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._records.values()]


def _extract_worker_vram_gb(record: dict[str, Any]) -> float | None:
    """Return total reported free/usable GPU VRAM in GB, or None when unreported."""
    hardware = record.get("hardware_summary")
    if not isinstance(hardware, dict):
        return None
    gpus = hardware.get("gpus")
    if not isinstance(gpus, list) or not gpus:
        return None
    total_mb = 0.0
    for gpu in gpus:
        if not isinstance(gpu, dict):
            continue
        free_mb = gpu.get("memory_free_mb")
        total_gpu_mb = gpu.get("memory_total_mb")
        if isinstance(free_mb, (int, float)) and free_mb > 0:
            total_mb += float(free_mb)
        elif isinstance(total_gpu_mb, (int, float)) and total_gpu_mb > 0:
            total_mb += float(total_gpu_mb)
    return round(total_mb / 1024.0, 2) if total_mb > 0 else None


def evaluate_heterogeneous_placement(
    workers: dict[str, dict[str, Any]],
    *,
    required_vram_gb: float,
    preferred_worker_id: str | None = None,
    excluded_worker_ids: set[str] | None = None,
    supported_runtimes: set[str] | None = None,
) -> dict[str, Any]:
    """Select the healthiest compatible worker across heterogeneous hosts."""
    excluded = excluded_worker_ids or set()
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for worker_id, record in sorted(workers.items()):
        if worker_id in excluded:
            rejected.append({"worker_id": worker_id, "reason": "excluded_after_failure"})
            continue
        status = str(record.get("status") or "unknown").lower()
        maintenance = bool(record.get("maintenance_state") or record.get("maintenance"))
        if maintenance:
            rejected.append({"worker_id": worker_id, "reason": "maintenance_mode"})
            continue
        if status not in {"healthy", "online", "ready"}:
            rejected.append({"worker_id": worker_id, "reason": f"worker_status_{status}"})
            continue
        if supported_runtimes:
            worker_runtimes = set(record.get("runtimes") or [])
            if worker_runtimes and not (worker_runtimes & supported_runtimes):
                rejected.append({"worker_id": worker_id, "reason": "runtime_unsupported"})
                continue

        vram_gb = _extract_worker_vram_gb(record)
        if vram_gb is None:
            rejected.append({"worker_id": worker_id, "reason": "hardware_not_reported"})
            continue
        if vram_gb < required_vram_gb:
            rejected.append(
                {
                    "worker_id": worker_id,
                    "reason": "insufficient_vram",
                    "available_vram_gb": vram_gb,
                    "required_vram_gb": required_vram_gb,
                }
            )
            continue

        headroom_gb = round(vram_gb - required_vram_gb, 2)
        candidates.append(
            {
                "worker_id": worker_id,
                "available_vram_gb": vram_gb,
                "headroom_gb": headroom_gb,
                "preferred": worker_id == preferred_worker_id,
            }
        )

    candidates.sort(
        key=lambda item: (not item["preferred"], -item["headroom_gb"], item["worker_id"])
    )
    selected = candidates[0]["worker_id"] if candidates else None
    return {
        "status": "scheduled" if selected else "no_healthy_candidate",
        "selected_worker_id": selected,
        "fallback_worker_ids": [item["worker_id"] for item in candidates[1:]],
        "candidates": candidates,
        "rejected": rejected,
    }


def reschedule_failed_deployment(
    workers: dict[str, dict[str, Any]],
    *,
    failed_worker_id: str,
    required_vram_gb: float,
    supported_runtimes: set[str] | None = None,
) -> dict[str, Any]:
    """Reschedule away from a failed worker onto the next healthy compatible host."""
    plan = evaluate_heterogeneous_placement(
        workers,
        required_vram_gb=required_vram_gb,
        excluded_worker_ids={failed_worker_id},
        supported_runtimes=supported_runtimes,
    )
    plan["failed_worker_id"] = failed_worker_id
    plan["rescheduled"] = plan["selected_worker_id"] is not None
    return plan


def build_compatibility_matrix(
    presets: dict[str, dict[str, Any]],
    workers: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build an explicit hardware/runtime compatibility matrix without fabricating metrics."""
    matrix: list[dict[str, Any]] = []
    for preset_id, preset in sorted(presets.items()):
        if not workers:
            matrix.append(
                {
                    "runtime_id": preset_id,
                    "label": preset.get("label", preset_id),
                    "worker_id": None,
                    "compatibility": "worker_not_registered",
                    "vram_gb": None,
                }
            )
            continue
        for worker_id, record in sorted(workers.items()):
            vram_gb = _extract_worker_vram_gb(record)
            maintenance = bool(record.get("maintenance_state") or record.get("maintenance"))
            if maintenance:
                compat = "worker_in_maintenance"
            elif vram_gb is None:
                compat = "not_reported"
            elif vram_gb >= 16.0:
                compat = "compatible"
            elif vram_gb >= 6.0:
                compat = "compatible_quantized"
            else:
                compat = "insufficient_vram"
            matrix.append(
                {
                    "runtime_id": preset_id,
                    "label": preset.get("label", preset_id),
                    "worker_id": worker_id,
                    "compatibility": compat,
                    "vram_gb": vram_gb,
                }
            )
    return matrix


class BenchmarkStore:
    """In-memory store for reproducible model benchmark runs."""

    def __init__(self) -> None:
        self._runs: list[dict[str, Any]] = []

    def record_run(
        self,
        *,
        model_id: str,
        runtime_id: str,
        worker_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        seed: int = 42,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if latency_ms <= 0:
            raise ValueError("latency_ms must be positive.")
        if prompt_tokens < 0 or completion_tokens < 0:
            raise ValueError("Token counts cannot be negative.")
        config_payload = json.dumps(
            {
                "model_id": model_id,
                "runtime_id": runtime_id,
                "seed": seed,
                "parameters": parameters or {},
            },
            sort_keys=True,
        )
        config_hash = hashlib.sha256(config_payload.encode("utf-8")).hexdigest()[:16]
        tps = round(completion_tokens / (latency_ms / 1000.0), 2) if completion_tokens > 0 else 0.0
        entry = {
            "run_id": f"bench-{len(self._runs) + 1:04d}",
            "model_id": model_id,
            "runtime_id": runtime_id,
            "worker_id": worker_id,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "latency_ms": round(latency_ms, 2),
            "tokens_per_sec": tps,
            "seed": seed,
            "reproducible_config_hash": config_hash,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        self._runs.append(entry)
        return entry

    def list_runs(self, *, model_id: str | None = None) -> list[dict[str, Any]]:
        if model_id:
            return [r for r in self._runs if r["model_id"] == model_id]
        return list(self._runs)
