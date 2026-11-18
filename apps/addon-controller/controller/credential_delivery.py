"""Offline, host-administrator-only delivery to an approved Docker secret volume.

No HTTP endpoint, shell interpolation, or secret in argv, environment or logs.
Delivery writes plaintext to the designated Docker volume; the volume is NOT
encrypted. Rotate provider credentials separately and restart applications
that do not reload files on change.
"""
from __future__ import annotations

import json
import os
import secrets
import stat
import subprocess
from pathlib import Path

from .credential_vault import CredentialVault, VaultError

LABEL_ADDON = "io.yonder.addon"
LABEL_APPROVED = "io.yonder.approved"
LABEL_PURPOSE = "io.yonder.purpose"


class DeliveryError(ValueError):
    pass


def _volume_details(addon_id: str) -> Path:
    """Docker daemon must be local; never accept caller-provided mount paths."""
    name = f"yonder-addon-secrets-{addon_id}"
    try:
        process = subprocess.run(
            ["/usr/bin/docker", "volume", "inspect", name],
            check=True, capture_output=True, timeout=15,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
        )
        payload = json.loads(process.stdout)
        details = payload[0]
        labels = details.get("Labels") or {}
        if (
            details.get("Name") != name
            or details.get("Driver") != "local"
            or labels.get(LABEL_APPROVED) != "true"
            or labels.get(LABEL_ADDON) != addon_id
            or labels.get(LABEL_PURPOSE) != "secrets"
        ):
            raise DeliveryError("Docker volume is not approved for this add-on")
        path = details.get("Mountpoint")
        if not isinstance(path, str) or not path.startswith("/"):
            raise DeliveryError("Invalid Docker secret volume mountpoint")
        # Docker daemon chooses mountpoint; do not resolve arbitrary caller paths.
        return Path(path)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError, KeyError) as exc:
        if isinstance(exc, DeliveryError):
            raise
        raise DeliveryError("Approved secret volume inspection failed") from exc


def _open_directory(path: Path) -> int:
    if path.is_symlink() or not path.is_dir():
        raise DeliveryError("Secret destination is missing or a symlink")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
            raise DeliveryError("Secret destination must not be writable by other users")
        if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
            raise DeliveryError("Secret destination must be owned by the administrator")
        return descriptor
    except OSError as exc:
        raise DeliveryError("Secret destination could not be opened securely") from exc


def _atomic_write(directory: int, filename: str, value: bytes) -> None:
    # filename is a validated credential identifier, not a relative path.
    temporary = f".{filename}.{secrets.token_hex(12)}.pending"
    fd = -1
    try:
        fd = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
            0o600, dir_fd=directory,
        )
        with os.fdopen(fd, "wb", closefd=True) as stream:
            fd = -1
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        # Never follow existing target symlinks or overwrite unexpected files.
        try:
            target_stat = os.stat(
                filename, dir_fd=directory, follow_symlinks=False
            )
        except FileNotFoundError:
            target_stat = None
        if target_stat is not None and (
            not stat.S_ISREG(target_stat.st_mode)
            or target_stat.st_nlink != 1
        ):
            raise DeliveryError("Existing secret target is not a regular, unlinked file")
        os.replace(
            temporary, filename, src_dir_fd=directory, dst_dir_fd=directory
        )
        os.fsync(directory)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def deliver(
    vault: CredentialVault, addon_id: str, credential_name: str,
    *, approved_addon_ids: set[str],
) -> None:
    """Deliver one explicitly selected credential and nothing else."""
    # Validate identifiers BEFORE contacting Docker.
    vault._identity(addon_id, credential_name)
    if addon_id not in approved_addon_ids:
        raise DeliveryError("Add-on is not in the local operator-approved deployment list")
    plaintext = vault.read_for_offline_delivery(addon_id, credential_name)
    directory = _open_directory(_volume_details(addon_id))
    try:
        _atomic_write(directory, credential_name, plaintext)
    finally:
        os.close(directory)


def remove_delivered(
    vault: CredentialVault, addon_id: str, credential_name: str,
    *, approved_addon_ids: set[str],
) -> None:
    """Remove a delivered file after confirmed source revocation."""
    vault._identity(addon_id, credential_name)
    if addon_id not in approved_addon_ids:
        raise DeliveryError("Add-on is not approved")
    records = vault.metadata(addon_id)
    matched = next((item for item in records if item["name"] == credential_name), None)
    if matched is None or matched["configured"]:
        raise DeliveryError("Revoke the credential in the local vault first")
    directory = _open_directory(_volume_details(addon_id))
    try:
        try:
            current = os.stat(
                credential_name, dir_fd=directory, follow_symlinks=False
            )
        except FileNotFoundError:
            return
        if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
            raise DeliveryError("Delivered target is not a safe regular file")
        os.unlink(credential_name, dir_fd=directory)
        os.fsync(directory)
    finally:
        os.close(directory)
