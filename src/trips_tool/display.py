"""How Trips looks in the command window: a spinner, and a readable answer."""

from __future__ import annotations

import re
import shutil
import sys
import threading
import time

from .plan import DEFAULT_TEAM
from .roster import label_of, spec_for
from .style import ink_of, tint

# A status update for the spinner. The text after this mark is a phase name,
# or ``done:<agent>`` when one worker has handed in its report.
STATUS = "\x1e"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# Spoken lines for the spinner. They change on a timer. They are mood, not a
# live report of what an agent is typing. The marks after them are real: a
# tick appears only when that agent's report has come back.
FLAVOR = {
    "plan": (
        "Reading the request",
        "Splitting the work three ways",
        "Choosing who takes each part",
        "Sketching the order of work",
        "Keeping the pieces apart",
    ),
    "write": (
        "Pulling the three reports together",
        "Cutting the repeated parts",
        "Writing the one answer",
        "Checking the pieces agree",
        "Smoothing the final reply",
    ),
}
# The number is which team member the line is about.
WORK_LINES = (
    (0, "{} finishing up"),
    (1, "{} reviewing code"),
    (2, "{} writing scripts"),
    (0, "{} rereading the folder"),
    (1, "{} checking the edges"),
    (2, "{} lining up the files"),
    (0, "{} drafting its part"),
    (1, "{} marking what to keep"),
    (2, "{} tightening the draft"),
)


def work_flavor(team, done=()) -> tuple[str, ...]:
    """Lines about the agents that are still working."""
    return tuple(text for _agent, text in work_lines(team, done))


def work_lines(team, done=()) -> list[tuple[str, str]]:
    """Each spinner line with the agent it is about, so it can be shown in that agent's colour.

    Lines about the whole team belong to the conductor, who is first in the team.
    """
    lines = [(team[who], text.format(label_of(team[who]))) for who, text in WORK_LINES if team[who] not in done]
    if len(done) < 2:
        lines.append((team[0], "Passing notes between the three"))
    return lines or [(team[0], "Collecting the last report")]


def phase_label(phase: str, team) -> str:
    if phase == "plan":
        return "Planning the split"
    if phase == "work":
        first, second, third = (label_of(name) for name in team)
        return f"{first}, {second}, and {third} are working"
    if phase == "write":
        return "Writing the answer"
    return phase


class Status:
    """A small spinner on the status line while a request is running."""

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, tty: bool | None = None, team=DEFAULT_TEAM) -> None:
        self.tty = sys.stderr.isatty() if tty is None else tty
        self.team = tuple(team)
        self.began = time.monotonic()
        self._phase = ""
        self._done: set[str] = set()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.tty or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._spin, name="trips-spinner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1)
            self._thread = None
        if self.tty:
            sys.stderr.write("\r\x1b[2K")
            sys.stderr.flush()

    def elapsed(self) -> float:
        return time.monotonic() - self.began

    def feed(self, message: str) -> None:
        if message.startswith(STATUS):
            phase = message[len(STATUS):]
            if phase.startswith("done:"):
                with self._lock:
                    self._done.add(phase[5:])
                if not self.tty:
                    print(f"{label_of(phase[5:])} finished", file=sys.stderr, flush=True)
                return
            with self._lock:
                self._phase = phase
            if not self.tty:
                print(phase_label(phase, self.team), file=sys.stderr, flush=True)
            return
        if self.tty:
            sys.stderr.write("\r\x1b[2K" + message + ("\n" if not message.endswith("\n") else ""))
            sys.stderr.flush()
            return
        print(message, file=sys.stderr, flush=True)

    def line(self, tick: int, columns: int = 80) -> str:
        """The status line for one moment, kept short enough to stay on one row."""
        with self._lock:
            phase = self._phase
            done = set(self._done)
        if not phase:
            return ""
        # Planning and writing are the conductor's work. A working line belongs to the agent it names.
        if phase == "work":
            phrases = work_lines(self.team, done)
        else:
            phrases = [(self.team[0], text) for text in FLAVOR.get(phase) or (phase,)]
        owner, phrase = phrases[(tick // 18) % len(phrases)]
        frame = self.FRAMES[tick % len(self.FRAMES)]
        minutes, rest = divmod(int(self.elapsed()), 60)
        clock = f"{minutes}:{rest:02d}"
        plain_marks = ""
        marks = ""
        if phase == "work":
            for name in self.team:
                spec = spec_for(name)
                sign = "✓" if name in done else frame
                plain_marks += f"  {label_of(name)} {sign}"
                label = tint(label_of(name), spec.rgb, spec.code) if spec else label_of(name)
                marks += f"  {label} " + (f"\x1b[32m{sign}\x1b[0m" if name in done else f"\x1b[2m{sign}\x1b[0m")
        if 2 + len(phrase) + len(plain_marks) + len(clock) + 3 > columns:
            plain_marks = marks = ""
        room = max(8, columns - len(plain_marks) - len(clock) - 6)
        spec = spec_for(owner)
        spoken = f"{frame} {phrase[:room]}"
        spoken = tint(spoken, spec.rgb, spec.code) if spec else f"\x1b[36m{spoken}\x1b[0m"
        return f"{spoken}{marks}  \x1b[2m{clock}\x1b[0m"

    def _spin(self) -> None:
        tick = 0
        while not self._stop.wait(0.12):
            line = self.line(tick, shutil.get_terminal_size((80, 24)).columns)
            if not line:
                continue
            sys.stderr.write("\r\x1b[2K" + line)
            sys.stderr.flush()
            tick += 1


def format_answer(text: str, width: int | None = None, color: bool | None = None, accent: str = "") -> str:
    """Show markdown as terminal text. Asterisks used for emphasis are not printed.

    ``accent`` names the conductor. Headings, bold text, code, links, and bullet
    marks are shown in that agent's colour. Ordinary sentences are left alone.
    """
    if width is None:
        width = max(40, shutil.get_terminal_size((80, 24)).columns - 2)
    if color is None:
        color = sys.stdout.isatty()
    width = max(20, width)
    ink = ink_of(accent) if color else ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    paragraph: list[str] = []
    code: list[str] = []
    in_code = False

    def flush_paragraph() -> None:
        if not paragraph:
            return
        joined = " ".join(part.strip() for part in paragraph if part.strip())
        paragraph.clear()
        if not joined:
            return
        out.extend(_wrap(_inline(joined, color, ink), width))
        out.append("")

    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("```"):
            flush_paragraph()
            if in_code:
                out.extend(_code_block(code, width, color, ink))
                code = []
                out.append("")
            in_code = not in_code
            continue
        if in_code:
            code.append(raw.rstrip())
            continue
        if not stripped:
            flush_paragraph()
            continue
        if stripped.startswith(("Wrote:", "Notes:", "Left these")):
            flush_paragraph()
            note = stripped if not color else f"\x1b[2m{stripped}\x1b[0m"
            out.append(note)
            continue
        if stripped in {"---", "***", "___"}:
            flush_paragraph()
            rule = "─" * min(width, 36)
            out.append(f"\x1b[2m{rule}\x1b[0m" if color else rule)
            out.append("")
            continue
        if stripped.startswith("#"):
            flush_paragraph()
            marks = len(stripped) - len(stripped.lstrip("#"))
            title = _inline(stripped[marks:].strip(), color, ink)
            if color:
                title = f"\x1b[1m{_one_ink(title, ink)}\x1b[0m"
            out.extend(_wrap(title, width))
            out.append("")
            continue
        bullet = re.match(r"^(?:[-*]|\d+\.)\s+(.*)$", stripped)
        if bullet:
            flush_paragraph()
            body = _wrap(_inline(bullet.group(1), color, ink), max(20, width - 2))
            mark = f"{ink or _CYAN}•\x1b[0m" if color else "•"
            out.append(f"{mark} {body[0]}")
            for extra in body[1:]:
                out.append(f"  {extra}")
            continue
        paragraph.append(stripped)
    if in_code and code:
        out.extend(_code_block(code, width, color, ink))
    flush_paragraph()
    while out and out[-1] == "":
        out.pop()
    rendered = "\n".join(out)
    if color and "\x1b[" in rendered and not rendered.endswith("\x1b[0m"):
        rendered += "\x1b[0m"
    return rendered


_PLAIN_INK = "\x1b[39m"
# Used for highlights when no conductor colour is given.
_CYAN = "\x1b[36m"
_BRIGHT_CYAN = "\x1b[96m"


def _one_ink(text: str, ink: str) -> str:
    """Put a whole highlighted stretch in one colour, dropping colour changes inside it."""
    if not ink:
        return text
    return ink + text.replace(_PLAIN_INK, "").replace(ink, "") + _PLAIN_INK


def _inline(text: str, color: bool, ink: str = "") -> str:
    """Turn markdown emphasis into terminal styles.

    ``ink`` is the conductor's colour code. Code, bold text, and links are
    shown in it. The words around them keep the window's normal colour.
    """
    def code(match: re.Match[str]) -> str:
        inner = match.group(1)
        return f"{ink or _BRIGHT_CYAN}{inner}{_PLAIN_INK}" if color else inner

    def bold(match: re.Match[str]) -> str:
        inner = match.group(1)
        return f"\x1b[1m{_one_ink(inner, ink)}\x1b[22m" if color else inner

    def italic(match: re.Match[str]) -> str:
        inner = match.group(1)
        return f"\x1b[3m{inner}\x1b[23m" if color else inner

    def link(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2)
        shown = label if url == label else f"{label} ({url})"
        return f"\x1b[4m{_one_ink(shown, ink)}\x1b[24m" if color else shown

    text = re.sub(r"`([^`]+)`", code, text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, text)
    text = re.sub(r"\*\*(.+?)\*\*", bold, text)
    text = re.sub(r"__(.+?)__", bold, text)
    text = re.sub(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", italic, text)
    return text


def _wrap(text: str, width: int) -> list[str]:
    """Break at spaces. A single word longer than the line is cut to fit."""
    lines: list[str] = []
    current = ""
    used = 0
    for word in text.split():
        size = _visible_width(word)
        if current and used + 1 + size > width:
            lines.append(current)
            current, used = "", 0
        if size > width:
            pieces = _cut(word, width)
            lines.extend(pieces[:-1])
            word, size = pieces[-1], _visible_width(pieces[-1])
        current = f"{current} {word}" if current else word
        used = used + 1 + size if used else size
    if current:
        lines.append(current)
    return lines or [""]


def _cut(text: str, width: int) -> list[str]:
    if not text:
        return [""]
    lines: list[str] = []
    current = ""
    visible = 0
    index = 0
    while index < len(text):
        if text[index] == "\x1b":
            mark = _ANSI.match(text, index)
            if mark:
                current += mark.group(0)
                index = mark.end()
                continue
        char = text[index]
        if char == " " and visible >= width:
            lines.append(current.rstrip())
            current = ""
            visible = 0
            index += 1
            continue
        if visible >= width:
            lines.append(current)
            current = ""
            visible = 0
        current += char
        visible += 1
        index += 1
    if current:
        lines.append(current.rstrip())
    return lines or [""]


def _code_block(lines: list[str], width: int, color: bool, ink: str = "") -> list[str]:
    shown = []
    for line in lines:
        clipped = line[:width]
        if color:
            shown.append(f"{ink or _BRIGHT_CYAN}{clipped}\x1b[0m")
        else:
            shown.append(clipped)
    return shown


def _visible_width(text: str) -> int:
    return len(_ANSI.sub("", text))


def _brief(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    for mark in (". ", "! ", "? "):
        at = cleaned.find(mark)
        if 0 < at < limit:
            cleaned = cleaned[:at + 1]
            break
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit - 1].rstrip() + "…"


def format_plan(summary: str, rows: list[tuple[str, str, list[str]]], width: int = 72, color: bool = False) -> str:
    """Three short boxes, one per agent, instead of one long plan paragraph."""
    width = max(36, min(width, 78))
    blocks = [_brief(summary, width)]
    for agent, task, files in rows:
        blocks.append(_agent_box(agent, task, files, width, color))
    return "\n\n".join(blocks)


def _agent_box(agent: str, task: str, files: list[str], width: int, color: bool) -> str:
    inner = width - 2
    spec = spec_for(agent)
    title = label_of(agent)
    dashes = max(1, width - len(title) - 4)
    top = f"╭ {title} " + "─" * dashes + "╮"
    if len(top) < width:
        top = top[:-1] + "─" * (width - len(top)) + "╮"
    top = top[:width - 1] + "╮"
    task_lines = _wrap(_brief(task, 140), max(8, inner - 2))[:2] or [""]
    if files:
        shown = ", ".join(files[:3])
        if len(files) > 3:
            shown += f" +{len(files) - 3}"
    else:
        shown = "no file edits"
    rgb, code = (spec.rgb, spec.code) if spec else ((200, 200, 200), "37")
    bot = "╰" + "─" * inner + "╯"

    def edge(text: str) -> str:
        return tint(text, rgb, code, color)

    def row(content: str, faint: bool) -> str:
        text = content[:inner - 2]
        gap = " " * (inner - 2 - len(text))
        body = f"\x1b[2m{text}{gap}\x1b[0m" if color and faint else text + gap
        return edge("│") + " " + body + " " + edge("│")

    lines = [edge(top), *[row(line, False) for line in task_lines], row(shown, True), edge(bot)]
    return "\n".join(lines)


def format_header(team, seconds: float, width: int, color: bool = False) -> str:
    """A thin rule above the answer: who was on the team and how long it took."""
    minutes, rest = divmod(int(seconds), 60)
    took = f"{minutes}:{rest:02d}"
    names = [("★ " if index == 0 else "") + label_of(name) for index, name in enumerate(team)]
    plain = "── " + " · ".join(names) + " ── " + took + " "
    fill = "─" * max(0, min(width, 78) - len(plain))
    if not color:
        return plain + fill
    shown = []
    for name, text in zip(team, names):
        spec = spec_for(name)
        shown.append(tint(text, spec.rgb, spec.code) if spec else text)
    faint = "\x1b[2m"
    return (
        f"{faint}── \x1b[0m" + f"{faint} · \x1b[0m".join(shown)
        + f"{faint} ── {took} {fill}\x1b[0m"
    )
