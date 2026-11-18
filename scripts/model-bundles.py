#!/usr/bin/env python3
"""Import / verify / list offline model bundles.

Usage:
  uv run python scripts/model-bundles.py list
  uv run python scripts/model-bundles.py verify vendor/model-bundles/my-bundle
  uv run python scripts/model-bundles.py import /path/to/bundle-dir --dest vendor/model-bundles

Never downloads weights. Never selects a runtime.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "contracts"))

from yonder_contracts.model_bundles import (  # noqa: E402
    list_bundle_dirs,
    public_bundle_summary,
    verify_bundle_dir,
)


def cmd_list(args: argparse.Namespace) -> int:
    root = Path(args.root)
    dirs = list_bundle_dirs(root)
    if not dirs:
        print(f"No bundles under {root} (status=not_bundled)")
        return 0
    for path in dirs:
        result = verify_bundle_dir(path)
        summary = public_bundle_summary(result)
        print(
            f"{path.name}\tstatus={summary['status']}\tvalid={summary['valid']}\t"
            f"modality={summary['modality']}\tprovider={summary['runtime_provider']}"
        )
        for issue in summary["issues"]:
            print(f"  - {issue['code']}: {issue['message']}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    path = Path(args.path)
    result = verify_bundle_dir(path)
    summary = public_bundle_summary(result)
    print(f"bundle={path}")
    print(f"status={summary['status']} valid={summary['valid']}")
    for issue in summary["issues"]:
        print(f"  - {issue['code']}: {issue['message']}")
    return 0 if result.valid else 1


def cmd_import(args: argparse.Namespace) -> int:
    source = Path(args.path)
    dest_root = Path(args.dest)
    if not (source / "bundle.yaml").exists():
        print("ERROR: source must contain bundle.yaml", file=sys.stderr)
        return 1
    result = verify_bundle_dir(source)
    if not result.valid and result.status.value not in {"not_bundled"}:
        print("ERROR: refusing to import invalid/incomplete bundle", file=sys.stderr)
        for issue in result.issues:
            print(f"  - {issue.code}: {issue.message}", file=sys.stderr)
        return 1
    bundle_id = result.manifest.id if result.manifest else source.name
    target = dest_root / bundle_id
    if target.exists() and not args.force:
        print(f"ERROR: {target} exists (use --force)", file=sys.stderr)
        return 1
    if target.exists():
        shutil.rmtree(target)
    dest_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    print(f"Imported {source} -> {target} (status={result.status.value})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=str(ROOT / "vendor" / "model-bundles"),
        help="Bundle root for list",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="List and verify bundles under --root")
    list_p.set_defaults(func=cmd_list)

    verify_p = sub.add_parser("verify", help="Verify one bundle directory")
    verify_p.add_argument("path")
    verify_p.set_defaults(func=cmd_verify)

    import_p = sub.add_parser("import", help="Copy a verified bundle into vendor/")
    import_p.add_argument("path")
    import_p.add_argument(
        "--dest",
        default=str(ROOT / "vendor" / "model-bundles"),
    )
    import_p.add_argument("--force", action="store_true")
    import_p.set_defaults(func=cmd_import)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
