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
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import ApprovedDeployment

KEY_ID = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
IMAGE_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


class ReleaseTrustError(ValueError):
    pass


class ReleaseDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(ge=1, le=1)
    addon_id: str
    version: str
    image: str
    publisher_key_id: str
    signature_ed25519_base64: str = Field(min_length=80, max_length=100)

    @model_validator(mode="after")
    def check_fields(self) -> ReleaseDescriptor:
        ApprovedDeployment(
            addon_id=self.addon_id, version=self.version, image=self.image
        )
        if not KEY_ID.fullmatch(self.publisher_key_id):
            raise ValueError("Invalid publisher key ID")
        return self

    def signing_bytes(self) -> bytes:
        payload = {
            "schema_version": self.schema_version,
            "addon_id": self.addon_id,
            "version": self.version,
            "image": self.image,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


class TrustedPublisher(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key_id: str
    public_key_ed25519_base64: str

    @model_validator(mode="after")
    def valid_key(self) -> TrustedPublisher:
        if not KEY_ID.fullmatch(self.key_id):
            raise ValueError("Invalid trust key ID")
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
    deployment: ApprovedDeployment, descriptor_path: Path, trust_path: Path
) -> dict[str, str]:
    """Validate publisher signature against installed trust roots, fail closed."""
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
    return {
        "publisher_key_id": descriptor.publisher_key_id,
        "addon_id": descriptor.addon_id,
        "version": descriptor.version,
        "image": descriptor.image,
        "verification": "ed25519_valid",
    }
