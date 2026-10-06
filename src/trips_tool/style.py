"""Colours and small drawing helpers shared by the Trips screens."""

from __future__ import annotations

import os
import re
import shutil
import sys

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
RESET = "\x1b[0m"
Rgb = tuple[int, int, int]


def enable_ansi() -> None:
    """Ask an older Windows console to honour colour and cursor codes."""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel = ctypes.windll.kernel32
        for handle_id in (-11, -12):
            handle = kernel.GetStdHandle(handle_id)
            mode = ctypes.c_ulong()
            if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        return


def color_on(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def truecolor() -> bool:
    if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return True
    return bool(os.environ.get("WT_SESSION"))


def visible_width(text: str) -> int:
    return len(ANSI.sub("", text))


def content_width(columns: int | None = None) -> int:
    """How wide the request box is. The picture and the team screen are centred over it."""
    if columns is None:
        columns = shutil.get_terminal_size((80, 24)).columns
    return min(max(columns - 2, 28), 100)


def centered(lines: list[str], columns: int) -> list[str]:
    """Shift a block to the middle of the request box, or of the window when it is wider than the box."""
    widest = max((visible_width(line) for line in lines), default=0)
    span = content_width(columns)
    margin = max(0, ((span if widest <= span else columns) - widest) // 2)
    return [" " * margin + line if line else line for line in lines]


def sgr(code: str, text: str, color: bool = True) -> str:
    return f"\x1b[{code}m{text}{RESET}" if color and text else text


def dim(text: str, color: bool = True) -> str:
    return sgr("2", text, color)


def bold(text: str, color: bool = True) -> str:
    return sgr("1", text, color)


def tint(text: str, rgb: Rgb, fallback: str, color: bool = True) -> str:
    """Colour text with an agent's own colour, or a plain code on older windows."""
    if not color:
        return text
    if truecolor():
        return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{text}{RESET}"
    return sgr(fallback, text)


def ink_of(agent: str) -> str:
    """The code that switches text to an agent's colour, or nothing for an unknown agent."""
    from .roster import spec_for

    spec = spec_for(agent) if agent else None
    if spec is None:
        return ""
    if truecolor():
        return f"\x1b[38;2;{spec.rgb[0]};{spec.rgb[1]};{spec.rgb[2]}m"
    return f"\x1b[{spec.code}m"


def repaint(lines: list[str], painted: int, stream=None) -> int:
    """Draw a block over the one drawn before it. Returns how many lines are now on screen."""
    stream = stream or sys.stdout
    lead = f"\x1b[{painted}A" if painted else ""
    stream.write(lead + "\r\x1b[J" + "\n".join(lines) + "\n")
    stream.flush()
    return len(lines)


def soften(rgb: Rgb, amount: float = 0.3) -> Rgb:
    """Pull a colour toward grey so a border does not shout."""
    return tuple(int(part + (128 - part) * amount) for part in rgb)  # type: ignore[return-value]


def fade_codes(length: int, stops: list[Rgb]) -> list[str]:
    """One colour code per column, fading from the first colour to the last.

    A window that cannot show full colour gets the dim code in every column.
    """
    if not truecolor() or not stops:
        return ["\x1b[2m"] * length
    if len(stops) == 1:
        stops = stops * 2
    span = max(1, length - 1)
    codes: list[str] = []
    for index in range(length):
        place = index / span * (len(stops) - 1)
        low = min(int(place), len(stops) - 2)
        mix = place - low
        a, b = stops[low], stops[low + 1]
        r, g, bl = (int(a[i] + (b[i] - a[i]) * mix) for i in range(3))
        codes.append(f"\x1b[38;2;{r};{g};{bl}m")
    return codes


def gradient(text: str, stops: list[Rgb], color: bool = True) -> str:
    """Fade text from the first colour to the last."""
    if not color or not text:
        return text
    codes = fade_codes(len(text), stops)
    return "".join(code + char for code, char in zip(codes, text)) + RESET
