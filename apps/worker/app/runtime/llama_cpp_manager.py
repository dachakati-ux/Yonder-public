"""Worker-local llama.cpp process manager for scheduled deployments."""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from yonder_configuration.models import LlamaCppWorkerRuntimeConfig
from yonder_telemetry import log_event

logger = logging.getLogger("yonder.worker.llama_cpp")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _artifact_bytes(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        return int(path.stat().st_size)
    except OSError:
        return None


SUPPORTED_PROFILES = frozenset({"chat", "embedding", "rerank"})


@dataclass
class ManagedRuntime:
    deployment_id: str
    model_definition_id: str | None
    status: str
    base_url: str
    port: int
    gpu_assignments: list[int] = field(default_factory=list)
    model_path: str | None = None
    alias: str | None = None
    profile: str = "chat"
    pid: int | None = None
    process: subprocess.Popen[str] | None = None
    updated_at: str = field(default_factory=_now)
    message: str = ""
    runtime_configuration: dict[str, Any] = field(default_factory=dict)
    load_started_at: str | None = None
    load_finished_at: str | None = None
    load_duration_ms: int | None = None
    artifact_bytes: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "deployment_id": self.deployment_id,
            "model_definition_id": self.model_definition_id,
            "status": self.status,
            "base_url": self.base_url,
            "port": self.port,
            "gpu_assignments": list(self.gpu_assignments),
            "model_path": self.model_path,
            "alias": self.alias,
            "profile": self.profile,
            "pid": self.pid,
            "updated_at": self.updated_at,
            "message": self.message,
            "runtime_configuration": dict(self.runtime_configuration),
            "load_started_at": self.load_started_at,
            "load_finished_at": self.load_finished_at,
            "load_duration_ms": self.load_duration_ms,
            "artifact_bytes": self.artifact_bytes,
        }


class LlamaCppRuntimeManager:
    """Start/stop loopback llama-server processes with GPU visibility."""

    def __init__(self, config: LlamaCppWorkerRuntimeConfig) -> None:
        self._config = config
        self._deployments: dict[str, ManagedRuntime] = {}
        self._ports_in_use: set[int] = set()

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled)

    @property
    def dry_run(self) -> bool:
        return bool(self._config.dry_run)

    def list_deployments(self) -> list[dict[str, Any]]:
        return [item.as_dict() for item in self._deployments.values()]

    def endpoint_for(self, deployment_id: str) -> str | None:
        """Return the worker-local endpoint for an active managed runtime.

        The worker API uses this only as a reverse-proxy target. It must never
        be advertised directly to a remote Gateway because loopback belongs to
        the worker host, not the control plane.
        """
        runtime = self._deployments.get(deployment_id)
        if runtime is None or runtime.status not in {"loading", "ready", "draining"}:
            return None
        return runtime.base_url

    def _allocate_port(self) -> int:
        port = self._config.base_port
        while port in self._ports_in_use or not self._port_free(port):
            port += 1
            if port > 65000:
                raise RuntimeError("No free ports available for llama.cpp runtime")
        self._ports_in_use.add(port)
        return port

    @staticmethod
    def _port_free(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                return False
        return True

    def _path_allowed(self, path: Path, *, roots: list[Path] | None = None) -> bool:
        check_roots = list(roots if roots is not None else (self._config.allowed_model_roots or []))
        if not check_roots:
            return True
        resolved = path.resolve()
        for root in check_roots:
            try:
                resolved.relative_to(root.expanduser().resolve())
                return True
            except ValueError:
                continue
        return False

    def _require_allowed_path(self, raw: str | None, *, label: str) -> Path | None:
        if not raw:
            return None
        path = Path(str(raw)).expanduser()
        if not self._path_allowed(path):
            raise RuntimeError(
                f"{label} is outside allowed model roots: {path}"
            )
        return path

    def _require_runtime_binary(self, runtime_configuration: dict[str, Any]) -> Path:
        override = runtime_configuration.get("binary_path")
        if override:
            path = Path(str(override)).expanduser()
            roots = list(self._config.allowed_runtime_roots or [])
            if roots and not self._path_allowed(path, roots=roots):
                raise RuntimeError(
                    f"binary_path is outside allowed runtime roots: {path}"
                )
            return path
        return Path(self._config.binary_path)

    def occupied_gpus(self, *, exclude: str | None = None) -> set[int]:
        return {
            index
            for index, count in self.lane_counts(exclude=exclude).items()
            if count > 0
        }

    def lane_counts(self, *, exclude: str | None = None) -> dict[int, int]:
        counts: dict[int, int] = {}
        for deployment_id, runtime in self._deployments.items():
            if exclude and deployment_id == exclude:
                continue
            if runtime.status in {"ready", "loading", "draining"}:
                for index in runtime.gpu_assignments:
                    counts[int(index)] = counts.get(int(index), 0) + 1
        return counts

    def _available_vram_mib(self, gpu_assignments: list[int]) -> list[int]:
        """Read live free VRAM for the selected physical GPUs.

        This worker-side check closes the gap between a possibly stale Gateway
        heartbeat and process launch. It also accounts for processes outside the
        worker manager, such as an operator-managed llama.cpp sidecar.
        """
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.free",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Cannot verify live GPU VRAM before load: "
                f"{result.stderr.strip() or 'nvidia-smi failed'}"
            )
        by_index: dict[int, int] = {}
        for line in result.stdout.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 2:
                continue
            try:
                by_index[int(parts[0])] = int(parts[1])
            except ValueError:
                continue
        missing = [index for index in gpu_assignments if index not in by_index]
        if missing:
            raise RuntimeError(
                f"Cannot verify live VRAM for GPU indexes {missing}; refusing load"
            )
        return [by_index[index] for index in gpu_assignments]

    def _resolve_ctx_size(self, runtime_configuration: dict[str, Any]) -> int:
        raw = runtime_configuration.get("context_length")
        if raw is None:
            return int(self._config.ctx_size)
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Invalid context_length: {raw!r}") from exc
        if value < 1:
            raise RuntimeError(f"context_length must be >= 1, got {value}")
        return value

    def _preflight_vram(
        self,
        *,
        gpu_assignments: list[int],
        runtime_configuration: dict[str, Any],
    ) -> list[int]:
        free_mib = self._available_vram_mib(gpu_assignments)
        reserve_mib = int(float(self._config.vram_reserve_gb) * 1024)
        minimum_gb = runtime_configuration.get("minimum_gpu_memory_gb")
        minimum_mib = (
            int(float(minimum_gb) * 1024) if minimum_gb is not None else None
        )
        if minimum_mib is None:
            raise RuntimeError(
                "minimum_gpu_memory_gb is required for a managed GPU load"
            )
        for index, available in zip(gpu_assignments, free_mib, strict=True):
            required = minimum_mib + reserve_mib
            if available < required:
                raise RuntimeError(
                    f"GPU {index} has {available} MiB free; load requires "
                    f"{minimum_mib} MiB plus {reserve_mib} MiB reserved"
                )
        return [max(available - reserve_mib, 1) for available in free_mib]

    def load(
        self,
        *,
        deployment_id: str,
        model_definition_id: str | None,
        runtime_configuration: dict[str, Any],
        gpu_assignments: list[int],
        maintenance: bool = False,
        already_occupied: set[int] | None = None,
    ) -> dict[str, Any]:
        started_at = _now()
        started_perf = time.perf_counter()
        try:
            result = self._load_inner(
                deployment_id=deployment_id,
                model_definition_id=model_definition_id,
                runtime_configuration=runtime_configuration,
                gpu_assignments=gpu_assignments,
                maintenance=maintenance,
                already_occupied=already_occupied,
                started_at=started_at,
                started_perf=started_perf,
            )
            finished_at = _now()
            duration_ms = int((time.perf_counter() - started_perf) * 1000)
            result["load_started_at"] = started_at
            result["load_finished_at"] = finished_at
            result["load_duration_ms"] = duration_ms
            if "artifact_bytes" not in result or result.get("artifact_bytes") is None:
                path_raw = result.get("model_path")
                result["artifact_bytes"] = _artifact_bytes(
                    Path(str(path_raw)) if path_raw else None
                )
            record = self._deployments.get(deployment_id)
            if record is not None:
                record.load_started_at = started_at
                record.load_finished_at = finished_at
                record.load_duration_ms = duration_ms
                record.artifact_bytes = result.get("artifact_bytes")
            log_event(
                logger,
                "runtime.load.completed",
                deployment_id=deployment_id,
                model_definition_id=model_definition_id,
                provider="llama-cpp",
                model_path=result.get("model_path"),
                artifact_bytes=result.get("artifact_bytes"),
                duration_ms=duration_ms,
                outcome="success",
                status=result.get("status"),
                gpu_assignments=list(gpu_assignments),
            )
            return result
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started_perf) * 1000)
            path_hint = (
                runtime_configuration.get("model_path")
                or runtime_configuration.get("source_path")
                or None
            )
            log_event(
                logger,
                "runtime.load.completed",
                level=logging.WARNING,
                deployment_id=deployment_id,
                model_definition_id=model_definition_id,
                provider="llama-cpp",
                model_path=path_hint,
                duration_ms=duration_ms,
                outcome="failed",
                status="failed",
                gpu_assignments=list(gpu_assignments),
                error=str(exc),
            )
            raise

    def _load_inner(
        self,
        *,
        deployment_id: str,
        model_definition_id: str | None,
        runtime_configuration: dict[str, Any],
        gpu_assignments: list[int],
        maintenance: bool = False,
        already_occupied: set[int] | None = None,
        started_at: str | None = None,
        started_perf: float | None = None,
    ) -> dict[str, Any]:
        del started_perf  # reserved for outer timing wrapper
        if maintenance:
            raise RuntimeError("Worker is in maintenance; refusing runtime load")
        if not gpu_assignments:
            raise RuntimeError("gpu_assignments are required for llama.cpp runtime load")
        if len(set(gpu_assignments)) != len(gpu_assignments):
            raise RuntimeError("gpu_assignments must be unique")
        if any(index < 0 for index in gpu_assignments):
            raise RuntimeError("gpu_assignments must be non-negative")
        occupied = set(already_occupied or ())
        max_lanes = int(getattr(self._config, "max_lanes_per_gpu", 3) or 3)
        local = self.lane_counts(exclude=deployment_id)
        blocked = sorted(
            {
                int(index)
                for index in gpu_assignments
                if int(index) in occupied or local.get(int(index), 0) >= max_lanes
            }
        )
        if blocked:
            raise RuntimeError(
                f"GPUs {blocked} are at the lane cap ({max_lanes} per GPU)"
            )

        profile = str(runtime_configuration.get("profile") or "chat")
        if profile not in SUPPORTED_PROFILES:
            raise RuntimeError(
                f"Unsupported llama.cpp profile: {profile!r} "
                f"(supported={sorted(SUPPORTED_PROFILES)})"
            )

        model_path_raw = (
            runtime_configuration.get("model_path")
            or runtime_configuration.get("source_path")
            or (runtime_configuration.get("source") or {}).get("path")
            or runtime_configuration.get("model")
        )
        model_path = self._require_allowed_path(
            str(model_path_raw) if model_path_raw else None,
            label="model_path",
        )
        mmproj_raw = runtime_configuration.get("mmproj_path")
        mmproj_path = self._require_allowed_path(
            str(mmproj_raw) if mmproj_raw else None,
            label="mmproj_path",
        )
        alias = (
            runtime_configuration.get("model_identifier")
            or runtime_configuration.get("alias")
            or model_definition_id
            or deployment_id
        )
        port = self._allocate_port()
        base_url = f"http://{self._config.listen_host}:{port}"
        artifact_bytes = _artifact_bytes(model_path)
        record = ManagedRuntime(
            deployment_id=deployment_id,
            model_definition_id=model_definition_id,
            status="loading",
            base_url=base_url,
            port=port,
            gpu_assignments=list(gpu_assignments),
            model_path=str(model_path) if model_path else None,
            alias=str(alias) if alias else None,
            profile=profile,
            runtime_configuration=dict(runtime_configuration),
            message="Starting llama.cpp runtime",
            load_started_at=started_at or _now(),
            artifact_bytes=artifact_bytes,
        )
        self._deployments[deployment_id] = record

        if self._config.dry_run or not self._config.enabled:
            record.status = "ready"
            record.message = (
                "Dry-run/stub runtime acknowledged (no process started)"
            )
            record.updated_at = _now()
            return record.as_dict()

        binary = self._require_runtime_binary(runtime_configuration)
        if not binary.is_file():
            self._ports_in_use.discard(port)
            self._deployments.pop(deployment_id, None)
            raise RuntimeError(f"llama.cpp binary not found: {binary}")
        if model_path is None or not model_path.is_file():
            self._ports_in_use.discard(port)
            self._deployments.pop(deployment_id, None)
            raise RuntimeError(f"Model path not found: {model_path}")
        if mmproj_path is not None and not mmproj_path.is_file():
            self._ports_in_use.discard(port)
            self._deployments.pop(deployment_id, None)
            raise RuntimeError(f"mmproj path not found: {mmproj_path}")

        try:
            split_weights = self._preflight_vram(
                gpu_assignments=gpu_assignments,
                runtime_configuration=runtime_configuration,
            )
        except Exception:
            self._ports_in_use.discard(port)
            self._deployments.pop(deployment_id, None)
            raise
        env = os.environ.copy()
        # Hiding non-selected GPUs is required: a zero tensor-split can still
        # allocate CUDA context memory on an ostensibly unused device.
        env["CUDA_VISIBLE_DEVICES"] = ",".join(str(index) for index in gpu_assignments)
        if self._config.library_path:
            lib = str(self._config.library_path)
            env["LD_LIBRARY_PATH"] = (
                f"{lib}:{env['LD_LIBRARY_PATH']}" if env.get("LD_LIBRARY_PATH") else lib
            )
            bin_dir = str(binary.parent)
            env["LD_LIBRARY_PATH"] = f"{bin_dir}:{env['LD_LIBRARY_PATH']}"

        args = [
            str(binary),
            "--model",
            str(model_path),
            "--alias",
            str(alias),
            "--host",
            self._config.listen_host,
            "--port",
            str(port),
            "--ctx-size",
            str(self._resolve_ctx_size(runtime_configuration)),
            "--n-gpu-layers",
            str(self._config.n_gpu_layers),
            "--threads",
            str(self._config.threads),
        ]
        if profile == "chat":
            args.extend(["--jinja", "--reasoning", "off"])
            if mmproj_path is not None:
                args.extend(["--mmproj", str(mmproj_path)])
        elif profile == "embedding":
            pooling = str(runtime_configuration.get("pooling") or "last")
            args.extend(["--embedding", "--pooling", pooling])
        elif profile == "rerank":
            pooling = str(runtime_configuration.get("pooling") or "rank")
            args.extend(
                ["--embedding", "--reranking", "--pooling", pooling]
            )
        if len(gpu_assignments) > 1:
            # Stable default: layer split. Tensor mode remains experimental and
            # disables llama.cpp auto-fit, so it is intentionally not exposed.
            split = ",".join(str(weight) for weight in split_weights)
            args.extend(
                [
                    "--split-mode",
                    self._config.multi_gpu_split_mode,
                    "--tensor-split",
                    split,
                ]
            )

        process = subprocess.Popen(
            args,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        record.process = process
        record.pid = process.pid
        try:
            self._wait_healthy(base_url)
        except Exception:
            self._stop_process(record)
            self._ports_in_use.discard(port)
            self._deployments.pop(deployment_id, None)
            raise
        record.status = "ready"
        record.message = f"llama.cpp {profile} runtime ready"
        record.updated_at = _now()
        return record.as_dict()

    def _wait_healthy(self, base_url: str) -> None:
        deadline = time.time() + self._config.health_timeout_seconds
        last_error = "timeout"
        while time.time() < deadline:
            try:
                response = httpx.get(f"{base_url}/health", timeout=2.0)
                if response.status_code == 200:
                    return
                last_error = f"status={response.status_code}"
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
            time.sleep(0.5)
        raise RuntimeError(f"llama.cpp runtime failed health check: {last_error}")

    def drain(self, deployment_id: str) -> dict[str, Any]:
        runtime = self._deployments.get(deployment_id)
        if runtime is None:
            raise RuntimeError(f"Unknown deployment: {deployment_id}")
        runtime.status = "draining"
        runtime.message = "Drain acknowledged"
        runtime.updated_at = _now()
        return runtime.as_dict()

    def unload(self, deployment_id: str) -> dict[str, Any]:
        runtime = self._deployments.get(deployment_id)
        if runtime is None:
            return {
                "deployment_id": deployment_id,
                "status": "stopped",
                "message": "Deployment already stopped",
                "updated_at": _now(),
            }
        self._stop_process(runtime)
        self._ports_in_use.discard(runtime.port)
        runtime.status = "stopped"
        runtime.message = "Runtime stopped"
        runtime.updated_at = _now()
        runtime.process = None
        runtime.pid = None
        return runtime.as_dict()

    @staticmethod
    def _stop_process(runtime: ManagedRuntime) -> None:
        process = runtime.process
        if process is None:
            return
        if process.poll() is not None:
            return
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
