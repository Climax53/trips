"""The opening screen: choose who conducts, then the two who work alongside."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field

from .keys import key_mode, raw_keys, read_key
from .roster import AgentSpec, readiness
from .safety import TripsError
from .style import bold, centered, color_on, dim, repaint, sgr, tint, visible_width

NUMBER_KEYS = "1234567890"
PANEL_WIDTH = 72


@dataclass
class Choice:
    spec: AgentSpec
    ready: bool
    why: str = ""


@dataclass
class TeamPicker:
    """What is highlighted and chosen. Keys change it; ``lines`` draws it."""

    choices: list[Choice]
    conductor: str = ""
    workers: list[str] = field(default_factory=list)
    stage: str = "conductor"
    index: int = 0
    message: str = ""
    _wanted: list[str] = field(default_factory=list)

    @classmethod
    def start(cls, choices: list[Choice], conductor: str, workers: list[str]) -> "TeamPicker":
        # Tools that can be used come first, so the screen can show them as one section.
        ordered = sorted(choices, key=lambda choice: not choice.ready)
        picker = cls(ordered, _wanted=list(workers))
        names = [choice.spec.name for choice in ordered]
        if conductor in names and ordered[names.index(conductor)].ready:
            picker.index = names.index(conductor)
        return picker

    @property
    def done(self) -> bool:
        return self.stage == "done"

    @property
    def team(self) -> list[str]:
        return [self.conductor, *self.workers]

    def number_of(self, name: str) -> str:
        """The key that picks this agent."""
        return NUMBER_KEYS[[choice.spec.name for choice in self.choices].index(name)]

    def handle(self, key: str) -> None:
        self.message = ""
        if key in ("up", "down"):
            self.index = (self.index + (1 if key == "down" else -1)) % len(self.choices)
            return
        pressed_number = key in NUMBER_KEYS and NUMBER_KEYS.index(key) < len(self.choices)
        if pressed_number:
            self.index = NUMBER_KEYS.index(key)
        current = self.choices[self.index]
        if self.stage == "conductor":
            if key == "enter" or pressed_number:
                if self._usable(current):
                    self._conduct(current.spec.name)
            return
        if key in ("esc", "backspace", "left"):
            self.stage = "conductor"
            self.index = [choice.spec.name for choice in self.choices].index(self.conductor)
        elif key == " " or pressed_number:
            self._toggle(current)
        elif key == "enter":
            if len(self.workers) == 2:
                self.stage = "done"
            else:
                self.message = "Pick two. Space adds or removes the highlighted one."

    def _usable(self, choice: Choice) -> bool:
        if choice.ready:
            return True
        hint = f" Install: {choice.spec.install}" if choice.spec.install and choice.why == "not installed" else ""
        self.message = f"{choice.spec.label} is {choice.why}." + (f" {choice.spec.note}" if choice.spec.note else hint)
        return False

    def _conduct(self, name: str) -> None:
        self.conductor = name
        open_names = [choice.spec.name for choice in self.choices if choice.ready and choice.spec.name != name]
        kept = [item for item in (self.workers or self._wanted) if item in open_names]
        for item in open_names:
            if len(kept) >= 2:
                break
            if item not in kept:
                kept.append(item)
        self.workers = kept[:2]
        self.stage = "workers"
        for index, choice in enumerate(self.choices):
            if choice.spec.name in self.workers:
                self.index = index
                break

    def _toggle(self, choice: Choice) -> None:
        name = choice.spec.name
        if name == self.conductor:
            self.message = f"{choice.spec.label} is already conducting."
        elif name in self.workers:
            self.workers.remove(name)
        elif self._usable(choice):
            self.workers.append(name)
            del self.workers[:-2]

    def lines(self, width: int = 80, color: bool = False) -> list[str]:
        """The screen as a framed panel: a heading, the tools in two sections, and the keys."""
        if self.stage == "done":
            return [self.summary(color)]
        inner = max(40, min(width - 4, PANEL_WIDTH)) - 2
        labels = {choice.spec.name: choice.spec.label for choice in self.choices}
        if self.stage == "conductor":
            step, title = "Step 1 of 2", "Choose the conductor"
            about = "The conductor plans the split and writes the one answer."
            keys = "↑↓ move   enter choose   1-9 pick by number"
        else:
            step, title = "Step 2 of 2", "Choose two workers"
            aboard = " and ".join(labels[name] for name in self.workers) or "nobody yet"
            about = f"{labels[self.conductor]} conducts. Working alongside: {aboard}."
            keys = "↑↓ move   space add or remove   enter start   esc back"
        name_width = max(len(choice.spec.label) for choice in self.choices) + 2
        ready = [(index, choice) for index, choice in enumerate(self.choices) if choice.ready]
        missing = [(index, choice) for index, choice in enumerate(self.choices) if not choice.ready]

        body = ["", "  " + bold(title, color), "  " + dim(about[:inner - 4], color), ""]
        for heading, group in ((f"INSTALLED · {len(ready)}", ready), (f"NOT AVAILABLE · {len(missing)}", missing)):
            if not group:
                continue
            body.append("  " + dim(heading, color))
            body.extend(self._row(index, choice, name_width, inner, color) for index, choice in group)
            body.append("")
        foot = sgr("33", self.message[:inner - 4], color) if self.message else dim(keys[:inner - 4], color)

        edge = lambda text: dim(text, color)  # noqa: E731
        rule = "─" * max(0, inner - len(step) - 10)
        out = [edge("╭─ ") + bold("Team", color) + edge(f" {rule} {step} ─╮")]
        out += [edge("│") + _pad(line, inner) + edge("│") for line in body]
        out.append(edge("├" + "─" * inner + "┤"))
        out.append(edge("│") + _pad("  " + foot, inner) + edge("│"))
        out.append(edge("╰" + "─" * inner + "╯"))
        return out

    def _row(self, index: int, choice: Choice, name_width: int, inner: int, color: bool) -> str:
        spec = choice.spec
        here = index == self.index
        number = NUMBER_KEYS[index] if index < len(NUMBER_KEYS) else " "
        conducting = self.stage == "workers" and spec.name == self.conductor
        chosen = self.stage == "workers" and spec.name in self.workers
        if conducting:
            mark, status = "★", "conductor"
        elif chosen:
            mark, status = "◉", "aboard"
        else:
            ready_mark = "●" if self.stage == "conductor" else "○"
            mark, status = (ready_mark if choice.ready else "·"), choice.why
        pointer = tint("❯", spec.rgb, spec.code, color) if here else " "
        # The status sits at the right edge. The maker fills what is left and is cut to fit.
        room = max(0, inner - name_width - len(status) - 13)
        detail = spec.maker[:room].ljust(room)
        name = mark + " " + spec.label.ljust(name_width)
        if not choice.ready:
            return f"  {pointer} " + dim(f"{number}  {name}{detail}  {status}", color)
        if here or conducting or chosen:
            name = tint(name, spec.rgb, spec.code, color)
        if here:
            name = bold(name, color)
        return f"  {pointer} {dim(number, color)}  {name}{dim(detail, color)}  {dim(status, color)}"

    def summary(self, color: bool = False) -> str:
        specs = {choice.spec.name: choice.spec for choice in self.choices}
        lead = specs[self.conductor]
        others = [tint(specs[name].label, specs[name].rgb, specs[name].code, color) for name in self.workers]
        return (
            "  " + tint("★ " + lead.label, lead.rgb, lead.code, color)
            + dim(" conducts · with ", color) + dim(" and ", color).join(others)
        )


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - visible_width(text))


def choices_for(roster: dict[str, AgentSpec]) -> list[Choice]:
    return [Choice(spec, *readiness(spec)) for spec in roster.values()]


def pick_team(roster: dict[str, AgentSpec], conductor: str, workers: list[str]) -> list[str]:
    """Ask at the keyboard. Returns the conductor first, then the two workers."""
    choices = choices_for(roster)
    ready = [choice for choice in choices if choice.ready]
    if len(ready) < 3:
        have = ", ".join(choice.spec.label for choice in ready) or "none"
        raise TripsError(
            f"Trips needs three AI tools installed, and found {len(ready)} ({have}). "
            "Run `trips doctor` to see what is missing."
        )
    picker = TeamPicker.start(choices, conductor, workers)
    if not raw_keys():
        return _ask_by_number(picker)
    color = color_on()
    painted = 0
    sys.stdout.write("\x1b[?25l")
    try:
        with key_mode():
            while True:
                width = shutil.get_terminal_size((80, 24)).columns
                lines = picker.lines(width, color)
                painted = repaint(lines if picker.done else centered(lines, width), painted)
                if picker.done:
                    return picker.team
                picker.handle(read_key())
    finally:
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


def _ask_by_number(picker: TeamPicker) -> list[str]:
    """For a window where single keys cannot be read: type the numbers and press Enter."""
    while picker.stage == "conductor":
        print(*picker.lines(), sep="\n")
        typed = input("  Conductor number, or Enter for the highlighted one: ").strip()
        picker.handle(typed[:1] if typed else "enter")
    print(*picker.lines(), sep="\n")
    typed = input("  Two worker numbers, or Enter to keep the marked ones: ").replace(",", " ").split()
    if typed:
        picker.workers = []
        for item in typed[:2]:
            picker.handle(item[:1])
    if len(picker.workers) != 2:
        raise TripsError("Two workers are needed alongside the conductor.")
    return picker.team
