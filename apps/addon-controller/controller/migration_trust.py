"""Detached Ed25519 migration attestation verified against operator trust roots.

The signed payload binds the exact canonical migration contract and matching
previous-release backup-manifest hash. This is NOT authorization to migrate
a production database. Administrator-managed publisher keys are shared with
OCI release trust but scoped to migration-artifact verification here.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .release_trust import (
    KEY_ID,
    ReleaseTrustError,
    ReleaseTrustStore,
    _private_file,
)
from .sqlite_migration import MigrationContract

_HEX = "0123456789abcdef"


class MigrationTrustError(ValueError):
    pass


def canonical_contract_hash(contract: MigrationContract) -> str:
    payload = json.dumps(
        contract.model_dump(mode="json"),
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class MigrationAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    addon_id: str
    source_version: str
    source_image: str
    target_version: str
    target_image: str
    contract_sha256: str = Field(min_length=64, max_length=64)
    backup_manifest_sha256: str = Field(min_length=64, max_length=64)
    publisher_key_id: str
    signature_ed25519_base64: str = Field(min_length=80, max_length=100)

    @model_validator(mode="after")
    def fields(self) -> MigrationAttestation:
        if (
            not KEY_ID.fullmatch(self.publisher_key_id)
            or any(char not in _HEX for char in self.contract_sha256)
            or any(char not in _HEX for char in self.backup_manifest_sha256)
        ):
            raise ValueError("Invalid signed migration artifact identifiers")
        return self

    def signing_bytes(self) -> bytes:
        payload = self.model_dump(exclude={"signature_ed25519_base64"})
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")


def verify_migration_attestation(
    contract: MigrationContract,
    backup_manifest: Path,
    attestation_path: Path,
    trust_path: Path,
) -> dict[str, str]:
    """Fail closed on stale, revoked, tampered or untrusted migration artifacts."""
    try:
        attestation = MigrationAttestation.model_validate(
            _private_file(attestation_path)
        )
        trust = ReleaseTrustStore.model_validate(_private_file(trust_path))
    except (ReleaseTrustError, ValueError, binascii.Error) as exc:
        raise MigrationTrustError("Invalid migration attestation or trust policy") from exc
    if (
        attestation.addon_id != contract.addon_id
        or attestation.source_version != contract.source_version
        or attestation.source_image != contract.source_image
        or attestation.target_version != contract.target_version
        or attestation.target_image != contract.target_image
        or attestation.contract_sha256 != canonical_contract_hash(contract)
    ):
        raise MigrationTrustError("Migration attestation does not match reviewed contract")
    if (
        backup_manifest.is_symlink() or not backup_manifest.is_file()
        or backup_manifest.stat().st_size > 2_000_000
    ):
        raise MigrationTrustError("Backup manifest is missing or unsafe")
    manifest_sha = hashlib.sha256(backup_manifest.read_bytes()).hexdigest()
    if attestation.backup_manifest_sha256 != manifest_sha:
        raise MigrationTrustError("Migration attestation does not match backup manifest")
    matching = next(
        (item for item in trust.publishers
         if item.key_id == attestation.publisher_key_id),
        None,
    )
    if matching is None or matching.revoked:
        raise MigrationTrustError("Migration publisher is untrusted or revoked")
    if (
        matching.not_after_utc is not None
        and datetime.now(UTC) >= matching.not_after_utc
    ):
        raise MigrationTrustError("Migration publisher key has expired")
    try:
        sig = base64.b64decode(
            attestation.signature_ed25519_base64, validate=True,
        )
        if len(sig) != 64:
            raise MigrationTrustError("Invalid migration signature length")
        key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(matching.public_key_ed25519_base64, validate=True)
        )
        key.verify(sig, attestation.signing_bytes())
    except (InvalidSignature, ValueError, binascii.Error) as exc:
        raise MigrationTrustError("Migration signature verification failed") from exc
    return {
        "status": "ed25519_verified",
        "publisher_key_id": matching.key_id,
        "addon_id": contract.addon_id,
        "contract_sha256": attestation.contract_sha256,
        "backup_manifest_sha256": manifest_sha,
        "production_migration_allowed": "false",
    }
