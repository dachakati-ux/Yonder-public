#!/usr/bin/env python3
"""Run coding fixtures across the lab chat fleet (Gateway-only).

Skips models that cannot place on the chosen worker. Writes per-model JSON under
outdir/<model_id>/ plus a fleet summary JSON/TSV.

Example (ai1):
  /opt/yonder/python/python/bin/python3 scripts/bench_fleet_coding_fixtures.py \\
    --worker worker-ai1 \\
    --outdir /srv/yonder-storage/yonder/gauntlet/20260809-fleet-coding
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

MODELS: list[dict] = [
    {"id": "yonder-nemotron-3-nano-4b", "display": "Nemotron-3-Nano-4B", "gpus": "0"},
    {"id": "yonder-qwen35-9b", "display": "Qwen3.5-9B", "gpus": "0"},
    {"id": "yonder-gemma4-12b-it-qat-gguf", "display": "Gemma-4-12B", "gpus": "0"},
    {"id": "yonder-qwen36-35b-a3b-q4", "display": "Qwen3.6-35B", "gpus": "0"},
    {"id": "yonder-qwen36-27b-q4", "display": "Qwen3.6-27B", "gpus": "0"},
    {"id": "yonder-gemma4-26b-a4b-it-qat-gguf", "display": "Gemma-4-26B", "gpus": "0"},
    {"id": "yonder-gemma4-31b-it-qat-gguf", "display": "Gemma-4-31B", "gpus": "0"},
    # 1× blocked on V100; needs two GPUs.
    {"id": "yonder-qwen3-coder-next-q4", "display": "Qwen3-Coder-Next", "gpus": "0,1"},
    {"id": "yonder-bonsai-27b-ternary", "display": "Bonsai 27B Ternary", "gpus": "0"},
]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def free_worker_gpus(gateway: str = "http://127.0.0.1:8080", worker: str = "worker-ai1") -> None:
    """Unload via Gateway, wait for VRAM; only then kill orphan llama-server processes."""
    import urllib.error
    import urllib.request

    def api_call(method: str, path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            gateway.rstrip("/") + path,
            data=data,
            method=method,
            headers={"content-type": "application/json"} if data else {},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            _ = exc.read()
            return {}

    try:
        deps = api_call("GET", "/api/admin/deployments").get("deployments") or []
    except Exception:  # noqa: BLE001
        deps = []
    for d in deps:
        did = d.get("deployment_id") or d.get("id")
        st = (d.get("status") or "").lower()
        mid = str(d.get("model_definition_id") or "")
        wid = str(d.get("worker_id") or "")
        if not did or st not in {"ready", "loading", "failed"}:
            continue
        if worker and wid and wid != worker:
            continue
        if "mock" in mid or wid.startswith("placeholder"):
            continue
        api_call("POST", f"/api/admin/deployments/{did}/unload", {})
        log(f"unload {did}")
    time.sleep(8)

    def gpu_used_mib() -> list[int]:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        out = []
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(int(float(line)))
            except ValueError:
                pass
        return out

    used = gpu_used_mib()
    if used and max(used) > 1024:
        log(f"VRAM still busy {used}; killing orphan llama-server")
        subprocess.run(
            [
                "bash",
                "-lc",
                "sudo pkill -9 -f '/opt/yonder/runtimes/.*/bin/llama-server' || true",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        time.sleep(8)
        # Clear stuck unloading rows so the next load is not blocked.
        subprocess.run(
            [
                "bash",
                "-lc",
                "sudo /opt/yonder/python/python/bin/python3 - <<'PY'\n"
                "import sqlite3\n"
                "con=sqlite3.connect('/var/lib/yonder/yonder.db')\n"
                "cur=con.cursor()\n"
                "n=cur.execute(\"update deployments set status='stopped', "
                "load_progress_percent=NULL, last_error=NULL where status='unloading'\").rowcount\n"
                "con.commit(); print('patched', n); con.close()\n"
                "PY",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        time.sleep(3)
    used2 = gpu_used_mib()
    log(f"GPU MiB after free: {used2}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default="http://127.0.0.1:8080")
    parser.add_argument("--worker", default="worker-ai1")
    parser.add_argument("--context-length", type=int, default=32768)
    parser.add_argument(
        "--script",
        default=str(Path(__file__).resolve().with_name("bench_model_coding_fixtures.py")),
    )
    parser.add_argument("--tasks-dir", default="")
    parser.add_argument("--outdir", required=True)
    parser.add_argument(
        "--models",
        default="",
        help="Optional comma list of model ids (default: full fleet chat set)",
    )
    parser.add_argument("--python", default="/opt/yonder/python/python/bin/python3")
    parser.add_argument("--skip-bonsai", action="store_true")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    selected = {x.strip() for x in args.models.split(",") if x.strip()}
    models = [m for m in MODELS if not selected or m["id"] in selected]
    if args.skip_bonsai:
        models = [m for m in models if m["id"] != "yonder-bonsai-27b-ternary"]

    summary: list[dict] = []
    for model in models:
        free_worker_gpus(args.gateway, args.worker)
        model_out = outdir / model["id"]
        model_out.mkdir(parents=True, exist_ok=True)
        log(f"=== {model['display']} ({model['id']}) gpus={model['gpus']} ===")
        cmd = [
            args.python,
            args.script,
            "--gateway",
            args.gateway,
            "--model",
            model["id"],
            "--worker",
            args.worker,
            "--gpus",
            model["gpus"],
            "--context-length",
            str(args.context_length),
            "--outdir",
            str(model_out),
        ]
        if args.tasks_dir:
            cmd.extend(["--tasks-dir", args.tasks_dir])
        proc = subprocess.run(cmd, check=False)
        free_worker_gpus(args.gateway, args.worker)
        latest = model_out / "coding-fixtures-latest.json"
        row: dict = {
            "model_id": model["id"],
            "display_name": model["display"],
            "gpus": model["gpus"],
            "script_rc": proc.returncode,
        }
        if latest.is_file():
            doc = json.loads(latest.read_text())
            row["passed"] = doc.get("passed")
            row["total"] = doc.get("total")
            row["tasks"] = [
                {"task_id": t.get("task_id"), "passed": t.get("passed"), "reason": t.get("reason")}
                for t in (doc.get("tasks") or [])
            ]
        else:
            row["passed"] = 0
            row["total"] = 0
            row["tasks"] = []
            row["error"] = f"missing latest json; rc={proc.returncode}"
        summary.append(row)
        log(
            f"=== done {model['id']} {row.get('passed')}/{row.get('total')} "
            f"rc={proc.returncode} ==="
        )

    fleet = {
        "stamp": stamp,
        "worker": args.worker,
        "context_length": args.context_length,
        "models": summary,
        "method": (
            "Per-model Gateway chat proposes corrected sources; "
            "overlay onto coder-eval fixtures; builtin pytest/ruff score locally."
        ),
    }
    latest = outdir / "fleet-coding-latest.json"
    stamped = outdir / f"fleet-coding-{stamp}.json"
    latest.write_text(json.dumps(fleet, indent=2) + "\n")
    stamped.write_text(json.dumps(fleet, indent=2) + "\n")
    tsv = outdir / f"fleet-coding-{stamp}.tsv"
    with tsv.open("w", encoding="utf-8") as fh:
        fh.write("model_id\tdisplay_name\tpassed\ttotal\tscript_rc\n")
        for row in summary:
            fh.write(
                f"{row['model_id']}\t{row['display_name']}\t{row.get('passed')}\t"
                f"{row.get('total')}\t{row.get('script_rc')}\n"
            )
    log(f"wrote {latest}")
    for row in summary:
        log(f"  {row['display_name']}: {row.get('passed')}/{row.get('total')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
