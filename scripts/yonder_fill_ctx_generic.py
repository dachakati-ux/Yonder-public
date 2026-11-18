#!/usr/bin/env python3
"""Near-ceiling context fill smoke (Gateway chat completions).

Generalizes scripts/yonder_fill_ctx_suite.py for any chat model / worker / mode.
Does not invent metrics: records HTTP + usage + marker retrieval honestly.

Example:
  GATEWAY_URL=http://127.0.0.1:8080 python3 scripts/yonder_fill_ctx_generic.py \\
    --model yonder-qwen36-35b-a3b-q4 --worker worker-ai1 \\
    --mode single_gpu --gpus 0 --ctx 65536 --alias yonder-chat
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


def log(msg: str, log_path: Path) -> None:
    line = f"[{time.strftime('%H:%M:%S', time.gmtime())}] {msg}"
    print(line, flush=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def api(
    gateway: str,
    method: str,
    path: str,
    body: dict | None = None,
    timeout: int = 900,
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
            return {"_http_error": exc.code, **json.loads(detail)}
        except Exception:
            return {"_http_error": exc.code, "raw": detail[:2000]}


def wait_ready(gateway: str, dep_id: str, timeout: int = 900) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = api(gateway, "GET", f"/api/admin/deployments/{dep_id}")
        st = (body.get("deployment") or body).get("status", "")
        if st == "ready":
            return st
        if st in {"failed", "unavailable", "cancelled", "stopped"}:
            return st
        time.sleep(5)
    return "timeout"


def chat(
    gateway: str, alias: str, messages: list[dict], max_tokens: int = 128
) -> dict:
    t0 = time.time()
    payload = json.dumps(
        {
            "model": alias,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "stream": False,
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
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            data = json.loads(resp.read())
            data["_latency_ms"] = int((time.time() - t0) * 1000)
            data["_http"] = 200
            return data
    except urllib.error.HTTPError as exc:
        ms = int((time.time() - t0) * 1000)
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(detail)
        except Exception:
            data = {"error": {"message": detail[:500]}}
        data["_latency_ms"] = ms
        data["_http"] = exc.code
        return data


def summarize(data: dict) -> str:
    err = data.get("error")
    if err or data.get("_http_error"):
        return (
            f"ERR|{data.get('_latency_ms')}|{data.get('_http') or data.get('_http_error')}|"
            f"{(err or {}).get('code')}|"
            f"{str((err or {}).get('message', data.get('raw', '')))[:200]}"
        )
    choice = (data.get("choices") or [{}])[0]
    msg = ((choice.get("message") or {}).get("content") or "").replace("\n", " ")
    usage = data.get("usage") or {}
    return (
        f"OK|{data.get('_latency_ms')}|{data.get('_http')}|"
        f"prompt_tokens={usage.get('prompt_tokens')}|"
        f"completion_tokens={usage.get('completion_tokens')}|"
        f"reply={msg[:180]}"
    )


def build_fill_messages(target_chars: int, marker: str) -> tuple[list[dict], int]:
    block = (
        "SECTION-{i}: ops runbook note. Change window Fri 22:00-02:00 ET. "
        "On-call rotates weekly. Prefer alias yonder-chat. Never invent VRAM. "
        "Ticket severity Sev1 pages immediately. "
    )
    parts: list[str] = []
    i = 0
    while True:
        i += 1
        parts.append(block.format(i=i))
        if sum(len(p) for p in parts) > target_chars - 800:
            break
    parts.append(
        f"MARKER={marker}. If asked for the marker, reply with only that value."
    )
    parts.append(
        "TASK: 1) Give the MARKER value. "
        "2) Name one change-window fact from the runbook. "
        "3) One sentence on Gateway-only inference."
    )
    content = "".join(parts)
    messages = [
        {
            "role": "system",
            "content": (
                "You are a careful Yonder lab assistant. "
                "Answer from the provided context. Be concise."
            ),
        },
        {"role": "user", "content": content},
    ]
    return messages, len(content)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--gateway", default="http://127.0.0.1:8080")
    p.add_argument("--model", required=True)
    p.add_argument("--worker", required=True)
    p.add_argument("--mode", choices=["single_gpu", "two_gpu"], required=True)
    p.add_argument("--gpus", required=True, help="comma-separated GPU indexes")
    p.add_argument("--ctx", type=int, required=True)
    p.add_argument("--alias", default="yonder-chat")
    p.add_argument("--outdir", default="/tmp")
    p.add_argument(
        "--pcts",
        default="40,70,95",
        help="comma-separated fill percentages of ctx (chars≈tokens*4 heuristic)",
    )
    p.add_argument(
        "--max-prompt-chars",
        type=int,
        default=180000,
        help="Cap prompt size so Gateway request limits do not mask n_ctx behavior",
    )
    args = p.parse_args()
    gpus = [int(x) for x in args.gpus.split(",") if x.strip() != ""]
    pcts = [int(x) for x in args.pcts.split(",") if x.strip() != ""]
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    tsv = out / f"fill-{args.model}-{args.mode}-{stamp}.tsv"
    log_path = out / f"fill-{args.model}-{args.mode}-{stamp}.log"
    with tsv.open("w", encoding="utf-8") as fh:
        fh.write(
            "model\tworker\tmode\tgpus\tctx\tprobe\tok\tlatency_ms\tprompt_chars\tsummary\n"
        )

    def row(*cols: object) -> None:
        with tsv.open("a", encoding="utf-8") as fh:
            fh.write("\t".join(str(c) for c in cols) + "\n")

    log(
        f"fill model={args.model} worker={args.worker} mode={args.mode} "
        f"gpus={gpus} ctx={args.ctx} alias={args.alias}",
        log_path,
    )
    load = api(
        args.gateway,
        "POST",
        "/api/admin/deployments/load",
        {
            "model_definition_id": args.model,
            "worker_id": args.worker,
            "placement_mode": args.mode,
            "gpu_indexes": gpus,
            "context_length": int(args.ctx),
        },
    )
    dep = (load.get("deployment") or {}).get("id")
    if not dep:
        log(f"LOAD FAILED: {load}", log_path)
        row(args.model, args.worker, args.mode, args.gpus, args.ctx, "load", 0, "", "", load)
        return 1
    st = wait_ready(args.gateway, dep)
    if st != "ready":
        log(f"not ready: {st}", log_path)
        api(args.gateway, "POST", f"/api/admin/deployments/{dep}/unload", {})
        row(args.model, args.worker, args.mode, args.gpus, args.ctx, "ready", 0, "", "", st)
        return 1
    bind = api(
        args.gateway,
        "PATCH",
        f"/api/admin/routes/{args.alias}",
        {"deployment_id": dep, "enabled": True},
    )
    log(f"bound alias {args.alias}: {bind.get('alias') or bind}", log_path)

    warm = chat(
        args.gateway,
        args.alias,
        [{"role": "user", "content": "Reply with exactly: READY"}],
        max_tokens=8,
    )
    warm_s = summarize(warm)
    log(f"warmup {warm_s}", log_path)
    warm_ok = "READY" in warm_s and "OK|" in warm_s
    row(
        args.model,
        args.worker,
        args.mode,
        args.gpus,
        args.ctx,
        "warmup",
        int(warm_ok),
        warm.get("_latency_ms"),
        40,
        warm_s,
    )
    if not warm_ok:
        api(args.gateway, "POST", f"/api/admin/deployments/{dep}/unload", {})
        return 1

    marker = f"ALPHA-{args.ctx}-{int(time.time())}"
    exit_code = 0
    for pct in pcts:
        # chars≈4*tokens heuristic, but cap so Gateway body limits do not
        # masquerade as n_ctx failures on large windows.
        uncapped = args.ctx * pct // 100 * 4
        chars = min(uncapped, int(args.max_prompt_chars), 750000)
        messages, pchars = build_fill_messages(chars, marker)
        eff_pct = round(100.0 * pchars / max(args.ctx * 4, 1), 1)
        log(
            f"fill pct={pct} (effective~{eff_pct}% of n_ctx chars) "
            f"prompt_chars={pchars} uncapped={uncapped}",
            log_path,
        )
        res = chat(args.gateway, args.alias, messages, max_tokens=128)
        summary = summarize(res)
        log(f"fill_{pct} {summary}", log_path)
        probe = f"fill_{pct}_capped" if uncapped > int(args.max_prompt_chars) else f"fill_{pct}"
        if pct >= 95 and uncapped <= int(args.max_prompt_chars):
            expect_fail = "ERR|" in summary
            row(
                args.model,
                args.worker,
                args.mode,
                args.gpus,
                args.ctx,
                probe,
                int(expect_fail or ("OK|" in summary)),
                res.get("_latency_ms"),
                pchars,
                summary,
            )
            log(f"fill_{pct} expect_fail_closed={expect_fail} recorded", log_path)
        else:
            ok = ("OK|" in summary) and (marker in summary)
            row(
                args.model,
                args.worker,
                args.mode,
                args.gpus,
                args.ctx,
                probe,
                int(ok),
                res.get("_latency_ms"),
                pchars,
                summary,
            )
            if not ok:
                exit_code = 2
                log(f"fill failed at pct={pct}", log_path)
                break

    api(args.gateway, "POST", f"/api/admin/deployments/{dep}/unload", {})
    log(f"done exit={exit_code} tsv={tsv}", log_path)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
