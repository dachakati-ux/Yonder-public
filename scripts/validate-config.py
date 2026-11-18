#!/usr/bin/env python3
"""Validate Yonder configuration before service startup."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "configuration"))
sys.path.insert(0, str(ROOT / "packages" / "contracts"))

from yonder_configuration import ConfigurationError, load_settings  # noqa: E402


def main() -> int:
    config_file = os.environ.get(
        "YONDER_CONFIG_FILE", str(ROOT / "config" / "environments" / "development.yaml")
    )
    try:
        settings = load_settings(config_file=config_file)
    except ConfigurationError as exc:
        print(f"CONFIGURATION INVALID: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"CONFIGURATION LOAD FAILED: {exc}", file=sys.stderr)
        return 1

    public = settings.export_public()
    print("Configuration OK")
    print(f"  environment: {settings.environment.value}")
    print(f"  security.mode: {settings.security.mode.value}")
    print(f"  gateway: {settings.gateway.host}:{settings.gateway.port}")
    print(f"  worker: {settings.worker.host}:{settings.worker.port}")
    if settings.security.mode.value == "development":
        print("  WARNING: DEVELOPMENT SECURITY MODE")
    print(f"  redacted_export_keys: {sorted(public.keys())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
