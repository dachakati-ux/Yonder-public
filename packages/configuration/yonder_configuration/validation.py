"""Configuration validation rules."""

from __future__ import annotations

from yonder_configuration.models import (
    SecurityMode,
    YonderEnvironment,
    YonderSettings,
)


class ConfigurationError(ValueError):
    """Raised when configuration is invalid or unsafe."""


def assert_security_mode_allowed(settings: YonderSettings) -> None:
    """Refuse production startup with development security injection."""

    if (
        settings.environment == YonderEnvironment.PRODUCTION
        and settings.security.mode == SecurityMode.DEVELOPMENT
    ):
        raise ConfigurationError(
            "Refusing startup: environment=production cannot use "
            "security.mode=development. Configure an approved upstream "
            "trusted request-context provider before running production."
        )


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1"}


def validate_settings(settings: YonderSettings) -> YonderSettings:
    """Validate typed settings after layered loading."""

    assert_security_mode_allowed(settings)

    if settings.security.mode == SecurityMode.DEVELOPMENT:
        if settings.is_production():
            # Defensive duplicate of assert_security_mode_allowed.
            raise ConfigurationError(
                "Development security mode is forbidden in production."
            )
        if not settings.allow_non_localhost_bind_in_development:
            if not _is_loopback_host(settings.gateway.host):
                raise ConfigurationError(
                    "Development security mode must bind the gateway to "
                    "localhost (127.0.0.1, ::1, or localhost) unless "
                    "allow_non_localhost_bind_in_development is explicitly enabled "
                    "for an isolated lab network."
                )
            if not _is_loopback_host(settings.worker.host):
                raise ConfigurationError(
                    "Development security mode must bind the worker to "
                    "localhost unless allow_non_localhost_bind_in_development "
                    "is explicitly enabled."
                )

    if settings.security.mode == SecurityMode.TRUSTED_INGRESS:
        if not settings.security.trusted_subject_header.strip():
            raise ConfigurationError(
                "trusted_ingress mode requires trusted_subject_header"
            )

    if settings.is_production() and settings.worker_registration.mode == "allow_all":
        raise ConfigurationError(
            "Production worker registration must fail closed; configure "
            "worker_registration.mode=allowlist and explicit worker ids."
        )

    if settings.is_production() and not (
        settings.security.worker_shared_secret or ""
    ).strip():
        raise ConfigurationError(
            "Production requires security.worker_shared_secret so that "
            "gateway<->worker service calls are authenticated. Set "
            "YONDER_SECURITY__WORKER_SHARED_SECRET from a managed secret."
        )
    if settings.is_production() and not settings.security.worker_token_header.strip():
        raise ConfigurationError(
            "security.worker_token_header must be non-empty in production."
        )

    if settings.admission.max_queue_depth < 0:
        raise ConfigurationError("admission.max_queue_depth must be >= 0")
    if settings.admission.max_active_requests_per_deployment < 1:
        raise ConfigurationError(
            "admission.max_active_requests_per_deployment must be >= 1"
        )
    if settings.admission.queue_timeout_seconds <= 0:
        raise ConfigurationError("admission.queue_timeout_seconds must be > 0")
    if settings.admission.inference_timeout_seconds <= 0:
        raise ConfigurationError("admission.inference_timeout_seconds must be > 0")

    worker_ids = [worker.id for worker in settings.workers]
    if len(worker_ids) != len(set(worker_ids)):
        raise ConfigurationError("Duplicate worker ids in static registration")

    return settings
