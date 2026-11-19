"""Add-on API v1 contract compatibility tests."""

from __future__ import annotations

import pytest
from app.services.addon_contract import AddonManifest
from pydantic import ValidationError


def manifest(**changes):
    values = {
        "schema_version": 1,
        "api_version": 1,
        "id": "example-addon",
        "name": "Example",
        "version": "0.1.0",
        "min_core_version": "0.1.0",
        "max_core_version_exclusive": "1.0.0",
        "status": "available",
        "install_mode": "bundled",
        "docs": "addons/example-addon/README.md",
        "permissions": ["models.view"],
        "dependencies": [],
    }
    values.update(changes)
    return AddonManifest.model_validate(values)


def test_valid_manifest_supports_current_core():
    item = manifest()
    assert item.compatibility() == "compatible"
    assert item.compatibility(core_version="0.0.9") == "core_too_old"
    assert item.compatibility(core_version="1.0.0") == "core_too_new"


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "../secrets"),
        ("api_version", 999),
        ("schema_version", 0),
        ("permissions", ["root.admin"]),
        ("permissions", ["models.view", "models.view"]),
        ("dependencies", ["../unsafe"]),
        ("dependencies", ["example-addon"]),
        ("docs", "../../README.md"),
        ("install_mode", "curl_and_execute"),
        ("version", "latest"),
        ("min_core_version", "0.1"),
    ],
)
def test_untrusted_manifest_fields_fail_validation(field, value):
    with pytest.raises(ValidationError):
        manifest(**{field: value})


def test_unexpected_code_execution_field_rejected():
    with pytest.raises(ValidationError):
        manifest(entrypoint="bash -c 'curl remote | sh'")


def test_planned_addon_cannot_enable_execution_mode():
    with pytest.raises(ValidationError):
        manifest(status="planned", install_mode="bundled")


def test_core_compatibility_range_must_be_coherent():
    with pytest.raises(ValidationError):
        manifest(min_core_version="1.0.0", max_core_version_exclusive="1.0.0")


def test_addon_ui_extension_rejects_external_urls_and_bad_caps():
    from app.services.addon_contract import AddonUiExtension
    from pydantic import ValidationError

    ext = AddonUiExtension(
        nav_label="Convyx Panel",
        mount_path="/addons/convyx",
        kind="service_status",
        required_capabilities=["coder.use"],
    )
    assert ext.mount_path == "/addons/convyx"

    for bad_path in ("https://evil.example/js", "//evil.example/x", "/addons/../secret"):
        with pytest.raises(ValidationError):
            AddonUiExtension(nav_label="Bad", mount_path=bad_path)

    with pytest.raises(ValidationError):
        AddonUiExtension(
            nav_label="Bad Cap",
            mount_path="/addons/ok",
            required_capabilities=["root.all"],
        )
