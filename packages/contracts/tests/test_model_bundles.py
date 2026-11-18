"""Tests for offline model-bundle verification."""

from __future__ import annotations

import hashlib
from pathlib import Path

from yonder_contracts.model_bundles import BundleStatus, verify_bundle_dir


def test_verify_incomplete_example_manifest(tmp_path: Path) -> None:
    bundle = tmp_path / "example"
    bundle.mkdir()
    (bundle / "bundle.yaml").write_text(
        """
id: demo
display_name: Demo
version: "1"
modality: chat
license: MIT
provenance: test
runtime:
  provider: not_selected
files:
  - path: files/weights.bin
    sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    size_bytes: 4
    required: true
status: not_bundled
""".strip(),
        encoding="utf-8",
    )
    result = verify_bundle_dir(bundle)
    assert result.valid is False
    assert result.status == BundleStatus.INCOMPLETE
    assert any(issue.code == "MISSING_FILE" for issue in result.issues)


def test_verify_ready_bundle_with_matching_checksum(tmp_path: Path) -> None:
    bundle = tmp_path / "ready"
    files = bundle / "files"
    files.mkdir(parents=True)
    payload = b"abcd"
    (files / "weights.bin").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (bundle / "bundle.yaml").write_text(
        f"""
id: ready-demo
display_name: Ready Demo
version: "1"
modality: embeddings
license: MIT
provenance: test
runtime:
  provider: mock
files:
  - path: files/weights.bin
    sha256: "{digest}"
    size_bytes: 4
    required: true
status: ready
""".strip(),
        encoding="utf-8",
    )
    result = verify_bundle_dir(bundle)
    assert result.valid is True
    assert result.status == BundleStatus.READY
