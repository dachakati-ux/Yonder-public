#!/usr/bin/env python3
"""Rebuild pure-Python Yonder wheels from source without hatchling/build.

Use when corporate PyPI is unreachable. Output is suitable for
`scripts/deploy-ai1-verified-runtime.sh` offline install.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import shutil
import zipfile
from pathlib import Path

PACKAGES = (
    (
        "packages/contracts/yonder_contracts",
        "yonder_contracts",
        "yonder-contracts",
        None,
    ),
    (
        "packages/configuration/yonder_configuration",
        "yonder_configuration",
        "yonder-configuration",
        None,
    ),
    (
        "packages/telemetry/yonder_telemetry",
        "yonder_telemetry",
        "yonder-telemetry",
        None,
    ),
    ("apps/gateway/app", "app", "yonder-gateway", "yonder-gateway = app.main:run"),
    ("apps/worker/app", "app", "yonder-worker", "yonder-worker = app.main:run"),
)


def copy_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "tests"),
    )


def write_metadata(dist_info: Path, name: str, version: str) -> None:
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "WHEEL").write_text(
        "Wheel-Version: 1.0\nGenerator: yonder-rebuild\nRoot-Is-Purelib: true\n"
        "Tag: py3-none-any\n",
        encoding="utf-8",
    )
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        "Requires-Python: >=3.12\n",
        encoding="utf-8",
    )


def build_one(
    *,
    root: Path,
    out: Path,
    src_rel: str,
    top: str,
    dist_name: str,
    version: str,
    console_script: str | None,
) -> Path:
    src = root / src_rel
    if not src.is_dir():
        raise SystemExit(f"Missing package source: {src}")
    work = out / f"_build_{dist_name}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    copy_tree(src, work / top)
    dist_info = work / f"{dist_name.replace('-', '_')}-{version}.dist-info"
    write_metadata(dist_info, dist_name, version)
    if console_script:
        (dist_info / "entry_points.txt").write_text(
            f"[console_scripts]\n{console_script}\n",
            encoding="utf-8",
        )

    records: list[tuple[str, str, int]] = []
    for path in sorted(work.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(work).as_posix()
        data = path.read_bytes()
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode(
            "ascii"
        ).rstrip("=")
        records.append((rel, f"sha256={digest}", len(data)))
    record_body = "".join(f"{rel},{digest},{size}\n" for rel, digest, size in records)
    record_body += f"{dist_info.name}/RECORD,,\n"
    (dist_info / "RECORD").write_text(record_body, encoding="utf-8")

    out_whl = out / f"{dist_name.replace('-', '_')}-{version}-py3-none-any.whl"
    if out_whl.exists():
        out_whl.unlink()
    with zipfile.ZipFile(out_whl, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(work.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(work).as_posix())
    shutil.rmtree(work)
    print(f"wrote {out_whl} ({out_whl.stat().st_size} bytes)")
    return out_whl


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Yonder checkout root",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("/tmp/yonder-wheels-fresh"),
        help="Wheel output directory",
    )
    parser.add_argument("--version", default="0.1.0")
    args = parser.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    for src_rel, top, dist_name, console_script in PACKAGES:
        build_one(
            root=args.root,
            out=args.outdir,
            src_rel=src_rel,
            top=top,
            dist_name=dist_name,
            version=args.version,
            console_script=console_script,
        )


if __name__ == "__main__":
    main()
