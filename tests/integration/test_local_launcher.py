"""Exercise launcher safety without Docker or real host mutations."""
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_shell_syntax():
    for path in ("start.sh", "scripts/update-yonder.sh"):
        subprocess.run(["bash", "-n", ROOT / path], check=True)


def test_unsafe_rollback_is_disabled():
    result = subprocess.run(
        ["bash", ROOT / "scripts/update-yonder.sh", "--rollback", "/nonexistent"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "Automatic rollback is disabled" in result.stderr


def test_launcher_health_and_dotenv(tmp_path):
    shutil.copy(ROOT / "start.sh", tmp_path)
    (tmp_path / ".env").write_text("SHOULD_NOT_RUN=$(touch injected)\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/bin/bash
case "$*" in
  *"config --environment"*) echo "YONDER_ENVIRONMENT=${TEST_MODE:-development}" ;;
  *"up --build -d --wait --wait-timeout 180"*) exit "${TEST_UP_STATUS:-0}" ;;
  *"port console 3000"*) echo "127.0.0.1:3100" ;;
  *"port gateway 8080"*) echo "127.0.0.1:8180" ;;
  *"port mcp 8095"*) echo "127.0.0.1:8195" ;;
  *) exit 0 ;;
esac
"""
    )
    docker.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text("#!/bin/bash\nexit 0\n")
    curl.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    for mode, status, expected in (
        ("development", "0", True),
        ("development", "1", False),
        ("production", "0", False),
    ):
        result = subprocess.run(
            ["bash", tmp_path / "start.sh", "--yes"],
            env={**env, "TEST_MODE": mode, "TEST_UP_STATUS": status},
            cwd=tmp_path, capture_output=True, text=True, check=False,
        )
        assert (result.returncode == 0) == expected, result.stdout + result.stderr
        assert ("Yonder is running!" in result.stdout) == expected
        if expected:
            assert "http://127.0.0.1:3100" in result.stdout
        assert not (tmp_path / "injected").exists()
