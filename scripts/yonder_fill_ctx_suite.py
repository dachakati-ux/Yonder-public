#!/usr/bin/env python3
"""Manual 1-GPU context fill suite for yonder-qwen36 on ai1."""
from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

GATEWAY = "http://127.0.0.1:8080"
MODEL_ID = "yonder-qwen36-35b-a3b-q4"
WORKER = "worker-ai1"
TSV = Path("/tmp/yonder-fill-ctx-results.tsv")
LOG = Path("/tmp/yonder-fill64k.log")


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S', time.gmtime())}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def sh(cmd: str) -> str:
    return subprocess.check_output(cmd, shell=True, text=True).strip()


def vram_used() -> int:
    return int(
        sh(
            "nvidia-smi --query-gpu=memory.used "
            "--format=csv,noheader,nounits -i 0"
        ).split()[0]
    )


def wait_free(min_free: int = 30000, timeout: int = 180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        free = int(
            sh(
                "nvidia-smi --query-gpu=memory.free "
                "--format=csv,noheader,nounits -i 0"
            ).split()[0]
        )
        if free >= min_free:
            time.sleep(10)
            return
        time.sleep(3)
    raise RuntimeError("GPU never freed")


def api(method: str, path: str, body: dict | None = None, timeout: int = 900) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        GATEWAY + path,
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


def wait_ready(dep_id: str, timeout: int = 600) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = api("GET", f"/api/admin/deployments/{dep_id}")
        st = (body.get("deployment") or body).get("status", "")
        if st == "ready":
            return st
        if st in {"failed", "unavailable", "cancelled", "stopped"}:
            return st
        time.sleep(5)
    return "timeout"


def bind_alias(dep_id: str) -> None:
    body = api(
        "PATCH",
        "/api/admin/routes/yonder-chat",
        {"deployment_id": dep_id, "enabled": True},
    )
    alias = body.get("alias") or {}
    log(
        f"alias yonder-chat -> {alias.get('deployment_id')} "
        f"state={alias.get('state')}"
    )


def chat(
    messages: list[dict],
    max_tokens: int = 128,
    temperature: float = 0.2,
) -> dict:
    t0 = time.time()
    body = {
        "model": "yonder-chat",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        GATEWAY + "/v1/chat/completions",
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


def summarize_chat(data: dict) -> str:
    err = data.get("error")
    if err:
        return (
            f"ERR|{data.get('_latency_ms')}|{data.get('_http')}|"
            f"{err.get('code')}|{str(err.get('message', ''))[:200]}"
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


def write_tsv(row: list[object]) -> None:
    with TSV.open("a", encoding="utf-8") as fh:
        fh.write("\t".join(str(x) for x in row) + "\n")


def run_suite(ctx: int) -> bool:
    log(f"======== LOAD single_gpu ctx={ctx} ========")
    wait_free()
    load = api(
        "POST",
        "/api/admin/deployments/load",
        {
            "model_definition_id": MODEL_ID,
            "worker_id": WORKER,
            "placement_mode": "single_gpu",
            "gpu_indexes": [0],
            "context_length": ctx,
        },
    )
    dep = (load.get("deployment") or {}).get("id")
    if not dep:
        log(f"LOAD FAILED: {load}")
        return False
    log(f"deployment={dep} waiting ready...")
    st = wait_ready(dep)
    if st != "ready":
        log(f"not ready: {st}")
        api("POST", f"/api/admin/deployments/{dep}/unload", {})
        return False
    bind_alias(dep)
    time.sleep(2)
    log(f"ready vram_used_mib={vram_used()}")

    warm = chat(
        [{"role": "user", "content": "Reply with exactly: READY"}],
        max_tokens=8,
        temperature=0,
    )
    warm_s = summarize_chat(warm)
    log(f"warmup {warm_s}")
    write_tsv(
        [ctx, "warmup", "OK" in warm_s, warm.get("_latency_ms"), vram_used(), 40, warm_s]
    )
    if "READY" not in warm_s:
        api("POST", f"/api/admin/deployments/{dep}/unload", {})
        wait_free()
        return False

    marker = f"ALPHA-{ctx}-{int(time.time())}"
    for pct in (40, 70, 95):
        chars = min(ctx * pct // 100 * 4, 750000)
        messages, pchars = build_fill_messages(chars, marker)
        log(f"fill pct={pct} prompt_chars={pchars} marker={marker}")
        res = chat(messages, max_tokens=128, temperature=0.2)
        summary = summarize_chat(res)
        log(f"fill_{pct} {summary}")
        ok = ("OK|" in summary) and (marker in summary)
        write_tsv(
            [
                ctx,
                f"fill_{pct}",
                int(ok),
                res.get("_latency_ms"),
                vram_used(),
                pchars,
                summary,
            ]
        )
        if not ok:
            log(f"fill failed at pct={pct}")
            api("POST", f"/api/admin/deployments/{dep}/unload", {})
            wait_free()
            return False

    msgs: list[dict] = [
        {"role": "system", "content": "Keep a running tally. Be brief."}
    ]
    multi_ok = True
    for i in range(1, 7):
        msgs.append(
            {
                "role": "user",
                "content": (
                    f"Fact {i}: host=ai1 gpu=0 turn={i}. "
                    f"Reply with exactly ACK-{i} and the count of facts so far."
                ),
            }
        )
        res = chat(msgs, max_tokens=48, temperature=0)
        summary = summarize_chat(res)
        log(f"turn{i} {summary}")
        reply = ""
        if not res.get("error"):
            reply = (
                ((res.get("choices") or [{}])[0].get("message") or {}).get("content")
                or ""
            )
            msgs.append({"role": "assistant", "content": reply})
        ok = f"ACK-{i}" in reply
        write_tsv(
            [
                ctx,
                f"turn_{i}",
                int(ok),
                res.get("_latency_ms"),
                vram_used(),
                len(json.dumps(msgs)),
                summary,
            ]
        )
        if not ok:
            multi_ok = False
            break

    log(f"multi_ok={multi_ok}; unloading")
    api("POST", f"/api/admin/deployments/{dep}/unload", {})
    wait_free()
    return multi_ok


def main() -> None:
    LOG.write_text("", encoding="utf-8")
    TSV.write_text(
        "ctx\tphase\tok\tlatency_ms\tvram_used_mib\tprompt_chars\tnotes\n",
        encoding="utf-8",
    )
    ok64 = run_suite(65536)
    if ok64:
        log("64k suite OK — raising to 81920")
        ok80 = run_suite(81920)
        log(f"80k suite ok={ok80}")
    else:
        log("64k suite failed — not raising context")
    log("FINAL RESULTS")
    print(TSV.read_text(encoding="utf-8"))
    print(sh("nvidia-smi --query-gpu=index,memory.used,memory.free --format=csv"))


if __name__ == "__main__":
    main()
