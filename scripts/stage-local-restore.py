"""Validate and unpack a local update archive into a new, isolated directory.

This never restores into a running container or existing data directory.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import tarfile
from contextlib import closing
from pathlib import Path, PurePosixPath


def stage_restore(archive_path: Path, destination: Path) -> None:
    with tarfile.open(archive_path, mode="r:gz") as archive:
        entries = {}
        total = 0
        for member in archive:
            path = PurePosixPath(member.name)
            if (
                not path.parts or path.parts[0] != "data" or ".." in path.parts
                or path.is_absolute() or "\\" in member.name
                or not (member.isdir() or member.isfile())
                or (path == PurePosixPath("data") and not member.isdir())
                or path in entries
            ):
                raise ValueError(f"Unsafe or duplicate archive entry: {member.name}")
            entries[path] = member
            total += member.size if member.isfile() else 0
        database = entries.get(PurePosixPath("data/yonder.db"))
        if database is None or not database.isfile():
            raise ValueError("Archive does not contain data/yonder.db")
        if total > shutil.disk_usage(destination.parent).free:
            raise ValueError("Insufficient disk space to stage restore")
        # Exclusive creation prevents replacing existing data, including an empty
        # directory. On failure remove only the directory created by this call.
        destination.mkdir(mode=0o700, exist_ok=False)
        try:
            for path, member in sorted(entries.items()):
                target = destination.joinpath(*path.parts[1:])
                if member.isdir():
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValueError(f"Cannot read archive entry: {member.name}")
                    with source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(0o600)
            for path in destination.rglob("*"):
                if path.is_file() and path.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
                    uri = path.resolve().as_uri() + "?mode=ro"
                    with closing(sqlite3.connect(uri, uri=True)) as db:
                        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                            raise ValueError(f"SQLite integrity check failed: {path.name}")
        except BaseException:
            shutil.rmtree(destination)
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        stage_restore(args.archive, args.destination)
    except (OSError, ValueError, sqlite3.Error, tarfile.TarError) as exc:
        parser.exit(1, f"Restore staging failed: {exc}\n")
    print("Restore staged and SQLite integrity verified; no live data was replaced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
