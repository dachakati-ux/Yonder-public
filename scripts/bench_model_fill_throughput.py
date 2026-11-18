#!/usr/bin/env python3
"""Fill-ratio decode/prefill throughput via Gateway (near end of context).

Loads a chat model at each n_ctx, then streams completions after prompts that
fill ~pct of the window (chars≈4*tokens lab heuristic). Records TTFT + decode
tok/s so operators can see slowdown vs a modest baseline.

Raises nothing permanently — pass --max-prompt-chars high enough that admission
does not cap the fill (temporarily bump Gateway admission for large windows).

Example (on ai1, after raising admission.max_prompt_chars):
  GATEWAY_URL=http://127.0.0.1:8080 python3 scripts/bench_model_fill_throughput.py \\
    --model yonder-bonsai-27b-ternary --worker worker-ai1 \\
    --contexts 32768,65536,131072,262144 --pcts 10,50,85,95 \\
    --outdir /srv/yonder-storage/yonder/gauntlet/20260808-bonsai-fill
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

NOT_REPORTED = "not_reported"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def api(gateway: str, method: str, path: str, body: dict | None = None, timeout: float = 900) -> Any:
    data = None if body is None else json.dumps(body).encode()
    url = gateway.rstrip("/") + path
    last_err: Exception | None = None
    for attempt in range(1, 10):
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
            if exc.code in {502, 503, 504} and attempt < 9:
                time.sleep(min(45, 3 * attempt))
                continue
            raise
        except (TimeoutError, urllib.error.URLError) as exc:
            last_err = exc
            if attempt < 9:
                time.sleep(min(45, 3 * attempt))
                continue
            raise
    raise RuntimeError(f"api failed after retries: {last_err}")


def unload_matching(gateway: str, model_id: str) -> None:
    deps = api(gateway, "GET", "/api/admin/deployments").get("deployments") or []
    for d in deps:
        did = d.get("deployment_id") or d.get("id")
        mid = str(d.get("model_definition_id") or "")
        st = (d.get("status") or "").lower()
        if did and model_id in mid and st in {"ready", "loading", "failed"}:
            try:
                api(gateway, "POST", f"/api/admin/deployments/{did}/unload", {})
            except Exception as exc:  # noqa: BLE001
                log(f"unload {did}: {exc}")
    time.sleep(6)


def wait_ready(gateway: str, deployment_id: str, timeout: float = 900) -> dict:
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
                    raise RuntimeError(str(d.get("message") or d))
        time.sleep(3)
    raise TimeoutError(f"not ready: {last}")


def make_fill_prompt(target_chars: int, marker: str) -> str:
    block = (
        "SECTION note: change window Fri 22:00-02:00 ET. On-call rotates weekly. "
        "Prefer Gateway-only inference. Never invent VRAM. Sev1 pages immediately. "
    )
    parts: list[str] = [f"MARKER={marker}. "]
    while sum(len(p) for p in parts) < max(0, target_chars - 240):
        parts.append(block)
    parts.append(
        f"TASK: Reply with exactly the marker value ({marker}), then write 8 short "
        "sentences about Gateway-only inference and why VRAM numbers must not be invented."
    )
    return "".join(parts)[:target_chars]


def stream_chat_no_usage(
    gateway: str,
    alias: str,
    messages: list[dict],
    *,
    max_tokens: int,
    timeout: float = 900,
) -> dict[str, Any]:
    """Fallback stream without stream_options (chars/4 token estimate)."""
    payload = json.dumps(
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
        data=payload,
        method="POST",
        headers={
            "content-type": "application/json",
            "content-length": str(len(payload)),
        },
    )
    t0 = time.perf_counter()
    ttft_ms: int | None = None
    reply_parts: list[str] = []
    http = 200
    err = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = ((chunk.get("choices") or [{}])[0].get("delta") or {})
                content = delta.get("content") or ""
                if content:
                    if ttft_ms is None:
                        ttft_ms = int((time.perf_counter() - t0) * 1000)
                    reply_parts.append(content)
    except urllib.error.HTTPError as exc:
        http = exc.code
        err = exc.read().decode("utf-8", errors="replace")[:500]
    except Exception as exc:  # noqa: BLE001
        http = 0
        err = str(exc)[:500]
    stream_ms = int((time.perf_counter() - t0) * 1000)
    reply = "".join(reply_parts)
    prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)
    prompt_tokens: int | str = max(1, prompt_chars // 4)
    completion_tokens: int | str = max(1, len(reply) // 4) if reply else NOT_REPORTED
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
    if isinstance(prompt_tokens, int) and prompt_tokens > 0 and ttft_ms and ttft_ms > 0:
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
        "token_source": "chars_div_4_estimate",
        "usage_note": "stream_options_rejected",
        "reply_preview": reply[:160].replace("\n", " "),
        "marker_ok": False,
    }


def stream_chat(
    gateway: str,
    alias: str,
    messages: list[dict],
    *,
    max_tokens: int,
    timeout: float = 900,
) -> dict[str, Any]:
    payload = json.dumps(
        {
            "model": alias,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "stream": True,
            # Prefer usage on the stream so we do not re-prefill huge fills.
            "stream_options": {"include_usage": True},
        }
    ).encode()
    req = urllib.request.Request(
        gateway + "/v1/chat/completions",
        data=payload,
        method="POST",
        headers={
            "content-type": "application/json",
            "content-length": str(len(payload)),
        },
    )
    t0 = time.perf_counter()
    ttft_ms: int | None = None
    reply_parts: list[str] = []
    http = 200
    err = None
    prompt_tokens: int | str = NOT_REPORTED
    completion_tokens: int | str = NOT_REPORTED
    usage_note = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                u = chunk.get("usage")
                if isinstance(u, dict):
                    if isinstance(u.get("prompt_tokens"), int):
                        prompt_tokens = u["prompt_tokens"]
                    if isinstance(u.get("completion_tokens"), int):
                        completion_tokens = u["completion_tokens"]
                    usage_note = "usage_from_stream"
                delta = ((chunk.get("choices") or [{}])[0].get("delta") or {})
                content = delta.get("content") or ""
                if content:
                    if ttft_ms is None:
                        ttft_ms = int((time.perf_counter() - t0) * 1000)
                    reply_parts.append(content)
    except urllib.error.HTTPError as exc:
        http = exc.code
        err = exc.read().decode("utf-8", errors="replace")[:500]
        # Retry once without stream_options if upstream rejects it.
        if http == 400 and "stream_options" in (err or ""):
            return stream_chat_no_usage(gateway, alias, messages, max_tokens=max_tokens, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        http = 0
        err = str(exc)[:500]
    stream_ms = int((time.perf_counter() - t0) * 1000)
    reply = "".join(reply_parts)
    token_source = "provider_usage"

    if not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
        token_source = "chars_div_4_estimate"
        prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)
        if not isinstance(prompt_tokens, int):
            prompt_tokens = max(1, prompt_chars // 4)
        if not isinstance(completion_tokens, int):
            completion_tokens = max(1, len(reply) // 4) if reply else NOT_REPORTED
        if not usage_note:
            usage_note = "estimate_no_stream_usage"

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
    if isinstance(prompt_tokens, int) and prompt_tokens > 0 and ttft_ms and ttft_ms > 0:
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
        "usage_note": usage_note,
        "reply_preview": reply[:160].replace("\n", " "),
        "marker_ok": False,  # filled by caller
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default="http://127.0.0.1:8080")
    parser.add_argument("--model", default="yonder-bonsai-27b-ternary")
    parser.add_argument("--worker", default="worker-ai1")
    parser.add_argument("--gpus", default="0")
    parser.add_argument("--mode", default="single_gpu")
    parser.add_argument("--alias", default="yonder-chat")
    parser.add_argument("--contexts", default="32768,65536,131072,262144")
    parser.add_argument("--pcts", default="10,50,85,95")
    parser.add_argument(
        "--max-prompt-chars",
        type=int,
        default=1_050_000,
        help="Hard cap on prompt body (must be <= Gateway admission.max_prompt_chars)",
    )
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--outdir", default="/tmp/yonder-fill-throughput")
    args = parser.parse_args()

    contexts = [int(x) for x in args.contexts.split(",") if x.strip()]
    pcts = [int(x) for x in args.pcts.split(",") if x.strip()]
    gpus = [int(x) for x in args.gpus.split(",") if x.strip()]
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    rows: list[dict] = []

    unload_matching(args.gateway, args.model)

    for ctx in contexts:
        log(f"LOAD {args.model} {args.mode} gpus={gpus} ctx={ctx}")
        created = api(
            args.gateway,
            "POST",
            "/api/admin/deployments/load",
            {
                "model_definition_id": args.model,
                "worker_id": args.worker,
                "placement_mode": args.mode,
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
            deps = api(args.gateway, "GET", "/api/admin/deployments").get("deployments") or []
            for d in reversed(deps):
                if args.model in str(d.get("model_definition_id") or "") and (
                    d.get("status") or ""
                ).lower() in {"ready", "loading"}:
                    dep = d.get("deployment_id") or d.get("id")
                    break
        if not dep:
            raise SystemExit(f"load missing id: {created}")
        wait_ready(args.gateway, str(dep))
        api(
            args.gateway,
            "PATCH",
            f"/api/admin/routes/{args.alias}",
            {"deployment_id": dep, "enabled": True},
        )
        log(f"READY {dep} bound {args.alias}")

        for pct in pcts:
            # Leave absolute headroom for chat template + reply. A pure %-of-window
            # fill at 95% overflows n_ctx once templates are applied (empty stream).
            reserve = max(2048, int(ctx * 0.04))
            fill_tokens = max(256, int(ctx * (pct / 100.0)) - reserve)
            target_chars = min(fill_tokens * 4, args.max_prompt_chars)
            capped = fill_tokens * 4 > args.max_prompt_chars
            # Soft-cap: never aim above n_ctx - reserve.
            if fill_tokens > ctx - reserve:
                fill_tokens = max(256, ctx - reserve)
                target_chars = min(fill_tokens * 4, args.max_prompt_chars)
                capped = True
            marker = f"FILL-{ctx}-{pct}-{int(time.time())}"
            prompt = make_fill_prompt(target_chars, marker)
            messages = [{"role": "user", "content": prompt}]
            log(
                f"ctx={ctx} pct={pct} chars={len(prompt)} "
                f"target_tokens~{fill_tokens} capped={capped}"
            )
            result = stream_chat(
                args.gateway,
                args.alias,
                messages,
                max_tokens=args.max_tokens,
            )
            marker_ok = marker in (result.get("reply_preview") or "")
            result["marker_ok"] = marker_ok
            status = "ok"
            if result.get("error") or result.get("http") != 200:
                status = "fail"
            elif not marker_ok:
                status = "weak"  # completed but marker miss
            row = {
                "model_id": args.model,
                "worker_id": args.worker,
                "mode": args.mode,
                "gpus": ",".join(str(g) for g in gpus),
                "context_length": ctx,
                "fill_pct": pct,
                "prompt_chars": len(prompt),
                "admission_capped": capped,
                "status": status,
                **{
                    k: result[k]
                    for k in (
                        "ttft_ms",
                        "latency_ms",
                        "prompt_tokens",
                        "completion_tokens",
                        "decode_tok_s",
                        "prefill_tok_s",
                        "token_source",
                        "marker_ok",
                        "reply_preview",
                        "usage_note",
                        "error",
                    )
                },
            }
            rows.append(row)
            log(
                f"  {status} decode={row['decode_tok_s']} prefill={row['prefill_tok_s']} "
                f"ttft={row['ttft_ms']} prompt_tok={row['prompt_tokens']} "
                f"marker_ok={marker_ok}"
            )

        api(args.gateway, "POST", f"/api/admin/deployments/{dep}/unload", {})
        time.sleep(8)

    doc = {
        "stamp": stamp,
        "gateway": args.gateway,
        "model": args.model,
        "method": (
            "Fill-ratio stream after load at n_ctx. "
            "prompt target ≈ fill_pct * 0.97 * n_ctx tokens via chars/4. "
            "decode_tok_s = completion_tokens / (latency_ms - ttft_ms); "
            "prefill_tok_s = prompt_tokens / ttft_ms."
        ),
        "rows": rows,
    }
    latest = outdir / "fill-throughput-latest.json"
    stamped = outdir / f"fill-throughput-{stamp}.json"
    latest.write_text(json.dumps(doc, indent=2) + "\n")
    stamped.write_text(json.dumps(doc, indent=2) + "\n")
    tsv = outdir / f"fill-throughput-{stamp}.tsv"
    cols = [
        "context_length",
        "fill_pct",
        "status",
        "prompt_chars",
        "prompt_tokens",
        "ttft_ms",
        "decode_tok_s",
        "prefill_tok_s",
        "admission_capped",
        "marker_ok",
    ]
    with tsv.open("w", encoding="utf-8") as fh:
        fh.write("\t".join(cols) + "\n")
        for r in rows:
            fh.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")
    log(f"wrote {latest} n={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
