#!/usr/bin/env python3
"""Exercise the disposable development stack; never run against production."""

import argparse
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def call(base, path, method="GET", body=None, expected=200):
    request = Request(
        base + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            status, raw = response.status, response.read()
    except HTTPError as exc:
        status, raw = exc.code, exc.read()
    assert status == expected, (path, status, expected, raw[:300])
    return json.loads(raw)


def core_chat(base):
    reply = call(base, "/v1/chat/completions", "POST", {
        "model": "yonder-chat",
        "messages": [{"role": "user", "content": "lifecycle smoke fixture"}],
        "stream": False,
    })
    assert "mock response" in reply["choices"][0]["message"]["content"].lower()


def active(base):
    items = call(base, "/api/admin/addons")["addons"]
    return {item["id"] for item in items if item["active"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8180)
    parser.add_argument("--phase", choices=("before", "after"), required=True)
    parser.add_argument("--state", required=True)
    args = parser.parse_args()
    from pathlib import Path

    base = f"http://127.0.0.1:{args.port}"
    state = Path(args.state)
    if args.phase == "before":
        assert not active(base)
        core_chat(base)
        conversation = call(base, "/api/chat/conversations", "POST", {
            "title": "Synthetic lifecycle smoke",
            "model_alias": "yonder-chat",
        })["conversation"]
        conversation_id = conversation["id"]
        call(base, f"/api/chat/conversations/{conversation_id}/messages", "POST", {
            "role": "user", "content": "Preserve this synthetic message.",
        })
        state.write_text(conversation_id, encoding="utf-8")
        call(base, "/api/admin/addons/workspaces/install", "POST", expected=400)
        call(base, "/api/admin/addons/security-insights/install", "POST")
        assert active(base) == {"security-insights"}
        call(base, "/api/admin/addons/security-insights/report")
        core_chat(base)
    else:
        conversation_id = state.read_text(encoding="utf-8")
        assert active(base) == {"security-insights"}
        call(base, "/api/admin/addons/security-insights/report")
        call(base, "/api/admin/addons/security-insights/uninstall", "POST")
        assert not active(base)
        call(base, "/api/admin/addons/security-insights/report", expected=400)
        conversation = call(
            base, f"/api/chat/conversations/{conversation_id}"
        )["conversation"]
        assert conversation["title"] == "Synthetic lifecycle smoke"
        assert any(
            msg["content"] == "Preserve this synthetic message."
            for msg in conversation["messages"]
        )
        core_chat(base)
    print(f"Add-on lifecycle {args.phase}: mock transport fixture, no real inference claim")


if __name__ == "__main__":
    main()
