"""Read one key at a time, with the same names on Windows, macOS, and Linux.

``read_key`` returns a single typed character, or a name such as ``up`` or
``enter``. Names are always longer than one character.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager

try:
    import msvcrt
except ImportError:
    msvcrt = None

try:
    import select
    import termios
    import tty
except ImportError:
    termios = None

_WINDOWS = {
    "H": "up", "P": "down", "K": "left", "M": "right",
    "G": "home", "O": "end", "S": "delete",
}
_POSIX = {
    "[A": "up", "[B": "down", "[C": "right", "[D": "left",
    "[H": "home", "OH": "home", "[1~": "home", "[7~": "home",
    "[F": "end", "OF": "end", "[4~": "end", "[8~": "end",
    "[3~": "delete",
}
_CONTROL = {
    "\r": "enter", "\n": "enter", "\x08": "backspace", "\x7f": "backspace",
    "\x1b": "esc", "\t": "tab", "\x15": "clear",
}


def raw_keys() -> bool:
    """True when single keys can be read from this window."""
    if not sys.stdin.isatty():
        return False
    return msvcrt is not None or termios is not None


@contextmanager
def key_mode():
    """Hold the keyboard in one-key-at-a-time mode while a screen is open."""
    if msvcrt is not None or termios is None:
        yield
        return
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def key_waiting() -> bool:
    if msvcrt is not None:
        return bool(msvcrt.kbhit())
    ready, _, _ = select.select([sys.stdin.fileno()], [], [], 0)
    return bool(ready)


def read_key() -> str:
    key = _windows_key() if msvcrt is not None else _posix_key()
    if key == "\x03":
        raise KeyboardInterrupt
    if key == "\x04":
        raise EOFError
    return _CONTROL.get(key, key)


def _windows_key() -> str:
    key = msvcrt.getwch()
    # Arrow and editing keys arrive as a marker followed by a letter. The letter
    # is already waiting inside the runtime, where kbhit cannot see it, so it is
    # read without checking first.
    if key in ("\x00", "\xe0"):
        return _WINDOWS.get(msvcrt.getwch(), "other")
    return key


def _posix_key() -> str:
    fd = sys.stdin.fileno()
    first = os.read(fd, 1)
    if not first:
        raise EOFError
    lead = first[0]
    extra = 3 if lead >= 0xF0 else 2 if lead >= 0xE0 else 1 if lead >= 0xC0 else 0
    if extra:
        first += os.read(fd, extra)
    key = first.decode("utf-8", errors="replace")
    if key != "\x1b":
        return key
    sequence = ""
    while len(sequence) < 6 and select.select([fd], [], [], 0.03)[0]:
        sequence += os.read(fd, 1).decode("utf-8", errors="replace")
        if sequence in _POSIX:
            return _POSIX[sequence]
    return "other" if sequence else key
