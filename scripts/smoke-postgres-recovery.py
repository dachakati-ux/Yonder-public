#!/usr/bin/env python3
"""Synthetic recovery fixture for the disposable PostgreSQL workflow only."""

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen


def call(port, path, method="GET", body=None):
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        method=method,
        headers={"Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None,
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8180)
    parser.add_argument("--phase", choices=("seed", "verify"), required=True)
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args()
    port = args.port
    if args.phase == "seed":
        convo = call(port, "/api/chat/conversations", "POST", {
            "title": "Synthetic PostgreSQL recovery fixture",
            "model_alias": "yonder-chat",
        })["conversation"]
        path = f"/api/chat/conversations/{convo['id']}/messages"
        msg = call(port, path, "POST", {
            "role": "user",
            "content": "Pre-backup synthetic message",
        })["message"]
        args.state.write_text(json.dumps({"id": convo["id"], "message": msg["id"]}))
    else:
        fixture = json.loads(args.state.read_text())
        path = f"/api/chat/conversations/{fixture['id']}"
        restored = call(port, path)["conversation"]
        assert restored["title"] == "Synthetic PostgreSQL recovery fixture"
        assert restored["model_alias"] == "yonder-chat"
        assert any(
            message["id"] == fixture["message"]
            and message["content"] == "Pre-backup synthetic message"
            for message in restored["messages"]
        )
        call(port, path + "/messages", "POST", {
            "role": "user", "content": "Post-restore synthetic message",
        })
        after = call(port, path)["conversation"]
        assert len(after["messages"]) == 2
        print("Restored conversation preserved and new write succeeded.")


if __name__ == "__main__":
    main()
