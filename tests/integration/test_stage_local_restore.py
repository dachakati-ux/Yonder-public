"""Offline restore staging must not overwrite live data or follow archive links."""
import importlib.util
import io
import sqlite3
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "stage_restore", ROOT / "scripts/stage-local-restore.py"
)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def _archive(tmp_path, *, extra=None, corrupt=False):
    db_path = tmp_path / "yonder.db"
    if corrupt:
        db_path.write_bytes(b"invalid database")
    else:
        with sqlite3.connect(db_path) as db:
            db.execute("CREATE TABLE probe (id INTEGER)")
            db.execute("INSERT INTO probe VALUES (42)")
            db.commit()
    archive = tmp_path / "backup.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(db_path, arcname="data/yonder.db")
        if extra is not None:
            tar.addfile(extra, io.BytesIO(b"x") if extra.isfile() else None)
    return archive


def test_restore_to_new_directory(tmp_path):
    archive = _archive(tmp_path)
    dest = tmp_path / "staged"
    module.stage_restore(archive, dest)
    with sqlite3.connect(dest / "yonder.db") as db:
        assert db.execute("SELECT id FROM probe").fetchone() == (42,)
    with pytest.raises(FileExistsError):
        module.stage_restore(archive, dest)
    assert (dest / "yonder.db").is_file()


@pytest.mark.parametrize("name", ["../escape", "data/../../escape", "/data/file", "data/yonder.db"])
def test_rejects_traversal_and_duplicate(tmp_path, name):
    extra = tarfile.TarInfo(name)
    extra.size = 1
    archive = _archive(tmp_path, extra=extra)
    dest = tmp_path / "staged"
    with pytest.raises(ValueError, match="Unsafe or duplicate"):
        module.stage_restore(archive, dest)
    assert not dest.exists()


def test_rejects_symlink(tmp_path):
    extra = tarfile.TarInfo("data/link")
    extra.type = tarfile.SYMTYPE
    extra.linkname = "/etc/passwd"
    with pytest.raises(ValueError, match="Unsafe or duplicate"):
        module.stage_restore(_archive(tmp_path, extra=extra), tmp_path / "staged")


def test_corrupt_backup_leaves_no_staged_data(tmp_path):
    dest = tmp_path / "staged"
    with pytest.raises(sqlite3.DatabaseError):
        module.stage_restore(_archive(tmp_path, corrupt=True), dest)
    assert not dest.exists()
