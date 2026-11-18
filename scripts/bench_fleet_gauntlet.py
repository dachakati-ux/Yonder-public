#!/usr/bin/env python3
"""Fleet model gauntlet — Gateway-orchestrated measurement battery.

Runs the same suite for verified chat / embedding / rerank models across
worker-ai1, worker-ai3, and worker-ai7. Never invents metrics: missing
hardware or ledger fields are recorded as not_reported.

Usage:
  GATEWAY_URL=http://127.0.0.1:8080 python3 scripts/bench_fleet_gauntlet.py \\
    --wave ai3-chat --ladder fast --pins first --skip-stress --dry-run
"""

from __future__ import annotations

import argparse
import atexit
import fcntl
import json
import math
import os
import signal
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

NOT_REPORTED = "not_reported"

# Fast-track parallel waves (one primary host per model — hosts are equivalent).
WAVES: dict[str, dict[str, Any]] = {
    # Phase 1 big models — priority order within each host; run waves in parallel
    # across ai1/ai3/ai7 so all 6 V100s can work (2 per host).
    "big-ai1": {
        "hosts": ["worker-ai1"],
        "models": [
            "yonder-qwen36-35b-a3b-q4",
            "yonder-gemma4-31b-it-qat-gguf",
            "yonder-qwen3-coder-next-q4",
        ],
        "mode": "both",
    },
    "big-ai3": {
        "hosts": ["worker-ai3"],
        "models": [
            "yonder-qwen36-27b-q4",
            "yonder-gemma4-26b-a4b-it-qat-gguf",
        ],
        "mode": "both",
    },
    "ai1-big": {
        "hosts": ["worker-ai1"],
        "models": [
            "yonder-gemma4-31b-it-qat-gguf",
            "yonder-qwen36-35b-a3b-q4",
            "yonder-qwen3-coder-next-q4",
        ],
        "mode": "both",
    },
    "ai3-chat": {
        "hosts": ["worker-ai3"],
        "models": [
            "yonder-nemotron-3-nano-4b",
            "yonder-qwen35-9b",
        ],
        "mode": "both",
    },
    "ai7-retrieval": {
        "hosts": ["worker-ai7"],
        "models": [
            "yonder-qwen3-embedding-4b",
            "yonder-qwen3-embedding-8b",
            "yonder-qwen3-reranker-4b",
        ],
        # single_gpu first tonight; two_gpu only if time
        "mode": "single_gpu",
    },
}

# Locked model matrix from the gauntlet plan (mirrors config/models placement_limits).
MODELS: list[dict[str, Any]] = [
    {
        "id": "yonder-nemotron-3-nano-4b",
        "profile": "chat",
        "single_gpu_max": 65536,
        "two_gpu_max": 131072,
    },
    {
        "id": "yonder-qwen35-9b",
        "profile": "chat",
        "single_gpu_max": 32768,
        "two_gpu_max": 65536,
    },
    {
        "id": "yonder-gemma4-31b-it-qat-gguf",
        "profile": "chat",
        "single_gpu_max": 65536,
        "two_gpu_max": 65536,
    },
    {
        "id": "yonder-qwen36-35b-a3b-q4",
        "profile": "chat",
        # single_gpu 65536 already measured 2026-07-31; ladder still reaffirms + two_gpu
        "single_gpu_max": 65536,
        "two_gpu_max": 65536,
    },
    {
        "id": "yonder-qwen36-27b-q4",
        "profile": "chat",
        "single_gpu_max": 65536,
        "two_gpu_max": 65536,
    },
    {
        "id": "yonder-gemma4-26b-a4b-it-qat-gguf",
        "profile": "chat",
        "single_gpu_max": 65536,
        "two_gpu_max": 65536,
    },
    {
        "id": "yonder-qwen3-coder-next-q4",
        "profile": "chat",
        "single_gpu_max": 0,  # blocked
        "two_gpu_max": 65536,
    },
    {
        "id": "yonder-qwen3-embedding-4b",
        "profile": "embedding",
        "single_gpu_max": 32768,
        "two_gpu_max": 32768,
    },
    {
        "id": "yonder-qwen3-embedding-8b",
        "profile": "embedding",
        "single_gpu_max": 32768,
        "two_gpu_max": 32768,
    },
    {
        "id": "yonder-qwen3-reranker-4b",
        "profile": "rerank",
        "single_gpu_max": 32768,
        "two_gpu_max": 32768,
    },
]

SINGLE_CTX_FULL = [2048, 4096, 8192, 16384, 32768, 65536]
TWO_CTX_FULL = [2048, 4096, 8192, 16384, 32768, 65536, 131072]
# Fast track: fewer steps for rough ceilings tonight.
SINGLE_CTX_FAST = [2048, 8192, 32768]
TWO_CTX_FAST = [2048, 8192, 32768, 65536, 131072]

ORPHAN_UNLOAD_STATES = {
    "ready",
    "loading",
    "queued",
    "starting",
    "draining",
}

HOST_GPUS: dict[str, dict[str, list[list[int]]]] = {
    "worker-ai1": {
        "single_gpu": [[0], [1]],
        "two_gpu": [[0, 1]],
    },
    "worker-ai3": {
        "single_gpu": [[0], [1]],
        "two_gpu": [[0, 1]],
    },
    "worker-ai7": {
        "single_gpu": [[0], [1]],
        "two_gpu": [[0, 1]],
    },
}

GAUNTLET_ALIASES = {
    "chat": "yonder-gauntlet-chat",
    "embedding": "yonder-gauntlet-embed",
    "rerank": "yonder-gauntlet-rerank",
}

NEEDLE = "GAUNTLET-NEEDLE-7F3A"


@dataclass
class RunRecord:
    stamp: str
    phase: str
    model_id: str
    profile: str
    worker_id: str
    mode: str
    gpu_indexes: str
    context_length: int | None
    deployment_id: str
    probe: str
    outcome: str
    error_code: str = ""
    error_detail: str = ""
    load_duration_ms: str = NOT_REPORTED
    worker_duration_ms: str = NOT_REPORTED
    artifact_bytes: str = NOT_REPORTED
    peak_vram_mib_csv: str = NOT_REPORTED
    peak_vram_total_mib: str = NOT_REPORTED
    ttft_ms: str = NOT_REPORTED
    request_duration_ms: str = NOT_REPORTED
    prompt_tokens: str = NOT_REPORTED
    completion_tokens: str = NOT_REPORTED
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        extra = data.pop("extra", {}) or {}
        data.update(extra)
        return data


class GatewayClient:
    def __init__(self, base_url: str, timeout: float = 600.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> tuple[int, dict[str, Any] | str]:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read().decode()
                if not raw:
                    return resp.status, {}
                try:
                    return resp.status, json.loads(raw)
                except json.JSONDecodeError:
                    return resp.status, raw
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode() if exc.fp else ""
            try:
                parsed: dict[str, Any] | str = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = raw
            return exc.code, parsed
        except Exception as exc:  # noqa: BLE001
            return 0, {"error": {"message": str(exc), "code": "CLIENT_ERROR"}}


class GauntletRunner:
    def __init__(
        self,
        *,
        gateway: GatewayClient,
        outdir: Path,
        hosts: list[str],
        modes: list[str],
        dry_run: bool,
        settle_seconds: float,
        ready_timeout: float,
        models: list[dict[str, Any]] | None = None,
        ladder: str = "fast",
        pins: str = "first",
        resume: bool = True,
    ) -> None:
        self.gw = gateway
        self.outdir = outdir
        self.hosts = hosts
        self.modes = modes
        self.dry_run = dry_run
        self.settle_seconds = settle_seconds
        self.ready_timeout = ready_timeout
        self.models = models or MODELS
        self.ladder = ladder
        self.pins = pins
        self.resume = resume
        self.runs_path = outdir / "runs.jsonl"
        self.summary_path = outdir / "summary.tsv"
        self.failures_path = outdir / "failures.tsv"
        self.state_path = outdir / "state.json"
        self.last_ok_ctx: dict[tuple[str, str, str], int] = {}
        self.completed_keys: set[str] = set()
        self.rejected_ladder: dict[str, int] = {}
        self.active_deployments: set[str] = set()
        self._summary_rows: list[dict[str, str]] = []
        self._cleanup_done = False
        self._aborted = False
        if self.resume:
            self._load_state()

    def log(self, msg: str) -> None:
        print(f"[gauntlet] {msg}", flush=True)

    def now(self) -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def step_key(
        phase: str,
        model_id: str,
        worker_id: str,
        mode: str,
        gpus: list[int] | str,
        context_length: int | None,
        probe: str,
    ) -> str:
        if isinstance(gpus, list):
            gpus_s = ",".join(str(i) for i in gpus)
        else:
            gpus_s = gpus
        ctx_s = "" if context_length is None else str(context_length)
        return f"{phase}|{model_id}|{worker_id}|{mode}|{gpus_s}|{ctx_s}|{probe}"

    def _load_state(self) -> None:
        if not self.state_path.exists():
            # Rebuild from runs.jsonl if present (shared-outdir resume).
            if self.runs_path.exists():
                self._rebuild_state_from_runs()
            return
        try:
            with self.state_path.open("r", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            self.log(f"state load failed ({exc}); continuing without resume cache")
            return
        for key, ctx in (data.get("last_ok_ctx") or {}).items():
            parts = str(key).split("|")
            if len(parts) == 3:
                self.last_ok_ctx[(parts[0], parts[1], parts[2])] = int(ctx)
        self.completed_keys = set(data.get("completed_keys") or [])
        self.rejected_ladder = {
            str(k): int(v) for k, v in (data.get("rejected_ladder") or {}).items()
        }
        self.log(
            f"resume: last_ok={len(self.last_ok_ctx)} "
            f"completed={len(self.completed_keys)} "
            f"rejected_ladders={len(self.rejected_ladder)}"
        )

    def _rebuild_state_from_runs(self) -> None:
        try:
            lines = self.runs_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = self.step_key(
                str(row.get("phase") or ""),
                str(row.get("model_id") or ""),
                str(row.get("worker_id") or ""),
                str(row.get("mode") or ""),
                str(row.get("gpu_indexes") or ""),
                row.get("context_length"),
                str(row.get("probe") or ""),
            )
            outcome = str(row.get("outcome") or "")
            if outcome in {"ok", "pass", "soft_fail", "skipped", "rejected"}:
                self.completed_keys.add(key)
            if (
                row.get("phase") == "context"
                and row.get("probe") == "load"
                and outcome == "ok"
                and row.get("context_length") is not None
            ):
                tkey = (
                    str(row["model_id"]),
                    str(row["worker_id"]),
                    str(row["mode"]),
                )
                ctx = int(row["context_length"])
                prev = self.last_ok_ctx.get(tkey)
                self.last_ok_ctx[tkey] = max(prev, ctx) if prev is not None else ctx
            if (
                row.get("phase") == "context"
                and row.get("probe") == "load"
                and outcome == "rejected"
                and row.get("context_length") is not None
            ):
                rkey = (
                    f"{row['model_id']}|{row['worker_id']}|{row['mode']}|"
                    f"{row.get('gpu_indexes') or ''}"
                )
                self.rejected_ladder[rkey] = int(row["context_length"])

    def _persist_state(self) -> None:
        self.outdir.mkdir(parents=True, exist_ok=True)
        # Merge under exclusive lock so parallel host waves can share outdir.
        with self.state_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            raw = handle.read()
            existing: dict[str, Any] = {}
            if raw.strip():
                try:
                    existing = json.loads(raw)
                except json.JSONDecodeError:
                    existing = {}
            last_ok = {
                str(k): int(v) for k, v in (existing.get("last_ok_ctx") or {}).items()
            }
            for (m, w, mode), ctx in self.last_ok_ctx.items():
                key = f"{m}|{w}|{mode}"
                # Conservative min when both writers have a value for same key.
                if key in last_ok:
                    last_ok[key] = min(last_ok[key], int(ctx))
                else:
                    last_ok[key] = int(ctx)
            completed = set(existing.get("completed_keys") or []) | self.completed_keys
            rejected = {
                str(k): int(v)
                for k, v in (existing.get("rejected_ladder") or {}).items()
            }
            rejected.update({str(k): int(v) for k, v in self.rejected_ladder.items()})
            payload = {
                "last_ok_ctx": last_ok,
                "completed_keys": sorted(completed),
                "rejected_ladder": rejected,
                # Process-local only (host orphan sweep covers cross-crash).
                "active_deployments": sorted(self.active_deployments),
                "updated_at": self.now(),
            }
            handle.seek(0)
            handle.truncate()
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            # Refresh local completed from merged view.
            self.completed_keys = completed
            self.rejected_ladder = rejected
            for key, ctx in last_ok.items():
                parts = key.split("|")
                if len(parts) == 3:
                    self.last_ok_ctx[(parts[0], parts[1], parts[2])] = int(ctx)

    def mark_completed(self, key: str, *, outcome: str = "ok") -> None:
        if outcome in {"ok", "pass", "soft_fail", "skipped", "rejected"}:
            self.completed_keys.add(key)
            if not self.dry_run:
                self._persist_state()

    def is_completed(self, key: str) -> bool:
        return self.resume and key in self.completed_keys

    def write_run(self, record: RunRecord) -> None:
        line = json.dumps(record.to_json(), default=str) + "\n"
        with self.runs_path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(line)
        key = self.step_key(
            record.phase,
            record.model_id,
            record.worker_id,
            record.mode,
            record.gpu_indexes,
            record.context_length,
            record.probe,
        )
        self.mark_completed(key, outcome=record.outcome)
        if record.outcome not in {"ok", "pass", "soft_fail", "skipped"}:
            with self.failures_path.open("a", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                handle.write(
                    "\t".join(
                        [
                            record.stamp,
                            record.phase,
                            record.model_id,
                            record.worker_id,
                            record.mode,
                            record.probe,
                            record.outcome,
                            record.error_code,
                            record.error_detail.replace("\t", " ").replace("\n", " ")[
                                :500
                            ],
                        ]
                    )
                    + "\n"
                )

    def matrix_jobs(self) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for model in self.models:
            for host in self.hosts:
                if host not in HOST_GPUS:
                    continue
                for mode in self.modes:
                    max_ctx = int(
                        model["single_gpu_max"]
                        if mode == "single_gpu"
                        else model["two_gpu_max"]
                    )
                    if max_ctx <= 0:
                        jobs.append(
                            {
                                "model": model,
                                "worker_id": host,
                                "mode": mode,
                                "gpu_indexes": [],
                                "skip_reason": "mode_blocked_by_manifest",
                            }
                        )
                        continue
                    pin_sets = HOST_GPUS[host][mode]
                    if self.pins == "first":
                        pin_sets = pin_sets[:1]
                    for gpus in pin_sets:
                        jobs.append(
                            {
                                "model": model,
                                "worker_id": host,
                                "mode": mode,
                                "gpu_indexes": gpus,
                                "max_ctx": max_ctx,
                            }
                        )
        return jobs

    def print_dry_run(self) -> None:
        self.log(
            f"DRY-RUN matrix (no loads) ladder={self.ladder} pins={self.pins}:"
        )
        for job in self.matrix_jobs():
            model = job["model"]
            if job.get("skip_reason"):
                self.log(
                    f"  SKIP {model['id']} {job['worker_id']} {job['mode']}: "
                    f"{job['skip_reason']}"
                )
                continue
            ctxs = self._contexts_for(job["mode"], job["max_ctx"])
            self.log(
                f"  {model['id']} profile={model['profile']} "
                f"{job['worker_id']} {job['mode']} gpus={job['gpu_indexes']} "
                f"ctx={ctxs}"
            )

    def _contexts_for(self, mode: str, max_ctx: int) -> list[int]:
        if self.ladder == "fast":
            base = SINGLE_CTX_FAST if mode == "single_gpu" else TWO_CTX_FAST
        else:
            base = SINGLE_CTX_FULL if mode == "single_gpu" else TWO_CTX_FULL
        return [c for c in base if c <= max_ctx]

    # ---- Gateway helpers ----

    def load_deployment(
        self,
        *,
        model_id: str,
        worker_id: str,
        mode: str,
        gpu_indexes: list[int],
        context_length: int,
    ) -> tuple[str | None, dict[str, Any], float]:
        body = {
            "model_definition_id": model_id,
            "worker_id": worker_id,
            "placement_mode": mode,
            "gpu_indexes": gpu_indexes,
            "context_length": context_length,
        }
        started = time.perf_counter()
        status, payload = self.gw.request("POST", "/api/admin/deployments/load", body)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if not isinstance(payload, dict):
            return None, {"error": {"message": str(payload), "code": "BAD_RESPONSE"}}, elapsed_ms
        dep = payload.get("deployment") or {}
        dep_id = dep.get("id")
        if status >= 400 or not dep_id:
            return None, payload, elapsed_ms
        dep_id_s = str(dep_id)
        self.active_deployments.add(dep_id_s)
        self._persist_state()
        if not self.wait_ready(dep_id_s):
            # Fail closed: unload orphan so the next ladder step can retry.
            self.unload(dep_id_s)
            return None, {
                "error": {
                    "message": f"deployment {dep_id_s} never reached ready",
                    "code": "LOAD_TIMEOUT",
                }
            }, elapsed_ms
        # Refresh deployment for timing fields in logs / runtime_configuration.
        _, refreshed = self.gw.request("GET", f"/api/admin/deployments/{dep_id_s}")
        if isinstance(refreshed, dict) and refreshed.get("deployment"):
            return dep_id_s, refreshed, elapsed_ms
        return dep_id_s, payload, elapsed_ms

    def wait_ready(self, deployment_id: str) -> bool:
        deadline = time.time() + self.ready_timeout
        while time.time() < deadline:
            status_code, payload = self.gw.request(
                "GET", f"/api/admin/deployments/{deployment_id}", timeout=30
            )
            if isinstance(payload, dict):
                dep = payload.get("deployment") or payload
                state = str(dep.get("status") or "")
                if state == "ready":
                    return True
                if state in {"failed", "unavailable", "cancelled", "stopped"}:
                    return False
            time.sleep(3)
        return False

    def unload(self, deployment_id: str) -> None:
        if not deployment_id:
            return
        status, payload = self.gw.request(
            "POST", f"/api/admin/deployments/{deployment_id}/unload", timeout=120
        )
        self.active_deployments.discard(deployment_id)
        if not self.dry_run:
            self._persist_state()
        if status >= 400:
            self.log(f"unload {deployment_id} failed status={status} detail={payload}")
        time.sleep(self.settle_seconds)

    def cleanup_deployments(self, *, reason: str, sweep_host_orphans: bool | None = None) -> None:
        if self._cleanup_done or self.dry_run:
            self._cleanup_done = True
            return
        self._cleanup_done = True
        if sweep_host_orphans is None:
            sweep_host_orphans = self._aborted
        self.log(
            f"cleanup ({reason}): tracked={len(self.active_deployments)} "
            f"sweep_orphans={sweep_host_orphans}"
        )
        tracked = sorted(self.active_deployments)
        for dep_id in tracked:
            self.log(f"cleanup unload tracked {dep_id}")
            try:
                self.unload(dep_id)
            except Exception as exc:  # noqa: BLE001
                self.log(f"cleanup tracked unload error: {exc}")
        if sweep_host_orphans:
            status, payload = self.gw.request("GET", "/api/admin/deployments", timeout=60)
            if status >= 400 or not isinstance(payload, dict):
                self.log(f"cleanup: could not list deployments status={status}")
            else:
                host_set = set(self.hosts)
                for dep in payload.get("deployments") or []:
                    if not isinstance(dep, dict):
                        continue
                    if dep.get("worker_id") not in host_set:
                        continue
                    state = str(dep.get("status") or "")
                    if state not in ORPHAN_UNLOAD_STATES:
                        continue
                    dep_id = str(dep.get("id") or "")
                    if not dep_id:
                        continue
                    self.log(
                        f"cleanup unload orphan {dep_id} state={state} "
                        f"worker={dep.get('worker_id')}"
                    )
                    try:
                        self.unload(dep_id)
                    except Exception as exc:  # noqa: BLE001
                        self.log(f"cleanup orphan unload error: {exc}")
        self._persist_state()

    def install_signal_handlers(self) -> None:
        def _handler(signum: int, _frame: Any) -> None:
            self._aborted = True
            self.log(f"signal {signum} received")
            self.cleanup_deployments(reason=f"signal_{signum}", sweep_host_orphans=True)
            raise SystemExit(128 + signum)

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)
        atexit.register(
            lambda: self.cleanup_deployments(
                reason="atexit",
                sweep_host_orphans=self._aborted,
            )
        )

    def set_alias(self, alias: str, deployment_id: str) -> None:
        status, payload = self.gw.request(
            "PATCH",
            f"/api/admin/routes/{alias}",
            {"deployment_id": deployment_id, "enabled": True},
            timeout=30,
        )
        if status >= 400:
            raise RuntimeError(f"alias bind failed: {payload}")

    def memory_used_mib(
        self, worker_id: str, gpu_indexes: list[int]
    ) -> tuple[str, str]:
        status, payload = self.gw.request("GET", "/api/admin/hardware", timeout=30)
        if status >= 400 or not isinstance(payload, dict):
            return NOT_REPORTED, NOT_REPORTED
        nodes = payload.get("nodes") or []
        target = next((n for n in nodes if n.get("worker_id") == worker_id), None)
        if target is None:
            return NOT_REPORTED, NOT_REPORTED
        hw = target.get("hardware") or {}
        gpus = hw.get("gpus") or []
        wanted = set(gpu_indexes)
        values: list[tuple[int, int]] = []
        for gpu in gpus:
            idx_metric = gpu.get("index") or {}
            idx = idx_metric.get("value") if isinstance(idx_metric, dict) else idx_metric
            if idx is None:
                continue
            idx = int(idx)
            if idx not in wanted:
                continue
            used = gpu.get("memory_used_bytes") or {}
            avail = used.get("availability") if isinstance(used, dict) else "available"
            raw = used.get("value") if isinstance(used, dict) else used
            if avail != "available" or raw is None:
                return NOT_REPORTED, NOT_REPORTED
            values.append((idx, int(raw) // (1024 * 1024)))
        values.sort()
        if len(values) != len(wanted):
            return NOT_REPORTED, NOT_REPORTED
        csv = ",".join(str(v) for _, v in values)
        total = str(sum(v for _, v in values))
        return csv, total

    def extract_load_timing(self, payload: dict[str, Any]) -> tuple[str, str, str]:
        dep = payload.get("deployment") or {}
        logs = dep.get("logs") or []
        load_ms = NOT_REPORTED
        worker_ms = NOT_REPORTED
        for line in reversed(logs):
            text = str(line)
            if "Load timing:" in text and "duration_ms=" in text:
                # Parse key=value fragments.
                parts = text.split()
                for part in parts:
                    if part.startswith("duration_ms="):
                        load_ms = part.split("=", 1)[1]
                    if part.startswith("worker_duration_ms="):
                        worker_ms = part.split("=", 1)[1]
                    if part.startswith("artifact_bytes="):
                        art = part.split("=", 1)[1]
                        return load_ms, worker_ms, art
                break
        rc = dep.get("runtime_configuration") or {}
        art = rc.get("artifact_bytes")
        if art is None:
            # model path size not always on deployment; leave not_reported
            art_s = NOT_REPORTED
        else:
            art_s = str(art)
        return load_ms, worker_ms, art_s

    # ---- Probes ----

    def bind_and_probe(
        self,
        *,
        phase: str,
        model: dict[str, Any],
        worker_id: str,
        mode: str,
        gpu_indexes: list[int],
        context_length: int,
        deployment_id: str,
        load_payload: dict[str, Any],
        client_load_ms: float,
        smoke_only: bool = False,
    ) -> None:
        profile = model["profile"]
        alias = GAUNTLET_ALIASES[profile]
        peak_csv, peak_total = self.memory_used_mib(worker_id, gpu_indexes)
        load_ms, worker_ms, art = self.extract_load_timing(load_payload)
        # Ledger fields stay not_reported when absent; client wall is separate.
        client_ms_s = f"{client_load_ms:.0f}"

        self.write_run(
            RunRecord(
                stamp=self.now(),
                phase=phase,
                model_id=model["id"],
                profile=profile,
                worker_id=worker_id,
                mode=mode,
                gpu_indexes=",".join(str(i) for i in gpu_indexes),
                context_length=context_length,
                deployment_id=deployment_id,
                probe="load",
                outcome="ok",
                load_duration_ms=load_ms,
                worker_duration_ms=worker_ms,
                artifact_bytes=art,
                peak_vram_mib_csv=peak_csv,
                peak_vram_total_mib=peak_total,
                request_duration_ms=client_ms_s,
                notes=f"client_load_wall_ms={client_ms_s}",
            )
        )

        try:
            self.set_alias(alias, deployment_id)
        except Exception as exc:  # noqa: BLE001
            self.write_run(
                RunRecord(
                    stamp=self.now(),
                    phase=phase,
                    model_id=model["id"],
                    profile=profile,
                    worker_id=worker_id,
                    mode=mode,
                    gpu_indexes=",".join(str(i) for i in gpu_indexes),
                    context_length=context_length,
                    deployment_id=deployment_id,
                    probe="alias_bind",
                    outcome="failed",
                    error_code="ALIAS_BIND",
                    error_detail=str(exc),
                )
            )
            return

        if smoke_only:
            self._smoke_probe(
                phase,
                model,
                worker_id,
                mode,
                gpu_indexes,
                context_length,
                deployment_id,
                alias,
            )
            return

        if profile == "chat":
            self.probe_chat(
                phase, model, worker_id, mode, gpu_indexes,
                context_length, deployment_id, alias,
            )
        elif profile == "embedding":
            self.probe_embedding(
                phase, model, worker_id, mode, gpu_indexes,
                context_length, deployment_id, alias,
            )
        elif profile == "rerank":
            self.probe_rerank(
                phase, model, worker_id, mode, gpu_indexes,
                context_length, deployment_id, alias,
            )

    def _smoke_probe(
        self,
        phase: str,
        model: dict[str, Any],
        worker_id: str,
        mode: str,
        gpu_indexes: list[int],
        context_length: int,
        deployment_id: str,
        alias: str,
    ) -> None:
        gpus = ",".join(str(i) for i in gpu_indexes)
        profile = model["profile"]
        if profile == "chat":
            self._chat_probe(
                phase,
                model,
                worker_id,
                mode,
                gpus,
                context_length,
                deployment_id,
                alias,
                probe="exact_ack",
                messages=[{"role": "user", "content": "Reply with exactly: gauntlet-ok"}],
                max_tokens=16,
                stream=False,
                expect_substr="gauntlet-ok",
            )
        elif profile == "embedding":
            vec = self._embed_one(alias, "gauntlet smoke")
            self.write_run(
                RunRecord(
                    stamp=self.now(),
                    phase=phase,
                    model_id=model["id"],
                    profile=profile,
                    worker_id=worker_id,
                    mode=mode,
                    gpu_indexes=gpus,
                    context_length=context_length,
                    deployment_id=deployment_id,
                    probe="dim_smoke",
                    outcome="pass" if vec else "failed",
                    notes=f"dim={len(vec) if vec else 0}",
                    error_detail="" if vec else "no vector",
                )
            )
        else:
            status, payload = self.gw.request(
                "POST",
                "/v1/rerank",
                {
                    "model": alias,
                    "query": "GPU",
                    "documents": ["GPU scheduling", "cake recipe"],
                    "top_n": 2,
                },
                timeout=120,
            )
            ok = status < 400 and isinstance(payload, dict)
            self.write_run(
                RunRecord(
                    stamp=self.now(),
                    phase=phase,
                    model_id=model["id"],
                    profile=profile,
                    worker_id=worker_id,
                    mode=mode,
                    gpu_indexes=gpus,
                    context_length=context_length,
                    deployment_id=deployment_id,
                    probe="order_sanity",
                    outcome="pass" if ok else "failed",
                    error_detail="" if ok else str(payload)[:300],
                )
            )

    def probe_chat(
        self,
        phase: str,
        model: dict[str, Any],
        worker_id: str,
        mode: str,
        gpu_indexes: list[int],
        context_length: int,
        deployment_id: str,
        alias: str,
    ) -> None:
        gpus = ",".join(str(i) for i in gpu_indexes)
        # exact_ack
        self._chat_probe(
            phase,
            model,
            worker_id,
            mode,
            gpus,
            context_length,
            deployment_id,
            alias,
            probe="exact_ack",
            messages=[{"role": "user", "content": "Reply with exactly: gauntlet-ok"}],
            max_tokens=16,
            stream=False,
            expect_substr="gauntlet-ok",
        )
        # short_reason
        self._chat_probe(
            phase,
            model,
            worker_id,
            mode,
            gpus,
            context_length,
            deployment_id,
            alias,
            probe="short_reason",
            messages=[
                {
                    "role": "user",
                    "content": "In one short sentence, what is a GPU used for in LLM inference?",
                }
            ],
            max_tokens=64,
            stream=False,
            expect_nonempty=True,
        )
        # long_prefill (only when ctx is large enough)
        if context_length >= 2048:
            fill_tokens = max(256, int(context_length * 0.8) - 64)
            # Rough char estimate (~4 chars/token) — not claimed as exact token count.
            filler = ("lorem " * (fill_tokens // 2))[: fill_tokens * 4]
            content = (
                f"The secret code is {NEEDLE}. Ignore all filler. "
                f"Filler follows:\n{filler}\n"
                f"What was the secret code? Reply with only the code."
            )
            self._chat_probe(
                phase,
                model,
                worker_id,
                mode,
                gpus,
                context_length,
                deployment_id,
                alias,
                probe="long_prefill",
                messages=[{"role": "user", "content": content}],
                max_tokens=32,
                stream=False,
                expect_substr=NEEDLE,
            )
        # stream_ttft
        self._chat_probe(
            phase,
            model,
            worker_id,
            mode,
            gpus,
            context_length,
            deployment_id,
            alias,
            probe="stream_ttft",
            messages=[{"role": "user", "content": "Say hello in five words or fewer."}],
            max_tokens=24,
            stream=True,
            expect_nonempty=True,
        )
        # jsonish
        self._chat_probe(
            phase,
            model,
            worker_id,
            mode,
            gpus,
            context_length,
            deployment_id,
            alias,
            probe="jsonish",
            messages=[
                {
                    "role": "user",
                    "content": (
                        'Respond with JSON only containing keys '
                        '"status" and "code". status must be "ok" and code must be 7.'
                    ),
                }
            ],
            max_tokens=64,
            stream=False,
            expect_substr='"status"',
            soft_keys=("status", "code"),
        )

    def _chat_probe(
        self,
        phase: str,
        model: dict[str, Any],
        worker_id: str,
        mode: str,
        gpus: str,
        context_length: int,
        deployment_id: str,
        alias: str,
        *,
        probe: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        stream: bool,
        expect_substr: str | None = None,
        expect_nonempty: bool = False,
        soft_keys: tuple[str, ...] = (),
    ) -> None:
        body = {
            "model": alias,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": stream,
            "temperature": 0.0,
        }
        started = time.perf_counter()
        if stream:
            outcome, detail, ttft, usage = self._chat_stream(body)
        else:
            status, payload = self.gw.request(
                "POST", "/v1/chat/completions", body, timeout=300
            )
            elapsed = (time.perf_counter() - started) * 1000
            ttft = NOT_REPORTED
            usage = {}
            if status >= 400 or not isinstance(payload, dict):
                outcome, detail = "failed", str(payload)[:400]
            else:
                content = (
                    ((payload.get("choices") or [{}])[0].get("message") or {}).get(
                        "content"
                    )
                    or ""
                )
                usage = payload.get("usage") or {}
                if expect_substr and expect_substr.lower() not in content.lower():
                    if soft_keys and all(k in content for k in soft_keys):
                        outcome, detail = "soft_fail", content[:200]
                    else:
                        outcome, detail = "failed", f"missing {expect_substr!r}: {content[:200]}"
                elif expect_nonempty and not content.strip():
                    outcome, detail = "failed", "empty content"
                elif soft_keys and not all(k in content for k in soft_keys):
                    outcome, detail = "soft_fail", content[:200]
                else:
                    outcome, detail = "pass", content[:200]
            request_ms = f"{elapsed:.1f}"
            self.write_run(
                RunRecord(
                    stamp=self.now(),
                    phase=phase,
                    model_id=model["id"],
                    profile=model["profile"],
                    worker_id=worker_id,
                    mode=mode,
                    gpu_indexes=gpus,
                    context_length=context_length,
                    deployment_id=deployment_id,
                    probe=probe,
                    outcome=outcome,
                    error_detail=detail if outcome != "pass" else "",
                    ttft_ms=ttft,
                    request_duration_ms=request_ms,
                    prompt_tokens=str(usage.get("prompt_tokens", NOT_REPORTED)),
                    completion_tokens=str(usage.get("completion_tokens", NOT_REPORTED)),
                    notes=detail if outcome == "pass" else "",
                )
            )
            return

        elapsed = (time.perf_counter() - started) * 1000
        self.write_run(
            RunRecord(
                stamp=self.now(),
                phase=phase,
                model_id=model["id"],
                profile=model["profile"],
                worker_id=worker_id,
                mode=mode,
                gpu_indexes=gpus,
                context_length=context_length,
                deployment_id=deployment_id,
                probe=probe,
                outcome=outcome,
                error_detail=detail if outcome != "pass" else "",
                ttft_ms=ttft,
                request_duration_ms=f"{elapsed:.1f}",
                prompt_tokens=str(usage.get("prompt_tokens", NOT_REPORTED)),
                completion_tokens=str(usage.get("completion_tokens", NOT_REPORTED)),
                notes=detail if outcome == "pass" else "",
            )
        )

    def _chat_stream(
        self, body: dict[str, Any]
    ) -> tuple[str, str, str, dict[str, Any]]:
        url = f"{self.gw.base_url}/v1/chat/completions"
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )
        started = time.perf_counter()
        ttft = NOT_REPORTED
        chunks: list[str] = []
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    if ttft == NOT_REPORTED:
                        ttft = f"{(time.perf_counter() - started) * 1000:.1f}"
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    delta = ((obj.get("choices") or [{}])[0].get("delta") or {}).get(
                        "content"
                    )
                    if delta:
                        chunks.append(str(delta))
        except Exception as exc:  # noqa: BLE001
            return "failed", str(exc)[:400], ttft, {}
        text = "".join(chunks)
        if not text.strip():
            return "failed", "empty stream content", ttft, {}
        return "pass", text[:200], ttft, {}

    def probe_embedding(
        self,
        phase: str,
        model: dict[str, Any],
        worker_id: str,
        mode: str,
        gpu_indexes: list[int],
        context_length: int,
        deployment_id: str,
        alias: str,
    ) -> None:
        gpus = ",".join(str(i) for i in gpu_indexes)
        # dim_smoke
        vec = self._embed_one(alias, "yonder embedding gauntlet")
        if vec is None:
            self.write_run(
                RunRecord(
                    stamp=self.now(),
                    phase=phase,
                    model_id=model["id"],
                    profile=model["profile"],
                    worker_id=worker_id,
                    mode=mode,
                    gpu_indexes=gpus,
                    context_length=context_length,
                    deployment_id=deployment_id,
                    probe="dim_smoke",
                    outcome="failed",
                    error_detail="no embedding vector",
                )
            )
            return
        dim = len(vec)
        finite = all(math.isfinite(float(x)) for x in vec)
        self.write_run(
            RunRecord(
                stamp=self.now(),
                phase=phase,
                model_id=model["id"],
                profile=model["profile"],
                worker_id=worker_id,
                mode=mode,
                gpu_indexes=gpus,
                context_length=context_length,
                deployment_id=deployment_id,
                probe="dim_smoke",
                outcome="pass" if dim > 0 and finite else "failed",
                notes=f"dim={dim}",
                error_detail="" if dim > 0 and finite else "non-finite or empty",
            )
        )
        for batch_name, n in (("batch_8", 8), ("batch_32", 32)):
            inputs = [f"gauntlet batch item {i}" for i in range(n)]
            started = time.perf_counter()
            status, payload = self.gw.request(
                "POST",
                "/v1/embeddings",
                {"model": alias, "input": inputs},
                timeout=300,
            )
            elapsed = (time.perf_counter() - started) * 1000
            ok = (
                status < 400
                and isinstance(payload, dict)
                and len(payload.get("data") or []) == n
            )
            self.write_run(
                RunRecord(
                    stamp=self.now(),
                    phase=phase,
                    model_id=model["id"],
                    profile=model["profile"],
                    worker_id=worker_id,
                    mode=mode,
                    gpu_indexes=gpus,
                    context_length=context_length,
                    deployment_id=deployment_id,
                    probe=batch_name,
                    outcome="pass" if ok else "failed",
                    request_duration_ms=f"{elapsed:.1f}",
                    error_detail="" if ok else str(payload)[:300],
                )
            )
        # order_sanity
        a = self._embed_one(alias, "kubernetes GPU scheduling")
        b = self._embed_one(alias, "kubernetes GPU scheduling")
        c = self._embed_one(alias, "strawberry cheesecake recipe")
        if a is None or b is None or c is None:
            outcome, detail = "failed", "missing vectors"
        else:
            self_sim = _cosine(a, b)
            other = _cosine(a, c)
            if self_sim > other:
                outcome, detail = "pass", f"self={self_sim:.4f} other={other:.4f}"
            else:
                outcome, detail = "failed", f"self={self_sim:.4f} other={other:.4f}"
        self.write_run(
            RunRecord(
                stamp=self.now(),
                phase=phase,
                model_id=model["id"],
                profile=model["profile"],
                worker_id=worker_id,
                mode=mode,
                gpu_indexes=gpus,
                context_length=context_length,
                deployment_id=deployment_id,
                probe="order_sanity",
                outcome=outcome,
                notes=detail,
                error_detail=detail if outcome != "pass" else "",
            )
        )

    def _embed_one(self, alias: str, text: str) -> list[float] | None:
        status, payload = self.gw.request(
            "POST",
            "/v1/embeddings",
            {"model": alias, "input": text},
            timeout=120,
        )
        if status >= 400 or not isinstance(payload, dict):
            return None
        data = payload.get("data") or []
        if not data:
            return None
        emb = data[0].get("embedding")
        if not isinstance(emb, list) or not emb:
            return None
        return [float(x) for x in emb]

    def probe_rerank(
        self,
        phase: str,
        model: dict[str, Any],
        worker_id: str,
        mode: str,
        gpu_indexes: list[int],
        context_length: int,
        deployment_id: str,
        alias: str,
    ) -> None:
        gpus = ",".join(str(i) for i in gpu_indexes)
        query = "How do I place a GGUF model on a V100 GPU?"
        docs = [
            "Yonder Gateway loads llama.cpp GGUF deployments onto selected GPUs.",
            "Strawberry jam pairs well with toasted sourdough bread.",
            "The Pacific Ocean is the largest ocean on Earth.",
            "Use placement_mode single_gpu and gpu_indexes to schedule models.",
        ]
        for probe, doc_list in (
            ("order_sanity", docs),
            ("batch_docs", docs + [f"distractor document {i}" for i in range(12)]),
        ):
            started = time.perf_counter()
            status, payload = self.gw.request(
                "POST",
                "/v1/rerank",
                {"model": alias, "query": query, "documents": doc_list, "top_n": 4},
                timeout=180,
            )
            elapsed = (time.perf_counter() - started) * 1000
            if status >= 400 or not isinstance(payload, dict):
                self.write_run(
                    RunRecord(
                        stamp=self.now(),
                        phase=phase,
                        model_id=model["id"],
                        profile=model["profile"],
                        worker_id=worker_id,
                        mode=mode,
                        gpu_indexes=gpus,
                        context_length=context_length,
                        deployment_id=deployment_id,
                        probe=probe,
                        outcome="failed",
                        request_duration_ms=f"{elapsed:.1f}",
                        error_detail=str(payload)[:300],
                    )
                )
                continue
            results = payload.get("results") or []
            top_idxs = [int(r.get("index")) for r in results[:2] if "index" in r]
            # Relevant docs are indexes 0 and 3 in the base list.
            ok = bool(top_idxs) and top_idxs[0] in {0, 3}
            self.write_run(
                RunRecord(
                    stamp=self.now(),
                    phase=phase,
                    model_id=model["id"],
                    profile=model["profile"],
                    worker_id=worker_id,
                    mode=mode,
                    gpu_indexes=gpus,
                    context_length=context_length,
                    deployment_id=deployment_id,
                    probe=probe,
                    outcome="pass" if ok else "failed",
                    request_duration_ms=f"{elapsed:.1f}",
                    notes=f"top_idxs={top_idxs}",
                    error_detail="" if ok else f"unexpected ranking {top_idxs}",
                )
            )

    # ---- Phases ----

    def phase_context(self) -> None:
        self.log("=== phase: context ladder ===")
        for job in self.matrix_jobs():
            model = job["model"]
            if job.get("skip_reason"):
                skip_key = self.step_key(
                    "context",
                    model["id"],
                    job["worker_id"],
                    job["mode"],
                    "",
                    None,
                    "matrix",
                )
                if not self.is_completed(skip_key):
                    self.write_run(
                        RunRecord(
                            stamp=self.now(),
                            phase="context",
                            model_id=model["id"],
                            profile=model["profile"],
                            worker_id=job["worker_id"],
                            mode=job["mode"],
                            gpu_indexes="",
                            context_length=None,
                            deployment_id="",
                            probe="matrix",
                            outcome="skipped",
                            notes=job["skip_reason"],
                        )
                    )
                continue
            worker_id = job["worker_id"]
            mode = job["mode"]
            gpus = job["gpu_indexes"]
            gpus_s = ",".join(str(i) for i in gpus)
            reject_key = f"{model['id']}|{worker_id}|{mode}|{gpus_s}"
            last_ok: int | None = self.last_ok_ctx.get((model["id"], worker_id, mode))
            for ctx in self._contexts_for(mode, job["max_ctx"]):
                load_key = self.step_key(
                    "context", model["id"], worker_id, mode, gpus, ctx, "load"
                )
                # Previously rejected at this or lower ctx on this pin → stop.
                rejected_at = self.rejected_ladder.get(reject_key)
                if rejected_at is not None and ctx >= rejected_at:
                    self.log(
                        f"resume skip climb {model['id']} {worker_id} {mode} "
                        f"gpus={gpus} ctx={ctx} (rejected_at={rejected_at})"
                    )
                    break
                if self.is_completed(load_key):
                    self.log(
                        f"resume skip context load {model['id']} {worker_id} "
                        f"{mode} gpus={gpus} ctx={ctx}"
                    )
                    last_ok = ctx if last_ok is None else max(last_ok, ctx)
                    continue
                self.log(
                    f"context load {model['id']} {worker_id} {mode} "
                    f"gpus={gpus} ctx={ctx}"
                )
                if self.dry_run:
                    continue
                dep_id, payload, client_ms = self.load_deployment(
                    model_id=model["id"],
                    worker_id=worker_id,
                    mode=mode,
                    gpu_indexes=gpus,
                    context_length=ctx,
                )
                if dep_id is None:
                    err = (payload.get("error") or {}) if isinstance(payload, dict) else {}
                    self.rejected_ladder[reject_key] = ctx
                    self.write_run(
                        RunRecord(
                            stamp=self.now(),
                            phase="context",
                            model_id=model["id"],
                            profile=model["profile"],
                            worker_id=worker_id,
                            mode=mode,
                            gpu_indexes=gpus_s,
                            context_length=ctx,
                            deployment_id="",
                            probe="load",
                            outcome="rejected",
                            error_code=str(err.get("code") or "LOAD_REJECTED"),
                            error_detail=str(err.get("message") or payload)[:500],
                            load_duration_ms=f"{client_ms:.0f}",
                        )
                    )
                    break
                time.sleep(self.settle_seconds)
                self.bind_and_probe(
                    phase="context",
                    model=model,
                    worker_id=worker_id,
                    mode=mode,
                    gpu_indexes=gpus,
                    context_length=ctx,
                    deployment_id=dep_id,
                    load_payload=payload if isinstance(payload, dict) else {},
                    client_load_ms=client_ms,
                    smoke_only=True,
                )
                peak_csv, peak_total = self.memory_used_mib(worker_id, gpus)
                self._summary_rows.append(
                    {
                        "model_id": model["id"],
                        "worker_id": worker_id,
                        "mode": mode,
                        "context": str(ctx),
                        "status": "ok",
                        "peak_vram_mib_csv": peak_csv,
                        "peak_vram_total_mib": peak_total,
                    }
                )
                last_ok = ctx
                self.unload(dep_id)
            if last_ok is not None and not self.dry_run:
                key = (model["id"], worker_id, mode)
                prev = self.last_ok_ctx.get(key)
                self.last_ok_ctx[key] = (
                    min(prev, last_ok) if prev is not None else last_ok
                )
                self._persist_state()
                self.log(
                    f"candidate measured max {model['id']} {worker_id} {mode}="
                    f"{self.last_ok_ctx[key]} (pin {gpus} last_ok={last_ok})"
                )

    def phase_inference(self) -> None:
        self.log("=== phase: inference suite ===")
        for job in self.matrix_jobs():
            model = job["model"]
            if job.get("skip_reason"):
                continue
            worker_id = job["worker_id"]
            mode = job["mode"]
            gpus = job["gpu_indexes"]
            # Inference uses first pin only (even when --pins all).
            if gpus != HOST_GPUS[worker_id][mode][0]:
                continue
            last_ok = self.last_ok_ctx.get((model["id"], worker_id, mode))
            if last_ok is None:
                last_ok = min(8192, int(job["max_ctx"]))
            targets = sorted({min(8192, last_ok), last_ok})
            for ctx in targets:
                suite_key = self.step_key(
                    "inference",
                    model["id"],
                    worker_id,
                    mode,
                    gpus,
                    ctx,
                    "suite",
                )
                if self.is_completed(suite_key):
                    self.log(
                        f"resume skip inference suite {model['id']} {worker_id} "
                        f"{mode} gpus={gpus} ctx={ctx}"
                    )
                    continue
                self.log(
                    f"inference {model['id']} {worker_id} {mode} gpus={gpus} ctx={ctx}"
                )
                if self.dry_run:
                    continue
                dep_id, payload, client_ms = self.load_deployment(
                    model_id=model["id"],
                    worker_id=worker_id,
                    mode=mode,
                    gpu_indexes=gpus,
                    context_length=ctx,
                )
                if dep_id is None:
                    err = (payload.get("error") or {}) if isinstance(payload, dict) else {}
                    self.write_run(
                        RunRecord(
                            stamp=self.now(),
                            phase="inference",
                            model_id=model["id"],
                            profile=model["profile"],
                            worker_id=worker_id,
                            mode=mode,
                            gpu_indexes=",".join(str(i) for i in gpus),
                            context_length=ctx,
                            deployment_id="",
                            probe="load",
                            outcome="rejected",
                            error_code=str(err.get("code") or "LOAD_REJECTED"),
                            error_detail=str(err.get("message") or payload)[:500],
                            load_duration_ms=f"{client_ms:.0f}",
                        )
                    )
                    continue
                time.sleep(self.settle_seconds)
                self.bind_and_probe(
                    phase="inference",
                    model=model,
                    worker_id=worker_id,
                    mode=mode,
                    gpu_indexes=gpus,
                    context_length=ctx,
                    deployment_id=dep_id,
                    load_payload=payload if isinstance(payload, dict) else {},
                    client_load_ms=client_ms,
                    smoke_only=False,
                )
                self.unload(dep_id)
                self.write_run(
                    RunRecord(
                        stamp=self.now(),
                        phase="inference",
                        model_id=model["id"],
                        profile=model["profile"],
                        worker_id=worker_id,
                        mode=mode,
                        gpu_indexes=",".join(str(i) for i in gpus),
                        context_length=ctx,
                        deployment_id=dep_id,
                        probe="suite",
                        outcome="pass",
                        notes="inference probes completed",
                    )
                )

    def phase_stress(self) -> None:
        self.log("=== phase: fleet stress ===")
        if self.dry_run:
            self.log(
                "stress scenarios: parallel_host_load, chat_storm_8/16, "
                "mixed_profile, two_gpu_pack"
            )
            return

        # Stress scenarios use the locked fleet defaults even if --models
        # subsets the context/inference matrix.
        by_id = {m["id"]: m for m in MODELS}
        chat_model = by_id["yonder-nemotron-3-nano-4b"]
        embed_model = by_id["yonder-qwen3-embedding-4b"]
        rerank_model = by_id["yonder-qwen3-reranker-4b"]
        big_model = by_id["yonder-gemma4-31b-it-qat-gguf"]

        # 1) Parallel host load of Nemotron on each host g0 @ 8192
        deps: dict[str, str] = {}
        self.log("stress: parallel host load")
        with ThreadPoolExecutor(max_workers=3) as pool:
            futs = {
                pool.submit(
                    self.load_deployment,
                    model_id=chat_model["id"],
                    worker_id=host,
                    mode="single_gpu",
                    gpu_indexes=[0],
                    context_length=8192,
                ): host
                for host in self.hosts
                if host in HOST_GPUS
            }
            for fut in as_completed(futs):
                host = futs[fut]
                dep_id, payload, client_ms = fut.result()
                if dep_id is None:
                    err = (payload.get("error") or {}) if isinstance(payload, dict) else {}
                    self.write_run(
                        RunRecord(
                            stamp=self.now(),
                            phase="stress",
                            model_id=chat_model["id"],
                            profile="chat",
                            worker_id=host,
                            mode="single_gpu",
                            gpu_indexes="0",
                            context_length=8192,
                            deployment_id="",
                            probe="parallel_host_load",
                            outcome="rejected",
                            error_code=str(err.get("code") or "LOAD_REJECTED"),
                            error_detail=str(err.get("message") or payload)[:500],
                            load_duration_ms=f"{client_ms:.0f}",
                        )
                    )
                else:
                    deps[host] = dep_id
                    self.write_run(
                        RunRecord(
                            stamp=self.now(),
                            phase="stress",
                            model_id=chat_model["id"],
                            profile="chat",
                            worker_id=host,
                            mode="single_gpu",
                            gpu_indexes="0",
                            context_length=8192,
                            deployment_id=dep_id,
                            probe="parallel_host_load",
                            outcome="ok",
                            load_duration_ms=f"{client_ms:.0f}",
                        )
                    )

        # Bind first successful deployment to gauntlet chat alias for storm.
        if deps:
            primary_host = next(iter(deps))
            self.set_alias(GAUNTLET_ALIASES["chat"], deps[primary_host])
            for concurrency in (8, 16):
                self.log(f"stress: chat_storm concurrency={concurrency}")
                self._chat_storm(concurrency=concurrency, duration_s=60)

        # Unload parallel loads
        for dep_id in deps.values():
            self.unload(dep_id)
        deps.clear()

        # 3) Mixed profile: chat ai1, embed ai3, rerank ai7
        self.log("stress: mixed_profile")
        mixed: list[tuple[str, str]] = []
        for host, model, mode, gpus, profile in (
            ("worker-ai1", chat_model, "single_gpu", [1], "chat"),
            ("worker-ai3", embed_model, "single_gpu", [0], "embedding"),
            ("worker-ai7", rerank_model, "single_gpu", [0], "rerank"),
        ):
            if host not in self.hosts:
                continue
            dep_id, payload, client_ms = self.load_deployment(
                model_id=model["id"],
                worker_id=host,
                mode=mode,
                gpu_indexes=gpus,
                context_length=8192,
            )
            if dep_id is None:
                continue
            self.set_alias(GAUNTLET_ALIASES[profile], dep_id)
            mixed.append((host, dep_id))
            self.write_run(
                RunRecord(
                    stamp=self.now(),
                    phase="stress",
                    model_id=model["id"],
                    profile=profile,
                    worker_id=host,
                    mode=mode,
                    gpu_indexes=",".join(str(i) for i in gpus),
                    context_length=8192,
                    deployment_id=dep_id,
                    probe="mixed_profile_load",
                    outcome="ok",
                    load_duration_ms=f"{client_ms:.0f}",
                )
            )
        self._mixed_storm(duration_s=60)
        for _, dep_id in mixed:
            self.unload(dep_id)

        # 4) Two-GPU pack on ai1 + companions
        self.log("stress: two_gpu_pack")
        pack_deps: list[str] = []
        if "worker-ai1" in self.hosts:
            dep_id, payload, client_ms = self.load_deployment(
                model_id=big_model["id"],
                worker_id="worker-ai1",
                mode="two_gpu",
                gpu_indexes=[0, 1],
                context_length=8192,
            )
            if dep_id:
                pack_deps.append(dep_id)
                self.write_run(
                    RunRecord(
                        stamp=self.now(),
                        phase="stress",
                        model_id=big_model["id"],
                        profile="chat",
                        worker_id="worker-ai1",
                        mode="two_gpu",
                        gpu_indexes="0,1",
                        context_length=8192,
                        deployment_id=dep_id,
                        probe="two_gpu_pack",
                        outcome="ok",
                        load_duration_ms=f"{client_ms:.0f}",
                    )
                )
            else:
                err = (payload.get("error") or {}) if isinstance(payload, dict) else {}
                self.write_run(
                    RunRecord(
                        stamp=self.now(),
                        phase="stress",
                        model_id=big_model["id"],
                        profile="chat",
                        worker_id="worker-ai1",
                        mode="two_gpu",
                        gpu_indexes="0,1",
                        context_length=8192,
                        deployment_id="",
                        probe="two_gpu_pack",
                        outcome="rejected",
                        error_code=str(err.get("code") or "LOAD_REJECTED"),
                        error_detail=str(err.get("message") or payload)[:500],
                    )
                )
        if "worker-ai3" in self.hosts:
            dep_id, _, client_ms = self.load_deployment(
                model_id=chat_model["id"],
                worker_id="worker-ai3",
                mode="single_gpu",
                gpu_indexes=[0],
                context_length=8192,
            )
            if dep_id:
                pack_deps.append(dep_id)
        if "worker-ai7" in self.hosts:
            dep_id, _, client_ms = self.load_deployment(
                model_id=embed_model["id"],
                worker_id="worker-ai7",
                mode="single_gpu",
                gpu_indexes=[0],
                context_length=8192,
            )
            if dep_id:
                pack_deps.append(dep_id)

        # Conflict probe: try to load another model onto ai1 g0 while occupied.
        if "worker-ai1" in self.hosts and pack_deps:
            conflict_id, conflict_payload, _ = self.load_deployment(
                model_id=chat_model["id"],
                worker_id="worker-ai1",
                mode="single_gpu",
                gpu_indexes=[0],
                context_length=2048,
            )
            if conflict_id is None:
                err = (
                    (conflict_payload.get("error") or {})
                    if isinstance(conflict_payload, dict)
                    else {}
                )
                self.write_run(
                    RunRecord(
                        stamp=self.now(),
                        phase="stress",
                        model_id=chat_model["id"],
                        profile="chat",
                        worker_id="worker-ai1",
                        mode="single_gpu",
                        gpu_indexes="0",
                        context_length=2048,
                        deployment_id="",
                        probe="occupancy_conflict",
                        outcome="pass",
                        notes="gateway refused overlapping load",
                        error_code=str(err.get("code") or ""),
                        error_detail=str(err.get("message") or "")[:300],
                    )
                )
            else:
                self.write_run(
                    RunRecord(
                        stamp=self.now(),
                        phase="stress",
                        model_id=chat_model["id"],
                        profile="chat",
                        worker_id="worker-ai1",
                        mode="single_gpu",
                        gpu_indexes="0",
                        context_length=2048,
                        deployment_id=conflict_id,
                        probe="occupancy_conflict",
                        outcome="failed",
                        error_detail="expected conflict rejection but load succeeded",
                    )
                )
                self.unload(conflict_id)

        for dep_id in pack_deps:
            self.unload(dep_id)

    def _chat_storm(self, *, concurrency: int, duration_s: int) -> None:
        alias = GAUNTLET_ALIASES["chat"]
        deadline = time.time() + duration_s
        latencies: list[float] = []
        successes = 0
        failures = 0

        def one() -> tuple[bool, float]:
            started = time.perf_counter()
            status, payload = self.gw.request(
                "POST",
                "/v1/chat/completions",
                {
                    "model": alias,
                    "messages": [
                        {"role": "user", "content": "Reply with exactly: storm-ok"}
                    ],
                    "max_tokens": 8,
                    "stream": False,
                    "temperature": 0,
                },
                timeout=60,
            )
            elapsed = (time.perf_counter() - started) * 1000
            ok = status < 400 and isinstance(payload, dict)
            if ok:
                content = (
                    ((payload.get("choices") or [{}])[0].get("message") or {}).get(
                        "content"
                    )
                    or ""
                )
                ok = "storm-ok" in content.lower() or bool(content.strip())
            return ok, elapsed

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            while time.time() < deadline:
                futs = [pool.submit(one) for _ in range(concurrency)]
                for fut in as_completed(futs):
                    ok, elapsed = fut.result()
                    latencies.append(elapsed)
                    if ok:
                        successes += 1
                    else:
                        failures += 1

        total = successes + failures
        p50 = statistics.median(latencies) if latencies else 0
        p95 = (
            statistics.quantiles(latencies, n=20)[18]
            if len(latencies) >= 20
            else (max(latencies) if latencies else 0)
        )
        self.write_run(
            RunRecord(
                stamp=self.now(),
                phase="stress",
                model_id="yonder-nemotron-3-nano-4b",
                profile="chat",
                worker_id="fleet",
                mode="single_gpu",
                gpu_indexes="",
                context_length=8192,
                deployment_id="",
                probe=f"chat_storm_{concurrency}",
                outcome="pass" if failures == 0 else "soft_fail",
                notes=(
                    f"total={total} ok={successes} fail={failures} "
                    f"p50_ms={p50:.1f} p95_ms={p95:.1f}"
                ),
                request_duration_ms=f"{p50:.1f}",
                extra={"p95_ms": p95, "successes": successes, "failures": failures},
            )
        )

    def _mixed_storm(self, *, duration_s: int) -> None:
        deadline = time.time() + duration_s
        counts = {"chat": 0, "embedding": 0, "rerank": 0, "fail": 0}

        def chat() -> str:
            status, _ = self.gw.request(
                "POST",
                "/v1/chat/completions",
                {
                    "model": GAUNTLET_ALIASES["chat"],
                    "messages": [{"role": "user", "content": "Say ok"}],
                    "max_tokens": 8,
                    "stream": False,
                },
                timeout=60,
            )
            return "chat" if status < 400 else "fail"

        def embed() -> str:
            status, _ = self.gw.request(
                "POST",
                "/v1/embeddings",
                {"model": GAUNTLET_ALIASES["embedding"], "input": "mixed storm"},
                timeout=60,
            )
            return "embedding" if status < 400 else "fail"

        def rerank() -> str:
            status, _ = self.gw.request(
                "POST",
                "/v1/rerank",
                {
                    "model": GAUNTLET_ALIASES["rerank"],
                    "query": "GPU placement",
                    "documents": ["GPU placement docs", "baking bread"],
                },
                timeout=60,
            )
            return "rerank" if status < 400 else "fail"

        with ThreadPoolExecutor(max_workers=9) as pool:
            while time.time() < deadline:
                futs = [
                    pool.submit(chat),
                    pool.submit(embed),
                    pool.submit(rerank),
                ]
                for fut in as_completed(futs):
                    counts[fut.result()] += 1

        self.write_run(
            RunRecord(
                stamp=self.now(),
                phase="stress",
                model_id="mixed",
                profile="mixed",
                worker_id="fleet",
                mode="mixed",
                gpu_indexes="",
                context_length=8192,
                deployment_id="",
                probe="mixed_storm",
                outcome="pass" if counts["fail"] == 0 else "soft_fail",
                notes=json.dumps(counts),
            )
        )

    def write_summary_and_readme(self, phases: list[str]) -> None:
        with self.summary_path.open("w", encoding="utf-8") as handle:
            handle.write(
                "model_id\tworker_id\tmode\tcontext\tstatus\t"
                "peak_vram_mib_csv\tpeak_vram_total_mib\n"
            )
            for row in self._summary_rows:
                handle.write(
                    "\t".join(
                        [
                            row["model_id"],
                            row["worker_id"],
                            row["mode"],
                            row["context"],
                            row["status"],
                            row["peak_vram_mib_csv"],
                            row["peak_vram_total_mib"],
                        ]
                    )
                    + "\n"
                )
            # Also derive last_ok from map
            for (model_id, worker_id, mode), ctx in sorted(self.last_ok_ctx.items()):
                handle.write(
                    f"{model_id}\t{worker_id}\t{mode}\t{ctx}\tlast_ok\t\t\n"
                )

        version = {}
        status, payload = self.gw.request("GET", "/version", timeout=10)
        if status < 400 and isinstance(payload, dict):
            version = payload

        git_sha = "not_reported"
        try:
            import subprocess

            git_sha = (
                subprocess.check_output(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=Path(__file__).resolve().parent.parent,
                    stderr=subprocess.DEVNULL,
                    text=True,
                ).strip()
                or "not_reported"
            )
        except Exception:  # noqa: BLE001
            git_sha = "not_reported"

        snippets = []
        for (model_id, worker_id, mode), ctx in sorted(self.last_ok_ctx.items()):
            snippets.append(
                f"# {model_id} {worker_id} {mode}:\n"
                f"#   max_context_length: {ctx}\n"
                f"#   evidence: measured\n"
            )

        readme = self.outdir / "README.md"
        readme.write_text(
            "\n".join(
                [
                    "# Yonder fleet gauntlet results",
                    "",
                    f"- stamp: {self.outdir.name}",
                    f"- git_sha: {git_sha}",
                    f"- gateway: {self.gw.base_url}",
                    f"- version: `{json.dumps(version)}`",
                    f"- hosts: {', '.join(self.hosts)}",
                    f"- phases: {', '.join(phases)}",
                    f"- dry_run: {self.dry_run}",
                    "",
                    "## Files",
                    "",
                    "- `runs.jsonl` — atomic step records",
                    "- `summary.tsv` — context ladder rollup",
                    "- `failures.tsv` — non-success outcomes",
                    "",
                    "## Suggested manifest snippets (operator review required)",
                    "",
                    "```yaml",
                    *snippets,
                    "```",
                    "",
                    "Do not invent peaks. Promote caps only after reviewing "
                    "peak_vram columns and failure rows.",
                    "",
                ]
            ),
            encoding="utf-8",
        )


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Yonder fleet model gauntlet")
    parser.add_argument(
        "--phase",
        default="all",
        choices=["context", "inference", "stress", "all"],
    )
    parser.add_argument(
        "--mode",
        default="",
        choices=["", "single_gpu", "two_gpu", "both"],
        help="Placement modes (wave preset may set a default)",
    )
    parser.add_argument(
        "--hosts",
        default="",
        help="Comma-separated worker ids (wave preset may set this)",
    )
    parser.add_argument(
        "--outdir",
        default="",
        help="Result directory (default /tmp/yonder-gauntlet-<stamp>)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--gateway-url",
        default=os.environ.get("GATEWAY_URL", "http://127.0.0.1:8080"),
    )
    parser.add_argument("--settle-seconds", type=float, default=8.0)
    parser.add_argument("--ready-timeout", type=float, default=900.0)
    parser.add_argument(
        "--models",
        default="",
        help="Optional comma-separated model ids to subset the matrix",
    )
    parser.add_argument(
        "--ladder",
        default="fast",
        choices=["fast", "full"],
        help="Context ladder density (fast=rough ceilings tonight)",
    )
    parser.add_argument(
        "--pins",
        default="first",
        choices=["first", "all"],
        help="GPU pin sets per host/mode (first=tonight)",
    )
    parser.add_argument(
        "--skip-stress",
        action="store_true",
        help="When --phase all, run context+inference only",
    )
    parser.add_argument(
        "--wave",
        default="",
        choices=["", *sorted(WAVES.keys())],
        help="Parallel campaign preset (ai1-big / ai3-chat / ai7-retrieval)",
    )
    parser.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        default=None,
        help="Resume from state.json / runs.jsonl (default if state exists)",
    )
    parser.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="Ignore prior state and redo steps",
    )
    return parser.parse_args(argv)


def apply_wave(args: argparse.Namespace) -> argparse.Namespace:
    if not args.wave:
        return args
    wave = WAVES[args.wave]
    if not args.hosts:
        args.hosts = ",".join(wave["hosts"])
    if not args.models:
        args.models = ",".join(wave["models"])
    if not args.mode:
        args.mode = str(wave.get("mode") or "both")
    return args


def main(argv: list[str] | None = None) -> int:
    args = apply_wave(parse_args(argv))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    outdir = Path(args.outdir) if args.outdir else Path(f"/tmp/yonder-gauntlet-{stamp}")
    outdir.mkdir(parents=True, exist_ok=True)

    if not args.mode:
        args.mode = "both"
    if not args.hosts:
        args.hosts = "worker-ai1,worker-ai3,worker-ai7"

    modes = (
        ["single_gpu", "two_gpu"]
        if args.mode == "both"
        else [args.mode]
    )
    hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]
    models = MODELS
    if args.models.strip():
        wanted = {m.strip() for m in args.models.split(",") if m.strip()}
        models = [m for m in MODELS if m["id"] in wanted]

    resume = args.resume
    if resume is None:
        resume = (outdir / "state.json").exists() or (outdir / "runs.jsonl").exists()

    runner = GauntletRunner(
        gateway=GatewayClient(args.gateway_url),
        outdir=outdir,
        hosts=hosts,
        modes=modes,
        dry_run=args.dry_run,
        settle_seconds=args.settle_seconds,
        ready_timeout=args.ready_timeout,
        models=models,
        ladder=args.ladder,
        pins=args.pins,
        resume=bool(resume),
    )
    if not args.dry_run:
        runner.install_signal_handlers()

    # Init result files
    if not runner.failures_path.exists():
        runner.failures_path.write_text(
            "stamp\tphase\tmodel_id\tworker_id\tmode\tprobe\toutcome\t"
            "error_code\terror_detail\n",
            encoding="utf-8",
        )

    runner.log(f"outdir={outdir}")
    runner.log(f"gateway={args.gateway_url}")
    runner.log(
        f"phase={args.phase} mode={args.mode} hosts={hosts} "
        f"ladder={args.ladder} pins={args.pins} wave={args.wave or '-'} "
        f"resume={resume} skip_stress={args.skip_stress}"
    )

    if args.dry_run:
        runner.print_dry_run()

    phases: list[str] = []
    if args.phase in {"context", "all"}:
        phases.append("context")
        runner.phase_context()
    if args.phase in {"inference", "all"}:
        phases.append("inference")
        runner.phase_inference()
    if args.phase == "stress" or (args.phase == "all" and not args.skip_stress):
        phases.append("stress")
        runner.phase_stress()

    runner.write_summary_and_readme(phases)
    runner.log(f"done — results in {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
