"""Structured logging and Prometheus helpers."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

_ALWAYS_REDACT = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "authorization",
    "database_url",
)
_CONTENT_FIELDS = ("prompt", "response_content", "message_content")

# Fields promoted into the JSON log top-level for operator grepping.
_PROMOTED_LOG_FIELDS = (
    "request_id",
    "model_alias",
    "model_definition_id",
    "deployment_id",
    "worker_id",
    "provider",
    "duration_ms",
    "artifact_bytes",
    "model_path",
    "outcome",
    "status",
    "gpu_assignments",
)


def redact_log_fields(
    fields: dict[str, Any],
    *,
    allow_content: bool = False,
) -> dict[str, Any]:
    """Redact secret-like and prompt/response fields from structured logs."""

    redacted: dict[str, Any] = {}
    for key, value in fields.items():
        lowered = key.lower()
        if any(fragment in lowered for fragment in _ALWAYS_REDACT):
            redacted[key] = "***REDACTED***"
            continue
        if not allow_content and any(fragment in lowered for fragment in _CONTENT_FIELDS):
            redacted[key] = "***REDACTED***"
            continue
        if isinstance(value, dict):
            redacted[key] = redact_log_fields(value, allow_content=allow_content)
        else:
            redacted[key] = value
    return redacted


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "service": getattr(record, "service", "yonder"),
            "event": getattr(record, "event", record.getMessage()),
            "message": record.getMessage(),
        }
        for key in _PROMOTED_LOG_FIELDS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(
    *, service: str, level: str = "INFO", json_logs: bool = True
) -> logging.Logger:
    logger = logging.getLogger(service)
    logger.handlers.clear()
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stdout)
    if json_logs:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    allow_content: bool = False,
    **fields: Any,
) -> None:
    safe_fields = redact_log_fields(fields, allow_content=allow_content)
    logger.log(
        level,
        event,
        extra={"event": event, "service": logger.name, **safe_fields},
    )


class MetricsRegistry:
    def __init__(self, service: str) -> None:
        self.service = service
        self.registry = CollectorRegistry()
        self.requests_total = Counter(
            "yonder_requests_total",
            "Total requests",
            ["service", "endpoint", "status"],
            registry=self.registry,
        )
        self.active_requests = Gauge(
            "yonder_active_requests",
            "Active requests",
            ["service"],
            registry=self.registry,
        )
        self.worker_connected = Gauge(
            "yonder_worker_connected",
            "Worker connectivity (1 connected, 0 disconnected)",
            ["service", "worker_id"],
            registry=self.registry,
        )
        self.model_loads_total = Counter(
            "yonder_model_loads_total",
            "Model deployment load attempts",
            ["service", "worker_id", "provider", "outcome"],
            registry=self.registry,
        )
        self.model_load_duration_seconds = Histogram(
            "yonder_model_load_duration_seconds",
            "Model deployment load wall time (gateway orchestration)",
            ["service", "worker_id", "provider", "outcome"],
            registry=self.registry,
            buckets=(
                1.0,
                2.0,
                5.0,
                10.0,
                30.0,
                60.0,
                120.0,
                300.0,
                600.0,
                1200.0,
            ),
        )

    def observe_model_load(
        self,
        *,
        worker_id: str,
        provider: str,
        outcome: str,
        duration_seconds: float,
    ) -> None:
        wid = worker_id or "unknown"
        prov = provider or "unknown"
        result = outcome or "unknown"
        self.model_loads_total.labels(
            service=self.service,
            worker_id=wid,
            provider=prov,
            outcome=result,
        ).inc()
        self.model_load_duration_seconds.labels(
            service=self.service,
            worker_id=wid,
            provider=prov,
            outcome=result,
        ).observe(max(0.0, float(duration_seconds)))

    def render(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST
