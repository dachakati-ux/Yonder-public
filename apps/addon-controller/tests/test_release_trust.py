"""Release signatures must bind a trusted publisher to exact immutable images."""

from __future__ import annotations

import base64
import json
import os

import pytest
from controller.engine import Controller, ControllerError
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


def test_controller_plan_requires_trusted_publisher_when_enforced(
    tmp_path, monkeypatch
):
    deployment, release, trust = fixtures(tmp_path)
    cfg = tmp_path / "deployments.json"
    cfg.write_text(json.dumps({
        "schema_version": 1,
        "deployments": [deployment.model_dump()],
    }))
    monkeypatch.setenv("YONDER_CONTROLLER_REQUIRE_SIGNATURES", "true")
    monkeypatch.setenv("YONDER_CONTROLLER_PUBLISHER_KEYS", str(trust))
    monkeypatch.setenv("YONDER_CONTROLLER_RELEASE_ROOT", str(tmp_path))
    controller = Controller(cfg, tmp_path / "state.db")
    plan = controller.plan("demo-addon", "install")
    assert plan["publisher_key_id"] == "yonder-official"
    assert plan["publisher_verification"] == "ed25519_valid"
    release.unlink()
    with pytest.raises(ControllerError, match="publisher verification"):
        controller.plan("demo-addon", "install")


def test_remote_pull_is_blocked_without_required_signatures(
    tmp_path, monkeypatch
):
    deployment, release, trust = fixtures(tmp_path)
    cfg = tmp_path / "deployments.json"
    deployment = deployment.model_copy(update={
        "image_acquisition": "signed_pull"
    })
    cfg.write_text(json.dumps({
        "schema_version": 1,
        "deployments": [deployment.model_dump()],
    }))
    monkeypatch.setenv("YONDER_CONTROLLER_REQUIRE_SIGNATURES", "false")
    controller = Controller(cfg, tmp_path / "unsigned.db")
    with pytest.raises(ControllerError, match="requires signed"):
        controller.plan("demo-addon", "install")
    monkeypatch.setenv("YONDER_CONTROLLER_REQUIRE_SIGNATURES", "true")
    monkeypatch.setenv("YONDER_CONTROLLER_PUBLISHER_KEYS", str(trust))
    monkeypatch.setenv("YONDER_CONTROLLER_RELEASE_ROOT", str(tmp_path))
    verified = Controller(cfg, tmp_path / "signed.db")
    result = verified.plan("demo-addon", "install")
    assert result["image_acquisition"] == "signed_pull"
    assert result["publisher_verification"] == "ed25519_valid"


def test_revoked_publisher_key_fails_closed(tmp_path):
    deployment, release, trust = fixtures(tmp_path)
    payload = json.loads(trust.read_text())
    payload["publishers"][0]["revoked"] = True
    trust.write_text(json.dumps(payload))
    with pytest.raises(ReleaseTrustError, match="revoked"):
        verify_release(deployment, release, trust)


def test_expired_publisher_key_fails_closed(tmp_path):
    deployment, release, trust = fixtures(tmp_path)
    payload = json.loads(trust.read_text())
    payload["publishers"][0]["not_after_utc"] = "2020-01-01T00:00:00+00:00"
    trust.write_text(json.dumps(payload))
    with pytest.raises(ReleaseTrustError, match="expired"):
        verify_release(deployment, release, trust)


def test_future_expiration_allows_valid_signature(tmp_path):
    deployment, release, trust = fixtures(tmp_path)
    payload = json.loads(trust.read_text())
    payload["publishers"][0]["not_after_utc"] = "2099-01-01T00:00:00+00:00"
    trust.write_text(json.dumps(payload))
    assert verify_release(deployment, release, trust)["verification"] == "ed25519_valid"


def test_naive_expiration_is_rejected(tmp_path):
    deployment, release, trust = fixtures(tmp_path)
    payload = json.loads(trust.read_text())
    payload["publishers"][0]["not_after_utc"] = "2099-01-01T00:00:00"
    trust.write_text(json.dumps(payload))
    with pytest.raises(ReleaseTrustError, match="Invalid signed"):
        verify_release(deployment, release, trust)


def test_deprecated_and_incompatible_core_versions_fail_closed(tmp_path):
    from controller.release_trust import TrustedPublisher, revoke_trust_key, rotate_trust_key

    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    trust = tmp_path / "publishers.json"
    trust.write_text(json.dumps({
        "schema_version": 1,
        "publishers": [{
            "key_id": "yonder-official",
            "public_key_ed25519_base64": base64.b64encode(pub).decode(),
        }],
    }))
    deployment = ApprovedDeployment(addon_id="demo-addon", version="0.1.0", image=IMAGE)

    # Deprecated release must be rejected even with a valid signature.
    dep_unsigned = ReleaseDescriptor(
        schema_version=1,
        addon_id="demo-addon",
        version="0.1.0",
        image=IMAGE,
        publisher_key_id="yonder-official",
        signature_ed25519_base64=base64.b64encode(b"0" * 64).decode(),
        deprecated=True,
        deprecation_reason="Superseded by 0.2.0",
    )
    dep_signed = dep_unsigned.model_copy(update={
        "signature_ed25519_base64": base64.b64encode(
            key.sign(dep_unsigned.signing_bytes())
        ).decode()
    })
    dep_path = tmp_path / "dep.json"
    dep_path.write_text(dep_signed.model_dump_json())
    with pytest.raises(ReleaseTrustError, match="deprecated"):
        verify_release(deployment, dep_path, trust)

    # Core version compatibility pinning.
    pin_unsigned = ReleaseDescriptor(
        schema_version=1,
        addon_id="demo-addon",
        version="0.1.0",
        image=IMAGE,
        publisher_key_id="yonder-official",
        signature_ed25519_base64=base64.b64encode(b"0" * 64).decode(),
        min_core_version="0.2.0",
        max_core_version="0.5.0",
    )
    pin_signed = pin_unsigned.model_copy(update={
        "signature_ed25519_base64": base64.b64encode(
            key.sign(pin_unsigned.signing_bytes())
        ).decode()
    })
    pin_path = tmp_path / "pin.json"
    pin_path.write_text(pin_signed.model_dump_json())
    with pytest.raises(ReleaseTrustError, match="Core >="):
        verify_release(deployment, pin_path, trust, core_version="0.1.0")
    assert verify_release(
        deployment, pin_path, trust, core_version="0.3.0"
    )["verification"] == "ed25519_valid"

    # Key rotation + revocation helpers.
    key2 = Ed25519PrivateKey.generate()
    pub2 = key2.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    rotated = rotate_trust_key(
        trust,
        TrustedPublisher(
            key_id="yonder-official-v2",
            public_key_ed25519_base64=base64.b64encode(pub2).decode(),
        ),
        revoke_key_id="yonder-official",
    )
    assert len(rotated.publishers) == 2
    with pytest.raises(ReleaseTrustError, match="revoked"):
        verify_release(deployment, pin_path, trust, core_version="0.3.0")
    revoked_v2 = revoke_trust_key(trust, "yonder-official-v2")
    assert all(p.revoked for p in revoked_v2.publishers)
