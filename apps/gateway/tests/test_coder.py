"""Coder workspace path jail + pipeline API tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.services.coder_runs import CoderRunOrchestrator, InMemoryCoderRunStore
from app.services.coder_workspace import CoderError, CoderWorkspaceService
from app.services.git_adapter import ForgejoAdapter, GitAdapterError, LocalGitAdapter
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def coder_service(tmp_path: Path) -> CoderWorkspaceService:
    cage = tmp_path / "cages"
    grantable = tmp_path / "grantable"
    grantable.mkdir()
    (grantable / "hello.txt").write_text("hello from grant\n", encoding="utf-8")
    events: list[dict] = []
    return CoderWorkspaceService(
        cage_root=cage,
        grantable_roots=[grantable],
        audit=lambda e: events.append(e),
    )


def test_cage_list_read_search_and_escape(coder_service: CoderWorkspaceService) -> None:
    ws = coder_service.create_cage("alice")
    wid = ws["id"]
    listed = coder_service.list_dir("alice", wid, ".")
    names = {e["name"] for e in listed["entries"]}
    assert "README.md" in names
    assert "samples" in names

    read = coder_service.read_file("alice", wid, "samples/bug.py")
    assert "return a - b" in read["content"]

    hits = coder_service.search("alice", wid, "BUG")
    assert any(h["path"].endswith("bug.py") for h in hits["hits"])

    with pytest.raises(CoderError) as esc:
        coder_service.read_file("alice", wid, "../outside")
    assert esc.value.code == "path_escape"

    with pytest.raises(CoderError) as abs_path:
        coder_service.list_dir("alice", wid, "/etc")
    assert abs_path.value.code == "path_escape"


def test_secret_path_denied(coder_service: CoderWorkspaceService) -> None:
    ws = coder_service.create_cage("alice")
    wid = ws["id"]
    root = coder_service._workspaces[wid].root
    (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    with pytest.raises(CoderError) as denied:
        coder_service.read_file("alice", wid, ".env")
    assert denied.value.code == "secret_path_denied"


def test_grant_unreadable_path_fail_closed(
    coder_service: CoderWorkspaceService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    grantable = next(p for p in coder_service.grantable_roots if (p / "hello.txt").exists())
    target = grantable / "locked"
    target.mkdir()

    def boom(self: Path) -> bool:  # noqa: ARG001
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "exists", boom)
    with pytest.raises(CoderError) as denied:
        coder_service.grant_path("bob", str(target))
    assert denied.value.code == "path_not_readable"


def test_grant_only_under_grantable(
    coder_service: CoderWorkspaceService, tmp_path: Path
) -> None:
    grantable = next(p for p in coder_service.grantable_roots if (p / "hello.txt").exists())
    ws = coder_service.grant_path("bob", str(grantable))
    read = coder_service.read_file("bob", ws["id"], "hello.txt")
    assert "hello from grant" in read["content"]

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(CoderError) as denied:
        coder_service.grant_path("bob", str(outside))
    assert denied.value.code == "path_not_grantable"


def test_apply_and_check_require_write_unlock(coder_service: CoderWorkspaceService) -> None:
    ws = coder_service.create_cage("alice")
    wid = ws["id"]
    with pytest.raises(CoderError) as locked:
        coder_service.apply_patch(
            "alice",
            wid,
            json.dumps({"path": "samples/bug.py", "content": "def add(a,b): return a+b\n"}),
            allow_write=True,
        )
    assert locked.value.code == "write_locked"

    coder_service.write_unlock = True
    applied = coder_service.apply_patch(
        "alice",
        wid,
        json.dumps({"path": "samples/bug.py", "content": "def add(a, b):\n    return a + b\n"}),
        allow_write=True,
    )
    assert applied["applied"][0]["path"] == "samples/bug.py"

    with pytest.raises(CoderError) as bad_cmd:
        coder_service.run_check("alice", wid, "rm -rf /", allow_write=True)
    assert bad_cmd.value.code == "command_not_allowlisted"

    with pytest.raises(CoderError) as secret_write:
        coder_service.apply_patch(
            "alice",
            wid,
            json.dumps({"path": ".env", "content": "SECRET=1\n"}),
            allow_write=True,
        )
    assert secret_write.value.code == "secret_path_denied"


def test_local_clone_and_forgejo_stub(tmp_path: Path) -> None:
    # Prefer workspace-local paths for .git writes under restricted sandboxes.
    base = ROOT / "data" / "coder-test-tmp" / "clone-stub"
    if base.exists():
        import shutil

        shutil.rmtree(base)
    base.mkdir(parents=True)
    src = base / "src"
    src.mkdir()
    (src / "hello.txt").write_text("hi\n", encoding="utf-8")
    git = LocalGitAdapter()
    dest = base / "dest"
    meta = git.clone(str(src), dest)
    assert (dest / "hello.txt").exists()
    assert meta["adapter"] == "local"

    forge = ForgejoAdapter(base_url="https://forgejo.example")
    with pytest.raises(GitAdapterError) as denied:
        forge.clone("https://forgejo.example/org/repo.git", base / "x")
    assert denied.value.code == "forgejo_not_configured"


def _pipeline_service(base: Path, *, write_unlock: bool = True) -> CoderWorkspaceService:
    import shutil

    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    cage = base / "cages"
    grantable = base / "grantable"
    grantable.mkdir()
    sample = ROOT / "config" / "lab" / "coder-evals" / "sample_repo"
    shutil.copytree(sample, grantable / "sample_repo")
    return CoderWorkspaceService(
        cage_root=cage,
        grantable_roots=[grantable],
        write_unlock=write_unlock,
        check_allowlist=["python -m pytest", "pytest"],
    )


@pytest.mark.asyncio
async def test_push_refused_without_dual_approve(tmp_path: Path) -> None:
    service = _pipeline_service(ROOT / "data" / "coder-test-tmp" / "pushgate")
    grantable = next(p for p in service.grantable_roots if (p / "sample_repo").exists())
    ws = service.clone_local("alice", str(grantable / "sample_repo"), label="sample")
    orch = CoderRunOrchestrator(workspaces=service, store=InMemoryCoderRunStore())
    run = orch.create_run("alice", ws["id"], "Fix add() so 2+3==5")
    run.stage = "push"
    run.artifacts["check"] = {"availability": "executed", "passed": True}
    run.verdicts = [
        {"agent": "A", "verdict": "approve"},
        {"agent": "B", "verdict": "reject"},
    ]
    await orch._push(run)
    assert run.status == "failed"
    assert run.error == "push_refused_without_dual_approve"


@pytest.mark.asyncio
async def test_push_refused_without_passing_check_evidence(tmp_path: Path) -> None:
    """Dual approve alone is not enough — push demands passing check evidence."""
    service = _pipeline_service(ROOT / "data" / "coder-test-tmp" / "pushevidence")
    grantable = next(p for p in service.grantable_roots if (p / "sample_repo").exists())
    ws = service.clone_local("alice", str(grantable / "sample_repo"), label="sample")
    orch = CoderRunOrchestrator(workspaces=service, store=InMemoryCoderRunStore())
    run = orch.create_run("alice", ws["id"], "Fix add()")
    run.stage = "push"
    run.artifacts["check"] = {"availability": "not_reported", "passed": False}
    run.verdicts = [
        {"agent": "A", "verdict": "approve"},
        {"agent": "B", "verdict": "approve"},
    ]
    await orch._push(run)
    assert run.status == "failed"
    assert run.error == "push_refused_without_dual_approve"


@pytest.mark.asyncio
async def test_dual_verify_rejects_when_check_evidence_missing(tmp_path: Path) -> None:
    """No executed+passing check ⇒ verdicts fail closed, run enters fix loop."""
    service = _pipeline_service(ROOT / "data" / "coder-test-tmp" / "verifyevidence")
    grantable = next(p for p in service.grantable_roots if (p / "sample_repo").exists())
    ws = service.clone_local("alice", str(grantable / "sample_repo"), label="sample")
    orch = CoderRunOrchestrator(
        workspaces=service, store=InMemoryCoderRunStore(), max_fix_iterations=0
    )
    run = orch.create_run("alice", ws["id"], "Fix add()")
    run.stage = "dual_verify"
    run.artifacts["check"] = {"availability": "not_reported", "passed": False}
    await orch._dual_verify(run)
    assert all(v["verdict"] == "reject" for v in run.verdicts)
    assert run.status == "failed"
    assert run.error and run.error.startswith("fix_budget_exhausted")


@pytest.mark.asyncio
async def test_agent_stage_without_llm_fails_honestly(tmp_path: Path) -> None:
    """No model wired ⇒ llm_unavailable failure. There is no pretend mode."""
    service = _pipeline_service(ROOT / "data" / "coder-test-tmp" / "nollm")
    grantable = next(p for p in service.grantable_roots if (p / "sample_repo").exists())
    ws = service.clone_local("alice", str(grantable / "sample_repo"), label="sample")
    orch = CoderRunOrchestrator(workspaces=service, store=InMemoryCoderRunStore(), llm=None)
    run = orch.create_run("alice", ws["id"], "Fix add()")
    finished = await orch.advance("alice", run.id, auto=True)
    assert finished.status == "failed"
    assert "llm_unavailable" in (finished.error or "")


@pytest.mark.asyncio
async def test_budget_exhaustion_is_terminal(tmp_path: Path) -> None:
    service = _pipeline_service(ROOT / "data" / "coder-test-tmp" / "budget")
    grantable = next(p for p in service.grantable_roots if (p / "sample_repo").exists())
    ws = service.clone_local("alice", str(grantable / "sample_repo"), label="sample")
    orch = CoderRunOrchestrator(workspaces=service, store=InMemoryCoderRunStore())
    run = orch.create_run("alice", ws["id"], "Fix add()")
    run.metrics["model_calls"] = orch.max_model_calls
    orch.store.save(run)
    finished = await orch.advance("alice", run.id, auto=True)
    assert finished.status == "failed"
    assert (finished.error or "").startswith("budget_exhausted:model_calls")


@pytest.mark.asyncio
async def test_executor_drives_run_to_blocked_and_cancel(tmp_path: Path) -> None:
    """Executor drives a run in the background; write-locked runs block; cancel works."""
    from app.services.coder_executor import CoderRunExecutor

    service = _pipeline_service(
        ROOT / "data" / "coder-test-tmp" / "executor", write_unlock=False
    )
    grantable = next(p for p in service.grantable_roots if (p / "sample_repo").exists())
    service.write_unlock = True
    ws = service.clone_local("alice", str(grantable / "sample_repo"), label="sample")
    service.write_unlock = False

    async def fake_llm(**kwargs):  # noqa: ANN003
        return {"content": "explored: math_utils.py has the bug", "tool_calls": []}

    orch = CoderRunOrchestrator(
        workspaces=service, store=InMemoryCoderRunStore(), llm=fake_llm
    )
    executor = CoderRunExecutor(orchestrator=orch)
    run = orch.create_run("alice", ws["id"], "Fix add()")
    submitted = await executor.submit("alice", run.id, wait=True)
    finished = orch.get_run("alice", run.id)
    # explore + plan succeed via the model; implement blocks on write lock.
    assert submitted["status"] in {"active", "blocked"}
    assert finished.status == "blocked"
    assert finished.error == "write_locked"
    assert finished.stage == "implement"

    # Cancel a non-running blocked run → cancelled terminal state.
    cancelled = await executor.cancel("alice", run.id)
    assert cancelled["cancelled"] is True
    assert orch.get_run("alice", run.id).status in {"cancelled", "blocked"}


@pytest.mark.asyncio
async def test_executor_resume_sweep_restarts_active_runs(tmp_path: Path) -> None:
    """Runs left active (e.g. by a restart) are picked up by the boot sweep."""
    from app.services.coder_executor import CoderRunExecutor

    service = _pipeline_service(ROOT / "data" / "coder-test-tmp" / "resume")
    grantable = next(p for p in service.grantable_roots if (p / "sample_repo").exists())
    ws = service.clone_local("alice", str(grantable / "sample_repo"), label="sample")

    async def fake_llm(**kwargs):  # noqa: ANN003
        return {"content": "summary", "tool_calls": []}

    store = InMemoryCoderRunStore()
    orch = CoderRunOrchestrator(workspaces=service, store=store, llm=fake_llm)
    run = orch.create_run("alice", ws["id"], "Fix add()")
    assert store.list_active_runs()

    # Fresh executor (as after a gateway restart) resumes the active run.
    executor = CoderRunExecutor(orchestrator=orch)
    resumed = await executor.resume_active_runs()
    assert run.id in resumed
    for _ in range(200):
        if store.get(run.id, "alice").status != "active":
            break
        await __import__("asyncio").sleep(0.01)
    assert store.get(run.id, "alice").status != "active"


def test_apply_patch_git_diff_multi_hunk(tmp_path: Path) -> None:
    """Unified diffs go through git apply — multi-hunk edits must not truncate."""
    service = _pipeline_service(ROOT / "data" / "coder-test-tmp" / "gitapply")
    grantable = next(p for p in service.grantable_roots if (p / "sample_repo").exists())
    ws = service.clone_local("alice", str(grantable / "sample_repo"), label="sample")
    wid = ws["id"]
    root = service._workspaces[wid].root
    original = "\n".join(f"line {i}" for i in range(1, 21)) + "\n"
    (root / "big.txt").write_text(original, encoding="utf-8")
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-m",
            "seed",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    # Edit line 2 and line 19 (two hunks), leaving the middle intact.
    edited = original.replace("line 2\n", "line 2 EDITED\n").replace(
        "line 19\n", "line 19 EDITED\n"
    )
    (root / "big.txt").write_text(edited, encoding="utf-8")
    diff = subprocess.run(
        ["git", "diff"], cwd=root, check=True, capture_output=True, text=True
    ).stdout
    subprocess.run(["git", "checkout", "--", "big.txt"], cwd=root, check=True, capture_output=True)

    result = service.apply_patch("alice", wid, diff, allow_write=True)
    assert "big.txt" in [a["path"] for a in result["applied"]]
    patched = (root / "big.txt").read_text(encoding="utf-8")
    assert "line 2 EDITED" in patched
    assert "line 19 EDITED" in patched
    assert "line 10" in patched  # middle preserved — no truncation
    assert patched.count("\n") == original.count("\n")


class _FakeMcp:
    """Minimal MCP client stand-in for coder-integration tests."""

    def __init__(self, *, enabled: bool = True) -> None:
        from types import SimpleNamespace

        self.config = SimpleNamespace(enabled=enabled)
        self.calls: list[dict] = []

    def list_tools(self, *, server=None):  # noqa: ANN001
        return {
            "tools": [
                {
                    "name": "mcp.docs.search",
                    "server": "docs",
                    "tool": "search",
                    "description": "Search internal docs",
                    "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
                },
                {
                    "name": "mcp.docs.delete",
                    "server": "docs",
                    "tool": "delete",
                    "description": "Delete (should NOT be exposed unless allowlisted)",
                    "parameters": {"type": "object", "properties": {}},
                },
            ],
            "availability": "ready",
        }

    def call(self, *, server, tool, arguments=None, principal_subject=None, request_id=None):  # noqa: ANN001
        self.calls.append(
            {
                "server": server,
                "tool": tool,
                "arguments": arguments or {},
                "principal_subject": principal_subject,
                "request_id": request_id,
            }
        )
        return {"content": f"result of {server}.{tool}", "is_error": False}


@pytest.mark.asyncio
async def test_coder_mcp_tools_gated_and_attributed(tmp_path: Path) -> None:
    service = _pipeline_service(ROOT / "data" / "coder-test-tmp" / "mcpcoder")
    grantable = next(p for p in service.grantable_roots if (p / "sample_repo").exists())
    ws = service.clone_local("alice", str(grantable / "sample_repo"), label="sample")
    fake_mcp = _FakeMcp()
    orch = CoderRunOrchestrator(
        workspaces=service,
        store=InMemoryCoderRunStore(),
        llm=lambda **_: {"content": "", "tool_calls": []},
        mcp_client=fake_mcp,
        mcp_tools=["mcp.docs.search"],  # only search is allowlisted
    )
    run = orch.create_run("alice", ws["id"], "task")

    from app.services.tools import ToolExecutor

    executor = ToolExecutor()
    await orch._register_mcp_tools(executor, run)
    names = {d.name for d in executor.list_definitions()}
    assert "mcp.docs.search" in names  # allowlisted → exposed
    assert "mcp.docs.delete" not in names  # not allowlisted → withheld (fail closed)

    # Calling the tool routes through the MCP client attributed to this run.
    from yonder_contracts.tools import ToolCall

    result = executor.execute(
        ToolCall(id="c1", name="mcp.docs.search", arguments={"q": "auth"})
    )
    assert "result of docs.search" in result.content
    assert fake_mcp.calls[0]["principal_subject"] == "alice"
    assert fake_mcp.calls[0]["request_id"] == run.id


@pytest.mark.asyncio
async def test_coder_mcp_disabled_or_empty_allowlist_is_fail_closed(tmp_path: Path) -> None:
    service = _pipeline_service(ROOT / "data" / "coder-test-tmp" / "mcpclosed")
    grantable = next(p for p in service.grantable_roots if (p / "sample_repo").exists())
    ws = service.clone_local("alice", str(grantable / "sample_repo"), label="sample")
    from app.services.tools import ToolExecutor

    # Empty allowlist → no MCP tools even though the client is enabled.
    orch = CoderRunOrchestrator(
        workspaces=service,
        store=InMemoryCoderRunStore(),
        llm=lambda **_: {"content": "", "tool_calls": []},
        mcp_client=_FakeMcp(enabled=True),
        mcp_tools=[],
    )
    run = orch.create_run("alice", ws["id"], "task")
    ex1 = ToolExecutor()
    await orch._register_mcp_tools(ex1, run)
    assert not any(d.name.startswith("mcp.") for d in ex1.list_definitions())

    # Allowlisted but client disabled → still nothing.
    orch2 = CoderRunOrchestrator(
        workspaces=service,
        store=InMemoryCoderRunStore(),
        llm=lambda **_: {"content": "", "tool_calls": []},
        mcp_client=_FakeMcp(enabled=False),
        mcp_tools=["mcp.docs.search"],
    )
    run2 = orch2.create_run("alice", ws["id"], "task")
    ex2 = ToolExecutor()
    await orch2._register_mcp_tools(ex2, run2)
    assert not any(d.name.startswith("mcp.") for d in ex2.list_definitions())


def test_write_file_tool_and_check_arg_validation(tmp_path: Path) -> None:
    service = _pipeline_service(ROOT / "data" / "coder-test-tmp" / "writetool")
    grantable = next(p for p in service.grantable_roots if (p / "sample_repo").exists())
    ws = service.clone_local("alice", str(grantable / "sample_repo"), label="sample")
    wid = ws["id"]

    written = service.write_file("alice", wid, "notes/new.py", "x = 1\n", allow_write=True)
    assert written["applied"][0]["path"] == "notes/new.py"

    with pytest.raises(CoderError) as secret:
        service.write_file("alice", wid, ".env", "SECRET=1\n", allow_write=True)
    assert secret.value.code == "secret_path_denied"

    # Allowlisted command name with escaping arguments is refused.
    with pytest.raises(CoderError) as abs_arg:
        service.run_check("alice", wid, "pytest /etc", allow_write=True)
    assert abs_arg.value.code == "check_args_denied"

    with pytest.raises(CoderError) as dotdot:
        service.run_check("alice", wid, "pytest ../outside", allow_write=True)
    assert dotdot.value.code == "check_args_denied"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Keep cages under the workspace so local git metadata is writable in CI/dev.
    cage = ROOT / "data" / "coder-test-cages" / tmp_path.name
    cage.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(
        "YONDER_CONFIG_FILE",
        str(ROOT / "config/environments/development.yaml"),
    )
    monkeypatch.setenv("YONDER_CODER__CAGE_ROOT", str(cage))
    monkeypatch.setenv("YONDER_CODER__ENABLED", "true")
    monkeypatch.setenv("YONDER_CODER__WRITE_UNLOCK", "false")
    from app.main import create_app

    return TestClient(create_app())


def test_coder_http_roundtrip(client: TestClient) -> None:
    status = client.get("/v1/coder/status")
    assert status.status_code == 200
    body = status.json()
    assert body["enabled"] is True
    assert body["trust_ceiling"] == "read_only"
    assert body["write_unlock"] is False
    assert body.get("pipeline") is True

    created = client.post("/v1/coder/workspaces", json={"label": "proof"})
    assert created.status_code == 200
    wid = created.json()["workspace"]["id"]

    listed = client.post(
        "/v1/coder/tools/list_dir",
        json={"workspace_id": wid, "path": "."},
    )
    assert listed.status_code == 200
    assert any(e["name"] == "README.md" for e in listed.json()["entries"])

    escaped = client.post(
        "/v1/coder/tools/read_file",
        json={"workspace_id": wid, "path": "../../etc/passwd"},
    )
    assert escaped.status_code == 403

    # Write tools denied while locked
    applied = client.post(
        "/v1/coder/tools/apply_patch",
        json={
            "workspace_id": wid,
            "patch": json.dumps({"path": "x.py", "content": "x=1\n"}),
        },
    )
    assert applied.status_code == 403


def test_coder_grant_eval_fixture(client: TestClient) -> None:
    eval_root = ROOT / "config" / "lab" / "coder-evals" / "sample_repo"
    assert eval_root.is_dir()
    granted = client.post(
        "/v1/coder/workspaces/grant",
        json={"path": str(eval_root), "label": "eval-sample"},
    )
    assert granted.status_code == 200
    wid = granted.json()["workspace"]["id"]
    read = client.post(
        "/v1/coder/tools/read_file",
        json={"workspace_id": wid, "path": "math_utils.py"},
    )
    assert read.status_code == 200
    assert "return a - b" in read.json()["content"]

    cloned = client.post(
        "/v1/coder/workspaces/clone",
        json={"source": str(eval_root), "label": "clone-sample"},
    )
    assert cloned.status_code == 200
    assert cloned.json()["workspace"]["kind"] == "clone"

    run = client.post(
        "/v1/coder/runs",
        json={
            "workspace_id": cloned.json()["workspace"]["id"],
            "task": "Fix add()",
            "check_command": "python -m pytest",
        },
    )
    assert run.status_code == 200
    assert run.json()["run"]["stage"] == "intake"

def test_docs_search_tool_over_corpus(tmp_path: Path) -> None:
    from app.services.docs_corpus import seed_default_docs
    from app.services.rag import LexicalRetriever, RagStore
    from app.services.tools import ToolExecutor
    from yonder_contracts.tools import ToolCall

    store = RagStore(tmp_path / "docs.sqlite3")
    counts = seed_default_docs(store, ROOT)
    assert counts["dependency_inventory"] == 1
    assert counts["yonder_docs"] > 0  # real Yonder docs ingested

    service = _pipeline_service(ROOT / "data" / "coder-test-tmp" / "docs")
    grantable = next(p for p in service.grantable_roots if (p / "sample_repo").exists())
    ws = service.clone_local("alice", str(grantable / "sample_repo"), label="sample")
    orch = CoderRunOrchestrator(
        workspaces=service,
        store=InMemoryCoderRunStore(),
        llm=lambda **_: {"content": "", "tool_calls": []},
        docs_retriever=LexicalRetriever(store),
        docs_max_results=3,
    )
    run = orch.create_run("alice", ws["id"], "task")
    ex = ToolExecutor()
    orch._register_docs_tool(ex, run)
    assert "coder_docs_search" in {d.name for d in ex.list_definitions()}

    res = ex.execute(
        ToolCall(id="c", name="coder_docs_search",
                 arguments={"query": "worker shared secret authentication"})
    )
    data = json.loads(res.content)
    assert data["ok"] is True
    assert len(data["results"]) >= 1
    assert any("security-boundary" in r["title"] for r in data["results"])

    empty = ex.execute(ToolCall(id="c2", name="coder_docs_search", arguments={"query": ""}))
    assert json.loads(empty.content)["ok"] is False


def test_docs_search_disabled_when_no_retriever(tmp_path: Path) -> None:
    from app.services.tools import ToolExecutor

    service = _pipeline_service(ROOT / "data" / "coder-test-tmp" / "docsoff")
    grantable = next(p for p in service.grantable_roots if (p / "sample_repo").exists())
    ws = service.clone_local("alice", str(grantable / "sample_repo"), label="sample")
    orch = CoderRunOrchestrator(
        workspaces=service,
        store=InMemoryCoderRunStore(),
        llm=lambda **_: {"content": "", "tool_calls": []},
        docs_retriever=None,
    )
    run = orch.create_run("alice", ws["id"], "task")
    ex = ToolExecutor()
    orch._register_docs_tool(ex, run)
    assert "coder_docs_search" not in {d.name for d in ex.list_definitions()}

class _FakeForgejo:
    def __init__(self, *, configured: bool = True, fail: bool = False) -> None:
        self._configured = configured
        self._fail = fail
        self.calls: list[str] = []

    def is_configured(self) -> bool:
        return self._configured

    def ensure_repo_exists(self, *, owner, repo):  # noqa: ANN001
        self.calls.append("ensure_repo")
        if self._fail:
            raise RuntimeError("forgejo down")
        return {"exists": True, "created": False, "full_name": f"{owner}/{repo}"}

    def push(self, root, *, remote="forgejo", branch):  # noqa: ANN001
        self.calls.append("push")
        return {"adapter": "forgejo", "branch": branch, "head": "deadbeef"}

    def open_pull_request(self, *, owner, repo, title, head, base, body):  # noqa: ANN001
        self.calls.append("pr")
        return {"number": 7, "url": f"http://forge/{owner}/{repo}/pulls/7", "state": "open"}


@pytest.mark.asyncio
async def test_forgejo_delivery_opens_pr_after_push(tmp_path: Path) -> None:
    service = _pipeline_service(ROOT / "data" / "coder-test-tmp" / "forgejodeliver")
    grantable = next(p for p in service.grantable_roots if (p / "sample_repo").exists())
    ws = service.clone_local("alice", str(grantable / "sample_repo"), label="sample")
    fake = _FakeForgejo(configured=True)
    orch = CoderRunOrchestrator(
        workspaces=service,
        store=InMemoryCoderRunStore(),
        forgejo=fake,
        forgejo_owner="lab",
    )
    run = orch.create_run("alice", ws["id"], "Fix add()")
    run.stage = "push"
    run.artifacts["check"] = {"availability": "executed", "passed": True}
    run.verdicts = [{"agent": "A", "verdict": "approve"}, {"agent": "B", "verdict": "approve"}]
    await orch._push(run)
    assert run.status == "completed"
    assert fake.calls == ["ensure_repo", "push", "pr"]
    assert run.push_result["forgejo"]["delivered"] is True
    assert run.push_result["forgejo"]["pull_request"]["number"] == 7


@pytest.mark.asyncio
async def test_forgejo_delivery_is_fail_soft(tmp_path: Path) -> None:
    """A Forgejo failure must not fail an otherwise-complete run (branch pushed)."""
    service = _pipeline_service(ROOT / "data" / "coder-test-tmp" / "forgejosoft")
    grantable = next(p for p in service.grantable_roots if (p / "sample_repo").exists())
    ws = service.clone_local("alice", str(grantable / "sample_repo"), label="sample")
    orch = CoderRunOrchestrator(
        workspaces=service,
        store=InMemoryCoderRunStore(),
        forgejo=_FakeForgejo(configured=True, fail=True),
        forgejo_owner="lab",
    )
    run = orch.create_run("alice", ws["id"], "Fix add()")
    run.stage = "push"
    run.artifacts["check"] = {"availability": "executed", "passed": True}
    run.verdicts = [{"agent": "A", "verdict": "approve"}, {"agent": "B", "verdict": "approve"}]
    await orch._push(run)
    assert run.status == "completed"  # local push succeeded; Forgejo failure recorded
    assert run.push_result["forgejo"]["delivered"] is False
    assert "error" in run.push_result["forgejo"]


@pytest.mark.asyncio
async def test_forgejo_delivery_skipped_when_unconfigured(tmp_path: Path) -> None:
    service = _pipeline_service(ROOT / "data" / "coder-test-tmp" / "forgejooff")
    grantable = next(p for p in service.grantable_roots if (p / "sample_repo").exists())
    ws = service.clone_local("alice", str(grantable / "sample_repo"), label="sample")
    orch = CoderRunOrchestrator(
        workspaces=service,
        store=InMemoryCoderRunStore(),
        forgejo=_FakeForgejo(configured=False),
        forgejo_owner="lab",
    )
    run = orch.create_run("alice", ws["id"], "Fix add()")
    run.stage = "push"
    run.artifacts["check"] = {"availability": "executed", "passed": True}
    run.verdicts = [{"agent": "A", "verdict": "approve"}, {"agent": "B", "verdict": "approve"}]
    await orch._push(run)
    assert run.status == "completed"
    assert "forgejo" not in (run.push_result or {})
