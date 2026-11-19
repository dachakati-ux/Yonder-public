"""Read-only update availability check against the official public GitHub repository.

Never executes remote code or modifies the host. Administrators choose when to update.
"""
from __future__ import annotations

import os
import re
import time

import httpx
from fastapi import APIRouter, Request
from yonder_contracts.errors import ErrorCode

from app.context import get_principal, get_request_id
from app.errors import error_response

router = APIRouter(prefix="/api/admin/updates", tags=["updates"])
RELEASE_API = "https://api.github.com/repos/dachakati-ux/Yonder-public/commits/main"
RELEASES_LATEST_API = (
    "https://api.github.com/repos/dachakati-ux/Yonder-public/releases/latest"
)
REPOSITORY_URL = "https://github.com/dachakati-ux/Yonder-public"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SEMVER_PATTERN = re.compile(r"^v?[0-9]+\.[0-9]+\.[0-9]+([+-][A-Za-z0-9_.-]+)?$")
_CACHE: dict[str, object] = {"checked": 0.0, "latest": None}


def update_status(installed: str, latest: str | None) -> str:
    """Never announce an update when local revision is unknown."""
    if not SHA_PATTERN.fullmatch(installed):
        return "installed_revision_unknown"
    if latest is None or not SHA_PATTERN.fullmatch(latest):
        return "check_unavailable"
    return "up_to_date" if latest == installed else "revision_differs"


@router.get("/check")
async def check_updates(request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not principal.has_capability("operations.view"):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks operations.view capability.",
            request_id,
        )
    installed = os.getenv("YONDER_BUILD_SHA", "").lower().strip()
    latest = _CACHE["latest"]
    checked = float(_CACHE["checked"])
    fetch_error = None
    if time.monotonic() - checked > 3600 or checked == 0:
        try:
            async with httpx.AsyncClient(
                timeout=8.0,
                follow_redirects=False,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Yonder-UpdateChecker/0.1",
                },
            ) as client:
                response = await client.get(RELEASE_API)
                response.raise_for_status()
                payload = response.json()
            candidate = payload.get("sha") if isinstance(payload, dict) else None
            if not isinstance(candidate, str) or not SHA_PATTERN.fullmatch(candidate):
                raise ValueError("Unexpected GitHub response")
            latest = candidate
            _CACHE.update({"latest": latest, "checked": time.monotonic()})
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            fetch_error = type(exc).__name__
            # Cache failures briefly rather than hammering GitHub.
            _CACHE["checked"] = time.monotonic() - 3300
    status = update_status(installed, latest if isinstance(latest, str) else None)
    return {
        "status": status,
        "installed_revision": installed if SHA_PATTERN.fullmatch(installed) else None,
        "latest_revision": latest if isinstance(latest, str) else None,
        "repository_url": REPOSITORY_URL,
        "update_guide": REPOSITORY_URL + "/blob/main/docs/updating.md",
        "check_error": fetch_error,
        "message": (
            "A different main revision is available. Review changes before updating."
            if status == "revision_differs"
            else "Could not determine whether this installation is current."
            if status in {"installed_revision_unknown", "check_unavailable"}
            else "Installation matches the current main revision."
        ),
        "note": "Main-branch comparison is not a stable release-channel check.",
    }


def evaluate_release_channel(
    *,
    installed_version: str,
    latest_tag: str | None,
    signature_verified: bool,
) -> dict[str, object]:
    """Compare installed version against a signed release tag without trusting unsigned tags."""
    clean_installed = installed_version.strip()
    if not clean_installed or not SEMVER_PATTERN.fullmatch(clean_installed):
        status = "installed_version_unknown"
    elif not latest_tag or not SEMVER_PATTERN.fullmatch(latest_tag):
        status = "release_unavailable"
    elif not signature_verified:
        status = "release_unverified"
    elif clean_installed.lstrip("v") == latest_tag.lstrip("v"):
        status = "up_to_date"
    else:
        status = "signed_release_available"
    return {
        "channel": "signed_release",
        "status": status,
        "installed_version": clean_installed or None,
        "latest_tag": latest_tag,
        "signature_verified": signature_verified,
        "repository_url": REPOSITORY_URL,
    }


@router.get("/release-check")
async def check_signed_release(request: Request):
    """Check the stable GitHub release channel separately from main-branch commits."""
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not principal.has_capability("operations.view"):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks operations.view capability.",
            request_id,
        )
    installed_version = os.getenv("YONDER_RELEASE_VERSION", "").strip()
    latest_tag: str | None = None
    signature_verified = False
    fetch_error: str | None = None
    try:
        async with httpx.AsyncClient(
            timeout=8.0,
            follow_redirects=False,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Yonder-UpdateChecker/0.1",
            },
        ) as client:
            response = await client.get(RELEASES_LATEST_API)
            response.raise_for_status()
            payload = response.json()
        tag = payload.get("tag_name") if isinstance(payload, dict) else None
        if isinstance(tag, str) and SEMVER_PATTERN.fullmatch(tag):
            latest_tag = tag
        assets = payload.get("assets") if isinstance(payload, dict) else None
        if isinstance(assets, list):
            asset_names = {
                str(a.get("name", "")) for a in assets if isinstance(a, dict)
            }
            signature_verified = any(
                name.endswith((".sig", ".asc", ".intoto.jsonl"))
                for name in asset_names
            )
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        fetch_error = type(exc).__name__

    result = evaluate_release_channel(
        installed_version=installed_version,
        latest_tag=latest_tag,
        signature_verified=signature_verified,
    )
    result["check_error"] = fetch_error
    result["update_guide"] = REPOSITORY_URL + "/blob/main/docs/updating.md"
    return result
