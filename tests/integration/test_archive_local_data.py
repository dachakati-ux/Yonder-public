"""Quiesced local data snapshot tests; no Docker required."""
import importlib.util
import io
import sqlite3
import tarfile
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "archive_local_data", Path(__file__).resolve().parents[2] / "scripts/archive-local-data.py"
)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_archive_preserves_database_and_state(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    with sqlite3.connect(root / "yonder.db") as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE example (value TEXT)")
        conn.execute("INSERT INTO example VALUES ('committed')")
        conn.commit()
        (root / "gateway-state.json").write_text('{"example": true}')
        output = io.BytesIO()
        module.archive_data(root, output)
    output.seek(0)
    with tarfile.open(fileobj=output, mode="r:gz") as archive:
        names = archive.getnames()
        assert "data/yonder.db" in names
        assert not any(name.endswith(("-wal", "-shm")) for name in names)
        state = archive.extractfile("data/gateway-state.json")
        db = archive.extractfile("data/yonder.db")
        assert state is not None and db is not None
        assert state.read() == b'{"example": true}'
        restored = tmp_path / "restored.db"
        restored.write_bytes(db.read())
    with sqlite3.connect(restored) as conn:
        assert conn.execute("SELECT value FROM example").fetchall() == [("committed",)]
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_archive_refuses_missing_database(tmp_path):
    with pytest.raises(ValueError, match="database is missing"):
        module.archive_data(tmp_path, io.BytesIO())


def test_archive_refuses_symlink(tmp_path):
    with sqlite3.connect(tmp_path / "yonder.db") as conn:
        conn.execute("CREATE TABLE example (value TEXT)")
    (tmp_path / "link").symlink_to(tmp_path / "yonder.db")
    with pytest.raises(ValueError, match="Unexpected symlink"):
        module.archive_data(tmp_path, io.BytesIO())


def test_archive_refuses_corrupt_database(tmp_path):
    (tmp_path / "yonder.db").write_bytes(b"not a database")
    out = io.BytesIO()
    with pytest.raises(sqlite3.DatabaseError):
        module.archive_data(tmp_path, out)
    assert out.getvalue() == b""
