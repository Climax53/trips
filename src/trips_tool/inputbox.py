"""The box where the next request is typed."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from .keys import key_mode, key_waiting, raw_keys, read_key
from .style import RESET, Rgb, color_on, content_width, dim, fade_codes, gradient, repaint, sgr, soften, tint

PLACEHOLDER = "Ask for one answer"
HINT = "enter send · ↑ earlier requests · team · help · exit"
MAX_ROWS = 8
# One member of the team as the box shows it: label, colour, and plain colour code.
Member = tuple[str, Rgb, str]


@dataclass
class Edit:
    text: str = ""
    cursor: int = 0

    def apply(self, key: str) -> None:
        """Change the text for one editing key. Other keys are ignored."""
        if key == "left":
            self.cursor = max(0, self.cursor - 1)
        elif key == "right":
            self.cursor = min(len(self.text), self.cursor + 1)
        elif key == "home":
            self.cursor = 0
        elif key == "end":
            self.cursor = len(self.text)
        elif key == "delete":
            self.text = self.text[:self.cursor] + self.text[self.cursor + 1:]
        elif key == "backspace":
            if self.cursor:
                self.text = self.text[:self.cursor - 1] + self.text[self.cursor:]
                self.cursor -= 1
        elif key in ("clear", "esc"):
            self.text, self.cursor = "", 0
        elif len(key) == 1 and key.isprintable():
            self.text = self.text[:self.cursor] + key + self.text[self.cursor:]
            self.cursor += 1

    def replace(self, text: str) -> None:
        self.text, self.cursor = text, len(text)


def box_width() -> int:
    return content_width()


def layout(text: str, inner: int) -> list[tuple[int, int]]:
    """Where each row of the box starts and ends in the text. Rows break after a space when one fits."""
    spans: list[tuple[int, int]] = []
    start = 0
    while len(text) - start > inner:
        space = text.rfind(" ", start, start + inner)
        end = space + 1 if space > start else start + inner
        spans.append((start, end))
        start = end
    spans.append((start, len(text)))
    if len(text) - start == inner:
        spans.append((len(text), len(text)))  # a full last row: the cursor goes on a new one
    return spans


def locate(spans: list[tuple[int, int]], cursor: int) -> tuple[int, int]:
    """The row and column of the cursor."""
    row = max(index for index, (start, _end) in enumerate(spans) if start <= cursor)
    return row, cursor - spans[row][0]


def step_row(text: str, cursor: int, inner: int, down: bool) -> int | None:
    """The cursor one row up or down in the same column, or None at the top or bottom row."""
    spans = layout(text, inner)
    row, column = locate(spans, cursor)
    target = row + (1 if down else -1)
    if not 0 <= target < len(spans):
        return None
    start, end = spans[target]
    return min(start + column, end if target == len(spans) - 1 else max(start, end - 1))


def request_frame(
    text: str,
    cursor: int,
    width: int,
    team: list[Member] | tuple = (),
    hint: str = "",
    show_cursor: bool = True,
    color: bool = True,
) -> list[str]:
    """The box as lines of text. Every box line is exactly ``width`` columns wide."""
    inner = max(1, width - 6)
    cursor = max(0, min(cursor, len(text)))
    stops = [soften(member[1]) for member in team]
    spans = layout(text, inner)
    rows = [text[start:end] for start, end in spans]
    cursor_row, cursor_col = locate(spans, cursor)
    first_row = max(0, cursor_row - MAX_ROWS + 1)

    left = gradient("│", stops[:1], color)
    right = gradient("│", stops[-1:], color)
    lead_rgb, lead_code = (team[0][1], team[0][2]) if team else ((110, 196, 222), "36")
    lines = [_top(width, team, stops, color)]
    for number in range(first_row, min(len(rows), first_row + MAX_ROWS)):
        row = rows[number]
        cells: list[str] = []
        for index in range(inner):
            ghost = not text and 0 <= index - 1 < len(PLACEHOLDER)
            glyph = row[index] if index < len(row) else PLACEHOLDER[index - 1] if ghost else " "
            if show_cursor and number == cursor_row and index == cursor_col:
                cells.append(f"\x1b[7m{glyph}{RESET}")
            elif ghost:
                cells.append(dim(glyph, color))
            else:
                cells.append(glyph)
        mark = tint("›", lead_rgb, lead_code, color) if number == 0 else " "
        lines.append(f"{left} {mark} {''.join(cells)} {right}")
    lines.append(gradient("╰" + "─" * max(0, width - 2) + "╯", stops, color))
    if hint:
        lines.append(dim(("  " + hint)[:width], color))
    return lines


def _top(width: int, team, stops: list[Rgb], color: bool) -> str:
    """The top edge: the name on the left, the team on the right, the conductor starred."""
    title = "Trips"
    names = [("★ " if index == 0 else "") + member[0] for index, member in enumerate(team)]
    if len(" · ".join(names)) + len(title) + 12 > width:
        names = []
    # Each part is its text and how to colour it. None means border, which fades by column.
    parts: list[tuple[str, object]] = [("╭─ ", None), (title, lambda text: sgr("1", text)), (" ", None)]
    tail: list[tuple[str, object]] = []
    for name, member in zip(names, team):
        tail.append((" · " if tail else " ", None))
        tail.append((name, lambda text, member=member: tint(text, member[1], member[2])))
    tail.append((" ─╮" if names else "╮", None))
    fill = width - sum(len(text) for text, _ in parts + tail)
    if fill < 0:
        return gradient("╭" + "─" * max(0, width - 2) + "╮", stops, color)
    parts += [("─" * fill, None)] + tail
    if not color:
        return "".join(text for text, _ in parts)
    codes = fade_codes(width, stops)
    out: list[str] = []
    column = 0
    for text, paint in parts:
        if paint is None:
            out.extend(codes[column + offset] + char for offset, char in enumerate(text))
        else:
            out.append(RESET + paint(text))
        column += len(text)
    return "".join(out) + RESET


def read_request(team: list[Member] | tuple = (), history: list[str] | None = None) -> str:
    """Show the box, wait for Enter, and return what was typed."""
    if not raw_keys():
        return input("trips> ")
    past = list(history or [])
    place = len(past)
    draft = ""
    edit = Edit()
    painted = 0
    color = color_on()
    sys.stdout.write("\x1b[?25l")
    try:
        with key_mode():
            while True:
                width = box_width()
                inner = max(1, width - 6)
                painted = repaint(request_frame(edit.text, edit.cursor, width, team, HINT, color=color), painted)
                keys = [read_key()]
                while key_waiting():
                    keys.append(read_key())
                for index, key in enumerate(keys):
                    if key == "enter":
                        if index == len(keys) - 1:
                            final = request_frame(edit.text, edit.cursor, width, team, show_cursor=False, color=color)
                            repaint(final, painted)
                            return edit.text.strip()
                        key = " "  # a line break inside pasted text
                    moved = step_row(edit.text, edit.cursor, inner, key == "down") if key in ("up", "down") else None
                    if moved is not None:
                        edit.cursor = moved
                    elif key == "up" and place > 0:
                        if place == len(past):
                            draft = edit.text
                        place -= 1
                        edit.replace(past[place])
                    elif key == "down" and place < len(past):
                        place += 1
                        edit.replace(past[place] if place < len(past) else draft)
                    else:
                        edit.apply(key)
    finally:
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()
