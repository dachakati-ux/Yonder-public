#!/usr/bin/env python3
"""Convert near-black logo backgrounds to true alpha.

Usage:
  uv run --with Pillow python scripts/make-logo-transparent.py \\
    --input path/to/logo.png --output path/to/out.png
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def make_transparent(
    source: Path,
    dest: Path,
    *,
    threshold: int = 28,
    soft_edge: int = 18,
) -> None:
    image = Image.open(source).convert("RGBA")
    pixels = image.load()
    assert pixels is not None
    width, height = image.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            luminance = (r + g + b) / 3
            if luminance <= threshold:
                pixels[x, y] = (r, g, b, 0)
            elif luminance <= threshold + soft_edge:
                # Soften halo around the mark so edges aren't harsh.
                fade = (luminance - threshold) / soft_edge
                pixels[x, y] = (r, g, b, max(0, min(255, int(a * fade))))
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, format="PNG", optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threshold", type=int, default=28)
    parser.add_argument("--soft-edge", type=int, default=18)
    args = parser.parse_args()
    make_transparent(
        args.input,
        args.output,
        threshold=args.threshold,
        soft_edge=args.soft_edge,
    )
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
