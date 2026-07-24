#!/usr/bin/env python3
"""Create a macOS ICNS file deterministically from the square source image."""

import sys
from pathlib import Path

from PIL import Image, ImageOps


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: build_icon.py INPUT.png OUTPUT.icns", file=sys.stderr)
        return 2

    source = Path(sys.argv[1])
    destination = Path(sys.argv[2])
    image = Image.open(source).convert("RGBA")
    image = ImageOps.fit(
        image,
        (1024, 1024),
        method=Image.Resampling.LANCZOS,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(
        destination,
        format="ICNS",
        sizes=[
            (16, 16),
            (32, 32),
            (64, 64),
            (128, 128),
            (256, 256),
            (512, 512),
            (1024, 1024),
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
