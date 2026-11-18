"""Offline file-tree backup validation for future stateful add-on upgrades.

This module never snapshots a live Docker volume, executes migrations, or
restores into a running service. It verifies an operator-provided, quiesced
backup bundle and proves its contents can be restored into an empty sandbox.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

_SAFE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/-]*(?::[a-z0-9._-]+)?@sha256:[a-f0-9]{64}$")
_MAX_FILES = 10000
_MAX_TOTAL = 10 * 1024**3
_CHUNK = 1024 * 1024


class BackupError(ValueError):
    """No filesystem internals or backup contents should leak through APIs."""


class BackupEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    size: int = Field(ge=0, le=_MAX_TOTAL)
    sha256: str

    @model_validator(mode="after")
    def restricted(self) -> BackupEntry:
        if not _SAFE_NAME.fullmatch(self.name) or self.name.startswith("."):
            raise ValueError("Backup paths must be flat safe file names")
        if not _DIGEST.fullmatch(self.sha256):
            raise ValueError("Invalid SHA-256 checksum")
        return self


class BackupManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(ge=1, le=1)
    addon_id: str
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    image: str
    created_at: datetime
    source_quiesced: bool
    backup_kind: str = Field(pattern=r"^(files|database_export)$")
    files: list[BackupEntry] = Field(min_length=1, max_length=_MAX_FILES)

    @model_validator(mode="after")
    def restricted(self) -> BackupManifest:
        if not re.fullmatch(r"^[a-z][a-z0-9-]{1,63}$", self.addon_id):
            raise ValueError("Invalid add-on")
        if not _IMAGE.fullmatch(self.image):
            raise ValueError("Invalid source image identity")
        if self.created_at.utcoffset() is None:
            raise ValueError("Backup timestamps must have a timezone")
        names = [entry.name for entry in self.files]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate backup paths")
        if sum(entry.size for entry in self.files) > _MAX_TOTAL:
            raise ValueError("Backup exceeds approved maximum")
        return self


def create_offline_bundle(
    source: Path, destination: Path, *,
    addon_id: str, version: str, image: str,
    backup_name: str = "database.dump",
    kind: str = "database_export",
    source_quiesced_acknowledged: bool = False,
) -> dict:
    """Copy one already-quiesced export into a new, private backup directory.

    The operator must stop/quiesce their application independently. This
    method cannot determine whether a database dump is transactionally safe.
    """
    if not source_quiesced_acknowledged:
        raise BackupError("Explicit source quiescence acknowledgment is required")
    if not _SAFE_NAME.fullmatch(backup_name) or backup_name.startswith("."):
        raise BackupError("Invalid backup file name")
    if backup_name == "manifest.json":
        raise BackupError("Backup name is reserved")
    if destination.is_symlink() or destination.exists():
        raise BackupError("Backup destination must not exist")
    with _open_regular(source) as input_file:
        destination.mkdir(mode=0o700, parents=False)
        try:
            os.chmod(destination, 0o700)
            written = 0
            checksum = hashlib.sha256()
            target_path = destination / backup_name
            fd = os.open(
                target_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY
                | getattr(os, "O_NOFOLLOW", 0), 0o600,
            )
            with os.fdopen(fd, "wb") as target:
                while block := input_file.read(_CHUNK):
                    written += len(block)
                    if written > _MAX_TOTAL:
                        raise BackupError("Export exceeds maximum backup size")
                    target.write(block)
                    checksum.update(block)
                target.flush()
                os.fsync(target.fileno())
            manifest = BackupManifest(
                schema_version=1,
                addon_id=addon_id,
                version=version,
                image=image,
                created_at=datetime.now(UTC),
                source_quiesced=True,
                backup_kind=kind,
                files=[BackupEntry(
                    name=backup_name, size=written,
                    sha256=checksum.hexdigest(),
                )],
            )
            record = destination / "manifest.json"
            fd = os.open(
                record, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                | getattr(os, "O_NOFOLLOW", 0), 0o600,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as manifest_file:
                json.dump(manifest.model_dump(mode="json"), manifest_file, indent=2)
                manifest_file.flush()
                os.fsync(manifest_file.fileno())
            return verify_bundle(
                destination, addon_id=addon_id, version=version, image=image,
            )
        except Exception:
            # A partially written bundle must never be mistaken for a valid
            # backup. Only unlink names this method could create.
            for name in (backup_name, "manifest.json"):
                (destination / name).unlink(missing_ok=True)
            destination.rmdir()
            raise


def _read_manifest(root: Path) -> BackupManifest:
    if root.is_symlink() or not root.is_dir():
        raise BackupError("Backup directory is missing or untrusted")
    if os.name == "posix" and root.stat().st_mode & 0o022:
        raise BackupError("Backup directory must not be group/other writable")
    path = root / "manifest.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
        raise BackupError("Missing or untrusted backup manifest")
    try:
        return BackupManifest.model_validate(json.loads(path.read_text("utf-8")))
    except (OSError, ValueError) as exc:
        raise BackupError("Invalid backup manifest") from exc


def _open_regular(path: Path):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(fd)
            raise BackupError("Backup entry is not an ordinary file")
        return os.fdopen(fd, "rb")
    except OSError as exc:
        raise BackupError("Backup entry cannot be opened safely") from exc


def verify_bundle(
    root: Path, *, addon_id: str, version: str, image: str,
    max_age_hours: int = 24,
) -> dict:
    """Verify exact previous-release backup bytes; no external side effects."""
    manifest = _read_manifest(root)
    if (
        manifest.addon_id != addon_id
        or manifest.version != version
        or manifest.image != image
    ):
        raise BackupError("Backup belongs to a different release")
    if not manifest.source_quiesced:
        raise BackupError("Source must be quiesced for consistent backup")
    age_seconds = (datetime.now(UTC) - manifest.created_at).total_seconds()
    if age_seconds < -300 or age_seconds > max_age_hours * 3600:
        raise BackupError("Backup time is outside the approved freshness window")
    observed = set(os.listdir(root))
    expected = {"manifest.json", *(entry.name for entry in manifest.files)}
    if observed != expected:
        raise BackupError("Unexpected or missing backup entries")
    for item in manifest.files:
        with _open_regular(root / item.name) as stream:
            digest = hashlib.sha256()
            count = 0
            while block := stream.read(_CHUNK):
                count += len(block)
                if count > item.size:
                    raise BackupError("Backup entry exceeds recorded size")
                digest.update(block)
            if count != item.size or digest.hexdigest() != item.sha256:
                raise BackupError("Backup checksum verification failed")
    return {
        "status": "verified_contents_only",
        "addon_id": addon_id,
        "version": version,
        "image": image,
        "file_count": len(manifest.files),
        "total_bytes": sum(item.size for item in manifest.files),
        "source_quiesced_declared": True,
        "restore_tested": False,
        "migration_safe": False,
    }


def verify_restore_into_empty_directory(
    root: Path, destination: Path, *, addon_id: str, version: str, image: str,
) -> dict:
    """Prove bytes can be restored into a new empty directory, not live data."""
    info = verify_bundle(root, addon_id=addon_id, version=version, image=image)
    if (
        destination.is_symlink() or not destination.is_dir()
        or any(destination.iterdir())
    ):
        raise BackupError("Restore target must be a separate empty directory")
    if os.name == "posix" and destination.stat().st_mode & 0o077:
        raise BackupError("Restore target must have private permissions")
    manifest = _read_manifest(root)
    for item in manifest.files:
        output_path = destination / item.name
        fd = os.open(
            output_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
            | getattr(os, "O_NOFOLLOW", 0), 0o600,
        )
        try:
            with os.fdopen(fd, "wb") as out, _open_regular(root / item.name) as source:
                while block := source.read(_CHUNK):
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
        except Exception:
            output_path.unlink(missing_ok=True)
            raise
        with _open_regular(output_path) as check:
            digest = hashlib.sha256()
            while block := check.read(_CHUNK):
                digest.update(block)
        if digest.hexdigest() != item.sha256:
            raise BackupError("Restored bytes did not match source checksum")
    return {
        **info,
        "status": "sandbox_restore_verified",
        "restore_tested": True,
        "restore_target": "separate_empty_directory",
        "migration_safe": False,
    }
