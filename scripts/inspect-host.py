#!/usr/bin/env python3
"""Collect and validate a structured Yonder host evidence report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "worker"))
sys.path.insert(0, str(ROOT / "packages" / "contracts"))

from app.hardware.collector import collect_hardware_inventory  # noqa: E402
from yonder_contracts.hardware import HardwareInventory  # noqa: E402


def build_report() -> HardwareInventory:
    report = collect_hardware_inventory()
    return HardwareInventory.model_validate(report.model_dump(mode="json"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for the validated JSON report; stdout is always emitted.",
    )
    args = parser.parse_args()

    payload = build_report().model_dump(mode="json")
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
