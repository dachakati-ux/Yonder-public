#!/usr/bin/env python3
"""Gateway-mediated decode throughput bench at fixed n_ctx presets.

Loads each chat model at 32768 / 65536 / 131072 / 262144 when the mode
placement max allows (single_gpu preferred, else two_gpu). Streams a short
generation after a moderate prefill so we can record TTFT + completion tokens
honestly — never invents usage.

Outputs JSON + TSV under --outdir (default under /tmp).

Example (on ai1):
  GATEWAY_URL=http://127.0.0.1:8080 python3 scripts/bench_model_throughput.py \\
    --outdir /srv/yonder-storage/yonder/gauntlet/20260808-throughput
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

NOT_REPORTED = "not_reported"
CONTEXTS = (32768, 65536, 131072, 262144)

# Prefer single_gpu when the mode max covers the target ctx.
MODELS: list[dict] = [
    {
        "id": "yonder-nemotron-3-nano-4b",
        "display": "Nemotron-3-Nano-4B",
        "single_max": 262144,
        "two_max": 262144,
    },
    {
        "id": "yonder-qwen35-9b",
        "display": "Qwen3.5-9B",
        "single_max": 262144,
        "two_max": 262144,
    },
    {
        "id": "yonder-gemma4-12b-it-qat-gguf",
        "display": "Gemma-4-12B",
        "single_max": 262144,
        "two_max": 262144,
    },
    {
        "id": "yonder-qwen36-35b-a3b-q4",
        "display": "Qwen3.6-35B",
        "single_max": 262144,
        "two_max": 262144,
    },
    {
        "id": "yonder-qwen36-27b-q4",
        "display": "Qwen3.6-27B",
        "single_max": 163840,
        "two_max": 262144,
    },
    {
        "id": "yonder-gemma4-26b-a4b-it-qat-gguf",
        "display": "Gemma-4-26B",
        "single_max": 262144,
        "two_max": 262144,
    },
    {
        "id": "yonder-gemma4-31b-it-qat-gguf",
        "display": "Gemma-4-31B",
        "single_max": 98304,
        "two_max": 262144,
    },
    {
        "id": "yonder-qwen3-coder-next-q4",
        "display": "Qwen3-Coder-Next",
        "single_max": 0,
        "two_max": 262144,
    },
    {
        "id": "yonder-bonsai-27b-ternary",
        "display": "Bonsai 27B Ternary",
        "single_max": 262144,
        "two_max": 262144,
    },
]

WORKERS = ("worker-ai1", "worker-ai3", "worker-ai7")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S', time.gmtime())}] {msg}", flush=True)


def api(
    gateway: str,
    method: str,
    path: str,
    body: dict | None = None,
    timeout: int = 1200,
) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        gateway + path,
        data=data,
        method=method,
        headers={"content-type": "application/json"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
        except Exception:
            parsed = {"raw": detail[:2000]}
        return {"_http_error": exc.code, **parsed}


def wait_ready(gateway: str, dep_id: str, timeout: int = 1200) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = api(gateway, "GET", f"/api/admin/deployments/{dep_id}")
        st = (body.get("deployment") or body).get("status", "")
        if st == "ready":
            return st
        if st in {"failed", "unavailable", "cancelled", "stopped"}:
            return st
        time.sleep(4)
    return "timeout"


def hardware_used(gateway: str, worker_id: str, gpus: list[int]) -> list[int | None]:
    """Return used MiB per requested GPU index (Gateway hardware inventory)."""
    body = api(gateway, "GET", "/api/admin/hardware")
    indexes = set(gpus)
    for n in body.get("nodes") or []:
        if n.get("worker_id") != worker_id:
            continue
        hw = n.get("hardware") or {}
        by_idx: dict[int, int | None] = {}
        for gpu in hw.get("gpus") or []:
            idx_metric = gpu.get("index") or {}
            idx = idx_metric.get("value") if isinstance(idx_metric, dict) else idx_metric
            if idx is None:
                continue
            idx = int(idx)
            if idx not in indexes:
                continue
            used = gpu.get("memory_used_bytes") or {}
            avail = used.get("availability") if isinstance(used, dict) else "available"
            raw = used.get("value") if isinstance(used, dict) else used
            if avail != "available" or raw is None:
                by_idx[idx] = None
            else:
                by_idx[idx] = int(raw) // (1024 * 1024)
        return [by_idx.get(i) for i in gpus]
    return [None for _ in gpus]


def choose_placement(model: dict, ctx: int) -> tuple[str, list[int]] | None:
    if int(model["single_max"]) >= ctx:
        return "single_gpu", [0]
    if int(model["two_max"]) >= ctx:
        return "two_gpu", [0, 1]
    return None


def stream_chat(
    gateway: str,
    alias: str,
    messages: list[dict],
    max_tokens: int,
    timeout: int = 900,
) -> dict:
    """Stream for TTFT, then non-stream for authoritative usage when needed.

    Gateway rejects OpenAI ``stream_options``; usage may still arrive on the
    final SSE chunk. If not, a second non-stream call records prompt/completion
    tokens honestly (never invents counts).
    """
    payload_stream = json.dumps(
        {
            "model": alias,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "stream": True,
        }
    ).encode()
    req = urllib.request.Request(
        gateway + "/v1/chat/completions",
        data=payload_stream,
        method="POST",
        headers={
            "content-type": "application/json",
            "content-length": str(len(payload_stream)),
            "accept": "text/event-stream",
        },
    )
    t0 = time.perf_counter()
    ttft_ms: int | None = None
    text_parts: list[str] = []
    usage: dict = {}
    http = 200
    err: str | None = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            http = getattr(resp, "status", 200) or 200
            while True:
                raw = resp.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if isinstance(chunk.get("usage"), dict):
                    usage = chunk["usage"]
                # Some gateways nest usage under choices-less final events.
                if isinstance(chunk.get("yonder"), dict) and isinstance(
                    chunk["yonder"].get("usage"), dict
                ):
                    usage = chunk["yonder"]["usage"]
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content") or ""
                if delta:
                    if ttft_ms is None:
                        ttft_ms = int((time.perf_counter() - t0) * 1000)
                    text_parts.append(delta)
    except urllib.error.HTTPError as exc:
        http = exc.code
        err = exc.read().decode("utf-8", errors="replace")[:1500]
    except Exception as exc:  # noqa: BLE001 — record honestly
        http = 0
        err = str(exc)[:1500]
    stream_ms = int((time.perf_counter() - t0) * 1000)
    reply = "".join(text_parts)

    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    token_source = "provider_usage"

    # Authoritative usage via non-stream when stream omitted it.
    usage_note = ""
    if http == 200 and not err and (
        not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int)
    ):
        payload_ns = json.dumps(
            {
                "model": alias,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.0,
                "stream": False,
            }
        ).encode()
        req_ns = urllib.request.Request(
            gateway + "/v1/chat/completions",
            data=payload_ns,
            method="POST",
            headers={
                "content-type": "application/json",
                "content-length": str(len(payload_ns)),
            },
        )
        t1 = time.perf_counter()
        try:
            with urllib.request.urlopen(req_ns, timeout=timeout) as resp:
                body = json.loads(resp.read())
                u = body.get("usage") or {}
                if isinstance(u.get("prompt_tokens"), int):
                    prompt_tokens = u["prompt_tokens"]
                if isinstance(u.get("completion_tokens"), int):
                    completion_tokens = u["completion_tokens"]
                usage_note = (
                    f"usage_from_nonstream latency_ms="
                    f"{int((time.perf_counter()-t1)*1000)}"
                )
        except Exception as exc:  # noqa: BLE001
            usage_note = f"nonstream_usage_failed:{exc}"[:200]

    # Lab context_window.chars_per_token_estimate is 4. When the runtime omits
    # usage (current llama.cpp OpenAI path returns null), record an explicit
    # estimate rather than inventing provider usage.
    if not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
        token_source = "chars_div_4_estimate"
        prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)
        if not isinstance(prompt_tokens, int):
            prompt_tokens = max(1, prompt_chars // 4)
        if not isinstance(completion_tokens, int):
            completion_tokens = max(1, len(reply) // 4) if reply else NOT_REPORTED
        usage_note = (
            (usage_note + " | " if usage_note else "")
            + "token counts estimated via lab chars/4 heuristic (provider usage null)"
        )

    if not isinstance(prompt_tokens, int):
        prompt_tokens = NOT_REPORTED
    if not isinstance(completion_tokens, int):
        completion_tokens = NOT_REPORTED

    decode_tps: float | str = NOT_REPORTED
    prefill_tps: float | str = NOT_REPORTED
    if (
        isinstance(completion_tokens, int)
        and completion_tokens > 0
        and ttft_ms is not None
        and stream_ms > ttft_ms
    ):
        decode_s = (stream_ms - ttft_ms) / 1000.0
        if decode_s > 0:
            decode_tps = round(completion_tokens / decode_s, 2)
    if (
        isinstance(prompt_tokens, int)
        and prompt_tokens > 0
        and ttft_ms is not None
        and ttft_ms > 0
    ):
        prefill_tps = round(prompt_tokens / (ttft_ms / 1000.0), 2)

    return {
        "http": http,
        "error": err,
        "ttft_ms": ttft_ms if ttft_ms is not None else NOT_REPORTED,
        "latency_ms": stream_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "decode_tok_s": decode_tps,
        "prefill_tok_s": prefill_tps,
        "token_source": token_source,
        "reply_chars": len(reply),
        "reply_preview": reply[:120].replace("\n", " "),
        "usage_note": usage_note,
    }


def make_prompt(target_chars: int) -> str:
    marker = "THROUGHPUT-MARKER-ALPHA"
    body = ("lorem ipsum dolor sit amet. " * 4000)[: max(0, target_chars - 200)]
    return (
        f"{marker}\n\n{body}\n\n"
        "Reply with exactly three lines:\n"
        f"1) {marker}\n"
        "2) ok\n"
        "3) done"
    )


def wait_gpus_idle(
    gateway: str, worker_id: str, gpus: list[int], *, want_le: int = 512, timeout: int = 120
) -> None:
    deadline = time.time() + timeout
    used: list[int | None] = []
    while time.time() < deadline:
        used = hardware_used(gateway, worker_id, gpus)
        if used and all(u is not None and u <= want_le for u in used):
            return
        # If inventory is missing, do not block for minutes — brief settle only.
        if used and all(u is None for u in used):
            time.sleep(4)
            return
        time.sleep(3)
    log(f"WARN idle wait timed out worker={worker_id} gpus={gpus} used={used}")


def unload(gateway: str, dep_id: str, worker_id: str, gpus: list[int]) -> None:
    api(gateway, "POST", f"/api/admin/deployments/{dep_id}/unload", {})
    wait_gpus_idle(gateway, worker_id, gpus)


def run_one(
    gateway: str,
    model: dict,
    worker: str,
    ctx: int,
    alias: str,
    prefill_chars: int,
    max_tokens: int,
) -> dict:
    placement = choose_placement(model, ctx)
    row: dict = {
        "model_id": model["id"],
        "display_name": model["display"],
        "worker_id": worker,
        "context_length": ctx,
        "mode": NOT_REPORTED,
        "gpus": NOT_REPORTED,
        "status": "skipped",
        "peak_used_mib": NOT_REPORTED,
        "ttft_ms": NOT_REPORTED,
        "latency_ms": NOT_REPORTED,
        "prompt_tokens": NOT_REPORTED,
        "completion_tokens": NOT_REPORTED,
        "decode_tok_s": NOT_REPORTED,
        "prefill_tok_s": NOT_REPORTED,
        "token_source": NOT_REPORTED,
        "notes": "",
    }
    if placement is None:
        row["notes"] = (
            f"ctx {ctx} exceeds single_max={model['single_max']} "
            f"and two_max={model['two_max']}"
        )
        return row

    mode, gpus = placement
    row["mode"] = mode
    row["gpus"] = ",".join(str(g) for g in gpus)
    log(f"LOAD {model['id']} {mode} gpus={gpus} ctx={ctx} on {worker}")
    wait_gpus_idle(gateway, worker, gpus)
    load_body = {
        "model_definition_id": model["id"],
        "worker_id": worker,
        "placement_mode": mode,
        "gpu_indexes": gpus,
        "context_length": ctx,
    }
    loaded = api(gateway, "POST", "/api/admin/deployments/load", load_body)
    if loaded.get("_http_error"):
        row["status"] = "load_failed"
        row["notes"] = json.dumps(loaded)[:500]
        return row
    dep = loaded.get("deployment") or loaded
    dep_id = dep.get("deployment_id") or dep.get("id")
    if not dep_id:
        row["status"] = "load_failed"
        row["notes"] = f"no deployment_id in {loaded!r}"[:500]
        return row
    st = wait_ready(gateway, dep_id)
    if st != "ready":
        row["status"] = f"not_ready:{st}"
        row["notes"] = f"deployment={dep_id}"
        unload(gateway, dep_id, worker, gpus)
        return row

    used = hardware_used(gateway, worker, gpus)
    if any(u is not None for u in used):
        row["peak_used_mib"] = ",".join(
            str(u) if u is not None else NOT_REPORTED for u in used
        )

    bind = api(
        gateway,
        "PATCH",
        f"/api/admin/routes/{alias}",
        {"deployment_id": dep_id, "enabled": True},
    )
    if bind.get("_http_error"):
        row["status"] = "alias_failed"
        row["notes"] = json.dumps(bind)[:500]
        unload(gateway, dep_id, worker, gpus)
        return row

    # Warmup (discard)
    stream_chat(
        gateway,
        alias,
        [{"role": "user", "content": "Reply with READY only."}],
        max_tokens=8,
    )

    prompt = make_prompt(prefill_chars)
    result = stream_chat(
        gateway,
        alias,
        [{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
    )
    row.update(
        {
            "status": "ok" if result["http"] == 200 and not result["error"] else "fail",
            "ttft_ms": result["ttft_ms"],
            "latency_ms": result["latency_ms"],
            "prompt_tokens": result["prompt_tokens"],
            "completion_tokens": result["completion_tokens"],
            "decode_tok_s": result["decode_tok_s"],
            "prefill_tok_s": result["prefill_tok_s"],
            "token_source": result.get("token_source", NOT_REPORTED),
            "notes": result["error"]
            or (
                f"reply_chars={result['reply_chars']} preview={result['reply_preview']}"
                + (f" | {result.get('usage_note')}" if result.get("usage_note") else "")
            ),
        }
    )
    unload(gateway, dep_id, worker, gpus)
    log(
        f"DONE {model['id']} ctx={ctx} decode={row['decode_tok_s']} "
        f"prefill={row['prefill_tok_s']} status={row['status']}"
    )
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gateway",
        default=__import__("os").environ.get("GATEWAY_URL", "http://127.0.0.1:8080"),
    )
    parser.add_argument(
        "--outdir",
        default="/tmp/yonder-throughput",
        help="Directory for JSON/TSV evidence",
    )
    parser.add_argument(
        "--prefill-chars",
        type=int,
        default=12000,
        help="Prompt size in chars (kept modest so admission allows large n_ctx loads)",
    )
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument(
        "--models",
        default="",
        help="Optional comma-separated model ids (default: all chat models)",
    )
    parser.add_argument(
        "--contexts",
        default=",".join(str(c) for c in CONTEXTS),
    )
    args = parser.parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    contexts = [int(x) for x in args.contexts.split(",") if x.strip()]
    selected = {m.strip() for m in args.models.split(",") if m.strip()}
    models = [m for m in MODELS if not selected or m["id"] in selected]

    # Sequential: shared chat path + cleaner VRAM peaks. Round-robin workers.
    rows: list[dict] = []
    for i, model in enumerate(models):
        worker = WORKERS[i % len(WORKERS)]
        alias = "yonder-chat"  # stable lab alias; sequential runs avoid races
        for ctx in contexts:
            try:
                row = run_one(
                    args.gateway,
                    model,
                    worker,
                    ctx,
                    alias,
                    args.prefill_chars,
                    args.max_tokens,
                )
            except Exception as exc:  # noqa: BLE001
                row = {
                    "model_id": model["id"],
                    "display_name": model["display"],
                    "worker_id": worker,
                    "context_length": ctx,
                    "mode": NOT_REPORTED,
                    "gpus": NOT_REPORTED,
                    "status": "exception",
                    "peak_used_mib": NOT_REPORTED,
                    "ttft_ms": NOT_REPORTED,
                    "latency_ms": NOT_REPORTED,
                    "prompt_tokens": NOT_REPORTED,
                    "completion_tokens": NOT_REPORTED,
                    "decode_tok_s": NOT_REPORTED,
                    "prefill_tok_s": NOT_REPORTED,
                    "notes": str(exc)[:500],
                }
            rows.append(row)
            # Persist incrementally
            (outdir / f"throughput-{stamp}.json").write_text(
                json.dumps(
                    {
                        "stamp": stamp,
                        "gateway": args.gateway,
                        "prefill_chars": args.prefill_chars,
                        "max_tokens": args.max_tokens,
                        "rows": rows,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

    cols = [
        "model_id",
        "display_name",
        "worker_id",
        "mode",
        "gpus",
        "context_length",
        "status",
        "peak_used_mib",
        "prompt_tokens",
        "completion_tokens",
        "ttft_ms",
        "latency_ms",
        "prefill_tok_s",
        "decode_tok_s",
        "token_source",
        "notes",
    ]
    tsv = outdir / f"throughput-{stamp}.tsv"
    with tsv.open("w", encoding="utf-8") as fh:
        fh.write("\t".join(cols) + "\n")
        for row in rows:
            fh.write("\t".join(str(row.get(c, "")) for c in cols) + "\n")

    # Console-friendly snapshot (latest)
    latest = outdir / "throughput-latest.json"
    latest.write_text(
        json.dumps(
            {
                "stamp": stamp,
                "gateway": args.gateway,
                "prefill_chars": args.prefill_chars,
                "max_tokens": args.max_tokens,
                "method": (
                    "Gateway stream chat after load at n_ctx; decode_tok_s = "
                    "completion_tokens / (latency_ms - ttft_ms); "
                    "prefill_tok_s = prompt_tokens / ttft_ms. "
                    "When provider usage is null, token counts use the lab "
                    "chars/4 estimate (token_source=chars_div_4_estimate). "
                    "Skipped when ctx exceeds placement max."
                ),
                "rows": rows,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    log(f"wrote {tsv}")
    log(f"wrote {latest}")
    return 0 if all(r["status"] in {"ok", "skipped"} for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
