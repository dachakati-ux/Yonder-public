"""Detached Ed25519 verification for immutable add-on OCI release descriptors.

The controller NEVER downloads keys or trusts a key supplied in the release.
Trusted public keys must be installed by a host administrator in a private
controller configuration file. Signatures cover canonical descriptor bytes.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import ApprovedDeployment

KEY_ID = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
IMAGE_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
SEMVER = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)$")


class ReleaseTrustError(ValueError):
    pass


def parse_semver(raw: str) -> tuple[int, int, int]:
    match = SEMVER.fullmatch(raw.strip())
    if match is None:
        raise ValueError("Version must be major.minor.patch")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


class ReleaseDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(ge=1, le=1)
    addon_id: str
    version: str
    image: str
    publisher_key_id: str
    signature_ed25519_base64: str = Field(min_length=80, max_length=100)
    deprecated: bool = False
    deprecation_reason: str = Field(default="", max_length=240)
    min_core_version: str | None = None
    max_core_version: str | None = None

    @model_validator(mode="after")
    def check_fields(self) -> ReleaseDescriptor:
        ApprovedDeployment(
            addon_id=self.addon_id, version=self.version, image=self.image
        )
        if not KEY_ID.fullmatch(self.publisher_key_id):
            raise ValueError("Invalid publisher key ID")
        if self.min_core_version is not None:
            parse_semver(self.min_core_version)
        if self.max_core_version is not None:
            parse_semver(self.max_core_version)
        if (
            self.min_core_version is not None
            and self.max_core_version is not None
            and parse_semver(self.min_core_version) > parse_semver(self.max_core_version)
        ):
            raise ValueError("min_core_version cannot exceed max_core_version")
        return self

    def signing_bytes(self) -> bytes:
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "addon_id": self.addon_id,
            "version": self.version,
            "image": self.image,
        }
        if self.deprecated:
            payload["deprecated"] = True
            if self.deprecation_reason:
                payload["deprecation_reason"] = self.deprecation_reason
        if self.min_core_version is not None:
            payload["min_core_version"] = self.min_core_version
        if self.max_core_version is not None:
            payload["max_core_version"] = self.max_core_version
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


class TrustedPublisher(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key_id: str
    public_key_ed25519_base64: str
    revoked: bool = False
    not_after_utc: datetime | None = None

    @model_validator(mode="after")
    def valid_key(self) -> TrustedPublisher:
        if not KEY_ID.fullmatch(self.key_id):
            raise ValueError("Invalid trust key ID")
        if self.not_after_utc is not None and self.not_after_utc.utcoffset() is None:
            raise ValueError("Publisher key expiration must be timezone-aware")
        raw = base64.b64decode(self.public_key_ed25519_base64, validate=True)
        if len(raw) != 32:
            raise ValueError("Ed25519 public key must be 32 bytes")
        return self


class ReleaseTrustStore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(ge=1, le=1)
    publishers: list[TrustedPublisher] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def distinct(self) -> ReleaseTrustStore:
        names = [item.key_id for item in self.publishers]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate publisher trust IDs")
        return self


def _private_file(path: Path, *, max_bytes: int = 65_536) -> dict:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > max_bytes:
        raise ReleaseTrustError("Missing or oversized local release policy")
    if os.name == "posix" and path.stat().st_mode & 0o022:
        raise ReleaseTrustError("Release policy may not be writable by group or others")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseTrustError("Invalid release policy JSON") from exc
    if not isinstance(value, dict):
        raise ReleaseTrustError("Release policy must be an object")
    return value


def verify_release(
    deployment: ApprovedDeployment,
    descriptor_path: Path,
    trust_path: Path,
    *,
    core_version: str | None = None,
) -> dict[str, str]:
    """Validate publisher signature, deprecation, and core compatibility, fail closed."""
    try:
        descriptor = ReleaseDescriptor.model_validate(_private_file(descriptor_path))
        trust = ReleaseTrustStore.model_validate(_private_file(trust_path))
    except (ValueError, binascii.Error) as exc:
        raise ReleaseTrustError("Invalid signed release or trust policy") from exc

    if (
        descriptor.addon_id != deployment.addon_id
        or descriptor.version != deployment.version
        or descriptor.image != deployment.image
    ):
        raise ReleaseTrustError("Signed release does not match approved deployment")
    match = next(
        (key for key in trust.publishers if key.key_id == descriptor.publisher_key_id),
        None,
    )
    if match is None:
        raise ReleaseTrustError("Release publisher is not trusted")
    if match.revoked:
        raise ReleaseTrustError("Release publisher key has been revoked")
    if match.not_after_utc is not None and datetime.now(UTC) >= match.not_after_utc:
        raise ReleaseTrustError("Release publisher key has expired")
    try:
        signature = base64.b64decode(
            descriptor.signature_ed25519_base64, validate=True
        )
        if len(signature) != 64:
            raise ValueError("Invalid Ed25519 signature size")
        key_bytes = base64.b64decode(match.public_key_ed25519_base64, validate=True)
        public_key = Ed25519PublicKey.from_public_bytes(key_bytes)
        public_key.verify(signature, descriptor.signing_bytes())
    except (InvalidSignature, ValueError, binascii.Error) as exc:
        raise ReleaseTrustError("Release signature verification failed") from exc
    if descriptor.deprecated:
        detail = f": {descriptor.deprecation_reason}" if descriptor.deprecation_reason else ""
        raise ReleaseTrustError(f"Signed release is deprecated{detail}")
    effective_core = core_version or os.getenv("YONDER_CORE_VERSION", "0.1.0")
    try:
        current_core = parse_semver(effective_core)
    except ValueError as exc:
        raise ReleaseTrustError("Invalid Yonder Core version for compatibility check") from exc
    if descriptor.min_core_version is not None:
        if current_core < parse_semver(descriptor.min_core_version):
            raise ReleaseTrustError(
                f"Release requires Yonder Core >= {descriptor.min_core_version}"
            )
    if descriptor.max_core_version is not None:
        if current_core > parse_semver(descriptor.max_core_version):
            raise ReleaseTrustError(
                f"Release requires Yonder Core <= {descriptor.max_core_version}"
            )
    return {
        "publisher_key_id": descriptor.publisher_key_id,
        "addon_id": descriptor.addon_id,
        "version": descriptor.version,
        "image": descriptor.image,
        "verification": "ed25519_valid",
    }


def _write_trust_store(trust_path: Path, store: ReleaseTrustStore) -> None:
    tmp = trust_path.with_name(f".{trust_path.name}.pending")
    mode = trust_path.stat().st_mode & 0o777 if trust_path.exists() else 0o600
    if mode & 0o022:
        mode = 0o600
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as stream:
            fd = -1
            stream.write(json.dumps(store.model_dump(mode="json"), indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, trust_path)
    finally:
        if fd >= 0:
            os.close(fd)
        tmp.unlink(missing_ok=True)


def revoke_trust_key(trust_path: Path, key_id: str) -> ReleaseTrustStore:
    """Mark an existing trusted publisher key as revoked."""
    try:
        store = ReleaseTrustStore.model_validate(_private_file(trust_path))
    except ValueError as exc:
        raise ReleaseTrustError("Invalid trust store") from exc
    updated: list[TrustedPublisher] = []
    found = False
    for pub in store.publishers:
        if pub.key_id == key_id:
            found = True
            updated.append(pub.model_copy(update={"revoked": True}))
        else:
            updated.append(pub)
    if not found:
        raise ReleaseTrustError("Publisher key ID not found in trust store")
    new_store = store.model_copy(update={"publishers": updated})
    _write_trust_store(trust_path, new_store)
    return new_store


def rotate_trust_key(
    trust_path: Path,
    new_publisher: TrustedPublisher,
    *,
    revoke_key_id: str | None = None,
) -> ReleaseTrustStore:
    """Add a new publisher trust root and optionally revoke an older key atomically."""
    try:
        store = ReleaseTrustStore.model_validate(_private_file(trust_path))
    except ValueError as exc:
        raise ReleaseTrustError("Invalid trust store") from exc
    updated: list[TrustedPublisher] = []
    revoked_found = revoke_key_id is None
    for pub in store.publishers:
        if pub.key_id == new_publisher.key_id:
            raise ReleaseTrustError("New publisher key ID already exists in trust store")
        if revoke_key_id is not None and pub.key_id == revoke_key_id:
            revoked_found = True
            updated.append(pub.model_copy(update={"revoked": True}))
        else:
            updated.append(pub)
    if not revoked_found:
        raise ReleaseTrustError("Key ID to revoke was not found in trust store")
    updated.append(new_publisher)
    new_store = store.model_copy(update={"publishers": updated})
    _write_trust_store(trust_path, new_store)
    return new_store
