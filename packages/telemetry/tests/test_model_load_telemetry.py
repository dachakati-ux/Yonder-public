"""Telemetry helpers for model-load timing."""

from __future__ import annotations

import json
import logging

from yonder_telemetry import JsonLogFormatter, MetricsRegistry, log_event


def test_json_formatter_promotes_load_fields() -> None:
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="yonder.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="deployment.load.completed",
        args=(),
        exc_info=None,
    )
    record.event = "deployment.load.completed"
    record.service = "yonder-gateway"
    record.deployment_id = "dep-1"
    record.model_definition_id = "model-a"
    record.worker_id = "worker-ai3"
    record.duration_ms = 12345
    record.artifact_bytes = 1024
    record.model_path = "/srv/yonder-storage/model-bundles/x.gguf"
    record.outcome = "success"
    payload = json.loads(formatter.format(record))
    assert payload["event"] == "deployment.load.completed"
    assert payload["duration_ms"] == 12345
    assert payload["worker_id"] == "worker-ai3"
    assert payload["artifact_bytes"] == 1024
    assert payload["outcome"] == "success"


def test_metrics_registry_observes_model_load() -> None:
    metrics = MetricsRegistry(service="yonder-gateway")
    metrics.observe_model_load(
        worker_id="worker-ai3",
        provider="llama-cpp",
        outcome="success",
        duration_seconds=12.5,
    )
    body, _content_type = metrics.render()
    text = body.decode()
    assert "yonder_model_loads_total" in text
    assert "yonder_model_load_duration_seconds" in text
    assert 'worker_id="worker-ai3"' in text


def test_log_event_accepts_load_fields(caplog) -> None:
    logger = logging.getLogger("yonder.test.load")
    with caplog.at_level(logging.INFO, logger="yonder.test.load"):
        log_event(
            logger,
            "deployment.load.completed",
            deployment_id="dep-1",
            duration_ms=50,
            outcome="success",
        )
    assert any("deployment.load.completed" in message for message in caplog.messages)
