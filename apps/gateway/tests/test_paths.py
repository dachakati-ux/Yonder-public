"""Repository root resolution for source checkouts and packaged installs."""

from __future__ import annotations

from pathlib import Path

from app.paths import resolve_repo_root

ROOT = Path(__file__).resolve().parents[3]


def test_resolve_repo_root_finds_source_tree() -> None:
    resolved = resolve_repo_root()
    assert resolved == ROOT
    assert (resolved / "config" / "models" / "yonder-chat-small.yaml").is_file()


def test_resolve_repo_root_honors_environment(monkeypatch, tmp_path: Path) -> None:
    models = tmp_path / "config" / "models"
    models.mkdir(parents=True)
    monkeypatch.setenv("YONDER_REPO_ROOT", str(tmp_path))

    assert resolve_repo_root() == tmp_path.resolve()


def test_resolve_repo_root_walks_from_installed_style_path(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("YONDER_REPO_ROOT", raising=False)
    repo = tmp_path / "repo"
    (repo / "config" / "models").mkdir(parents=True)
    fake_module = (
        tmp_path
        / "venv"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "app"
        / "paths.py"
    )
    fake_module.parent.mkdir(parents=True)
    fake_module.write_text("# placeholder\n", encoding="utf-8")

    # Simulate an installed process whose cwd is the unpacked repo payload.
    monkeypatch.chdir(repo)
    assert resolve_repo_root(start=fake_module) == repo.resolve()
