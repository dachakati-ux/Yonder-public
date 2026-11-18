#!/usr/bin/env python3
"""Yonder Coder eval harness — drives real runs through the gateway API.

For each task fixture under config/lab/coder-evals/tasks/, this:
  1. clones the fixture into a coder workspace (POST /v1/coder/workspaces/clone),
  2. creates a run with the task's check_command,
  3. advances it to a resting state via the executor (wait=true),
  4. records outcome + metrics.

Nothing is mocked: the run only completes if the configured local model
actually produced a passing change and both verdicts approved. Requires the
gateway running with coder.write_unlock=true and a READY coder alias.

Usage:
  python scripts/coder-eval.py --gateway http://127.0.0.1:8080 \
      [--task bug-subtract-add] [--timeout 900] [--json report.json]

Stdlib only — safe to run on an air-gapped lab host.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = REPO_ROOT / "config" / "lab" / "coder-evals" / "tasks"


def _post(url: str, payload: dict, headers: dict[str, str]) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("content-type", "application/json")
    for key, value in headers.items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (lab-local)
        return json.loads(resp.read().decode("utf-8"))


def _get(url: str, headers: dict[str, str]) -> dict:
    req = urllib.request.Request(url, method="GET")
    for key, value in headers.items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _load_task(task_dir: Path) -> dict:
    """Minimal task.yaml reader (id/kind/task/check_command) — no yaml dep."""
    meta: dict[str, str] = {}
    for line in (task_dir / "task.yaml").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"')
    meta.setdefault("id", task_dir.name)
    meta.setdefault("check_command", "python -m pytest")
    return meta


def run_task(gateway: str, headers: dict[str, str], task_dir: Path, timeout: int) -> dict:
    meta = _load_task(task_dir)
    started = time.time()
    result: dict = {"id": meta["id"], "kind": meta.get("kind", "unknown")}
    try:
        clone = _post(
            f"{gateway}/v1/coder/workspaces/clone",
            {"source": str(task_dir), "label": meta["id"]},
            headers,
        )
        workspace_id = clone["workspace"]["id"]
        run = _post(
            f"{gateway}/v1/coder/runs",
            {
                "workspace_id": workspace_id,
                "task": meta["task"],
                "check_command": meta["check_command"],
            },
            headers,
        )
        run_id = run["run"]["id"]
        # Kick off the background executor; then poll to a resting state.
        _post(f"{gateway}/v1/coder/runs/{run_id}/advance", {"wait": False}, headers)
        status = "active"
        detail: dict = {}
        while time.time() - started < timeout:
            detail = _get(f"{gateway}/v1/coder/runs/{run_id}", headers)["run"]
            status = detail["status"]
            if status in {"completed", "failed", "cancelled", "blocked"}:
                break
            time.sleep(3)
        metrics = detail.get("metrics", {})
        result.update(
            {
                "run_id": run_id,
                "status": status,
                "stage": detail.get("stage"),
                "passed": status == "completed",
                "error": detail.get("error"),
                "model_calls": metrics.get("model_calls"),
                "fix_iterations": metrics.get("fix_iterations"),
                "wall_clock_seconds": round(time.time() - started, 1),
                "dual_approved": detail.get("dual_approved"),
            }
        )
    except urllib.error.HTTPError as exc:
        result.update(
            {
                "status": "http_error",
                "passed": False,
                "error": f"{exc.code}: {exc.read()[:300]!r}",
            }
        )
    except Exception as exc:  # noqa: BLE001
        result.update({"status": "harness_error", "passed": False, "error": str(exc)})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Yonder Coder eval harness")
    parser.add_argument("--gateway", default="http://127.0.0.1:8080")
    parser.add_argument("--task", help="Run a single task id (default: all)")
    parser.add_argument("--timeout", type=int, default=900, help="Per-task seconds")
    parser.add_argument("--subject-header", default="X-Yonder-Trusted-Subject")
    parser.add_argument("--subject", default="", help="Trusted subject (trusted_ingress)")
    parser.add_argument("--capabilities-header", default="X-Yonder-Trusted-Capabilities")
    parser.add_argument("--json", help="Write full report JSON to this path")
    args = parser.parse_args()

    headers: dict[str, str] = {}
    if args.subject:
        headers[args.subject_header] = args.subject
        headers[args.capabilities_header] = "coder.use"

    task_dirs = sorted(d for d in TASKS_DIR.iterdir() if (d / "task.yaml").exists())
    if args.task:
        task_dirs = [d for d in task_dirs if d.name == args.task]
        if not task_dirs:
            print(f"No such task: {args.task}", file=sys.stderr)
            return 2

    results = []
    for task_dir in task_dirs:
        print(f"→ {task_dir.name} …", flush=True)
        res = run_task(args.gateway, headers, task_dir, args.timeout)
        mark = "PASS" if res.get("passed") else "FAIL"
        print(
            f"  {mark}  status={res.get('status')} "
            f"calls={res.get('model_calls')} "
            f"fixes={res.get('fix_iterations')} "
            f"{res.get('wall_clock_seconds')}s "
            f"{('· ' + str(res.get('error'))) if res.get('error') else ''}",
            flush=True,
        )
        results.append(res)

    passed = sum(1 for r in results if r.get("passed"))
    total = len(results)
    print(f"\n=== {passed}/{total} passed ===")
    by_kind: dict[str, list[bool]] = {}
    for r in results:
        by_kind.setdefault(r.get("kind", "unknown"), []).append(bool(r.get("passed")))
    for kind, outcomes in sorted(by_kind.items()):
        print(f"  {kind}: {sum(outcomes)}/{len(outcomes)}")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"passed": passed, "total": total, "results": results}, indent=2),
            encoding="utf-8",
        )
        print(f"Report written to {args.json}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
