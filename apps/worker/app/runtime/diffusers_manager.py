"""Worker-owned Diffusers pipeline and generation job manager.

Torch and Diffusers are optional worker runtime dependencies. They are imported
only for a real load; dry-run control-plane tests do not require either package.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yonder_configuration.models import DiffusersWorkerRuntimeConfig

SUPPORTED_FAMILIES = {
    "image": frozenset({"sdxl", "z-image"}),
    "video": frozenset({"cogvideox", "wan"}),
}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def resolve_cuda_device(
    gpu_assignments: list[int], *, configured: str = "cuda"
) -> str:
    """Map placement GPUs to a torch device string.

    Diffusers runs in-process with llama.cpp peers, so bind the physical
    CUDA index (``cuda:1``). Do not remap visibility in-process: after CUDA
    has initialized, late visibility changes are ignored and
    ``pipeline.to("cuda")`` sticks on GPU 0.
    """
    if not gpu_assignments:
        raise RuntimeError("gpu_assignments are required for Diffusers device binding")
    index = int(gpu_assignments[0])
    if index < 0:
        raise RuntimeError("gpu_assignments must be non-negative")
    if configured == "cuda":
        return f"cuda:{index}"
    return configured


@dataclass
class ManagedDiffusersPipeline:
    deployment_id: str
    model_definition_id: str | None
    profile: str
    family: str
    model_path: str | None
    gpu_assignments: list[int]
    status: str = "loading"
    pipeline: Any = None
    device: str = "cuda:0"
    updated_at: str = field(default_factory=_now)
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "deployment_id": self.deployment_id,
            "model_definition_id": self.model_definition_id,
            "profile": self.profile,
            "family": self.family,
            "model_path": self.model_path,
            "gpu_assignments": list(self.gpu_assignments),
            "status": self.status,
            "device": self.device,
            "dtype": "float16",
            "updated_at": self.updated_at,
            "message": self.message,
        }


class DiffusersRuntimeManager:
    """Own Diffusers pipelines and generation jobs inside the worker process."""

    def __init__(self, config: DiffusersWorkerRuntimeConfig) -> None:
        self._config = config
        self._deployments: dict[str, ManagedDiffusersPipeline] = {}
        self._jobs: dict[str, dict[str, Any]] = {}
        self._artifacts: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled)

    @property
    def dry_run(self) -> bool:
        return bool(self._config.dry_run)

    def list_deployments(self) -> list[dict[str, Any]]:
        with self._lock:
            return [item.as_dict() for item in self._deployments.values()]

    def _path_allowed(self, path: Path) -> bool:
        resolved = path.expanduser().resolve()
        for root in self._config.allowed_model_roots:
            try:
                resolved.relative_to(root.expanduser().resolve())
                return True
            except ValueError:
                continue
        return False

    def occupied_gpus(self, *, exclude: str | None = None) -> set[int]:
        return {
            index
            for index, count in self.lane_counts(exclude=exclude).items()
            if count > 0
        }

    def lane_counts(self, *, exclude: str | None = None) -> dict[int, int]:
        counts: dict[int, int] = {}
        for deployment_id, runtime in self._deployments.items():
            if deployment_id == exclude:
                continue
            if runtime.status in {"loading", "ready", "draining"}:
                for index in runtime.gpu_assignments:
                    counts[int(index)] = counts.get(int(index), 0) + 1
        return counts

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
        if maintenance:
            raise RuntimeError("Worker is in maintenance; refusing Diffusers load")
        if not gpu_assignments:
            raise RuntimeError("gpu_assignments are required for Diffusers load")
        if len(set(gpu_assignments)) != len(gpu_assignments):
            raise RuntimeError("gpu_assignments must be unique")
        if any(index < 0 for index in gpu_assignments):
            raise RuntimeError("gpu_assignments must be non-negative")

        max_lanes = int(getattr(self._config, "max_lanes_per_gpu", 3) or 3)
        with self._lock:
            # already_occupied is the set of GPUs at lane cap from peers.
            capped = set(already_occupied or ())
            local = self.lane_counts(exclude=deployment_id)
            blocked = sorted(
                {
                    int(index)
                    for index in gpu_assignments
                    if int(index) in capped or local.get(int(index), 0) >= max_lanes
                }
            )
            if blocked:
                raise RuntimeError(
                    f"GPUs {blocked} are at the lane cap ({max_lanes} per GPU)"
                )

            profile = str(runtime_configuration.get("profile") or "").lower()
            family = str(
                runtime_configuration.get("family")
                or runtime_configuration.get("pipeline_family")
                or ""
            ).lower()
            if profile not in SUPPORTED_FAMILIES:
                raise RuntimeError(
                    f"Unsupported Diffusers profile: {profile!r} "
                    f"(supported={sorted(SUPPORTED_FAMILIES)})"
                )
            if family not in SUPPORTED_FAMILIES[profile]:
                raise RuntimeError(
                    f"Unsupported {profile} Diffusers family: {family!r} "
                    f"(supported={sorted(SUPPORTED_FAMILIES[profile])})"
                )

            raw_path = (
                runtime_configuration.get("model_path")
                or runtime_configuration.get("source_path")
                or (runtime_configuration.get("source") or {}).get("path")
            )
            if not raw_path:
                raise RuntimeError("model_path is required for Diffusers load")
            model_path = Path(str(raw_path)).expanduser()
            if not self._path_allowed(model_path):
                raise RuntimeError(
                    f"model_path is outside allowed model roots: {model_path}"
                )

            device = resolve_cuda_device(
                gpu_assignments, configured=str(self._config.device)
            )
            record = ManagedDiffusersPipeline(
                deployment_id=deployment_id,
                model_definition_id=model_definition_id,
                profile=profile,
                family=family,
                model_path=str(model_path),
                gpu_assignments=list(gpu_assignments),
                device=device,
                message="Loading Diffusers pipeline",
            )
            self._deployments[deployment_id] = record

            if self.dry_run:
                record.status = "ready"
                record.message = (
                    "Dry-run Diffusers load acknowledged; torch was not imported"
                )
                record.updated_at = _now()
                return record.as_dict()

            if not self.enabled:
                self._deployments.pop(deployment_id, None)
                raise RuntimeError("Diffusers worker runtime is disabled")
            if not model_path.exists():
                self._deployments.pop(deployment_id, None)
                raise RuntimeError(f"Diffusers model path not found: {model_path}")

            try:
                record.pipeline = self._load_pipeline(
                    model_path=model_path,
                    profile=profile,
                    family=family,
                    device=device,
                )
            except Exception:
                self._deployments.pop(deployment_id, None)
                raise
            record.status = "ready"
            record.message = f"Diffusers {family} {profile} pipeline ready"
            record.updated_at = _now()
            return record.as_dict()

    @staticmethod
    def _resolve_sdxl_weights(model_path: Path) -> Path | None:
        """Return a single-file SDXL checkpoint if that is what the path points at."""
        resolved = model_path.expanduser().resolve()
        if resolved.is_file() and resolved.suffix.lower() in {".safetensors", ".ckpt"}:
            return resolved
        if resolved.is_dir():
            if (resolved / "model_index.json").is_file():
                return None
            matches = sorted(
                [
                    *resolved.glob("*.safetensors"),
                    *resolved.glob("*.ckpt"),
                ]
            )
            if len(matches) == 1:
                return matches[0]
        return None

    def _load_pipeline(
        self,
        *,
        model_path: Path,
        profile: str,
        family: str,
        device: str,
    ) -> Any:
        try:
            import diffusers
            import torch
        except ImportError as exc:
            raise RuntimeError(
                "Real Diffusers loads require optional worker torch and diffusers "
                "runtime packages"
            ) from exc

        pipeline_names = {
            "sdxl": ("StableDiffusionXLPipeline",),
            # ZImagePipeline is required for Tongyi Z-Image trees (Diffusers >=0.39).
            "z-image": ("ZImagePipeline", "DiffusionPipeline"),
            "cogvideox": ("CogVideoXPipeline", "DiffusionPipeline"),
            # WanPipeline landed after 0.32; fall back to generic Diffusers load.
            "wan": ("WanPipeline", "DiffusionPipeline"),
        }
        pipeline_type = None
        for name in pipeline_names[family]:
            pipeline_type = getattr(diffusers, name, None)
            if pipeline_type is not None:
                break
        if pipeline_type is None:
            raise RuntimeError(
                f"Installed diffusers does not provide any of "
                f"{pipeline_names[family]} for family={family!r}"
            )

        single_file: Path | None = None
        if family == "sdxl":
            single_file = self._resolve_sdxl_weights(model_path)

        if single_file is not None:
            from_single_file = getattr(pipeline_type, "from_single_file", None)
            if from_single_file is None:
                raise RuntimeError(
                    "StableDiffusionXLPipeline.from_single_file is unavailable "
                    "in the installed diffusers package"
                )
            # Prefer a sibling Diffusers config tree so air-gapped hosts do not
            # contact Hugging Face for the SDXL base config.
            config_dir = None
            for candidate in (
                single_file.parent / "diffusers-config",
                Path("/srv/yonder-storage/model-bundles/sdxl-turbo-diffusers-config"),
            ):
                if (candidate / "model_index.json").is_file():
                    config_dir = candidate
                    break
            load_kwargs: dict[str, Any] = {
                "torch_dtype": torch.float16,
                "local_files_only": True,
            }
            if config_dir is not None:
                load_kwargs["config"] = str(config_dir)
            pipeline = from_single_file(str(single_file), **load_kwargs)
        else:
            pipeline = pipeline_type.from_pretrained(
                str(model_path),
                torch_dtype=torch.float16,
                local_files_only=True,
            )
        pipeline.to(device)
        return pipeline

    def unload(self, deployment_id: str) -> dict[str, Any]:
        with self._lock:
            runtime = self._deployments.pop(deployment_id, None)
            if runtime is None:
                return {
                    "deployment_id": deployment_id,
                    "status": "stopped",
                    "message": "Deployment already stopped",
                    "updated_at": _now(),
                }
            runtime.pipeline = None
            runtime.status = "stopped"
            runtime.message = "Diffusers pipeline unloaded"
            runtime.updated_at = _now()
            if not self.dry_run:
                try:
                    import torch

                    torch.cuda.empty_cache()
                except ImportError:
                    pass
            return runtime.as_dict()

    def drain(self, deployment_id: str) -> dict[str, Any]:
        with self._lock:
            runtime = self._deployments.get(deployment_id)
            if runtime is None:
                raise RuntimeError(f"Unknown deployment: {deployment_id}")
            runtime.status = "draining"
            runtime.message = "Diffusers drain acknowledged"
            runtime.updated_at = _now()
            return runtime.as_dict()

    def create_job(
        self,
        *,
        job_id: str,
        deployment_id: str | None,
        mode: str,
        prompt: str,
        negative_prompt: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        now = _now()
        if self.dry_run:
            job = {
                "accepted": True,
                "job_id": job_id,
                "status": "queued",
                "availability": "dry_run",
                "mode": mode,
                "deployment_id": deployment_id,
                "progress": [
                    {
                        "job_id": job_id,
                        "step": "accepted",
                        "message": (
                            "Dry-run generation acknowledged; no artifact was produced"
                        ),
                        "percent": 0.0,
                    }
                ],
                "assets": [],
                "created_at": now,
                "updated_at": now,
            }
            with self._lock:
                self._jobs[job_id] = job
            return dict(job)

        if not self.enabled:
            raise RuntimeError("Diffusers worker runtime is disabled")
        with self._lock:
            runtime = self._deployments.get(deployment_id or "")
            if runtime is None or runtime.status != "ready":
                raise RuntimeError(
                    f"Diffusers deployment is not ready: {deployment_id}"
                )
            job = {
                "accepted": True,
                "job_id": job_id,
                "status": "queued",
                "availability": "available",
                "mode": mode,
                "deployment_id": deployment_id,
                "progress": [],
                "assets": [],
                "created_at": now,
                "updated_at": now,
            }
            self._jobs[job_id] = job
        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, runtime, prompt, negative_prompt, dict(params)),
            daemon=True,
        )
        thread.start()
        return dict(job)

    def _set_progress(
        self,
        job_id: str,
        *,
        step: str,
        message: str,
        percent: float | None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            entry = {
                "job_id": job_id,
                "step": step,
                "message": message,
                "percent": percent,
            }
            progress = list(job.get("progress") or [])
            if progress and progress[-1].get("step") == step:
                progress[-1] = entry
            else:
                progress.append(entry)
            job["progress"] = progress
            job["updated_at"] = _now()

    def _run_job(
        self,
        job_id: str,
        runtime: ManagedDiffusersPipeline,
        prompt: str,
        negative_prompt: str,
        params: dict[str, Any],
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "running"
            job["updated_at"] = _now()
        steps = int(params.get("steps", 20))
        self._set_progress(
            job_id,
            step="running",
            message=f"Starting {runtime.profile} generation ({steps} steps)",
            percent=1.0,
        )
        try:
            call_args: dict[str, Any] = {
                "prompt": prompt,
                "negative_prompt": negative_prompt or None,
                "num_inference_steps": steps,
                "guidance_scale": float(params.get("guidance", 7.5)),
            }
            if runtime.profile == "video":
                # Keep canaries / lab demos inside a single 32GB V100.
                if "num_frames" in params:
                    call_args["num_frames"] = int(params["num_frames"])
                elif "frames" in params:
                    call_args["num_frames"] = int(params["frames"])
                else:
                    call_args["num_frames"] = 17
            if "height" in params:
                call_args["height"] = int(params["height"])
            if "width" in params:
                call_args["width"] = int(params["width"])

            def _on_step(
                _pipe: Any,
                step_index: int,
                _timestep: Any,
                callback_kwargs: dict[str, Any],
            ) -> dict[str, Any]:
                # Diffusers reports 0-based step indexes.
                done = min(step_index + 1, steps)
                percent = round(100.0 * done / max(steps, 1), 1)
                self._set_progress(
                    job_id,
                    step="denoise",
                    message=f"Denoising step {done}/{steps}",
                    percent=percent,
                )
                return callback_kwargs

            # Prefer callback_on_step_end (Diffusers ≥0.27); fall back silently.
            try:
                result = runtime.pipeline(
                    callback_on_step_end=_on_step,
                    **{key: value for key, value in call_args.items() if value is not None},
                )
            except TypeError:
                result = runtime.pipeline(
                    **{key: value for key, value in call_args.items() if value is not None}
                )
            self._set_progress(
                job_id,
                step="encode",
                message="Writing artifact",
                percent=97.0,
            )
            output_root = self._config.output_root.expanduser().resolve()
            output_root.mkdir(parents=True, exist_ok=True)
            if runtime.profile == "image":
                path = output_root / f"{job_id}.png"
                result.images[0].save(path)
                mime_type = "image/png"
                kind = "image"
            else:
                from diffusers.utils import export_to_video

                path = output_root / f"{job_id}.mp4"
                frames = result.frames[0]
                export_to_video(frames, str(path))
                mime_type = "video/mp4"
                kind = "video"
            artifact_id = f"worker-{uuid.uuid4().hex[:16]}"
            asset = {
                "artifact_id": artifact_id,
                "kind": kind,
                "mime_type": mime_type,
                "filename": path.name,
                "byte_size": path.stat().st_size,
            }
            with self._lock:
                self._artifacts[artifact_id] = {
                    "artifact_id": artifact_id,
                    "path": str(path),
                    "mime_type": mime_type,
                    "filename": path.name,
                    "byte_size": path.stat().st_size,
                    "job_id": job_id,
                }
                job["status"] = "succeeded"
                job["assets"] = [asset]
                job["updated_at"] = _now()
            self._set_progress(
                job_id,
                step="done",
                message="Generation succeeded",
                percent=100.0,
            )
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                job["status"] = "failed"
                job["error"] = f"{type(exc).__name__}: {exc}"
                job["updated_at"] = _now()
            self._set_progress(
                job_id,
                step="failed",
                message=f"{type(exc).__name__}: {exc}",
                percent=100.0,
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job is not None else None

    def resolve_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self._lock:
            meta = self._artifacts.get(artifact_id)
            if meta is None:
                return None
            path = Path(str(meta["path"])).expanduser().resolve()
            root = self._config.output_root.expanduser().resolve()
            try:
                path.relative_to(root)
            except ValueError:
                return None
            if not path.is_file():
                return None
            return {
                **meta,
                "path": str(path),
                "byte_size": path.stat().st_size,
            }

    def cancel_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job["status"] == "queued":
                job["status"] = "cancelled"
                job["updated_at"] = _now()
            return dict(job)
