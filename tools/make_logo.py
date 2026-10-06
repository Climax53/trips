"""Rebuild src/trips_tool/train.txt from a picture. Needs Pillow; Trips itself does not.

    python tools/make_logo.py path/to/picture.png

The picture should be light lines on a dark background. It is turned into
Braille characters at a few sizes, and Trips shows the largest that fits.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parents[1] / "src" / "trips_tool" / "train.txt"
# name, columns, part of the picture as fractions (left, top, right, bottom)
SIZES = (
    ("scene", 110, (0.0, 0.0, 1.0, 1.0)),
    ("engine-large", 90, (0.33, 0.18, 0.965, 0.91)),
    ("engine", 64, (0.34, 0.195, 0.957, 0.88)),
)
BRIGHT = 110   # a pixel this light counts as part of a line
SHARE = 0.1    # a dot is lit when this share of the pixels under it are part of a line
DOTS = ((0, 0, 0x01), (1, 0, 0x02), (2, 0, 0x04), (3, 0, 0x40), (0, 1, 0x08), (1, 1, 0x10), (2, 1, 0x20), (3, 1, 0x80))


def braille(image: Image.Image, columns: int) -> list[str]:
    gray = image.convert("L")
    width = columns * 2
    height = int(round(width * gray.height / gray.width / 4)) * 4
    source_w, source_h = gray.size
    data = gray.tobytes()
    lit = [[0] * width for _ in range(height)]
    seen = [[0] * width for _ in range(height)]
    for y in range(source_h):
        row = y * height // source_h
        for x in range(source_w):
            column = x * width // source_w
            seen[row][column] += 1
            if data[y * source_w + x] >= BRIGHT:
                lit[row][column] += 1
    lines: list[str] = []
    for top in range(0, height, 4):
        text = ""
        for left in range(0, width, 2):
            bits = 0
            for dy, dx, bit in DOTS:
                if lit[top + dy][left + dx] >= max(1, seen[top + dy][left + dx] * SHARE):
                    bits |= bit
            text += chr(0x2800 + bits) if bits else " "
        lines.append(text.rstrip())
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    picture = Image.open(sys.argv[1])
    blocks: list[str] = []
    for name, columns, (left, top, right, bottom) in SIZES:
        w, h = picture.size
        part = picture.crop((int(left * w), int(top * h), int(right * w), int(bottom * h)))
        lines = braille(part, columns)
        blocks.append(f"@@ {name}\n" + "\n".join(lines) + "\n")
        print(f"{name}: {max(len(line) for line in lines)} columns, {len(lines)} rows")
    OUT.write_text("".join(blocks), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
