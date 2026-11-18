#!/usr/bin/env python3
"""Coding fixture battery — model proposes fixes; local pytest scores them.

For each task under config/lab/coder-evals/tasks/, paste the broken sources into
a Gateway chat completion, ask for corrected file(s), extract python from the
reply, overlay onto a temp copy of the fixture, and run the fixture's
check_command (usually pytest). No write_unlock / agent loop required.

Example:
  GATEWAY_URL=http://127.0.0.1:8080 python3 scripts/bench_model_coding_fixtures.py \\
    --model yonder-bonsai-27b-ternary --worker worker-ai1 \\
    --outdir /srv/yonder-storage/yonder/gauntlet/20260808-bonsai-coding
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = REPO_ROOT / "config" / "lab" / "coder-evals" / "tasks"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def api(gateway: str, method: str, path: str, body: dict | None = None, timeout: float = 600) -> Any:
    data = None if body is None else json.dumps(body).encode()
    url = gateway.rstrip("/") + path
    last_err: Exception | None = None
    for attempt in range(1, 9):
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"content-type": "application/json"} if data else {},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            last_err = exc
            # Worker/GPU still draining after orphan kill / gateway restart.
            if exc.code in {502, 503, 504} and attempt < 8:
                time.sleep(min(30, 2 * attempt))
                continue
            raise
        except (TimeoutError, urllib.error.URLError) as exc:
            last_err = exc
            if attempt < 8:
                time.sleep(min(30, 2 * attempt))
                continue
            raise
    raise RuntimeError(f"api failed after retries: {last_err}")


def load_task_meta(task_dir: Path) -> dict[str, str]:
    meta: dict[str, str] = {"id": task_dir.name, "check_command": "python -m pytest"}
    path = task_dir / "task.yaml"
    if not path.is_file():
        return meta
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"')
    return meta


def source_files(task_dir: Path) -> list[Path]:
    skip = {"task.yaml", "pyproject.toml"}
    files = []
    for path in sorted(task_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name in skip or path.name.startswith("test_"):
            continue
        if path.suffix in {".py", ".ts", ".js"}:
            files.append(path)
    return files


def build_prompt(meta: dict[str, str], sources: list[Path]) -> str:
    parts = [
        "You are fixing a small coding task. Return ONLY corrected source file(s).",
        "Format each file as:",
        "### FILE: <filename>",
        "```python",
        "<full file contents>",
        "```",
        "Do not wrap the whole answer in an extra fence. Include every source file listed.",
        f"Task id: {meta.get('id')}",
        f"Kind: {meta.get('kind', 'unknown')}",
        f"Instruction: {meta.get('task', '')}",
        "",
        "Broken sources:",
    ]
    for path in sources:
        parts.append(f"\n### FILE: {path.name}\n```python\n{path.read_text(encoding='utf-8')}```\n")
    return "\n".join(parts)


def extract_files(reply: str) -> dict[str, str]:
    out: dict[str, str] = {}
    # ### FILE: name\n```python\n...\n```
    pattern = re.compile(
        r"###\s*FILE:\s*([^\n`]+)\s*```(?:python)?\s*(.*?)```",
        re.S | re.I,
    )
    for match in pattern.finditer(reply):
        name = match.group(1).strip()
        code = match.group(2).strip() + "\n"
        out[Path(name).name] = code
    if out:
        return out
    # Fallback: single python fence → first source name unknown; caller handles
    blocks = re.findall(r"```(?:python)?\s*(.*?)```", reply, flags=re.S | re.I)
    if len(blocks) == 1:
        out["__single__"] = blocks[0].strip() + "\n"
    return out


def chat(gateway: str, model: str, prompt: str, max_tokens: int = 2400) -> dict:
    t0 = time.perf_counter()
    try:
        body = api(
            gateway,
            "POST",
            "/v1/chat/completions",
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.1,
                "stream": False,
            },
            timeout=600,
        )
        err = None
    except urllib.error.HTTPError as exc:
        body = {}
        err = exc.read().decode("utf-8", errors="replace")[:600]
    except Exception as exc:  # noqa: BLE001
        body = {}
        err = str(exc)
    ms = int((time.perf_counter() - t0) * 1000)
    content = (((body.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
    usage = body.get("usage") or {}
    return {
        "latency_ms": ms,
        "content": content,
        "error": err,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
    }


def lab_python() -> str:
    """Prefer a real interpreter over ai1's /usr/local/bin/python3 venv wrap."""
    for candidate in (
        os.environ.get("YONDER_EVAL_PYTHON"),
        "/opt/yonder/python/python/bin/python3",
        shutil.which("python3"),
        shutil.which("python"),
    ):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return "python3"


def run_builtin_pytest(workdir: Path, python_bin: str) -> tuple[bool, str]:
    """Minimal pytest-style runner (no pytest dependency on lab hosts)."""
    runner = r'''
import importlib.util, sys, traceback
from pathlib import Path
root = Path(".").resolve()
sys.path.insert(0, str(root))
failed = 0
ran = 0
for path in sorted(root.glob("test_*.py")):
    name = path.stem
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        failed += 1
        print(f"IMPORT FAIL {path.name}:")
        traceback.print_exc()
        continue
    for attr in sorted(dir(mod)):
        if not attr.startswith("test_"):
            continue
        fn = getattr(mod, attr)
        if not callable(fn):
            continue
        ran += 1
        try:
            fn()
            print(f"PASS {path.name}::{attr}")
        except Exception:
            failed += 1
            print(f"FAIL {path.name}::{attr}")
            traceback.print_exc()
print(f"summary ran={ran} failed={failed}")
raise SystemExit(1 if failed or ran == 0 else 0)
'''
    proc = subprocess.run(
        [python_bin, "-c", runner],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    out = ((proc.stdout or "") + (proc.stderr or ""))[-800:]
    return proc.returncode == 0, f"exit={proc.returncode} builtin_pytest {out}"


def run_builtin_ruff_check(workdir: Path, python_bin: str) -> tuple[bool, str]:
    """AST unused-import check approximating `ruff check` for fixture chores."""
    checker = r'''
import ast, sys
from pathlib import Path
issues = []
for path in sorted(Path(".").glob("*.py")):
    if path.name.startswith("test_"):
        continue
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    imported = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            used.add(node.value.id)
    for name in sorted(imported):
        if name not in used:
            issues.append(f"{path.name}: unused import {name}")
if issues:
    print("\n".join(issues))
    raise SystemExit(1)
print("ok")
'''
    proc = subprocess.run(
        [python_bin, "-c", checker],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    out = ((proc.stdout or "") + (proc.stderr or ""))[-800:]
    return proc.returncode == 0, f"exit={proc.returncode} builtin_ruff {out}"


def run_check(workdir: Path, check_command: str) -> tuple[bool, str]:
    cmd = (check_command or "python -m pytest").strip()
    python_bin = lab_python()
    lowered = cmd.lower()
    if "pytest" in lowered or cmd in {"python -m pytest", "python3 -m pytest"}:
        return run_builtin_pytest(workdir, python_bin)
    if lowered.startswith("ruff"):
        return run_builtin_ruff_check(workdir, python_bin)
    # Fallback: rewrite python → lab interpreter and hope deps exist.
    cmd = re.sub(r"(^|[;&|]\s*)python3?(\s|$)", rf"\1{python_bin}\2", cmd)
    env = dict(os.environ)
    home = env.get("HOME") or ""
    local_bin = f"{home}/.local/bin" if home else ""
    if local_bin and local_bin not in (env.get("PATH") or ""):
        env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"
    proc = subprocess.run(
        cmd,
        cwd=str(workdir),
        shell=True,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )
    out = ((proc.stdout or "") + (proc.stderr or ""))[-800:]
    return proc.returncode == 0, f"exit={proc.returncode} cmd={cmd!r} {out}"


def unload_ready(gateway: str, *, worker: str | None = None, exclude_model: str | None = None) -> None:
    deps = api(gateway, "GET", "/api/admin/deployments").get("deployments") or []
    for d in deps:
        did = d.get("deployment_id") or d.get("id")
        st = (d.get("status") or "").lower()
        mid = str(d.get("model_definition_id") or "")
        wid = str(d.get("worker_id") or "")
        if not did or st not in {"ready", "loading", "failed"}:
            continue
        if worker and wid and wid != worker:
            continue
        if exclude_model and exclude_model in mid:
            continue
        # Leave mock / placeholder alone if they aren't on a real worker GPU.
        if "mock" in mid or wid.startswith("placeholder"):
            continue
        try:
            api(gateway, "POST", f"/api/admin/deployments/{did}/unload", {})
            log(f"unload {did}")
        except Exception as exc:  # noqa: BLE001
            log(f"unload {did}: {exc}")
    time.sleep(6)


def ensure_loaded(gateway: str, model: str, worker: str, ctx: int, gpus: list[int]) -> str:
    deps = api(gateway, "GET", "/api/admin/deployments").get("deployments") or []
    for d in deps:
        mid = str(d.get("model_definition_id") or "")
        st = (d.get("status") or "").lower()
        did = d.get("deployment_id") or d.get("id")
        wid = str(d.get("worker_id") or "")
        if did and model in mid and st == "ready" and (not wid or wid == worker):
            return str(did)
    unload_ready(gateway, worker=worker)
    created = api(
        gateway,
        "POST",
        "/api/admin/deployments/load",
        {
            "model_definition_id": model,
            "worker_id": worker,
            "placement_mode": "single_gpu" if len(gpus) == 1 else "two_gpu",
            "gpu_indexes": gpus,
            "context_length": ctx,
        },
    )
    dep = (
        created.get("deployment_id")
        or created.get("id")
        or (created.get("deployment") or {}).get("id")
    )
    if not dep:
        time.sleep(2)
        deps = api(gateway, "GET", "/api/admin/deployments").get("deployments") or []
        for d in reversed(deps):
            if model in str(d.get("model_definition_id") or ""):
                dep = d.get("deployment_id") or d.get("id")
                break
    if not dep:
        raise RuntimeError(f"load failed: {created}")
    deadline = time.time() + 600
    while time.time() < deadline:
        deps = api(gateway, "GET", "/api/admin/deployments").get("deployments") or []
        for d in deps:
            if (d.get("deployment_id") or d.get("id")) == dep:
                st = (d.get("status") or "").lower()
                if st == "ready":
                    return str(dep)
                if st == "failed":
                    raise RuntimeError(str(d.get("message") or d.get("failure_reason") or d))
        time.sleep(3)
    raise TimeoutError(dep)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default="http://127.0.0.1:8080")
    parser.add_argument("--model", default="yonder-bonsai-27b-ternary")
    parser.add_argument("--worker", default="worker-ai1")
    parser.add_argument("--gpus", default="0")
    parser.add_argument("--context-length", type=int, default=32768)
    parser.add_argument("--tasks-dir", type=Path, default=TASKS_DIR)
    parser.add_argument("--outdir", default="/tmp/yonder-coding-fixtures")
    parser.add_argument("--keep-loaded", action="store_true")
    parser.add_argument("--task", default="", help="Optional single task id")
    args = parser.parse_args()

    gpus = [int(x) for x in args.gpus.split(",") if x.strip()]
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    log(f"LOAD {args.model}")
    dep = ensure_loaded(args.gateway, args.model, args.worker, args.context_length, gpus)
    api(
        args.gateway,
        "PATCH",
        "/api/admin/routes/yonder-chat",
        {"deployment_id": dep, "enabled": True},
    )
    # Always chat via the bound yonder-chat alias. Some manifests (e.g. coder-next)
    # are not registered as their own OpenAI model aliases.
    chat_model = "yonder-chat"
    log(f"READY {dep} via {chat_model}")

    task_dirs = sorted(
        p for p in args.tasks_dir.iterdir() if p.is_dir() and (p / "task.yaml").is_file()
    )
    if args.task:
        task_dirs = [p for p in task_dirs if p.name == args.task]

    results: list[dict] = []
    for task_dir in task_dirs:
        meta = load_task_meta(task_dir)
        sources = source_files(task_dir)
        if not sources:
            results.append(
                {
                    "task_id": meta["id"],
                    "passed": False,
                    "reason": "no_source_files",
                }
            )
            continue
        prompt = build_prompt(meta, sources)
        log(f"TASK {meta['id']} ({meta.get('kind')}) sources={[p.name for p in sources]}")
        reply = chat(args.gateway, chat_model, prompt)
        if reply.get("error"):
            results.append(
                {
                    "task_id": meta["id"],
                    "kind": meta.get("kind"),
                    "passed": False,
                    "reason": f"http_error: {reply['error'][:200]}",
                    "latency_ms": reply["latency_ms"],
                }
            )
            log(f"  FAIL {meta['id']} http")
            continue
        files = extract_files(reply["content"])
        if "__single__" in files and len(sources) == 1:
            files = {sources[0].name: files.pop("__single__")}
        missing = [p.name for p in sources if p.name not in files]
        if missing:
            results.append(
                {
                    "task_id": meta["id"],
                    "kind": meta.get("kind"),
                    "passed": False,
                    "reason": f"missing_files:{missing}",
                    "latency_ms": reply["latency_ms"],
                    "content_preview": reply["content"][:400],
                }
            )
            log(f"  FAIL {meta['id']} missing {missing}")
            continue

        with tempfile.TemporaryDirectory(prefix=f"yonder-code-{meta['id']}-") as tmp:
            work = Path(tmp)
            for item in task_dir.iterdir():
                if item.is_file():
                    shutil.copy2(item, work / item.name)
            for name, code in files.items():
                (work / name).write_text(code, encoding="utf-8")
            ok, detail = run_check(work, meta.get("check_command") or "python -m pytest")
        results.append(
            {
                "task_id": meta["id"],
                "kind": meta.get("kind"),
                "passed": ok,
                "reason": detail[:400],
                "latency_ms": reply["latency_ms"],
                "prompt_tokens": reply.get("prompt_tokens"),
                "completion_tokens": reply.get("completion_tokens"),
                "files_written": sorted(files.keys()),
            }
        )
        log(f"  {'PASS' if ok else 'FAIL'} {meta['id']} — {detail[:120]}")

    passed = sum(1 for r in results if r.get("passed"))
    doc = {
        "stamp": stamp,
        "model": args.model,
        "worker": args.worker,
        "deployment_id": dep,
        "passed": passed,
        "total": len(results),
        "tasks": results,
        "method": (
            "Chat completion proposes full corrected sources; "
            "overlay onto fixture copy; run task check_command locally."
        ),
    }
    latest = outdir / "coding-fixtures-latest.json"
    stamped = outdir / f"coding-fixtures-{stamp}.json"
    latest.write_text(json.dumps(doc, indent=2) + "\n")
    stamped.write_text(json.dumps(doc, indent=2) + "\n")
    log(f"wrote {latest} ({passed}/{len(results)} pass)")

    if not args.keep_loaded:
        try:
            api(args.gateway, "POST", f"/api/admin/deployments/{dep}/unload", {})
        except Exception as exc:  # noqa: BLE001
            log(f"unload: {exc}")
    return 0 if passed == len(results) and results else 1


if __name__ == "__main__":
    raise SystemExit(main())
