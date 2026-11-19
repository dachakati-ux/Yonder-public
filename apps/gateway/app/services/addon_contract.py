"""Stable v1 add-on manifest contract; metadata only, never executable hooks."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from yonder_contracts.versioning import SERVICE_VERSION

ADDON_ID = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
SUPPORTED_API_VERSION = 1

# Capabilities are declared for operator review, not automatically granted.
KNOWN_CAPABILITIES = frozenset(
    {
        "operations.view", "operations.manage", "models.view", "models.manage",
        "coder.use", "lab.use", "chat.use", "hardware.view",
        "academy.instruct", "tools.use", "rag.use", "rag.manage",
    }
)


def _version(value: str) -> tuple[int, int, int]:
    if not SEMVER.fullmatch(value):
        raise ValueError("Version must be a semantic MAJOR.MINOR.PATCH.")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


class AddonUiExtension(BaseModel):
    """Declarative UI mount metadata; never loads remote scripts or arbitrary URLs."""

    model_config = ConfigDict(extra="forbid")

    nav_label: str = Field(min_length=2, max_length=40)
    mount_path: str = Field(min_length=2, max_length=120)
    kind: Literal["console_route", "schema_panel", "service_status"] = "schema_panel"
    required_capabilities: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("mount_path")
    @classmethod
    def valid_mount_path(cls, value: str) -> str:
        if (
            not value.startswith("/")
            or value.startswith("//")
            or "://" in value
            or ".." in value.split("/")
            or any(ch.isspace() for ch in value)
        ):
            raise ValueError("UI mount_path must be a safe relative route starting with '/'.")
        return value

    @field_validator("required_capabilities")
    @classmethod
    def valid_caps(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or not set(value).issubset(KNOWN_CAPABILITIES):
            raise ValueError("Unknown or duplicate UI capability requirement.")
        return value


class AddonManifest(BaseModel):
    """Reviewed package declaration; does not load arbitrary Python or shell code."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1, le=1)
    api_version: int = Field(ge=1, le=1)
    id: str
    name: str = Field(min_length=1, max_length=100)
    version: str
    min_core_version: str
    max_core_version_exclusive: str | None = None
    category: str = Field(default="other", min_length=1, max_length=40)
    summary: str = Field(default="", max_length=500)
    status: str
    install_mode: str
    permissions: list[str] = Field(default_factory=list, max_length=30)
    dependencies: list[str] = Field(default_factory=list, max_length=20)
    requires_restart: bool = False
    docs: str
    warning: str = Field(default="", max_length=1000)
    service_id: str | None = None
    config_schema_version: int = Field(default=1, ge=1)
    ui_extensions: list[AddonUiExtension] = Field(default_factory=list, max_length=8)

    @field_validator("service_id")
    @classmethod
    def valid_service_id(cls, value: str | None) -> str | None:
        if value is not None and not ADDON_ID.fullmatch(value):
            raise ValueError("Invalid add-on service ID.")
        return value

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not ADDON_ID.fullmatch(value):
            raise ValueError("Invalid add-on ID.")
        return value

    @field_validator("version", "min_core_version", "max_core_version_exclusive")
    @classmethod
    def valid_version(cls, value: str | None) -> str | None:
        if value is not None:
            _version(value)
        return value

    @field_validator("permissions")
    @classmethod
    def valid_permissions(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or not set(value).issubset(KNOWN_CAPABILITIES):
            raise ValueError("Unknown or duplicate capability declaration.")
        return value

    @field_validator("dependencies")
    @classmethod
    def valid_dependencies(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not ADDON_ID.fullmatch(x) for x in value):
            raise ValueError("Invalid or duplicate dependency ID.")
        return value

    @field_validator("docs")
    @classmethod
    def valid_docs(cls, value: str) -> str:
        if not re.fullmatch(r"addons/[a-z][a-z0-9-]+/README\.md", value):
            raise ValueError("Documentation must be a bundled add-on README.")
        return value

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str) -> str:
        if value not in {"available", "planned"}:
            raise ValueError("Unsupported add-on status.")
        return value

    @field_validator("install_mode")
    @classmethod
    def valid_mode(cls, value: str) -> str:
        if value not in {"bundled", "not_available"}:
            raise ValueError("No remote executable installation is permitted.")
        return value

    @model_validator(mode="after")
    def coherent(self) -> AddonManifest:
        if self.id in self.dependencies:
            raise ValueError("Add-on may not depend on itself.")
        if self.status == "planned" and self.install_mode != "not_available":
            raise ValueError("Planned add-ons must not be installable.")
        if self.status == "available" and self.install_mode != "bundled":
            raise ValueError("Available add-ons must be bundled.")
        if self.docs != f"addons/{self.id}/README.md":
            raise ValueError("Documentation path must match add-on ID.")
        if self.max_core_version_exclusive is not None and (
            _version(self.min_core_version)
            >= _version(self.max_core_version_exclusive)
        ):
            raise ValueError("Invalid core compatibility interval.")
        return self

    def compatibility(self, *, core_version: str = SERVICE_VERSION) -> str:
        current = _version(core_version)
        if current < _version(self.min_core_version):
            return "core_too_old"
        maximum = self.max_core_version_exclusive
        if maximum is not None and current >= _version(maximum):
            return "core_too_new"
        return "compatible"

    def display(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
