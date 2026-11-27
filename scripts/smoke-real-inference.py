#!/usr/bin/env python3
"""Run a synthetic real-model chat canary via a local Yonder Gateway."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PROMPT = "Write one short sentence about a tree."


def check_content(content):
    assert isinstance(content, str) and content.strip(), "Empty model response"
    assert "mock response" not in content.lower(), "Mock response is not real inference"


def run(port: int, alias: str, *, expect_unavailable: bool = False) -> dict:
    base = f"http://127.0.0.1:{port}"
    with urlopen(base + "/v1/onboarding/status", timeout=20) as response:
        status = json.load(response)
    assert status["gateway_ready"]
    matches = [row for row in status["aliases"] if row["alias"] == alias]
    assert len(matches) == 1 and matches[0]["kind"] == "model", "Non-mock alias required"
    if not expect_unavailable:
        assert matches[0]["status"] == "route_ready"
    payload = {
        "model": alias, "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": 48, "temperature": 0, "stream": False,
    }

    def request():
        return Request(base + "/v1/chat/completions", data=json.dumps(payload).encode(),
                       headers={"Content-Type": "application/json"}, method="POST")

    evidence = {"alias": alias, "utc": datetime.now(UTC).isoformat(),
                "scope": "CPU real-model transport; not a quality or lab GPU benchmark"}
    start = time.monotonic()
    if expect_unavailable:
        try:
            with urlopen(request(), timeout=150):
                raise AssertionError("Unavailable runtime unexpectedly returned HTTP success")
        except HTTPError as error:
            body = json.load(error)
            assert error.code in {502, 503, 504}, error.code
            assert body.get("error", {}).get("code")
            evidence.update(status="unavailable_without_fallback", http_status=error.code)
            return evidence

    with urlopen(request(), timeout=150) as response:
        body = json.load(response)
    elapsed = time.monotonic() - start
    assert body["model"] == alias
    content = body["choices"][0]["message"]["content"]
    check_content(content)
    usage = body.get("usage")
    evidence.update(nonstream_seconds=round(elapsed, 3),
                    nonstream_output_sha256=hashlib.sha256(content.encode()).hexdigest(),
                    usage=usage)
    payload["stream"] = True
    fragments = []
    completed = False
    first = None
    start = time.monotonic()
    with urlopen(request(), timeout=150) as response:
        assert "text/event-stream" in response.headers["content-type"]
        for line in response:
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                completed = True
                break
            event = json.loads(data)
            assert not event.get("error"), "Stream returned an error"
            if event.get("object") != "chat.completion.chunk":
                continue
            for choice in event.get("choices", []):
                text = choice.get("delta", {}).get("content")
                if text:
                    if first is None:
                        first = time.monotonic() - start
                    fragments.append(text)
    assert completed, "Stream ended without completion sentinel"
    content = "".join(fragments)
    check_content(content)
    assert first is not None
    evidence.update(status="passed", stream_seconds=round(time.monotonic() - start, 3),
                    first_content_seconds=round(first, 3),
                    stream_output_sha256=hashlib.sha256(content.encode()).hexdigest())
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--alias", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expect-unavailable", action="store_true")
    args = parser.parse_args()
    result = run(args.port, args.alias, expect_unavailable=args.expect_unavailable)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
