"""Local encrypted add-on credential inventory.

No credential plaintext or encryption key ever enters the HTTP API.
The offline administrator CLI owns writes. This is NOT yet secret delivery
into application containers; that requires a separately reviewed broker.
"""
from __future__ import annotations

import base64
import os
import re
import sqlite3
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ADDON = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
SECRET = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
AAD_PREFIX = b"yonder-addon-credential-v1:"
MAX_VALUE_BYTES = 16_384


class VaultError(ValueError):
    """No raw secret bytes should ever appear in these errors."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _secure_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise VaultError("Credential store or key file is missing or not a regular file")
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise VaultError("Credential files require restrictive permissions (0600)")


def create_master_key(path: Path) -> None:
    """Atomic exclusive creation: never overwrite an existing encryption key."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.exists():
        raise VaultError("Encryption key already exists; refusing overwrite")
    raw = base64.urlsafe_b64encode(os.urandom(32))
    try:
        with path.open("xb") as output:
            os.chmod(path, 0o600)
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    except OSError as exc:
        raise VaultError("Unable to create private master key") from exc


def read_master_key(path: Path) -> bytes:
    _secure_file(path)
    try:
        encoded = path.read_bytes().strip()
        raw = base64.urlsafe_b64decode(encoded)
    except (OSError, ValueError) as exc:
        raise VaultError("Encryption key is unreadable") from exc
    if len(raw) != 32 or len(encoded) != 44:
        raise VaultError("Encryption key has invalid length")
    return raw


class CredentialVault:
    def __init__(self, database: Path, key_path: Path):
        self.database = database
        self.key_path = key_path
        self._lock = threading.RLock()
        self.database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # A 0600 database must be provisioned before sqlite can create it.
        if not self.database.exists():
            fd = os.open(self.database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        _secure_file(self.database)
        with self._connection() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS credentials (
                    addon_id TEXT NOT NULL,
                    credential_name TEXT NOT NULL,
                    nonce BLOB NOT NULL,
                    ciphertext BLOB NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(addon_id, credential_name)
                )"""
            )

    def _connection(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=20)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _identity(addon_id: str, name: str) -> bytes:
        if not ADDON.fullmatch(addon_id) or not SECRET.fullmatch(name):
            raise VaultError("Invalid credential identifier")
        return AAD_PREFIX + addon_id.encode() + b":" + name.encode()

    def put(self, addon_id: str, name: str, value: bytes) -> int:
        aad = self._identity(addon_id, name)
        if not value or len(value) > MAX_VALUE_BYTES:
            raise VaultError("Credential length outside approved limits")
        cipher = AESGCM(read_master_key(self.key_path))
        nonce = os.urandom(12)
        encrypted = cipher.encrypt(nonce, value, aad)
        with self._lock, self._connection() as db:
            row = db.execute(
                "SELECT revision FROM credentials WHERE addon_id=? AND credential_name=?",
                (addon_id, name),
            ).fetchone()
            revision = (int(row["revision"]) + 1) if row else 1
            db.execute(
                "INSERT INTO credentials "
                "(addon_id, credential_name, nonce, ciphertext, revision, updated_at, revoked) "
                "VALUES (?,?,?,?,?,?,0) ON CONFLICT(addon_id,credential_name) "
                "DO UPDATE SET nonce=excluded.nonce, ciphertext=excluded.ciphertext, "
                "revision=excluded.revision, updated_at=excluded.updated_at, revoked=0",
                (addon_id, name, nonce, encrypted, revision, _now()),
            )
            return revision

    def revoke(self, addon_id: str, name: str) -> int:
        self._identity(addon_id, name)
        with self._lock, self._connection() as db:
            row = db.execute(
                "SELECT revision FROM credentials WHERE addon_id=? AND credential_name=?",
                (addon_id, name),
            ).fetchone()
            if row is None:
                raise VaultError("Credential does not exist")
            revision = int(row["revision"]) + 1
            # Eliminate active encrypted bytes; disk backups still need rotation policy.
            db.execute(
                "UPDATE credentials SET ciphertext=?, nonce=?, revoked=1, "
                "revision=?, updated_at=? WHERE addon_id=? AND credential_name=?",
                (b"", b"", revision, _now(), addon_id, name),
            )
            return revision

    def metadata(self, addon_id: str | None = None) -> list[dict]:
        if addon_id is not None and not ADDON.fullmatch(addon_id):
            raise VaultError("Invalid add-on identifier")
        query = (
            "SELECT addon_id,credential_name,revision,updated_at,revoked "
            "FROM credentials"
        )
        params: tuple[str, ...] = ()
        if addon_id is not None:
            query += " WHERE addon_id=?"
            params = (addon_id,)
        query += " ORDER BY addon_id,credential_name"
        with self._lock, self._connection() as db:
            rows = db.execute(query, params).fetchall()
            return [
                {
                    "addon_id": item["addon_id"],
                    "name": item["credential_name"],
                    "revision": item["revision"],
                    "updated_at": item["updated_at"],
                    "configured": not bool(item["revoked"]),
                }
                for item in rows
            ]

    def _decrypt(self, item: sqlite3.Row, key: bytes) -> bytes:
        return AESGCM(key).decrypt(
            item["nonce"], item["ciphertext"],
            self._identity(item["addon_id"], item["credential_name"]),
        )

    def rotate_master_key(self, replacement: Path) -> int:
        """Re-encrypt every active record, atomically, after external key backup.

        The caller must swap the master-key file only AFTER commit. To avoid
        unsafe two-phase key cutover, this method intentionally does not expose
        an automatic rotation API; use a coordinated offline rotation procedure.
        """
        raise VaultError("Offline master-key rotation needs coordinated key cutover")
