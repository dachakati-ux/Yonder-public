"""Release signatures must bind a trusted publisher to exact immutable images."""

from __future__ import annotations

import base64
import json
import os

import pytest
from controller.models import ApprovedDeployment
from controller.release_trust import ReleaseDescriptor, ReleaseTrustError, verify_release
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

IMAGE = "registry.example.org/yonder/demo@sha256:" + "a" * 64


def fixtures(tmp_path):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    trust = tmp_path / "publishers.json"
    trust.write_text(json.dumps({
        "schema_version": 1,
        "publishers": [{
            "key_id": "yonder-official",
            "public_key_ed25519_base64": base64.b64encode(public).decode(),
        }],
    }))
    release = tmp_path / "demo-addon.json"
    unsigned = ReleaseDescriptor(
        schema_version=1,
        addon_id="demo-addon",
        version="0.1.0",
        image=IMAGE,
        publisher_key_id="yonder-official",
        signature_ed25519_base64=base64.b64encode(b"0" * 64).decode(),
    )
    signed = unsigned.model_copy(update={
        "signature_ed25519_base64": base64.b64encode(
            key.sign(unsigned.signing_bytes())
        ).decode(),
    })
    release.write_text(json.dumps(signed.model_dump()))
    if os.name == "posix":
        os.chmod(trust, 0o600)
        os.chmod(release, 0o600)
    deployment = ApprovedDeployment(
        addon_id="demo-addon", version="0.1.0", image=IMAGE
    )
    return deployment, release, trust


def test_valid_release_accepted(tmp_path):
    deployment, release, trust = fixtures(tmp_path)
    proof = verify_release(deployment, release, trust)
    assert proof["publisher_key_id"] == "yonder-official"
    assert proof["verification"] == "ed25519_valid"


def test_tampered_release_rejected(tmp_path):
    deployment, release, trust = fixtures(tmp_path)
    payload = json.loads(release.read_text())
    payload["version"] = "0.1.1"
    release.write_text(json.dumps(payload))
    with pytest.raises(ReleaseTrustError):
        verify_release(deployment, release, trust)


def test_tampered_signature_rejected(tmp_path):
    deployment, release, trust = fixtures(tmp_path)
    payload = json.loads(release.read_text())
    payload["signature_ed25519_base64"] = base64.b64encode(b"1" * 64).decode()
    release.write_text(json.dumps(payload))
    with pytest.raises(ReleaseTrustError, match="signature"):
        verify_release(deployment, release, trust)


def test_untrusted_publisher_rejected(tmp_path):
    deployment, release, trust = fixtures(tmp_path)
    payload = json.loads(release.read_text())
    payload["publisher_key_id"] = "unknown-publisher"
    release.write_text(json.dumps(payload))
    with pytest.raises(ReleaseTrustError, match="not trusted"):
        verify_release(deployment, release, trust)


def test_deployment_must_match_signed_digest(tmp_path):
    deployment, release, trust = fixtures(tmp_path)
    changed = deployment.model_copy(update={
        "image": "registry.example.org/yonder/demo@sha256:" + "b" * 64
    })
    with pytest.raises(ReleaseTrustError, match="does not match"):
        verify_release(changed, release, trust)


def test_group_writable_policy_rejected(tmp_path):
    deployment, release, trust = fixtures(tmp_path)
    if os.name != "posix":
        pytest.skip("POSIX permissions required")
    os.chmod(trust, 0o666)
    with pytest.raises(ReleaseTrustError, match="trust policy"):
        verify_release(deployment, release, trust)
