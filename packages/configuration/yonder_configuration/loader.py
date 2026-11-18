"""Layered configuration loading with secret redaction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from yonder_configuration.models import YonderSettings
from yonder_configuration.validation import validate_settings

_SECRET_KEY_FRAGMENTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "private_key",
    "database_url",
)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Configuration root must be a mapping: {path}")
    return data


def load_settings(
    *,
    config_file: Path | str | None = None,
    environ: dict[str, str] | None = None,
) -> YonderSettings:
    """Load settings: defaults → YAML file → environment variables."""

    file_overlay: dict[str, Any] = {}
    resolved_file: Path | None = None

    if config_file is not None:
        resolved_file = Path(config_file)
        file_overlay = _load_yaml(resolved_file)
    else:
        # Allow YONDER_CONFIG_FILE from the process environment before model init.
        import os

        env = environ if environ is not None else os.environ
        configured = env.get("YONDER_CONFIG_FILE")
        if configured:
            resolved_file = Path(configured)
            file_overlay = _load_yaml(resolved_file)

    # File first, then environment overlays so YONDER_* wins over YAML.
    init_data = dict(file_overlay)
    if resolved_file is not None:
        init_data["config_file"] = str(resolved_file)

    if environ is not None:
        settings = YonderSettings.model_validate(
            _deep_merge(init_data, _env_overlay_from_mapping(environ))
        )
    else:
        import os

        settings = YonderSettings.model_validate(
            _deep_merge(init_data, _env_overlay_from_mapping(dict(os.environ)))
        )

    return validate_settings(settings)


def _env_overlay_from_mapping(environ: dict[str, str]) -> dict[str, Any]:
    """Minimal YONDER_ env overlay for deterministic tests."""

    overlay: dict[str, Any] = {}
    prefix = "YONDER_"
    for key, value in environ.items():
        if not key.startswith(prefix):
            continue
        path = key[len(prefix) :].lower().split("__")
        cursor: dict[str, Any] = overlay
        for part in path[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[path[-1]] = value
    return overlay


def _redact_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS):
        if value in (None, "", []):
            return value
        return "***REDACTED***"
    if isinstance(value, dict):
        return {child_key: _redact_value(child_key, child) for child_key, child in value.items()}
    if isinstance(value, list):
        return [_redact_value(key, item) for item in value]
    return value


def redact_settings(settings: YonderSettings) -> dict[str, Any]:
    dumped = settings.model_dump(mode="json")
    return {key: _redact_value(key, value) for key, value in dumped.items()}
