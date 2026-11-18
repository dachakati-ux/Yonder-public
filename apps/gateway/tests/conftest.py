"""Shared gateway test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def gateway_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setenv(
        "YONDER_CONFIG_FILE",
        str(root / "config/environments/development.yaml"),
    )
    state_file = tmp_path / "gateway-state.json"
    monkeypatch.setenv("YONDER_STATE_FILE", str(state_file))
    # Isolate each test on its own SQLite database (dev YAML defaults to shared path).
    monkeypatch.setenv("YONDER_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    return state_file
