"""Configuration and security-mode validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from yonder_configuration import (
    ConfigurationError,
    YonderSettings,
    assert_security_mode_allowed,
    load_settings,
    redact_settings,
    validate_settings,
)
from yonder_configuration.models import SecurityMode, YonderEnvironment

ROOT = Path(__file__).resolve().parents[3]


def test_development_config_loads() -> None:
    settings = load_settings(config_file=ROOT / "config/environments/development.yaml")
    assert settings.environment == YonderEnvironment.DEVELOPMENT
    assert settings.security.mode == SecurityMode.DEVELOPMENT
    assert settings.gateway.host == "127.0.0.1"


def test_production_with_development_security_refused() -> None:
    settings = YonderSettings(
        environment=YonderEnvironment.PRODUCTION,
        security={"mode": "development"},
    )
    with pytest.raises(ConfigurationError, match="production"):
        assert_security_mode_allowed(settings)


def test_validate_settings_refuses_production_development_combo() -> None:
    settings = YonderSettings(
        environment=YonderEnvironment.PRODUCTION,
        security={"mode": "development"},
    )
    with pytest.raises(ConfigurationError):
        validate_settings(settings)


def test_development_non_localhost_bind_refused_by_default() -> None:
    settings = YonderSettings(
        environment=YonderEnvironment.DEVELOPMENT,
        security={"mode": "development"},
        gateway={"host": "0.0.0.0", "port": 8080},
        allow_non_localhost_bind_in_development=False,
    )
    with pytest.raises(ConfigurationError, match="localhost"):
        validate_settings(settings)


def test_trusted_ingress_allowed_in_production() -> None:
    settings = YonderSettings(
        environment=YonderEnvironment.PRODUCTION,
        security={
            "mode": "trusted_ingress",
            "worker_shared_secret": "unit-test-worker-secret",
        },
        gateway={"host": "0.0.0.0", "port": 8080},
        worker={"host": "0.0.0.0", "port": 8090},
        worker_registration={"mode": "allowlist", "allowed_worker_ids": []},
    )
    validated = validate_settings(settings)
    assert validated.security.mode == SecurityMode.TRUSTED_INGRESS


def test_production_requires_worker_shared_secret() -> None:
    settings = YonderSettings(
        environment=YonderEnvironment.PRODUCTION,
        security={"mode": "trusted_ingress"},
        gateway={"host": "0.0.0.0", "port": 8080},
        worker={"host": "0.0.0.0", "port": 8090},
        worker_registration={"mode": "allowlist", "allowed_worker_ids": []},
    )
    with pytest.raises(ConfigurationError, match="worker_shared_secret"):
        validate_settings(settings)


def test_production_worker_registration_allow_all_is_refused() -> None:
    settings = YonderSettings(
        environment=YonderEnvironment.PRODUCTION,
        security={"mode": "trusted_ingress"},
        worker_registration={"mode": "allow_all"},
    )
    with pytest.raises(ConfigurationError, match="fail closed"):
        validate_settings(settings)


def test_redact_settings_hides_secret_like_fields() -> None:
    settings = YonderSettings(
        database_url="postgresql://user:secret@localhost/db",
        security={
            "development_principal": {
                "attributes": {"api_token": "super-secret"},
            }
        },
    )
    redacted = redact_settings(settings)
    assert redacted["database_url"] == "***REDACTED***"
    attrs = redacted["security"]["development_principal"]["attributes"]
    assert attrs["api_token"] == "***REDACTED***"
