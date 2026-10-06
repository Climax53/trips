"""The Trips command line. No arguments opens a conversation."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from .agents import FakeRunner, LiveRunner
from .config import config_path, load_config, save_team
from .display import Status, format_answer, format_header
from .inputbox import read_request
from .jobs import acquire_lock, clean_jobs, release_lock
from .logo import BOX_ROOM, TEAM_SCREEN_ROOM, show_logo
from .orchestrator import run_request
from .picker import choices_for, pick_team
from .roster import build_roster, fill_team, label_of, resolve_cli
from .safety import TripsError
from .style import color_on, dim, enable_ansi, tint

HELP = """Type a request and press Enter. Trips replies once, then waits for the next one.
team     choose a different conductor or different workers
clean    delete saved job notes under .trips/jobs (the conversation stays)
help     show this again
exit     leave
"""


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(prog="trips", add_help=True)
    parser.add_argument("request", nargs="*", help="Optional one-shot request. With no request, Trips stays open.")
    parser.add_argument("--project", default=".", help="Project folder. Defaults to the folder you are in.")
    parser.add_argument("--conductor", help="Who plans the split and writes the answer, for example claude.")
    parser.add_argument("--with", dest="workers", help="The two other agents, for example codex,gemini.")
    parser.add_argument("--last", action="store_true", help="Use the same team as last time without asking.")
    parser.add_argument("--here", action="store_true", help="Do not ask before using a folder outside your trusted folders.")
    parser.add_argument("--doctor", action="store_true", help="Check the setup without calling the agents.")
    args = parser.parse_args(argv)
    project = Path(args.project).resolve()
    if not project.is_dir():
        print(f"That folder does not exist: {project}", file=sys.stderr)
        return 1
    try:
        config = load_config()
        roster = build_roster(config)
    except TripsError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.doctor or args.request == ["doctor"]:
        return doctor(project, config, roster)
    if not _folder_allowed(project, config, args.here, interactive=not args.request):
        return 1
    try:
        # The picture goes above the team screen when the window is tall enough
        # for both. Otherwise it waits and is shown above the request box.
        size = str(config.get("logo", "medium")).lower()
        shown = show_logo(TEAM_SCREEN_ROOM, picture_only=True, size=size)
        team = choose_team(roster, config, args.conductor, args.workers, args.last)
        if not shown:
            show_logo(BOX_ROOM, size=size)
    except TripsError as exc:
        print(exc, file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print()
        return 130
    if os.environ.get("TRIPS_FAKE") == "1":
        runner = FakeRunner()
    else:
        runner = LiveRunner(tuple(str(item) for item in config["strip_env"]))
    lock = None
    try:
        lock = acquire_lock(project)
        if args.request:
            text = " ".join(args.request).strip()
            return _one(project, text, runner, config, team)
        return _repl(project, runner, config, roster, team)
    except TripsError as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        release_lock(lock)


def choose_team(roster: dict, config: dict, conductor: str | None, workers: str | None, last: bool) -> list[str]:
    """The conductor first, then two workers.

    Named on the command line or in TRIPS_CONDUCTOR / TRIPS_WITH, they are used
    as given. Otherwise a person at the keyboard is asked, starting from the
    team used last time.
    """
    named_conductor = (conductor or os.environ.get("TRIPS_CONDUCTOR", "")).strip().lower()
    named_workers = (workers or os.environ.get("TRIPS_WITH", "")).strip().lower()
    lead = named_conductor or config["conductor"]
    if lead not in roster:
        if named_conductor:
            raise TripsError(f"Unknown conductor '{lead}'. Choose from: {', '.join(roster)}")
        lead = next(iter(roster))
    wanted = [item for item in named_workers.replace(",", " ").split()] if named_workers else list(config["workers"])
    wanted = [name for name in wanted if named_workers or name in roster]
    if named_conductor or named_workers or last or not sys.stdin.isatty():
        return fill_team(roster, lead, wanted)
    team = pick_team(roster, lead, wanted)
    save_team(team[0], team[1:])
    return team


def doctor(project: Path, config: dict, roster: dict) -> int:
    color = color_on()
    print(f"Folder: {project}")
    print(f"Notes would go in: {project / '.trips'}")
    print(f"Settings: {config_path()}" + ("" if config_path().exists() else " (not created yet, defaults in use)"))
    print("Trips does not copy this folder. Agents read it and propose text. Trips writes accepted files here.")
    print()
    ready = 0
    for choice in choices_for(roster):
        spec = choice.spec
        name = tint(spec.label.ljust(10), spec.rgb, spec.code, color)
        if choice.ready:
            ready += 1
            print(f"  ✓ {name} {' '.join(resolve_cli(spec.name, spec.cli))}")
        else:
            hint = spec.note if choice.why != "not installed" else spec.install
            print(dim(f"  · {spec.label.ljust(10)} {choice.why}" + (f"  ({hint})" if hint else ""), color))
    print()
    team = fill_team(roster, config["conductor"] if config["conductor"] in roster else next(iter(roster)),
                     [name for name in config["workers"] if name in roster])
    print(f"Team if you do not choose: {label_of(team[0])} conducts, with {label_of(team[1])} and {label_of(team[2])}")
    if config["strip_env"]:
        print("Hidden from the agents: " + ", ".join(str(item) for item in config["strip_env"]))
    if ready < 3:
        print(f"Trips needs three of these installed. {ready} found.")
        return 1
    return 0


def _members(roster: dict, team: list[str]) -> list[tuple]:
    return [(roster[name].label, roster[name].rgb, roster[name].code) for name in team]


def _repl(project: Path, runner, config: dict, roster: dict, team: list[str]) -> int:
    color = color_on()
    print(dim(str(project), color))
    if not sys.stdin.isatty():
        print(f"Conductor: {label_of(team[0])}, with {label_of(team[1])} and {label_of(team[2])}")
    print()
    asked: list[str] = []
    while True:
        try:
            line = read_request(_members(roster, team), asked)
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        command = line.strip()
        if not command:
            continue
        lowered = command.lower().lstrip("/")
        if lowered in {"exit", "quit"}:
            return 0
        if lowered == "help":
            print(HELP)
            continue
        if lowered == "clean":
            removed = clean_jobs(project)
            print(f"Removed {removed} job folder(s). The conversation is still in .trips.")
            continue
        if lowered in {"team", "conductor"}:
            team = _switch(roster, team)
            continue
        asked.append(command)
        _one(project, command, runner, config, team)
    return 0


def _switch(roster: dict, team: list[str]) -> list[str]:
    if not sys.stdin.isatty():
        print("The team can only be changed at the keyboard.")
        return team
    try:
        chosen = pick_team(roster, team[0], team[1:])
    except TripsError as exc:
        print(exc, file=sys.stderr)
        return team
    except KeyboardInterrupt:
        print()
        return team
    save_team(chosen[0], chosen[1:])
    print()
    return chosen


def _one(project: Path, request: str, runner, config: dict, team: list[str]) -> int:
    timeout_s = float(config.get("worker_timeout_minutes", 20)) * 60
    history = int(config.get("history_turns", 6))
    status = Status(team=team)
    status.start()
    try:
        outcome = run_request(
            project,
            request,
            runner,
            conductor=team[0],
            timeout_s=timeout_s,
            history=history,
            progress=status.feed,
            team=team,
        )
    except KeyboardInterrupt:
        runner.stop_all()
        print("\nStopped.", file=sys.stderr)
        return 130
    except TripsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        status.stop()
    width = max(40, shutil.get_terminal_size((80, 24)).columns - 2)
    print()
    if sys.stdout.isatty():
        print(format_header(team, status.elapsed(), width, color_on()))
        print()
    print(format_answer(outcome.answer, accent=team[0]), flush=True)
    print(flush=True)
    return 0


def _folder_allowed(project: Path, config: dict, force: bool, interactive: bool) -> bool:
    trusted = [Path(str(item)).expanduser() for item in config["trusted_folders"] if str(item).strip()]
    if force or not trusted or os.environ.get("TRIPS_ALLOW_OUTSIDE") == "1":
        return True
    for folder in trusted:
        try:
            project.relative_to(folder.resolve())
            return True
        except (ValueError, OSError):
            continue
    print(f"{project} is outside your trusted folders ({', '.join(str(item) for item in trusted)}).")
    if not interactive:
        print("Re-run with --here if you mean to use this folder.", file=sys.stderr)
        return False
    try:
        answer = input("Create .trips in this folder anyway? Type yes: ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer.strip().lower() == "yes"


def _configure_stdio() -> None:
    enable_ansi()
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
