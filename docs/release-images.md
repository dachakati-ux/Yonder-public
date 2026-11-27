# Versioned prebuilt images (candidate pipeline)

No stable image release has been published yet. The source launcher remains the supported development path until this candidate workflow and the release gates are validated.

The image candidate pipeline builds Gateway (also used for MCP), Worker and Console for Linux amd64 and boots the exact local images with no source builds. It checks the published service endpoints. Pull requests and manual builds default to **no publication**.

## When an approved release exists

Check out the exact reviewed tag; obtain its published image manifest and verify the commit/platform. From that checkout on a Docker-capable local development machine:

```bash
bash scripts/start-release.sh VERSION_TAG check
bash scripts/start-release.sh VERSION_TAG up
```

Replace `VERSION_TAG` with an actual published semantic version such as the tag listed in the release manifest. The launcher does not accept `latest`. Docker and a current Compose v2 with overlay reset and health-aware startup support are required. The prebuilt Console uses browser Gateway URL `http://127.0.0.1:8080`; use the source launcher to change that gateway port. Only Linux amd64 images are currently built; native ARM builds and supported macOS/Windows combinations need separate verification. This is a trusted, loopback-only development deployment, not a shared or institutional installer.

For immutable selection, set these variables to the exact digest references from the published manifest (the tag still identifies the matching source checkout):

```dotenv
YONDER_GATEWAY_IMAGE=ghcr.io/dachakati-ux/yonder-gateway@sha256:REPLACE_WITH_MANIFEST_DIGEST
YONDER_WORKER_IMAGE=ghcr.io/dachakati-ux/yonder-worker@sha256:REPLACE_WITH_MANIFEST_DIGEST
YONDER_CONSOLE_IMAGE=ghcr.io/dachakati-ux/yonder-console@sha256:REPLACE_WITH_MANIFEST_DIGEST
```

Stop with `bash scripts/start-release.sh VERSION_TAG down`; use `logs` for diagnostics. Do not use the source-based updater for image releases. Back up state and perform coordinated migration/recovery before selecting a new version. Never use an unreviewed tag or overwrite a data volume.

## Maintainer publication checklist

1. Finish the release-readiness gates, including dependency review, real model canaries and backup/restore. Create a reviewed immutable Git tag on main only after acceptance.
2. Verify that the full CI and Clean-host Compose smoke workflows on main passed for that exact commit.
3. Run **Versioned image candidate** on the matching tag with the exact version input and `publish=true`. The build job has no package-write permission and uploads the exact tested images only when publication is requested. The separate publish job checks the tag, ancestry, successful main workflows and the images' revision labels.
4. Verify all registry references are accessible to intended users. Capture the manifest, test digest-pinned pulls on a fresh host, and attach the manifest to the reviewed GitHub release with release notes and known limitations.
5. Treat partially published tags as failed candidates; do not reuse or overwrite a published version. Registry-level tag immutability and signing, SBOMs, base-image digest pinning, multi-architecture builds and release attestation remain explicit requirements before stable release.

The workflow is a packaging candidate, not authorization to publish Yonder 1.0 or certify institutional readiness.

## Supply-chain evidence

Every image candidate now scans the exact locally tested Gateway, Worker, and Console images with a commit-pinned SBOM action, and records SPDX JSON, image IDs and checksums. No signing or package write permission is granted to the PR build. On explicit reviewed-tag publication, the privileged job validates the saved archive checksum, SBOM checksums and loaded image IDs, checks same-commit main CI, dependency audit, browser, SQLite and PostgreSQL recovery gates, then publishes the exact images. It generates signed GitHub artifact provenance and SPDX SBOM attestations bound to registry digests, as well as an attestation for the release manifest. Publication and attestation verification must still be exercised on an actual reviewed tag; a successful candidate build is not proof of either.

Verify the manifest and a published digest with GitHub CLI before deployment:

```bash
gh attestation verify yonder-image-manifest.json --repo dachakati-ux/Yonder-public
gh attestation verify oci://ghcr.io/dachakati-ux/yonder-gateway@sha256:REPLACE_WITH_DIGEST --repo dachakati-ux/Yonder-public
```

Confirm that the verified provenance corresponds to the expected release workflow, reviewed revision and tag. Never substitute a mutable tag for a recorded digest. SBOM generation inventories packages; it does not establish that all OS or model runtime packages are vulnerability-free.
