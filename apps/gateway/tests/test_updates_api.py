"""Tests for update and signed release channel checks (M01.5)."""

from __future__ import annotations

from app.api.updates import evaluate_release_channel, update_status


def test_update_status_main_revision() -> None:
    sha_a = "a" * 40
    sha_b = "b" * 40
    assert update_status("unknown", sha_a) == "installed_revision_unknown"
    assert update_status(sha_a, None) == "check_unavailable"
    assert update_status(sha_a, sha_a) == "up_to_date"
    assert update_status(sha_a, sha_b) == "revision_differs"


def test_evaluate_release_channel_requires_verified_signature() -> None:
    unverified = evaluate_release_channel(
        installed_version="v0.1.0",
        latest_tag="v0.2.0",
        signature_verified=False,
    )
    assert unverified["channel"] == "signed_release"
    assert unverified["status"] == "release_unverified"

    available = evaluate_release_channel(
        installed_version="v0.1.0",
        latest_tag="v0.2.0",
        signature_verified=True,
    )
    assert available["status"] == "signed_release_available"

    current = evaluate_release_channel(
        installed_version="0.2.0",
        latest_tag="v0.2.0",
        signature_verified=True,
    )
    assert current["status"] == "up_to_date"
