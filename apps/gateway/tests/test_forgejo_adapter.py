"""ForgejoAdapter tests — fully mocked, no live Forgejo server.

Covers fail-closed behaviour, token resolution from a secret reference, and
the REST paths for ensure_repo_exists / open_pull_request using
httpx.MockTransport. Asserts the token never appears in any returned dict.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.services.git_adapter import (
    ForgejoAdapter,
    GitAdapterError,
    _inject_token,
    _strip_userinfo,
)

BASE_URL = "http://127.0.0.1:3001"
TOKEN_ENV = "YONDER_FORGEJO_TOKEN"
TOKEN_VALUE = "super-secret-forgejo-token-value"  # noqa: S105 (test fixture)


def _mocked(
    handler: Any, *, base_url: str = BASE_URL, token_ref: str = TOKEN_ENV
) -> ForgejoAdapter:
    """ForgejoAdapter whose REST client uses a MockTransport handler."""
    adapter = ForgejoAdapter(base_url=base_url, token_ref=token_ref)
    transport = httpx.MockTransport(handler)
    adapter._make_client = lambda: httpx.Client(  # type: ignore[method-assign]
        base_url=base_url, transport=transport, follow_redirects=False
    )
    return adapter


def _no_calls(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected network call: {request.method} {request.url}")


# --------------------------------------------------------------------------
# fail-closed: unconfigured / missing secret
# --------------------------------------------------------------------------


def test_is_configured_false_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    assert ForgejoAdapter().is_configured() is False
    assert ForgejoAdapter(base_url=BASE_URL).is_configured() is False
    assert ForgejoAdapter(base_url=BASE_URL, token_ref=TOKEN_ENV).is_configured() is False


def test_is_configured_true_when_secret_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN_VALUE)
    assert ForgejoAdapter(base_url=BASE_URL, token_ref=TOKEN_ENV).is_configured() is True


def test_empty_base_url_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN_VALUE)
    adapter = _mocked(_no_calls, base_url="")
    with pytest.raises(GitAdapterError) as exc:
        adapter.ensure_repo_exists(owner="yonder-lab", repo="tooling")
    assert exc.value.code == "forgejo_not_configured"


def test_empty_token_ref_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN_VALUE)
    adapter = _mocked(_no_calls, token_ref="")
    with pytest.raises(GitAdapterError) as exc:
        adapter.open_pull_request(
            owner="o", repo="r", title="t", head="feat", base="main"
        )
    assert exc.value.code == "forgejo_not_configured"


def test_missing_env_var_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    adapter = _mocked(_no_calls)
    for call in (
        lambda: adapter.ensure_repo_exists(owner="o", repo="r"),
        lambda: adapter.open_pull_request(owner="o", repo="r", title="t", head="h"),
    ):
        with pytest.raises(GitAdapterError) as exc:
            call()
        assert exc.value.code == "forgejo_not_configured"


def test_empty_env_var_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, "   ")  # whitespace-only counts as empty
    adapter = _mocked(_no_calls)
    with pytest.raises(GitAdapterError) as exc:
        adapter.ensure_repo_exists(owner="o", repo="r")
    assert exc.value.code == "forgejo_not_configured"


def test_error_never_contains_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A not-configured error must report the ref NAME, not any token value."""
    monkeypatch.setenv(TOKEN_ENV, "")
    adapter = _mocked(_no_calls)
    with pytest.raises(GitAdapterError) as exc:
        adapter.ensure_repo_exists(owner="o", repo="r")
    assert exc.value.details.get("token_ref") == TOKEN_ENV
    assert TOKEN_VALUE not in str(exc.value.details)


# --------------------------------------------------------------------------
# token resolution
# --------------------------------------------------------------------------


def test_token_resolved_from_env_and_sent_as_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN_VALUE)
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(200, json={"full_name": "yonder-lab/tooling"})

    adapter = _mocked(handler)
    result = adapter.ensure_repo_exists(owner="yonder-lab", repo="tooling")
    assert seen["auth"] == f"token {TOKEN_VALUE}"
    assert result["exists"] is True


# --------------------------------------------------------------------------
# ensure_repo_exists
# --------------------------------------------------------------------------


def test_ensure_repo_exists_already_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN_VALUE)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/repos/yonder-lab/tooling"
        return httpx.Response(200, json={"full_name": "yonder-lab/tooling"})

    result = _mocked(handler).ensure_repo_exists(owner="yonder-lab", repo="tooling")
    assert result == {
        "exists": True,
        "created": False,
        "full_name": "yonder-lab/tooling",
    }


def test_ensure_repo_exists_404_then_create(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN_VALUE)
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(404, json={"message": "Not Found"})
        assert request.method == "POST"
        assert request.url.path == "/api/v1/user/repos"
        return httpx.Response(201, json={"full_name": "yonder-lab/tooling"})

    result = _mocked(handler).ensure_repo_exists(owner="yonder-lab", repo="tooling")
    assert result == {
        "exists": True,
        "created": True,
        "full_name": "yonder-lab/tooling",
    }
    assert calls == [
        ("GET", "/api/v1/repos/yonder-lab/tooling"),
        ("POST", "/api/v1/user/repos"),
    ]


def test_ensure_repo_exists_non_404_error_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN_VALUE)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(GitAdapterError) as exc:
        _mocked(handler).ensure_repo_exists(owner="o", repo="r")
    assert exc.value.code == "forgejo_api_error"
    assert exc.value.details["status"] == 500


def test_httpx_error_maps_to_forgejo_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN_VALUE)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(GitAdapterError) as exc:
        _mocked(handler).ensure_repo_exists(owner="o", repo="r")
    assert exc.value.code == "forgejo_http_error"
    assert TOKEN_VALUE not in str(exc.value.details)


# --------------------------------------------------------------------------
# open_pull_request
# --------------------------------------------------------------------------


def test_open_pull_request_created(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN_VALUE)
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/repos/yonder-lab/tooling/pulls"
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "number": 42,
                "html_url": "http://127.0.0.1:3001/yonder-lab/tooling/pulls/42",
                "state": "open",
            },
        )

    result = _mocked(handler).open_pull_request(
        owner="yonder-lab",
        repo="tooling",
        title="Fix add()",
        head="coder/fix-add",
        base="main",
        body="Automated fix",
    )
    assert result == {
        "number": 42,
        "url": "http://127.0.0.1:3001/yonder-lab/tooling/pulls/42",
        "state": "open",
    }
    assert captured["body"] == {
        "title": "Fix add()",
        "head": "coder/fix-add",
        "base": "main",
        "body": "Automated fix",
    }


def test_open_pull_request_already_exists_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN_VALUE)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"message": "pull request already exists"})

    result = _mocked(handler).open_pull_request(
        owner="o", repo="r", title="t", head="h", base="main"
    )
    assert result["already_exists"] is True
    assert result["state"] == "already_exists"
    assert result["number"] is None


def test_open_pull_request_server_error_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN_VALUE)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="validation failed")

    with pytest.raises(GitAdapterError) as exc:
        _mocked(handler).open_pull_request(owner="o", repo="r", title="t", head="h")
    assert exc.value.code == "forgejo_api_error"
    assert exc.value.details["status"] == 422


# --------------------------------------------------------------------------
# token never leaks into returned dicts
# --------------------------------------------------------------------------


def test_token_not_in_any_returned_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN_VALUE)

    def repo_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"full_name": "o/r"})

    def pr_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201, json={"number": 1, "html_url": "http://127.0.0.1:3001/o/r/pulls/1"}
        )

    repo_result = _mocked(repo_handler).ensure_repo_exists(owner="o", repo="r")
    pr_result = _mocked(pr_handler).open_pull_request(
        owner="o", repo="r", title="t", head="h"
    )
    assert TOKEN_VALUE not in str(repo_result)
    assert TOKEN_VALUE not in str(pr_result)


# --------------------------------------------------------------------------
# URL token helpers (used by push/clone to keep the token off disk)
# --------------------------------------------------------------------------


def test_inject_and_strip_userinfo_roundtrip() -> None:
    url = "http://127.0.0.1:3001/yonder-lab/tooling.git"
    injected = _inject_token(url, TOKEN_VALUE)
    assert injected == (
        f"http://oauth2:{TOKEN_VALUE}@127.0.0.1:3001/yonder-lab/tooling.git"
    )
    # Stripping removes credentials again — nothing left on disk.
    assert _strip_userinfo(injected) == url
    assert TOKEN_VALUE not in _strip_userinfo(injected)


def test_push_host_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """push must refuse to send the token to a host other than base_url."""
    import subprocess

    monkeypatch.setenv(TOKEN_ENV, TOKEN_VALUE)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "http://evil.example/o/r.git"],
        cwd=repo,
        check=True,
    )
    adapter = ForgejoAdapter(base_url=BASE_URL, token_ref=TOKEN_ENV)
    with pytest.raises(GitAdapterError) as exc:
        adapter.push(repo, branch="main")
    assert exc.value.code == "forgejo_remote_mismatch"
    # The token must not have been written into .git/config.
    assert TOKEN_VALUE not in (repo / ".git" / "config").read_text()
