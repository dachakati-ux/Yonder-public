"""Offline signing utility for reviewed add-on release descriptors.

Run this on a trusted release-signing machine, never the controller host.
The signing key is provided as a local PEM file and never uploaded to Yonder.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path

from controller.models import ApprovedDeployment
from controller.release_trust import ReleaseDescriptor
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> None:
    parser = argparse.ArgumentParser(description="Sign an approved add-on OCI image release")
    parser.add_argument("--addon-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--image", required=True, help="Full OCI repository@sha256 digest")
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    ApprovedDeployment(
        addon_id=args.addon_id, version=args.version, image=args.image
    )
    if args.out.exists():
        parser.error("Output already exists; do not overwrite signed releases")
    if args.private_key.is_symlink():
        parser.error("Signing key path must not be a symlink")
    if os.name == "posix" and args.private_key.stat().st_mode & 0o077:
        parser.error("Signing key must not be readable by group/others")
    raw = args.private_key.read_bytes()
    key = serialization.load_pem_private_key(raw, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        parser.error("Signing key must be an Ed25519 private key")
    descriptor = ReleaseDescriptor(
        schema_version=1,
        addon_id=args.addon_id,
        version=args.version,
        image=args.image,
        publisher_key_id=args.key_id,
        signature_ed25519_base64=base64.b64encode(b"0" * 64).decode(),
    )
    signature = key.sign(descriptor.signing_bytes())
    signed = descriptor.model_copy(
        update={"signature_ed25519_base64": base64.b64encode(signature).decode()}
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(signed.model_dump(), stream, indent=2, sort_keys=True)
        stream.write("\n")
    print("Signed release descriptor written to", args.out)
    print("Publisher key ID:", args.key_id)


if __name__ == "__main__":
    main()
