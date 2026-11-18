#!/usr/bin/env python3
"""Gateway-mediated ability battery for a chat model (chat / coding / reasoning).

Loads the model once, runs scored prompts through /v1/chat/completions, and
writes JSON evidence. Does not invent scores — each task records the raw reply
plus a deterministic pass/fail heuristic.

Example (on ai1):
  GATEWAY_URL=http://127.0.0.1:8080 python3 scripts/bench_model_ability.py \\
    --model yonder-bonsai-27b-ternary --worker worker-ai1 \\
    --outdir /srv/yonder-storage/yonder/gauntlet/20260808-bonsai-ability
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

NOT_REPORTED = "not_reported"


def api(gateway: str, method: str, path: str, body: dict | None = None, timeout: float = 600) -> Any:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        gateway.rstrip("/") + path,
        data=data,
        method=method,
        headers={"content-type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def unload_all(gateway: str) -> None:
    try:
        deps = api(gateway, "GET", "/api/admin/deployments").get("deployments") or []
    except Exception as exc:
        log(f"list deployments failed: {exc}")
        return
    for d in deps:
        did = d.get("deployment_id") or d.get("id")
        st = (d.get("status") or "").lower()
        if did and st in {"ready", "loading", "failed"}:
            try:
                api(gateway, "POST", f"/api/admin/deployments/{did}/unload", {})
            except Exception as exc:
                log(f"unload {did}: {exc}")
    time.sleep(6)


def wait_ready(gateway: str, deployment_id: str, timeout: float = 600) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        deps = api(gateway, "GET", "/api/admin/deployments").get("deployments") or []
        for d in deps:
            did = d.get("deployment_id") or d.get("id")
            if did == deployment_id:
                last = d
                st = (d.get("status") or "").lower()
                if st == "ready":
                    return d
                if st == "failed":
                    raise RuntimeError(f"load failed: {d.get('message') or d}")
        time.sleep(3)
    raise TimeoutError(f"not ready: {last}")


def chat(
    gateway: str,
    model: str,
    messages: list[dict],
    *,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    timeout: float = 600,
) -> dict:
    started = time.perf_counter()
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    try:
        resp = api(gateway, "POST", "/v1/chat/completions", body, timeout=timeout)
        err = None
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")[:800]
        resp = {}
    except Exception as exc:
        err = str(exc)
        resp = {}
    latency_ms = int((time.perf_counter() - started) * 1000)
    choice = ((resp.get("choices") or [{}])[0]) if resp else {}
    message = choice.get("message") or {}
    content = message.get("content") or ""
    usage = resp.get("usage") or {}
    return {
        "latency_ms": latency_ms,
        "content": content,
        "finish_reason": choice.get("finish_reason") or NOT_REPORTED,
        "prompt_tokens": usage.get("prompt_tokens", NOT_REPORTED),
        "completion_tokens": usage.get("completion_tokens", NOT_REPORTED),
        "error": err,
        "raw_keys": sorted(resp.keys()) if resp else [],
    }


ScoreFn = Callable[[str], tuple[bool, str]]


def score_exact_marker(marker: str) -> ScoreFn:
    def _score(text: str) -> tuple[bool, str]:
        ok = marker.lower() in text.lower()
        return ok, f"expects substring {marker!r}"

    return _score


def score_regex(pattern: str, flags: int = re.I | re.S) -> ScoreFn:
    rx = re.compile(pattern, flags)

    def _score(text: str) -> tuple[bool, str]:
        ok = bool(rx.search(text))
        return ok, f"expects regex {pattern!r}"

    return _score


def score_code_runs(snippet_rx: str, run_check: Callable[[str], tuple[bool, str]]) -> ScoreFn:
    rx = re.compile(snippet_rx, re.S)

    def _score(text: str) -> tuple[bool, str]:
        blocks = re.findall(r"```(?:python)?\s*(.*?)```", text, flags=re.S | re.I)
        candidate = blocks[-1].strip() if blocks else text.strip()
        if not rx.search(candidate):
            return False, "code fence / pattern missing"
        return run_check(candidate)

    return _score


def _exec_python(code: str, expect: str) -> tuple[bool, str]:
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(code)
        path = fh.name
    try:
        proc = subprocess.run(
            ["python3", path],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception as exc:
        return False, f"exec failed: {exc}"
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return False, f"exit={proc.returncode} out={out[:300]}"
    if expect not in out:
        return False, f"missing expect {expect!r} in {out[:300]!r}"
    return True, "executed ok"


TASKS: list[dict[str, Any]] = [
    {
        "id": "chat-identity",
        "family": "chat",
        "prompt": "Reply with exactly this token and nothing else: BONSAI-CHAT-OK",
        "max_tokens": 64,
        "score": score_exact_marker("BONSAI-CHAT-OK"),
    },
    {
        "id": "chat-instruction-follow",
        "family": "chat",
        "prompt": (
            "List exactly three US state capitals as a JSON array of strings. "
            "No markdown, no commentary — only the JSON array."
        ),
        "max_tokens": 128,
        "score": score_regex(r'^\s*\[\s*"[^"]+"\s*,\s*"[^"]+"\s*,\s*"[^"]+"\s*\]\s*$'),
    },
    {
        "id": "chat-summarize",
        "family": "chat",
        "prompt": (
            "Summarize in ONE sentence under 25 words: "
            "Yonder is an internal AI platform that routes chat through a Gateway "
            "to llama.cpp workers on V100 GPUs across ai1, ai3, and ai7."
        ),
        "max_tokens": 128,
        "score": score_regex(r"(?i)(yonder|gateway|v100|worker)"),
    },
    {
        "id": "reason-math",
        "family": "reasoning",
        "prompt": (
            "What is 17 * 19? Show brief working, then end with a line exactly: "
            "ANSWER: <number>"
        ),
        "max_tokens": 256,
        "score": score_regex(r"(?m)^ANSWER:\s*323\s*$"),
    },
    {
        "id": "reason-logic",
        "family": "reasoning",
        "prompt": (
            "All flurbs are greep. No greep is blue. Is it possible a flurb is blue? "
            "Answer YES or NO on the last line as: ANSWER: YES|NO"
        ),
        "max_tokens": 256,
        "score": score_regex(r"(?m)^ANSWER:\s*NO\s*$"),
    },
    {
        "id": "code-fizzbuzz",
        "family": "coding",
        "prompt": (
            "Write a Python function fizzbuzz(n: int) -> list[str] that returns "
            "values for 1..n using classic FizzBuzz rules. Then print "
            "fizzbuzz(15). Output ONLY a python code block."
        ),
        "max_tokens": 800,
        "score": score_code_runs(
            r"def\s+fizzbuzz",
            lambda code: _exec_python(
                code
                + "\n"
                + "print(','.join(fizzbuzz(15)))\n",
                "1,2,Fizz,4,Buzz,Fizz,7,8,Fizz,Buzz,11,Fizz,13,14,FizzBuzz",
            ),
        ),
    },
    {
        "id": "code-bugfix",
        "family": "coding",
        "prompt": (
            "This Python function is wrong:\n\n"
            "def add(a, b):\n    return a - b\n\n"
            "Rewrite the corrected function only (python code block). "
            "Then print(add(2, 3))."
        ),
        "max_tokens": 400,
        "score": score_code_runs(
            r"def\s+add",
            lambda code: _exec_python(code + "\nprint(add(2, 3))\n", "5"),
        ),
    },
    {
        "id": "code-algorithm",
        "family": "coding",
        "prompt": (
            "Write Python function binary_search(arr: list[int], target: int) -> int "
            "returning the index or -1. Assume arr is sorted ascending. "
            "Include a small self-check that prints FOUND when "
            "binary_search([1,3,5,7,9], 7) == 3. Output only a python code block."
        ),
        "max_tokens": 700,
        "score": score_code_runs(
            r"def\s+binary_search",
            lambda code: _exec_python(
                code
                if "FOUND" in code
                else code
                + "\n"
                + "assert binary_search([1,3,5,7,9], 7) == 3\nprint('FOUND')\n",
                "FOUND",
            ),
        ),
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gateway",
        default=__import__("os").environ.get("GATEWAY_URL", "http://127.0.0.1:8080"),
    )
    parser.add_argument("--model", default="yonder-bonsai-27b-ternary")
    parser.add_argument("--worker", default="worker-ai1")
    parser.add_argument("--gpus", default="0")
    parser.add_argument("--mode", default="single_gpu", choices=("single_gpu", "two_gpu"))
    parser.add_argument("--context-length", type=int, default=32768)
    parser.add_argument("--outdir", default="/tmp/yonder-ability")
    parser.add_argument("--skip-load", action="store_true")
    parser.add_argument("--keep-loaded", action="store_true")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    gpus = [int(x) for x in args.gpus.split(",") if x.strip() != ""]

    deployment_id = None
    if not args.skip_load:
        unload_all(args.gateway)
        log(f"LOAD {args.model} {args.mode} gpus={gpus} ctx={args.context_length}")
        load_body = {
            "model_definition_id": args.model,
            "worker_id": args.worker,
            "placement_mode": args.mode,
            "gpu_indexes": gpus,
            "context_length": args.context_length,
        }
        # Some gateways use nested placement — try flat first.
        try:
            created = api(args.gateway, "POST", "/api/admin/deployments/load", load_body)
        except urllib.error.HTTPError:
            created = api(
                args.gateway,
                "POST",
                "/api/admin/deployments",
                {
                    "model_definition_id": args.model,
                    "placement": {
                        "worker_id": args.worker,
                        "mode": args.mode,
                        "gpu_indexes": gpus,
                    },
                    "context_length": args.context_length,
                },
            )
        deployment_id = (
            created.get("deployment_id")
            or created.get("id")
            or (created.get("deployment") or {}).get("id")
        )
        if not deployment_id:
            # Poll newest matching deployment
            time.sleep(2)
            deps = api(args.gateway, "GET", "/api/admin/deployments").get("deployments") or []
            for d in reversed(deps):
                mid = d.get("model_definition_id") or ""
                if args.model in mid or mid == args.model:
                    deployment_id = d.get("deployment_id") or d.get("id")
                    break
        if not deployment_id:
            raise SystemExit(f"load response missing id: {created}")
        wait_ready(args.gateway, str(deployment_id))
        log(f"READY {deployment_id}")

    # Prefer stable alias if present in /v1/models, else model id.
    models = api(args.gateway, "GET", "/v1/models")
    ids = {m.get("id") for m in models.get("data") or []}
    chat_model = args.model if args.model in ids else args.model

    results: list[dict] = []
    for task in TASKS:
        log(f"TASK {task['id']} ({task['family']})")
        reply = chat(
            args.gateway,
            chat_model,
            [{"role": "user", "content": task["prompt"]}],
            max_tokens=int(task.get("max_tokens") or 512),
            temperature=0.2,
        )
        passed = False
        reason = "no_score"
        if reply.get("error"):
            reason = f"http_error: {reply['error'][:200]}"
        else:
            score_fn: ScoreFn = task["score"]
            passed, reason = score_fn(reply.get("content") or "")
        row = {
            "task_id": task["id"],
            "family": task["family"],
            "passed": passed,
            "reason": reason,
            "latency_ms": reply.get("latency_ms"),
            "prompt_tokens": reply.get("prompt_tokens"),
            "completion_tokens": reply.get("completion_tokens"),
            "finish_reason": reply.get("finish_reason"),
            "content_preview": (reply.get("content") or "")[:600],
            "error": reply.get("error"),
        }
        results.append(row)
        log(f"  {'PASS' if passed else 'FAIL'} {task['id']} — {reason}")

    by_family: dict[str, dict[str, int]] = {}
    for row in results:
        fam = row["family"]
        bucket = by_family.setdefault(fam, {"pass": 0, "fail": 0})
        bucket["pass" if row["passed"] else "fail"] += 1

    doc = {
        "stamp": stamp,
        "gateway": args.gateway,
        "model": args.model,
        "worker": args.worker,
        "mode": args.mode,
        "gpus": gpus,
        "context_length": args.context_length,
        "deployment_id": deployment_id,
        "summary": by_family,
        "passed": sum(1 for r in results if r["passed"]),
        "total": len(results),
        "tasks": results,
    }
    latest = outdir / "ability-latest.json"
    stamped = outdir / f"ability-{stamp}.json"
    latest.write_text(json.dumps(doc, indent=2) + "\n")
    stamped.write_text(json.dumps(doc, indent=2) + "\n")
    log(f"wrote {latest} ({doc['passed']}/{doc['total']} pass)")

    if deployment_id and not args.keep_loaded:
        try:
            api(args.gateway, "POST", f"/api/admin/deployments/{deployment_id}/unload", {})
        except Exception as exc:
            log(f"unload: {exc}")

    return 0 if doc["passed"] == doc["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
