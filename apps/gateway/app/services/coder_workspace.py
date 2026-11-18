"""Coder workspace path jail — read tools, optional write/check, Gateway-local."""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.git_adapter import GitAdapterError, LocalGitAdapter
from app.services.secret_scan import redact_secrets

SECRET_NAME_RE = re.compile(
    r"(^|/)(\.env(\..*)?|.*\.(pem|key|p12|pfx|tfvars|token)|id_rsa|id_ed25519|"
    r"credentials\.json|service.?account.*\.json|\.aws/|\.ssh/|\.npmrc|\.netrc|"
    r"secrets?\.(ya?ml|json)|\.git/config)(/|$)",
    re.IGNORECASE,
)

DEFAULT_CHECK_ALLOWLIST = (
    "pytest",
    "python -m pytest",
    "python3 -m pytest",
    "npm test",
    "npm run lint",
    "npm run build",
    "ruff",
    "ruff check",
    "mypy",
)


class CoderError(Exception):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass
class WorkspaceGrant:
    id: str
    subject: str
    kind: str  # cage | grant | clone
    root: Path
    created_at: str
    label: str = ""
    source: str = ""
    git_head: str = ""
    remote_url: str = ""

    def to_public(self, *, write_unlock: bool = False) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "root_display": str(self.root),
            "label": self.label,
            "created_at": self.created_at,
            "read_only": not write_unlock,
            "source": self.source or None,
            "git_head": self.git_head or None,
            "remote_url": self.remote_url or None,
        }


@dataclass
class CoderWorkspaceService:
    """Principal-scoped workspace grants with path jail + tools."""

    cage_root: Path
    grantable_roots: list[Path]
    max_read_bytes: int = 262_144
    max_list_entries: int = 200
    max_search_hits: int = 50
    max_search_file_bytes: int = 1_048_576
    write_unlock: bool = False
    check_allowlist: list[str] = field(default_factory=lambda: list(DEFAULT_CHECK_ALLOWLIST))
    check_timeout_seconds: int = 120
    check_isolation: str = "none"
    check_isolation_image: str = "registry.access.redhat.com/ubi9/python-312"
    git: LocalGitAdapter = field(default_factory=LocalGitAdapter)
    audit: Callable[[dict[str, Any]], None] | None = None
    on_workspace_changed: Callable[[WorkspaceGrant | None, str | None], None] | None = None
    _workspaces: dict[str, WorkspaceGrant] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cage_root = self.cage_root.expanduser().resolve()
        self.cage_root.mkdir(parents=True, exist_ok=True)
        self.grantable_roots = [p.expanduser().resolve() for p in self.grantable_roots]
        if self.cage_root not in self.grantable_roots:
            self.grantable_roots.insert(0, self.cage_root)
        self.check_allowlist = [str(x).strip() for x in self.check_allowlist if str(x).strip()]

    def status(self) -> dict[str, Any]:
        phase = "a_assistant"
        if self.write_unlock:
            phase = "f_write"
        elif self._workspaces:
            kinds = {w.kind for w in self._workspaces.values()}
            if "clone" in kinds:
                phase = "d_git"
            elif "grant" in kinds:
                phase = "c_human_path"
            else:
                phase = "b_sandbox_read"
        tools = ["coder_list_dir", "coder_read_file", "coder_search"]
        if self.write_unlock:
            tools.extend(["coder_apply_patch", "coder_write_file", "coder_run_check"])
        trust = "write_unlocked" if self.write_unlock else "read_only"
        return {
            "enabled": True,
            "phase": phase,
            "alias": "yonder-coder",
            "trust_ceiling": trust,
            "write_unlock": bool(self.write_unlock),
            "tools": tools,
            "check_isolation": self.check_isolation,
            "check_allowlist": list(self.check_allowlist),
            "cage_root": str(self.cage_root),
            "grantable_roots": [str(p) for p in self.grantable_roots],
            "pipeline_stages": [
                "intake",
                "explore",
                "plan",
                "implement",
                "check",
                "dual_verify",
                "push",
            ],
            "note": (
                "Gateway-only coder. Path jail rejects escape and secrets. "
                "Write/exec unlock is explicit and currently "
                f"{'enabled' if self.write_unlock else 'disabled'}. "
                "Push requires dual-agent approve. Forgejo is not configured yet."
            ),
        }

    def list_workspaces(self, subject: str) -> list[dict[str, Any]]:
        return [
            w.to_public(write_unlock=self.write_unlock)
            for w in self._workspaces.values()
            if w.subject == subject
        ]

    def restore_workspace(self, grant: WorkspaceGrant) -> None:
        """Rehydrate a grant into the in-process map (durable store bootstrap)."""
        self._workspaces[grant.id] = grant

    def create_cage(self, subject: str, *, label: str = "") -> dict[str, Any]:
        wid = f"cage-{uuid.uuid4().hex[:12]}"
        root = (self.cage_root / subject.replace("/", "_") / wid).resolve()
        root.mkdir(parents=True, exist_ok=False)
        (root / "README.md").write_text(
            "# Yonder coder cage\n\nRead-only sandbox. Paste or mirror files here for review.\n",
            encoding="utf-8",
        )
        (root / "samples" / "bug.py").parent.mkdir(parents=True, exist_ok=True)
        (root / "samples" / "bug.py").write_text(
            "def add(a, b):\n    # BUG: subtracts instead of adds\n    return a - b\n",
            encoding="utf-8",
        )
        grant = WorkspaceGrant(
            id=wid,
            subject=subject,
            kind="cage",
            root=root,
            created_at=_utcnow(),
            label=label or "cage",
        )
        self._workspaces[wid] = grant
        self._notify_workspace(grant, None)
        self._audit(
            {
                "event_type": "coder.workspace.create",
                "resource_type": "coder_workspace",
                "resource_id": wid,
                "principal_subject": subject,
                "result": "ok",
                "metadata": {"kind": "cage", "root": str(root)},
            }
        )
        return grant.to_public(write_unlock=self.write_unlock)

    def grant_path(self, subject: str, raw_path: str, *, label: str = "") -> dict[str, Any]:
        try:
            path = Path(raw_path).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise CoderError("path_not_found", f"Cannot resolve grant path: {exc}") from exc
        try:
            exists = path.exists()
            is_dir = path.is_dir() if exists else False
        except PermissionError as exc:
            raise CoderError(
                "path_not_readable",
                "Grant path is not readable by the Gateway process.",
                details={"path": str(path)},
            ) from exc
        if not exists or not is_dir:
            raise CoderError("path_not_found", "Grant path must be an existing directory.")
        if not any(path == root or path.is_relative_to(root) for root in self.grantable_roots):
            raise CoderError(
                "path_not_grantable",
                "Path is outside grantable roots.",
                details={"grantable_roots": [str(p) for p in self.grantable_roots]},
            )
        wid = f"grant-{uuid.uuid4().hex[:12]}"
        grant = WorkspaceGrant(
            id=wid,
            subject=subject,
            kind="grant",
            root=path,
            created_at=_utcnow(),
            label=label or str(path),
            source=str(path),
        )
        self._workspaces[wid] = grant
        self._notify_workspace(grant, None)
        self._audit(
            {
                "event_type": "coder.workspace.grant",
                "resource_type": "coder_workspace",
                "resource_id": wid,
                "principal_subject": subject,
                "result": "ok",
                "metadata": {"kind": "grant", "root": str(path)},
            }
        )
        return grant.to_public(write_unlock=self.write_unlock)

    def clone_local(
        self,
        subject: str,
        source: str,
        *,
        label: str = "",
        ref: str | None = None,
    ) -> dict[str, Any]:
        """Clone or import a local path into a cage under cage_root."""
        try:
            src = Path(source.replace("file://", "")).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise CoderError("source_not_found", f"Cannot resolve source: {exc}") from exc
        if not src.exists() or not src.is_dir():
            raise CoderError(
                "source_not_found",
                "Local clone source must be an existing directory.",
            )
        if not any(src == root or src.is_relative_to(root) for root in self.grantable_roots):
            # Also allow cloning from grantable + any path that is already under cage_root
            # after explicit grant of parent; for proof, eval roots are grantable.
            raise CoderError(
                "path_not_grantable",
                "Clone source is outside grantable roots.",
                details={"grantable_roots": [str(p) for p in self.grantable_roots]},
            )
        wid = f"clone-{uuid.uuid4().hex[:12]}"
        dest = (self.cage_root / subject.replace("/", "_") / wid).resolve()
        try:
            meta = self.git.clone(str(src), dest, ref=ref)
        except GitAdapterError as exc:
            raise CoderError(exc.code, exc.message, details=exc.details) from exc
        bare_root = self.cage_root / subject.replace("/", "_") / "_remotes"
        try:
            remote = self.git.ensure_local_remote(dest, bare_root)
        except GitAdapterError as exc:
            raise CoderError(exc.code, exc.message, details=exc.details) from exc
        grant = WorkspaceGrant(
            id=wid,
            subject=subject,
            kind="clone",
            root=dest,
            created_at=_utcnow(),
            label=label or src.name,
            source=str(src),
            git_head=str(meta.get("head") or ""),
            remote_url=remote,
        )
        self._workspaces[wid] = grant
        self._notify_workspace(grant, None)
        self._audit(
            {
                "event_type": "coder.workspace.clone",
                "resource_type": "coder_workspace",
                "resource_id": wid,
                "principal_subject": subject,
                "result": "ok",
                "metadata": {
                    "kind": "clone",
                    "source": str(src),
                    "root": str(dest),
                    "head": grant.git_head,
                    "remote": remote,
                },
            }
        )
        return grant.to_public(write_unlock=self.write_unlock)

    def revoke(self, subject: str, workspace_id: str, *, delete_cage_files: bool = True) -> None:
        grant = self._require_workspace(subject, workspace_id)
        del self._workspaces[workspace_id]
        self._notify_workspace(None, workspace_id)
        if grant.kind in {"cage", "clone"} and delete_cage_files:
            shutil.rmtree(grant.root, ignore_errors=True)
        self._audit(
            {
                "event_type": "coder.workspace.revoke",
                "resource_type": "coder_workspace",
                "resource_id": workspace_id,
                "principal_subject": subject,
                "result": "ok",
                "metadata": {"kind": grant.kind},
            }
        )

    def list_dir(self, subject: str, workspace_id: str, rel_path: str = ".") -> dict[str, Any]:
        root = self._require_workspace(subject, workspace_id).root
        target = self._resolve_in_jail(root, rel_path)
        if not target.exists():
            raise CoderError("path_not_found", "Path does not exist in workspace.")
        if not target.is_dir():
            raise CoderError("not_a_directory", "list_dir requires a directory.")
        entries = []
        for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if len(entries) >= self.max_list_entries:
                break
            if _is_secret_path(child.relative_to(root).as_posix()):
                continue
            entries.append(
                {
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else None,
                }
            )
        self._audit_tool(subject, workspace_id, "coder_list_dir", rel_path, "ok")
        return {
            "workspace_id": workspace_id,
            "path": rel_path,
            "entries": entries,
            "truncated": len(entries) >= self.max_list_entries,
            "read_only": not self.write_unlock,
        }

    def read_file(self, subject: str, workspace_id: str, rel_path: str) -> dict[str, Any]:
        root = self._require_workspace(subject, workspace_id).root
        target = self._resolve_in_jail(root, rel_path)
        if _is_secret_path(rel_path) or _is_secret_path(target.relative_to(root).as_posix()):
            raise CoderError("secret_path_denied", "Refusing secret-like path.")
        if not target.exists() or not target.is_file():
            raise CoderError("path_not_found", "File does not exist in workspace.")
        size = target.stat().st_size
        if size > self.max_read_bytes:
            raise CoderError(
                "file_too_large",
                f"File exceeds max_read_bytes ({self.max_read_bytes}).",
                details={"size": size},
            )
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise CoderError("binary_denied", "Refusing non-UTF-8 file.") from exc
        text = self._scan_outbound(subject, workspace_id, text, context=f"read:{rel_path}")
        self._audit_tool(
            subject,
            workspace_id,
            "coder_read_file",
            rel_path,
            "ok",
            metadata={"bytes": len(text.encode("utf-8"))},
        )
        return {
            "workspace_id": workspace_id,
            "path": rel_path,
            "content": text,
            "bytes": len(text.encode("utf-8")),
            "read_only": not self.write_unlock,
        }

    def search(
        self,
        subject: str,
        workspace_id: str,
        query: str,
        rel_path: str = ".",
    ) -> dict[str, Any]:
        q = (query or "").strip()
        if not q:
            raise CoderError("query_required", "Search query is required.")
        root = self._require_workspace(subject, workspace_id).root
        start = self._resolve_in_jail(root, rel_path)
        if not start.exists():
            raise CoderError("path_not_found", "Search path does not exist.")
        hits: list[dict[str, Any]] = []
        files = [start] if start.is_file() else start.rglob("*")
        for path in files:
            if len(hits) >= self.max_search_hits:
                break
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if _is_secret_path(rel):
                continue
            if path.stat().st_size > self.max_search_file_bytes:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if q in line:
                    hits.append(
                        {
                            "path": rel,
                            "line": i,
                            "text": self._scan_outbound(
                                subject,
                                workspace_id,
                                line[:400],
                                context=f"search:{rel}",
                            ),
                        }
                    )
                    if len(hits) >= self.max_search_hits:
                        break
        self._audit_tool(
            subject,
            workspace_id,
            "coder_search",
            rel_path,
            "ok",
            metadata={"query": q, "hits": len(hits)},
        )
        return {
            "workspace_id": workspace_id,
            "query": q,
            "path": rel_path,
            "hits": hits,
            "truncated": len(hits) >= self.max_search_hits,
            "read_only": not self.write_unlock,
        }

    def apply_patch(
        self,
        subject: str,
        workspace_id: str,
        patch: str,
        *,
        allow_write: bool = False,
    ) -> dict[str, Any]:
        """Apply a unified diff via git apply --3way, or a JSON whole-file write.

        JSON form {"path": ..., "content": ...} (or a list of those) is an
        explicit whole-file write. Anything else must be a valid unified diff
        and is applied by git itself — no best-effort reconstruction.
        """
        if not self.write_unlock or not allow_write:
            raise CoderError(
                "write_locked",
                "apply_patch requires write_unlock and an unlocked pipeline stage.",
            )
        root = self._require_workspace(subject, workspace_id).root
        text = (patch or "").replace("\r\n", "\n")
        if not text.strip():
            raise CoderError("patch_required", "Patch content is required.")

        stripped = text.strip()
        applied: list[dict[str, Any]]
        if stripped.startswith("{") or stripped.startswith("["):
            applied = self._apply_json_writes(root, stripped)
        else:
            applied = self._apply_git_diff(root, text)
        self._audit_tool(
            subject,
            workspace_id,
            "coder_apply_patch",
            ".",
            "ok",
            metadata={"files": [a["path"] for a in applied]},
        )
        return {
            "workspace_id": workspace_id,
            "applied": applied,
            "read_only": False,
        }

    def write_file(
        self,
        subject: str,
        workspace_id: str,
        rel_path: str,
        content: str,
        *,
        allow_write: bool = False,
    ) -> dict[str, Any]:
        """Whole-file create/replace inside the jail (write-unlocked stages only)."""
        if not self.write_unlock or not allow_write:
            raise CoderError(
                "write_locked",
                "write_file requires write_unlock and an unlocked pipeline stage.",
            )
        root = self._require_workspace(subject, workspace_id).root
        written = _write_file(root, rel_path, content, self._resolve_in_jail)
        self._audit_tool(
            subject,
            workspace_id,
            "coder_write_file",
            rel_path,
            "ok",
            metadata={"bytes": written["bytes"]},
        )
        return {"workspace_id": workspace_id, "applied": [written], "read_only": False}

    def checkpoint(self, subject: str, workspace_id: str, message: str) -> str:
        """Commit current tree state as a checkpoint; returns HEAD sha."""
        grant = self._require_workspace(subject, workspace_id)
        try:
            if not (grant.root / ".git").exists():
                self.git.ensure_repo(grant.root)
            return self.git.checkpoint(grant.root, message)
        except GitAdapterError as exc:
            raise CoderError(exc.code, exc.message, details=exc.details) from exc

    def rollback(self, subject: str, workspace_id: str, ref: str) -> str:
        """Reset the working tree to a checkpoint sha; returns new HEAD."""
        grant = self._require_workspace(subject, workspace_id)
        try:
            return self.git.reset_hard(grant.root, ref)
        except GitAdapterError as exc:
            raise CoderError(exc.code, exc.message, details=exc.details) from exc

    def _apply_json_writes(self, root: Path, payload_text: str) -> list[dict[str, Any]]:
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise CoderError(
                "patch_invalid",
                "Patch looked like JSON but did not parse.",
            ) from exc
        items: list[dict[str, Any]]
        if isinstance(payload, dict):
            items = [payload]
        elif isinstance(payload, list):
            items = [item for item in payload if isinstance(item, dict)]
        else:
            raise CoderError("patch_invalid", "JSON patch must be an object or list.")
        out: list[dict[str, Any]] = []
        for item in items:
            if "path" not in item or "content" not in item:
                raise CoderError(
                    "patch_invalid", "JSON patch entries need path and content."
                )
            out.append(
                _write_file(root, str(item["path"]), str(item["content"]), self._resolve_in_jail)
            )
        if not out:
            raise CoderError("patch_invalid", "JSON patch contained no file entries.")
        return out

    def _apply_git_diff(self, root: Path, diff_text: str) -> list[dict[str, Any]]:
        for rel in _paths_in_diff(diff_text):
            if _is_secret_path(rel):
                raise CoderError("secret_path_denied", "Refusing to patch secret-like path.")
            # Jail check: resolving raises on escape.
            self._resolve_in_jail(root, rel)
        try:
            if not (root / ".git").exists():
                self.git.ensure_repo(root)
            result = self.git.apply_patch(root, diff_text)
        except GitAdapterError as exc:
            raise CoderError(
                "patch_apply_failed",
                "git apply rejected the diff.",
                details={"code": exc.code, **exc.details},
            ) from exc
        return [{"path": p, "bytes": None} for p in result.get("changed_files") or []]

    def run_check(
        self,
        subject: str,
        workspace_id: str,
        command: str,
        *,
        allow_write: bool = False,
    ) -> dict[str, Any]:
        if not self.write_unlock or not allow_write:
            raise CoderError(
                "write_locked",
                "run_check requires write_unlock and an unlocked pipeline stage.",
            )
        cmd = (command or "").strip()
        if not cmd:
            raise CoderError("command_required", "Check command is required.")
        if not _command_allowlisted(cmd, self.check_allowlist):
            raise CoderError(
                "command_not_allowlisted",
                "Command is not on the coder check allowlist.",
                details={"command": cmd, "allowlist": list(self.check_allowlist)},
            )
        root = self._require_workspace(subject, workspace_id).root
        argv = shlex.split(cmd)
        _validate_check_args(argv, root)
        if self.check_isolation == "none":
            # Prefer the Gateway interpreter so `python -m pytest` is honest
            # in lab/dev. (Isolated modes bring their own interpreter.)
            if argv and argv[0] in {"python", "python3"}:
                import sys

                argv[0] = sys.executable
        else:
            argv = self._isolation_wrap(argv, root)
        try:
            completed = _run_in_process_group(
                argv, cwd=root, timeout=self.check_timeout_seconds
            )
        except FileNotFoundError:
            result = {
                "workspace_id": workspace_id,
                "command": cmd,
                "exit_code": None,
                "stdout": "",
                "stderr": "executable_not_found",
                "availability": "not_reported",
                "passed": False,
            }
            self._audit_tool(
                subject, workspace_id, "coder_run_check", ".", "not_reported", metadata=result
            )
            return result
        except subprocess.TimeoutExpired:
            raise CoderError("check_timeout", "Allowlisted check timed out.") from None
        passed = completed.returncode == 0
        result = {
            "workspace_id": workspace_id,
            "command": cmd,
            "exit_code": completed.returncode,
            "stdout": self._scan_outbound(
                subject, workspace_id, completed.stdout[-8000:], context="check:stdout"
            ),
            "stderr": self._scan_outbound(
                subject, workspace_id, completed.stderr[-8000:], context="check:stderr"
            ),
            "availability": "executed",
            "passed": passed,
        }
        self._audit_tool(
            subject,
            workspace_id,
            "coder_run_check",
            ".",
            "ok" if passed else "failed",
            metadata={"command": cmd, "exit_code": completed.returncode},
        )
        return result

    def git_diff(self, subject: str, workspace_id: str) -> str:
        grant = self._require_workspace(subject, workspace_id)
        try:
            diff = self.git.diff(grant.root)
        except GitAdapterError as exc:
            raise CoderError(exc.code, exc.message, details=exc.details) from exc
        return self._scan_outbound(subject, workspace_id, diff, context="git_diff")

    def git_status(self, subject: str, workspace_id: str) -> dict[str, Any]:
        grant = self._require_workspace(subject, workspace_id)
        try:
            return self.git.status(grant.root)
        except GitAdapterError as exc:
            raise CoderError(exc.code, exc.message, details=exc.details) from exc

    def push_branch(
        self,
        subject: str,
        workspace_id: str,
        *,
        branch: str,
        commit_message: str,
    ) -> dict[str, Any]:
        if not self.write_unlock:
            raise CoderError("write_locked", "push requires write_unlock.")
        grant = self._require_workspace(subject, workspace_id)
        try:
            if not (grant.root / ".git").exists():
                self.git.ensure_repo(grant.root)
            if not grant.remote_url:
                bare_root = self.cage_root / subject.replace("/", "_") / "_remotes"
                grant.remote_url = self.git.ensure_local_remote(grant.root, bare_root)
                self._notify_workspace(grant, None)
            self.git.create_branch(grant.root, branch)
            sha = self.git.commit_all(grant.root, commit_message)
            pushed = self.git.push(grant.root, remote="origin", branch=branch)
            grant.git_head = sha
            self._notify_workspace(grant, None)
            return {
                "workspace_id": workspace_id,
                "branch": branch,
                "head": sha,
                "remote": grant.remote_url,
                "push": pushed,
                "adapter": "local",
            }
        except GitAdapterError as exc:
            raise CoderError(exc.code, exc.message, details=exc.details) from exc

    def build_tool_executor(
        self,
        subject: str,
        workspace_id: str,
        *,
        allow_write: bool = False,
    ):
        """Workspace-scoped ToolExecutor for the agent loop."""
        from yonder_contracts.tools import ToolDefinition

        from app.services.tools import ToolExecutor

        executor = ToolExecutor()
        wid = workspace_id

        def _list(args: dict[str, Any]) -> str:
            return json.dumps(self.list_dir(subject, wid, str(args.get("path") or ".")))

        def _read(args: dict[str, Any]) -> str:
            return json.dumps(self.read_file(subject, wid, str(args.get("path") or "")))

        def _search(args: dict[str, Any]) -> str:
            return json.dumps(
                self.search(
                    subject,
                    wid,
                    str(args.get("query") or ""),
                    str(args.get("path") or "."),
                )
            )

        executor.register(
            ToolDefinition(
                name="coder_list_dir",
                description="List files in the coder workspace jail.",
                parameters_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            ),
            _list,
        )
        executor.register(
            ToolDefinition(
                name="coder_read_file",
                description="Read a UTF-8 file from the coder workspace jail.",
                parameters_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
            _read,
        )
        executor.register(
            ToolDefinition(
                name="coder_search",
                description="Substring search inside the coder workspace jail.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string"},
                    },
                    "required": ["query"],
                },
            ),
            _search,
        )
        if self.write_unlock and allow_write:

            def _apply(args: dict[str, Any]) -> str:
                return json.dumps(
                    self.apply_patch(
                        subject, wid, str(args.get("patch") or ""), allow_write=True
                    )
                )

            def _write(args: dict[str, Any]) -> str:
                return json.dumps(
                    self.write_file(
                        subject,
                        wid,
                        str(args.get("path") or ""),
                        str(args.get("content") or ""),
                        allow_write=True,
                    )
                )

            def _check(args: dict[str, Any]) -> str:
                return json.dumps(
                    self.run_check(
                        subject, wid, str(args.get("command") or ""), allow_write=True
                    )
                )

            executor.register(
                ToolDefinition(
                    name="coder_apply_patch",
                    description=(
                        "Apply a unified diff (git apply --3way) inside the workspace "
                        "jail. Prefer coder_write_file for whole-file rewrites."
                    ),
                    parameters_schema={
                        "type": "object",
                        "properties": {"patch": {"type": "string"}},
                        "required": ["patch"],
                    },
                ),
                _apply,
            )
            executor.register(
                ToolDefinition(
                    name="coder_write_file",
                    description=(
                        "Create or fully replace one UTF-8 file inside the workspace jail."
                    ),
                    parameters_schema={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                ),
                _write,
            )
            executor.register(
                ToolDefinition(
                    name="coder_run_check",
                    description=(
                        "Run an allowlisted check command in the workspace root. "
                        "Arguments must stay inside the workspace."
                    ),
                    parameters_schema={
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                ),
                _check,
            )
        return executor

    def _isolation_wrap(self, argv: list[str], root: Path) -> list[str]:
        """Wrap a check command in the configured isolation. Fails closed."""
        binary = "podman" if self.check_isolation == "podman" else "systemd-run"
        if shutil.which(binary) is None:
            raise CoderError(
                "isolation_unavailable",
                f"check_isolation={self.check_isolation} is configured but "
                f"{binary} is not installed. Refusing to run the check "
                "unisolated.",
                details={"isolation": self.check_isolation, "binary": binary},
            )
        if self.check_isolation == "podman":
            return [
                "podman",
                "run",
                "--rm",
                "--network=none",
                "--memory=2g",
                "--pids-limit=256",
                "--cpus=2",
                "-v",
                f"{root}:/workspace:rw",
                "-w",
                "/workspace",
                self.check_isolation_image,
                *argv,
            ]
        # systemd-run: resource limits only (no network isolation for --user
        # scopes) — podman is the mode that earns write_unlock on shared hosts.
        return [
            "systemd-run",
            "--user",
            "--scope",
            "--collect",
            "--quiet",
            "-p",
            "MemoryMax=2G",
            "-p",
            "TasksMax=256",
            *argv,
        ]

    def _scan_outbound(
        self, subject: str, workspace_id: str, text: str, *, context: str
    ) -> str:
        """Content-level secret redaction on anything leaving the jail.

        Fails closed: a scanner error refuses the operation instead of
        letting unscanned content out.
        """
        try:
            result = redact_secrets(text)
        except Exception as exc:  # noqa: BLE001 — fail closed, never leak
            raise CoderError(
                "secret_scan_failed",
                "Secret scanner failed; refusing to release content.",
                details={"context": context},
            ) from exc
        if result.redactions:
            self._audit(
                {
                    "event_type": "coder.secret_redacted",
                    "resource_type": "coder_workspace",
                    "resource_id": workspace_id,
                    "principal_subject": subject,
                    "result": "redacted",
                    "metadata": {
                        "context": context,
                        "count": result.redactions,
                        "kinds": list(result.kinds),
                    },
                }
            )
        return result.text

    def _require_workspace(self, subject: str, workspace_id: str) -> WorkspaceGrant:
        grant = self._workspaces.get(workspace_id)
        if grant is None or grant.subject != subject:
            raise CoderError("workspace_not_found", "Workspace not found for principal.")
        return grant

    def _resolve_in_jail(self, root: Path, rel_path: str) -> Path:
        raw = (rel_path or ".").strip() or "."
        if raw.startswith("/") or raw.startswith("~"):
            raise CoderError("path_escape", "Absolute paths are not allowed.")
        candidate = (root / raw).resolve()
        if not (candidate == root or candidate.is_relative_to(root)):
            raise CoderError("path_escape", "Path escapes workspace jail.")
        return candidate

    def _notify_workspace(
        self, grant: WorkspaceGrant | None, deleted_id: str | None
    ) -> None:
        if self.on_workspace_changed:
            self.on_workspace_changed(grant, deleted_id)

    def _audit(self, event: dict[str, Any]) -> None:
        if self.audit:
            self.audit(event)

    def _audit_tool(
        self,
        subject: str,
        workspace_id: str,
        tool: str,
        path: str,
        result: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "event_type": f"coder.tool.{tool}",
            "resource_type": "coder_workspace",
            "resource_id": workspace_id,
            "principal_subject": subject,
            "result": result,
            "metadata": {"path": path, **(metadata or {})},
        }
        self._audit(payload)


def _is_secret_path(rel: str) -> bool:
    return bool(SECRET_NAME_RE.search(rel.replace("\\", "/")))


def _command_allowlisted(command: str, allowlist: list[str]) -> bool:
    normalized = " ".join(command.strip().split())
    for allowed in allowlist:
        allowed_n = " ".join(allowed.strip().split())
        if normalized == allowed_n or normalized.startswith(allowed_n + " "):
            return True
    return False


def _paths_in_diff(text: str) -> list[str]:
    """Extract target paths from a unified diff (for jail/secret checks)."""
    paths: list[str] = []
    for line in text.splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            raw = line[4:].strip()
            if raw in {"/dev/null", ""}:
                continue
            if raw.startswith(("a/", "b/")):
                raw = raw[2:]
            if raw not in paths:
                paths.append(raw)
    return paths


def _validate_check_args(argv: list[str], root: Path) -> None:
    """Constrain allowlisted commands to the workspace (no absolute/.. targets)."""
    for arg in argv[1:]:
        value = arg.split("=", 1)[1] if arg.startswith("-") and "=" in arg else arg
        if value.startswith("-"):
            continue
        if value.startswith(("/", "~")) or value.split(":", 1)[0].startswith("/"):
            raise CoderError(
                "check_args_denied",
                "Check arguments may not reference absolute paths.",
                details={"argument": arg},
            )
        if ".." in Path(value).parts:
            raise CoderError(
                "check_args_denied",
                "Check arguments may not traverse outside the workspace.",
                details={"argument": arg},
            )
        if "/" in value:
            candidate = (root / value).resolve()
            if not (candidate == root or candidate.is_relative_to(root)):
                raise CoderError(
                    "check_args_denied",
                    "Check argument path escapes the workspace.",
                    details={"argument": arg},
                )


def _run_in_process_group(
    argv: list[str], *, cwd: Path, timeout: int
) -> subprocess.CompletedProcess[str]:
    """Run a check in its own process group so timeouts kill grandchildren too."""
    import os
    import signal

    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait()
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def _write_file(
    root: Path,
    rel: str,
    content: str,
    resolve: Callable[[Path, str], Path],
) -> dict[str, Any]:
    if _is_secret_path(rel):
        raise CoderError("secret_path_denied", "Refusing to write secret-like path.")
    target = resolve(root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"path": rel, "bytes": len(content.encode("utf-8"))}


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()
