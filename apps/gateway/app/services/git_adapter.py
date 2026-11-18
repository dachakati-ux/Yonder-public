"""Git adapters for coder workspaces — local now, Forgejo later."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx


class GitAdapterError(Exception):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class GitAdapter(Protocol):
    def clone(self, source: str, dest: Path, *, ref: str | None = None) -> dict[str, Any]: ...

    def status(self, repo: Path) -> dict[str, Any]: ...

    def diff(self, repo: Path, *, staged: bool = False) -> str: ...

    def create_branch(self, repo: Path, name: str) -> None: ...

    def commit_all(self, repo: Path, message: str) -> str: ...

    def push(self, repo: Path, *, remote: str = "origin", branch: str) -> dict[str, Any]: ...

    def ensure_local_remote(self, repo: Path, bare_root: Path) -> str: ...


@dataclass
class LocalGitAdapter:
    """Clone/link local paths and push to a local bare remote. No network remotes."""

    git_bin: str = "git"

    def clone(self, source: str, dest: Path, *, ref: str | None = None) -> dict[str, Any]:
        src = _resolve_local_source(source)
        if dest.exists():
            raise GitAdapterError("dest_exists", f"Clone destination already exists: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if (src / ".git").exists() or (src / ".git").is_file():
            cmd = [self.git_bin, "clone", "--local", str(src), str(dest)]
            if ref:
                cmd = [self.git_bin, "clone", "--local", "--branch", ref, str(src), str(dest)]
            _run(cmd)
        else:
            # Non-git directory: copy tree then init so later commit/push works.
            shutil.copytree(src, dest, dirs_exist_ok=False)
            _safe_git_init(dest)
            _run([self.git_bin, "add", "-A"], cwd=dest)
            _run(
                [
                    self.git_bin,
                    "-c",
                    "user.email=coder@yonder.local",
                    "-c",
                    "user.name=Yonder Coder",
                    "commit",
                    "-m",
                    "Initial import from local path",
                    "--allow-empty",
                ],
                cwd=dest,
            )
            if ref:
                try:
                    _run([self.git_bin, "checkout", "-B", ref], cwd=dest)
                except GitAdapterError:
                    pass
        head = _run([self.git_bin, "rev-parse", "HEAD"], cwd=dest).stdout.strip()
        return {
            "adapter": "local",
            "source": str(src),
            "dest": str(dest),
            "ref": ref,
            "head": head,
            "availability": "local",
        }

    def status(self, repo: Path) -> dict[str, Any]:
        _require_repo(repo)
        out = _run([self.git_bin, "status", "--porcelain=v1", "-b"], cwd=repo)
        head = _run([self.git_bin, "rev-parse", "HEAD"], cwd=repo).stdout.strip()
        branch = _run(
            [self.git_bin, "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo
        ).stdout.strip()
        return {
            "adapter": "local",
            "head": head,
            "branch": branch,
            "porcelain": out.stdout,
            "dirty": bool(out.stdout.strip().splitlines()[1:] if out.stdout.strip() else False)
            or bool(
                [
                    line
                    for line in out.stdout.splitlines()
                    if line and not line.startswith("##")
                ]
            ),
        }

    def diff(self, repo: Path, *, staged: bool = False) -> str:
        _require_repo(repo)
        cmd = [self.git_bin, "diff", "--cached"] if staged else [self.git_bin, "diff"]
        return _run(cmd, cwd=repo).stdout

    def apply_patch(self, repo: Path, diff: str) -> dict[str, Any]:
        """Apply a unified diff with git apply --3way. Fails closed on conflict."""
        _require_repo(repo)
        result = _run(
            [self.git_bin, "apply", "--3way", "--whitespace=nowarn", "-"],
            cwd=repo,
            input_text=diff if diff.endswith("\n") else diff + "\n",
        )
        # --3way may stage applied hunks; diff against HEAD sees both cases.
        changed = _run(
            [self.git_bin, "diff", "HEAD", "--name-only"], cwd=repo
        ).stdout.split()
        return {
            "adapter": "local",
            "applied": True,
            "changed_files": changed,
            "stderr": result.stderr[-2000:],
        }

    def checkpoint(self, repo: Path, message: str) -> str:
        """Commit all current changes as a checkpoint; returns HEAD sha.

        No-op (returns current HEAD) when the tree is clean.
        """
        return self.commit_all(repo, message)

    def reset_hard(self, repo: Path, ref: str) -> str:
        """Roll the working tree back to a checkpoint. Returns new HEAD sha."""
        _require_repo(repo)
        _run([self.git_bin, "reset", "--hard", ref], cwd=repo)
        _run([self.git_bin, "clean", "-fd"], cwd=repo)
        return _run([self.git_bin, "rev-parse", "HEAD"], cwd=repo).stdout.strip()

    def head(self, repo: Path) -> str:
        _require_repo(repo)
        return _run([self.git_bin, "rev-parse", "HEAD"], cwd=repo).stdout.strip()

    def create_branch(self, repo: Path, name: str) -> None:
        _require_repo(repo)
        _run([self.git_bin, "checkout", "-B", name], cwd=repo)

    def commit_all(self, repo: Path, message: str) -> str:
        _require_repo(repo)
        _run([self.git_bin, "add", "-A"], cwd=repo)
        # Empty commit is not useful for push gate — fail closed if nothing to commit.
        porcelain = _run([self.git_bin, "status", "--porcelain"], cwd=repo).stdout.strip()
        if not porcelain:
            return _run([self.git_bin, "rev-parse", "HEAD"], cwd=repo).stdout.strip()
        _run(
            [
                self.git_bin,
                "-c",
                "user.email=coder@yonder.local",
                "-c",
                "user.name=Yonder Coder",
                "commit",
                "-m",
                message,
            ],
            cwd=repo,
        )
        return _run([self.git_bin, "rev-parse", "HEAD"], cwd=repo).stdout.strip()

    def push(self, repo: Path, *, remote: str = "origin", branch: str) -> dict[str, Any]:
        _require_repo(repo)
        result = _run([self.git_bin, "push", "-u", remote, branch], cwd=repo)
        head = _run([self.git_bin, "rev-parse", "HEAD"], cwd=repo).stdout.strip()
        return {
            "adapter": "local",
            "remote": remote,
            "branch": branch,
            "head": head,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "availability": "local",
        }

    def ensure_repo(self, repo: Path) -> None:
        """Initialize a git repo if missing (for cage workspaces)."""
        repo.mkdir(parents=True, exist_ok=True)
        if (repo / ".git").exists():
            return
        _safe_git_init(repo)
        _run([self.git_bin, "add", "-A"], cwd=repo)
        _run(
            [
                self.git_bin,
                "-c",
                "user.email=coder@yonder.local",
                "-c",
                "user.name=Yonder Coder",
                "commit",
                "-m",
                "Initial cage snapshot",
                "--allow-empty",
            ],
            cwd=repo,
        )

    def ensure_local_remote(self, repo: Path, bare_root: Path) -> str:
        """Create a bare mirror under bare_root and set remote origin if missing."""
        _require_repo(repo)
        bare_root.mkdir(parents=True, exist_ok=True)
        bare = bare_root / f"{repo.name}.git"
        if not bare.exists():
            _safe_git_init(bare, bare=True)
        remotes = _run([self.git_bin, "remote"], cwd=repo).stdout.split()
        if "origin" not in remotes:
            _run([self.git_bin, "remote", "add", "origin", str(bare)], cwd=repo)
        else:
            _run([self.git_bin, "remote", "set-url", "origin", str(bare)], cwd=repo)
        return str(bare)


@dataclass
class ForgejoAdapter:
    """Push a branch and open a PR against a loopback-only Forgejo forge.

    Fail-closed: every network method raises ``forgejo_not_configured`` unless
    ``base_url`` and ``token_ref`` are set AND the referenced environment
    variable holds a non-empty token. ``token_ref`` is the *name* of an env var
    (a secret reference), never the token itself. The token is never logged,
    echoed, returned, or left on disk in ``.git/config``.
    """

    base_url: str = ""
    token_ref: str = ""
    timeout_seconds: float = 30.0
    _local: LocalGitAdapter = field(default_factory=LocalGitAdapter, repr=False)

    # ---- configuration / token resolution ------------------------------

    def is_configured(self) -> bool:
        """True only if base_url, token_ref, and the referenced secret exist."""
        if not self.base_url or not self.token_ref:
            return False
        return bool(os.environ.get(self.token_ref, "").strip())

    def _resolve_token(self) -> str:
        """Resolve the API token from the env var named by ``token_ref``.

        Fail closed with ``forgejo_not_configured`` if anything is missing.
        The token value is never included in the raised error.
        """
        if not self.base_url or not self.token_ref:
            raise GitAdapterError(
                "forgejo_not_configured",
                "Forgejo adapter is not configured (base_url/token_ref empty).",
                details={
                    "base_url_set": bool(self.base_url),
                    "token_ref_set": bool(self.token_ref),
                },
            )
        token = os.environ.get(self.token_ref, "").strip()
        if not token:
            raise GitAdapterError(
                "forgejo_not_configured",
                "Forgejo token secret is unset or empty.",
                # Report only the *name* of the reference, never the value.
                details={"token_ref": self.token_ref},
            )
        return token

    def _make_client(self) -> httpx.Client:
        """Build the REST client. Tests monkeypatch this to inject a transport."""
        return httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            follow_redirects=False,
        )

    # ---- Forgejo REST API ----------------------------------------------

    def ensure_repo_exists(self, *, owner: str, repo: str) -> dict[str, Any]:
        """Ensure ``owner/repo`` exists on Forgejo, creating it if absent.

        GET /api/v1/repos/{owner}/{repo}; on 404 create via POST
        /api/v1/user/repos. Returns {exists, created, full_name}.
        """
        token = self._resolve_token()
        full_name = f"{owner}/{repo}"
        with self._make_client() as client:
            resp = self._request(
                client, "GET", f"/api/v1/repos/{owner}/{repo}", token=token
            )
            if resp.status_code == 200:
                data = _safe_json(resp)
                return {
                    "exists": True,
                    "created": False,
                    "full_name": data.get("full_name", full_name),
                }
            if resp.status_code != 404:
                self._raise_http(resp, token)
            # Not found → create under the authenticated user.
            created = self._request(
                client,
                "POST",
                "/api/v1/user/repos",
                token=token,
                json={"name": repo, "private": True, "auto_init": False},
            )
            if created.status_code not in (200, 201):
                self._raise_http(created, token)
            data = _safe_json(created)
            return {
                "exists": True,
                "created": True,
                "full_name": data.get("full_name", full_name),
            }

    def open_pull_request(
        self,
        *,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str = "main",
        body: str = "",
    ) -> dict[str, Any]:
        """Open a PR (POST /api/v1/repos/{owner}/{repo}/pulls).

        Returns {number, url, state}. If Forgejo reports the PR already exists
        (HTTP 409) this returns a non-error dict noting it rather than raising.
        """
        token = self._resolve_token()
        payload = {"title": title, "head": head, "base": base, "body": body}
        with self._make_client() as client:
            resp = self._request(
                client,
                "POST",
                f"/api/v1/repos/{owner}/{repo}/pulls",
                token=token,
                json=payload,
            )
        if resp.status_code in (200, 201):
            data = _safe_json(resp)
            return {
                "number": data.get("number"),
                "url": data.get("html_url") or data.get("url"),
                "state": data.get("state", "open"),
            }
        if resp.status_code == 409:
            # PR already exists for this head/base — not an error, report it.
            return {
                "number": None,
                "url": None,
                "state": "already_exists",
                "already_exists": True,
                "detail": _scrub(resp.text[:500], token),
            }
        self._raise_http(resp, token)

    def _request(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        *,
        token: str,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        headers = {"Authorization": f"token {token}", "Accept": "application/json"}
        try:
            return client.request(method, path, headers=headers, json=json)
        except httpx.HTTPError as exc:
            raise GitAdapterError(
                "forgejo_http_error",
                "Forgejo HTTP request failed.",
                # str(exc) never contains the token (it lives in a header).
                details={"path": path, "error": _scrub(str(exc), token)[:200]},
            ) from exc

    def _raise_http(self, resp: httpx.Response, token: str) -> None:
        raise GitAdapterError(
            "forgejo_api_error",
            f"Forgejo API returned HTTP {resp.status_code}.",
            details={"status": resp.status_code, "body": _scrub(resp.text[:500], token)},
        )

    # ---- git push over HTTP with token scrubbed off disk ----------------

    def push(self, repo: Path, *, remote: str = "origin", branch: str) -> dict[str, Any]:
        """Push ``branch`` to the Forgejo ``remote`` over authenticated HTTP.

        Temporarily rewrites the remote URL to embed the token, pushes, then
        immediately resets the remote to a tokenless URL so no credential is
        left in ``.git/config``. The token never appears in the return value or
        in any raised error.
        """
        _require_repo(repo)
        token = self._resolve_token()
        current = _run(
            ["git", "remote", "get-url", remote], cwd=repo
        ).stdout.strip()
        remote_host = _host_of(current)
        expected_host = _host_of(self.base_url)
        if remote_host is None or remote_host != expected_host:
            raise GitAdapterError(
                "forgejo_remote_mismatch",
                "Remote URL host does not match the configured Forgejo base_url.",
                details={"remote_host": remote_host, "expected_host": expected_host},
            )
        tokenless = _strip_userinfo(current)
        auth_url = _inject_token(current, token)
        try:
            try:
                _run(["git", "remote", "set-url", remote, auth_url], cwd=repo)
                _run(["git", "push", "-u", remote, branch], cwd=repo)
                head = _run(
                    ["git", "rev-parse", "HEAD"], cwd=repo
                ).stdout.strip()
            finally:
                # Always scrub the token back off disk, even on failure.
                try:
                    _run(["git", "remote", "set-url", remote, tokenless], cwd=repo)
                except GitAdapterError:
                    pass
        except GitAdapterError as exc:
            # Never let the token leak through git's cmd/stdout/stderr details.
            raise _scrub_error(exc, token) from None
        return {
            "adapter": "forgejo",
            "branch": branch,
            "head": head,
            "remote_host": remote_host,
        }

    # ---- local git mechanics: delegate to LocalGitAdapter ---------------

    def status(self, repo: Path) -> dict[str, Any]:
        return self._local.status(repo)

    def diff(self, repo: Path, *, staged: bool = False) -> str:
        return self._local.diff(repo, staged=staged)

    def create_branch(self, repo: Path, name: str) -> None:
        self._local.create_branch(repo, name)

    def commit_all(self, repo: Path, message: str) -> str:
        return self._local.commit_all(repo, message)

    # ---- operations that require live Forgejo config --------------------

    def clone(self, source: str, dest: Path, *, ref: str | None = None) -> dict[str, Any]:
        # Cloning a Forgejo repo is a network operation; fail closed until
        # configured. (Local-path clones belong to LocalGitAdapter.)
        if not self.is_configured():
            raise GitAdapterError(
                "forgejo_not_configured",
                "Forgejo adapter is not configured; cannot clone remote repos.",
                details={"base_url_set": bool(self.base_url), "ref": ref},
            )
        token = self._resolve_token()
        if dest.exists():
            raise GitAdapterError("dest_exists", f"Clone destination already exists: {dest}")
        if _host_of(source) != _host_of(self.base_url):
            raise GitAdapterError(
                "forgejo_remote_mismatch",
                "Clone source host does not match the configured Forgejo base_url.",
                details={"source_host": _host_of(source), "expected_host": _host_of(self.base_url)},
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        auth_url = _inject_token(source, token)
        try:
            cmd = ["git", "clone", auth_url, str(dest)]
            if ref:
                cmd = ["git", "clone", "--branch", ref, auth_url, str(dest)]
            _run(cmd)
            # Scrub the token out of the freshly cloned repo's origin URL.
            try:
                _run(
                    ["git", "remote", "set-url", "origin", _strip_userinfo(source)],
                    cwd=dest,
                )
            except GitAdapterError:
                pass
            head = _run(["git", "rev-parse", "HEAD"], cwd=dest).stdout.strip()
        except GitAdapterError as exc:
            raise _scrub_error(exc, token) from None
        return {
            "adapter": "forgejo",
            "dest": str(dest),
            "ref": ref,
            "head": head,
            "remote_host": _host_of(source),
        }

    def ensure_local_remote(self, repo: Path, bare_root: Path) -> str:
        # Forgejo uses an HTTP remote, not a local bare mirror.
        raise GitAdapterError(
            "forgejo_not_configured",
            "Forgejo adapter uses an HTTP remote; local bare remotes are not used.",
        )


def _host_of(url: str) -> str | None:
    """Return 'host[:port]' for an http(s) URL, or None if not http(s)."""
    parts = urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname
    return f"{host}:{parts.port}" if parts.port else host


def _strip_userinfo(url: str) -> str:
    """Return the URL with any userinfo (credentials) removed from the netloc."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _inject_token(url: str, token: str) -> str:
    """Return the URL with ``oauth2:<token>@`` userinfo injected into netloc."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    hostport = f"{host}:{parts.port}" if parts.port else host
    netloc = f"oauth2:{token}@{hostport}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _scrub(text: str, token: str) -> str:
    """Redact the token from arbitrary text (defensive; never trust echoes)."""
    if not token or not text:
        return text
    return text.replace(token, "***")


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _scrub_error(exc: GitAdapterError, token: str) -> GitAdapterError:
    """Return a copy of a GitAdapterError with the token redacted everywhere."""
    if not token:
        return exc

    def clean(value: Any) -> Any:
        if isinstance(value, str):
            return value.replace(token, "***")
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        return value

    return GitAdapterError(exc.code, clean(exc.message), details=clean(exc.details))


def _resolve_local_source(source: str) -> Path:
    raw = (source or "").strip()
    if not raw:
        raise GitAdapterError("source_required", "Local clone source path is required.")
    if raw.startswith("file://"):
        raw = raw[len("file://") :]
    if "://" in raw and not raw.startswith("file://"):
        raise GitAdapterError(
            "remote_not_allowed",
            "Only local filesystem paths are allowed until Forgejo is configured.",
            details={"source": raw},
        )
    path = Path(raw).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise GitAdapterError("source_not_found", "Local source must be an existing directory.")
    return path


def _safe_git_init(repo: Path, *, bare: bool = False) -> None:
    """Create a minimal git repo without template hooks (sandbox / NFS safe)."""
    if bare:
        git_dir = repo
        repo.mkdir(parents=True, exist_ok=True)
        (git_dir / "objects").mkdir(exist_ok=True)
        (git_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (git_dir / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = true\n",
            encoding="utf-8",
        )
        return
    git_dir = repo / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)
    (git_dir / "objects").mkdir(exist_ok=True)
    (git_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n",
        encoding="utf-8",
    )


def _require_repo(repo: Path) -> None:
    if not repo.exists() or not (repo / ".git").exists() and not (repo / ".git").is_file():
        # .git can be a file for worktrees; still require something.
        git_dir = repo / ".git"
        if not git_dir.exists():
            raise GitAdapterError("not_a_git_repo", f"Not a git repository: {repo}")


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        **dict(**{k: v for k, v in __import__("os").environ.items()}),
        # Avoid template hooks (can fail in restricted sandboxes / NFS).
        "GIT_TEMPLATE_DIR": "",
    }
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            env=env,
            input=input_text,
        )
    except FileNotFoundError as exc:
        raise GitAdapterError("git_not_found", "git binary not found on Gateway host.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitAdapterError("git_timeout", "git command timed out.") from exc
    if completed.returncode != 0:
        raise GitAdapterError(
            "git_failed",
            "git command failed.",
            details={
                "cmd": cmd,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-2000:],
                "stderr": completed.stderr[-2000:],
            },
        )
    return completed
