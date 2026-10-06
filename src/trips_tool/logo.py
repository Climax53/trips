"""The picture shown when Trips opens: a robot driving a steam engine.

``train.txt`` holds the picture as Braille characters at a few sizes, made
from a drawing by ``tools/make_logo.py``. Trips shows the largest one the
window has room for. A window too small for any of them gets the small engine
drawn below. Either way it is drawn in white.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from .style import centered, color_on, dim, sgr

ART = Path(__file__).resolve().parent / "train.txt"

ENGINE = (
    "       ▒▓▓▒░ ░▒▒░  ░░   ░",
    "      ▄██▄                 ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄",
    "       ██   ▟▙   ▄█▄     ╻  │  ╭──●──╮  │",
    " ╭───────┬──────────────┬───┤ ─┤ ◉ ◉ ├─ │",
    " │  ◖◗   │              │   │  │ ╰─╯ │  │",
    " │       │  T R I P S   │   │  ╰──┬──╯  │",
    " │       │ ·  ·  ·  ·   │   │ ╭───┴───╮ │",
    " ╰───────┴──────────────┴───┴─┴───────┴─╯",
    " ◢█◣▐█▌══(◉)═(◉)═(◉)         (●)   (●)",
)
# Rail with a tie under it every third column.
TRACK = "━━┯"
WIDTH = max(len(line) for line in ENGINE)
# Rows the screen under the picture needs: the team screen, or only the request box.
TEAM_SCREEN_ROOM = 24
BOX_ROOM = 6
# The widest picture each ``logo`` setting allows. ``small`` is the drawn engine only.
SIZE_LIMIT = {"small": 0, "medium": 70, "large": 10_000}


def pictures() -> list[list[str]]:
    """Every size of the picture in ``train.txt``, largest first. Empty when the file is missing."""
    try:
        text = ART.read_text(encoding="utf-8")
    except OSError:
        return []
    found: list[list[str]] = []
    for block in text.split("@@ ")[1:]:
        lines = block.split("\n")[1:]
        while lines and not lines[-1]:
            lines.pop()
        if lines:
            found.append(lines)
    found.sort(key=lambda lines: -max(len(line) for line in lines))
    return found


def picture_for(columns: int, rows: int, size: str = "medium") -> list[str] | None:
    """The largest picture that fits in this many columns and rows, and that the size setting allows."""
    limit = SIZE_LIMIT.get(size, SIZE_LIMIT["medium"])
    for lines in pictures():
        wide = max(len(line) for line in lines)
        if wide <= limit and wide + 2 <= columns and len(lines) <= rows:
            return lines
    return None


def small_engine(columns: int) -> list[str]:
    if columns < WIDTH + 2:
        return []
    return [*ENGINE, " " + (TRACK * WIDTH)[:WIDTH - 1]]


def logo_lines(columns: int = 80, color: bool = False, rows: int = 0, size: str = "medium") -> list[str]:
    """The picture as lines of text, or nothing when the window is too narrow for it.

    ``rows`` is how many rows the picture may use. The small engine is used
    when no size of the picture fits in them.
    """
    picture = picture_for(columns, rows, size)
    plain = picture or small_engine(columns)
    if not plain:
        return []
    # Pad every row to the same width first, so the whole picture moves to the middle as one block.
    wide = max(len(line) for line in plain)
    plain = centered([line.ljust(wide) for line in plain], columns)
    if not picture:
        *body, track = plain
        return [_ink(line, color) for line in body] + [dim(track, color)]
    return [_ink(line, color) for line in plain]


def _ink(line: str, color: bool) -> str:
    return sgr("97", line, color)


def show_logo(room: int = TEAM_SCREEN_ROOM, picture_only: bool = False, size: str = "medium") -> bool:
    """Print the picture. ``room`` is how many rows to leave free under it.

    With ``picture_only`` nothing is printed unless a size of the picture
    fits. Returns whether anything was printed.
    """
    if not sys.stdout.isatty() or os.environ.get("TRIPS_NO_LOGO") == "1" or size == "off":
        return True
    window = shutil.get_terminal_size((100, 30))
    available = window.lines - room
    if picture_only and picture_for(window.columns, available, size) is None:
        return False
    lines = logo_lines(window.columns, color_on(), available, size)
    if lines:
        print("\n".join(lines) + "\n", flush=True)
    return True
