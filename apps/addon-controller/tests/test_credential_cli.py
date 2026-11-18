"""Offline operator command must enforce credential-name grants."""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest
import credentials_cli
from controller.credential_vault import CredentialVault, create_master_key
from credentials_cli import main


@pytest.mark.parametrize("credential_name, allowed", [
    ("SERVICE_TOKEN", True),
    ("OTHER_TOKEN", False),
])
def test_offline_cli_grants_only_declared_credentials(
    tmp_path, monkeypatch, credential_name, allowed
):
    key = tmp_path / "master.key"
    db = tmp_path / "vault.db"
    create_master_key(key)
    vault = CredentialVault(db, key)
    vault.put("convyx", credential_name, b"example-private-token")
    deployment = tmp_path / "deployments.json"
    deployment.write_text(json.dumps({
        "schema_version": 1,
        "deployments": [{
            "addon_id": "convyx", "version": "0.1.0",
            "image": "ghcr.io/example/convyx@sha256:" + "a" * 64,
            "secret_volume": True,
            "credential_names": ["SERVICE_TOKEN"],
        }],
    }))
    directory = tmp_path / "volume"
    directory.mkdir(mode=0o700)
    os.chmod(directory, 0o700)
    monkeypatch.setattr(
        "controller.credential_delivery._volume_details", lambda _: directory
    )
    monkeypatch.setattr(credentials_cli, "os", SimpleNamespace(geteuid=lambda: 0))
    monkeypatch.setattr(
        sys, "argv", [
            "credentials_cli.py", "--database", str(db),
            "--key-file", str(key), "--approvals", str(deployment),
            "deliver", "convyx", credential_name,
        ],
    )
    status = main()
    assert (status == 0) is allowed
    target = directory / credential_name
    assert target.exists() is allowed
    if allowed:
        assert target.read_bytes() == b"example-private-token"
