"""Trips keeps work inside the project folder and returns one answer."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trips_tool.agents import AgentOutput, child_env, command_for  # noqa: E402
from trips_tool.cli import choose_team, main  # noqa: E402
from trips_tool.config import load_config, save_team  # noqa: E402
from trips_tool.inputbox import Edit, request_frame  # noqa: E402
from trips_tool.picker import Choice, TeamPicker  # noqa: E402
from trips_tool.roster import BUILTIN, build_roster, command_from_npm_shim, fill_team  # noqa: E402
from trips_tool.logo import logo_lines  # noqa: E402
from trips_tool.jobs import clean_jobs  # noqa: E402
from trips_tool.orchestrator import run_request  # noqa: E402
from trips_tool.plan import parse_plan, parse_worker  # noqa: E402
from trips_tool.safety import (  # noqa: E402
    TripsError,
    changed_files,
    ensure_gitignore,
    extract_object,
    model_text,
    safe_path,
    snapshot,
)


def setUpModule():
    # Keep automated runs from waiting on the team screen or drawing the logo,
    # and away from the settings of whoever is running the tests.
    os.environ["TRIPS_CONDUCTOR"] = "claude"
    os.environ["TRIPS_NO_LOGO"] = "1"
    os.environ["TRIPS_CONFIG"] = str(Path(tempfile.mkdtemp()) / "config.json")


class SafetyTests(unittest.TestCase):
    def test_paths_stay_inside_the_project(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            inside = safe_path(root, "src/app.py")
            self.assertEqual(inside, (root / "src" / "app.py").resolve())
            for bad in ("../outside.txt", r"..\outside.txt", "/tmp/x", r"C:\Windows\x", ".trips/secret.md"):
                with self.assertRaises(TripsError):
                    safe_path(root, bad)

    def test_snapshot_skips_trips_notes_and_does_not_walk_heavy_folders(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
            (root / ".trips" / "jobs").mkdir(parents=True)
            (root / ".trips" / "jobs" / "note.txt").write_text("note\n", encoding="utf-8")
            (root / "node_modules" / "pkg").mkdir(parents=True)
            (root / "node_modules" / "pkg" / "index.js").write_text("x", encoding="utf-8")
            before = snapshot(root)
            self.assertIn("src/app.py", before)
            self.assertNotIn(".trips/jobs/note.txt", before)
            self.assertNotIn("node_modules/pkg/index.js", before)
            self.assertIn("node_modules/", before)
            (root / ".trips" / "jobs" / "later.txt").write_text("later\n", encoding="utf-8")
            self.assertEqual(changed_files(before, snapshot(root)), [])
            (root / "node_modules" / "pkg" / "extra.js").write_text("y", encoding="utf-8")
            self.assertIn("node_modules/", changed_files(before, snapshot(root)))

    def test_gitignore_is_added_once_for_a_repo(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            ensure_gitignore(root)
            self.assertFalse((root / ".gitignore").exists())
            (root / ".git").mkdir()
            ensure_gitignore(root)
            ensure_gitignore(root)
            self.assertEqual((root / ".gitignore").read_text(encoding="utf-8"), ".trips/\n")


class PlanTests(unittest.TestCase):
    def test_overlap_and_missing_agent_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            good = {
                "summary": "Split the work.",
                "assignments": [
                    {"agent": "grok", "task": "Write the note.", "files": ["a.txt"]},
                    {"agent": "claude", "task": "Check the wording.", "files": []},
                    {"agent": "codex", "task": "Check the path.", "files": ["b.txt"]},
                ],
            }
            plan = parse_plan(root, good, ("claude", "codex", "grok"))
            self.assertEqual([item.agent for item in plan.assignments], ["claude", "codex", "grok"])
            overlap = json.loads(json.dumps(good))
            overlap["assignments"][2]["files"] = ["a.txt"]
            with self.assertRaises(TripsError):
                parse_plan(root, overlap, ("claude", "codex", "grok"))
            missing = json.loads(json.dumps(good))
            missing["assignments"].pop()
            with self.assertRaises(TripsError):
                parse_plan(root, missing, ("claude", "codex", "grok"))

    def test_worker_cannot_propose_someone_elses_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result = parse_worker(
                root,
                "grok",
                ["a.txt"],
                {"report": "Done.", "files": [
                    {"path": "a.txt", "content": "hello\n"},
                    {"path": "b.txt", "content": "nope\n"},
                ]},
                "",
            )
            self.assertEqual([item.path for item in result.files], ["a.txt"])
            self.assertTrue(any("b.txt" in note for note in result.notes))


class CommandTests(unittest.TestCase):
    def test_workers_are_read_only_and_stay_on_the_project(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            notes = root / ".trips" / "job"
            notes.mkdir(parents=True)
            previous = {
                "TRIPS_GROK": os.environ.get("TRIPS_GROK"),
                "TRIPS_CLAUDE": os.environ.get("TRIPS_CLAUDE"),
                "TRIPS_CODEX": os.environ.get("TRIPS_CODEX"),
            }
            os.environ["TRIPS_GROK"] = "grok-test"
            os.environ["TRIPS_CLAUDE"] = "claude-test"
            os.environ["TRIPS_CODEX"] = "codex-test"
            try:
                grok = command_for("grok", root, notes / "prompt.txt", notes / "last.txt")
                claude = command_for("claude", root, notes / "prompt.txt", notes / "last.txt")
                codex = command_for("codex", root, notes / "prompt.txt", notes / "last.txt")
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            self.assertIn("read_file,grep,list_dir", grok)
            self.assertIn("run_terminal_cmd,search_replace,write,Agent,web_search,web_fetch", grok)
            self.assertNotIn("--worktree", grok)
            self.assertIn("Edit,Write,NotebookEdit,Bash,PowerShell,WebSearch,WebFetch", claude)
            self.assertIn("read-only", codex)
            self.assertNotIn("--worktree", codex)
            self.assertTrue(str(notes / "last.txt") in codex)
            joined = " ".join(grok + claude + codex)
            self.assertNotIn("tempfile", joined.lower())

    def test_npm_shim_expands_to_the_real_program(self):
        with tempfile.TemporaryDirectory() as raw:
            cmd = Path(raw) / "claude.cmd"
            cmd.write_text(
                r'"%dp0%\node_modules\@anthropic-ai\claude-code\bin\claude.exe" %*',
                encoding="utf-8",
            )
            resolved = command_from_npm_shim(cmd)
            self.assertEqual(
                resolved,
                [str(Path(raw) / r"node_modules\@anthropic-ai\claude-code\bin\claude.exe")],
            )
            self.assertNotIn("%dp0%", resolved[0])

    def test_doctor_word_does_not_create_notes(self):
        with tempfile.TemporaryDirectory() as raw:
            code = main(["--project", raw, "doctor"])
            self.assertEqual(code, 0)
            self.assertFalse((Path(raw) / ".trips").exists())

    def test_product_source_does_not_copy_the_project(self):
        source = Path(__file__).resolve().parents[1] / "src"
        banned = ("tempfile", "mkdtemp", "--worktree", "worktree")
        for path in source.rglob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            for word in banned:
                self.assertNotIn(word, text, f"{path.name} mentions {word}")


class LogoTests(unittest.TestCase):
    def test_engine_has_one_robot_and_joins_from_row_to_row(self):
        from trips_tool.logo import ENGINE
        from trips_tool.style import visible_width
        wide = logo_lines(100)
        self.assertEqual(sum(line.count("◉ ◉") for line in wide), 1)
        self.assertIn("T R I P S", " ".join(wide))
        self.assertLessEqual(len(wide), 10)
        self.assertEqual(len({len(row) for row in ENGINE[3:8]}), 1)
        for line in wide:
            self.assertLess(len(line.strip()), 44)
        self.assertEqual(logo_lines(40), [])
        # Every upright stroke of the cab and boiler has a stroke directly above or below it.
        uprights = "│┤├┴┬╭╮╰╯"
        for row in range(2, 8):
            for column, char in enumerate(ENGINE[row]):
                if char in "│┤├":
                    above = ENGINE[row - 1].ljust(60)[column]
                    below = ENGINE[row + 1].ljust(60)[column]
                    self.assertTrue(above in uprights + "▄" or below in uprights, (row, column))

    def test_largest_picture_that_fits_is_used_and_small_windows_get_the_engine(self):
        from trips_tool.logo import pictures, picture_for
        from trips_tool.style import visible_width
        sizes = [(max(len(line) for line in lines), len(lines)) for lines in pictures()]
        self.assertEqual(len(sizes), 3)
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        for lines in pictures():
            self.assertTrue(all(char == " " or "⠀" <= char <= "⣿" for line in lines for char in line))
        widest, tallest = sizes[0]
        smallest_wide, smallest_tall = sizes[-1]
        self.assertEqual(len(picture_for(widest + 2, tallest, "large")), tallest)
        self.assertEqual(len(picture_for(widest + 1, tallest, "large")), sizes[1][1])
        self.assertEqual(len(picture_for(200, smallest_tall, "large")), smallest_tall)
        self.assertIsNone(picture_for(200, smallest_tall - 1, "large"))
        self.assertIsNone(picture_for(smallest_wide + 1, 100, "large"))
        # The usual setting stops at the smallest picture however big the window is.
        self.assertEqual(len(picture_for(200, 100)), smallest_tall)
        self.assertIsNone(picture_for(200, 100, "small"))
        for size in ("small", "medium", "large"):
            for columns, rows in ((200, 60), (100, 40), (80, 24), (80, 10), (50, 60)):
                lines = logo_lines(columns, rows=rows, size=size)
                self.assertTrue(lines)
                self.assertTrue(all(visible_width(line) < columns for line in lines))
                self.assertEqual("T R I P S" in " ".join(lines), picture_for(columns, rows, size) is None)

    def test_picture_sits_in_the_middle_of_the_request_box(self):
        from trips_tool.style import content_width
        for columns in (80, 100, 140):
            for rows in (0, 30):
                lines = logo_lines(columns, rows=rows)
                left = min(len(line) - len(line.lstrip(" ")) for line in lines if line.strip())
                right = content_width(columns) - max(len(line.rstrip()) for line in lines)
                self.assertLessEqual(abs(left - right), 3, (columns, rows, left, right))

    def test_picture_is_white_whoever_conducts(self):
        os.environ["COLORTERM"] = "truecolor"
        try:
            for rows in (0, 30):
                lines = logo_lines(100, color=True, rows=rows)
                self.assertNotIn("38;2", " ".join(lines))
                self.assertTrue(lines[0].startswith("[97m"))
        finally:
            os.environ.pop("COLORTERM", None)


class FormatTests(unittest.TestCase):
    def test_bold_markers_are_not_shown(self):
        from trips_tool.display import format_answer
        colored = format_answer("Please **fix the login** now.", width=60, color=True)
        plain = format_answer("Please **fix the login** now.", width=60, color=False)
        self.assertNotIn("**", colored)
        self.assertNotIn("**", plain)
        self.assertIn("\x1b[1mfix the login\x1b[22m", colored)
        self.assertIn("fix the login", plain)

    def test_code_lists_and_headings_are_readable(self):
        from trips_tool.display import format_answer
        text = format_answer("# Plan\n\n- Run `trips`\n\nSee [docs](https://example.com).", width=40, color=False)
        self.assertIn("Plan", text)
        self.assertNotIn("# Plan", text)
        self.assertIn("• Run trips", text)
        self.assertNotIn("`", text)
        self.assertIn("docs (https://example.com)", text)

    def test_plan_is_three_short_boxes(self):
        from trips_tool.display import format_plan
        long_task = "This task goes on and on. " * 40
        text = format_plan(
            "Split the greeting into three small parts. Then keep talking forever.",
            [
                ("grok", long_task, ["trips-hello.md"]),
                ("claude", "Check the wording.", []),
                ("codex", "Check the folder.", []),
            ],
            width=48,
            color=False,
        )
        self.assertIn("╭ Grok ", text)
        self.assertIn("╭ Claude ", text)
        self.assertIn("╭ Codex ", text)
        self.assertIn("trips-hello.md", text)
        self.assertIn("no file edits", text)
        self.assertLess(len(text), len(long_task))
        self.assertNotIn("keep talking forever", text)
        for line in text.splitlines():
            if line.startswith(("╭", "│", "╰")):
                self.assertEqual(len(line), 48)
        from trips_tool.display import _visible_width
        colored = format_plan(
            "Split the greeting.",
            [("grok", "Write the note.", ["trips-hello.md"]), ("claude", "Check it.", []), ("codex", "Check the folder.", [])],
            width=48,
            color=True,
        )
        import re
        for line in colored.splitlines():
            plain = re.sub(r"\x1b\[[0-9;]*m", "", line)
            if plain.startswith(("╭", "│", "╰")):
                self.assertEqual(_visible_width(line), 48)
        from trips_tool.display import work_flavor
        spoken = " ".join(work_flavor(("grok", "claude", "codex"))).lower()
        self.assertIn("grok finishing up", spoken)
        self.assertIn("claude reviewing code", spoken)
        self.assertIn("codex writing scripts", spoken)
        still = " ".join(work_flavor(("grok", "claude", "codex"), done={"grok"})).lower()
        self.assertNotIn("grok", still)

    def test_long_lines_wrap(self):
        from trips_tool.display import format_answer
        text = format_answer("word " * 30, width=20, color=False)
        self.assertTrue(all(len(line) <= 20 for line in text.splitlines()))


class BoxTests(unittest.TestCase):
    TEAM = [("Claude", (217, 119, 87), "35"), ("Codex", (16, 163, 127), "33"), ("Gemini", (66, 133, 244), "94")]

    def test_every_box_line_is_the_asked_width(self):
        from trips_tool.style import visible_width
        for width in (28, 36, 48, 80, 100):
            for sample, cursor in (("", 0), ("hello", 2), ("x" * 90, 80), ("y" * 900, 900)):
                for team in ((), self.TEAM):
                    for color in (True, False):
                        frame = request_frame(sample, cursor, width, team, color=color)
                        for line in frame:
                            self.assertEqual(visible_width(line), width, (width, sample[:5], color))

    def test_box_names_the_team_and_grows_with_the_text(self):
        short = request_frame("hello", 5, 60, self.TEAM, color=False)
        self.assertIn("Trips", short[0])
        self.assertIn("★ Claude · Codex · Gemini", short[0])
        self.assertEqual(len(short), 3)
        long = request_frame("z" * 130, 130, 60, self.TEAM, color=False)
        self.assertEqual(len(long), 5)
        hinted = request_frame("", 0, 60, self.TEAM, hint="enter send", color=False)
        self.assertIn("enter send", hinted[-1])
        self.assertIn("Ask for one answer", hinted[1])

    def test_editing_keys(self):
        edit = Edit()
        for key in "helo":
            edit.apply(key)
        edit.apply("left")
        edit.apply("l")
        self.assertEqual((edit.text, edit.cursor), ("hello", 4))
        edit.apply("home")
        edit.apply("delete")
        edit.apply("end")
        edit.apply("backspace")
        self.assertEqual(edit.text, "ell")
        edit.apply("up")
        self.assertEqual(edit.text, "ell")
        edit.apply("esc")
        self.assertEqual((edit.text, edit.cursor), ("", 0))


class TeamTests(unittest.TestCase):
    def picker(self, missing=("qwen",), conductor="claude", workers=("codex", "grok")):
        choices = [Choice(spec, spec.name not in missing, "not installed" if spec.name in missing else "")
                   for spec in BUILTIN]
        return TeamPicker.start(choices, conductor, list(workers))

    def test_enter_twice_keeps_the_last_team(self):
        picker = self.picker()
        picker.handle("enter")
        self.assertEqual(picker.stage, "workers")
        picker.handle("enter")
        self.assertTrue(picker.done)
        self.assertEqual(picker.team, ["claude", "codex", "grok"])

    def test_any_installed_agent_can_conduct_with_any_two_others(self):
        picker = self.picker()
        picker.handle(picker.number_of("gemini"))
        self.assertEqual(picker.conductor, "gemini")
        self.assertEqual(picker.workers, ["codex", "grok"])
        picker.handle(picker.number_of("claude"))
        self.assertEqual(picker.workers, ["grok", "claude"])
        picker.handle(picker.number_of("gemini"))
        self.assertIn("already conducting", picker.message)
        picker.handle("enter")
        self.assertEqual(picker.team, ["gemini", "grok", "claude"])

    def test_missing_tools_cannot_be_chosen_and_two_workers_are_required(self):
        picker = self.picker()
        self.assertEqual(picker.choices[-1].spec.name, "qwen")
        picker.handle(picker.number_of("qwen"))
        self.assertEqual(picker.stage, "conductor")
        self.assertIn("not installed", picker.message)
        picker.handle("1")
        picker.handle(picker.number_of("codex"))
        picker.handle("enter")
        self.assertFalse(picker.done)
        self.assertIn("Pick two", picker.message)
        picker.handle("esc")
        self.assertEqual(picker.stage, "conductor")

    def test_screen_rows_fit_the_window(self):
        from trips_tool.style import visible_width
        picker = self.picker()
        for stage in ("conductor", "workers"):
            for width in (60, 80, 120):
                for color in (True, False):
                    lines = picker.lines(width, color)
                    self.assertEqual(len({visible_width(line) for line in lines}), 1)
                    self.assertLess(visible_width(lines[0]), width)
                    plain = [re.sub(r"\[[0-9;]*m", "", line) for line in lines]
                    self.assertTrue(plain[0].startswith("╭─ Team "))
                    self.assertTrue(plain[0].endswith(f"Step {1 if stage == 'conductor' else 2} of 2 ─╮"))
                    self.assertTrue(any("INSTALLED · 9" in line for line in plain))
                    self.assertTrue(any("NOT AVAILABLE · 1" in line for line in plain))
                    self.assertTrue(plain[-3].startswith("├") and plain[-1].startswith("╰"))
            picker.handle("enter")
        self.assertIn("conducts", picker.lines()[0])

    def test_team_from_the_command_line_and_the_config(self):
        roster = build_roster({})
        self.assertEqual(fill_team(roster, "codex", ["claude", "gemini"]), ["codex", "claude", "gemini"])
        self.assertEqual(fill_team(roster, "codex", ["codex", "claude", "gemini"]), ["codex", "claude", "gemini"])
        self.assertEqual(len(set(fill_team(roster, "grok", ["claude"]))), 3)
        with self.assertRaises(TripsError):
            fill_team(roster, "nobody", [])
        config = load_config()
        self.assertEqual(choose_team(roster, config, "gemini", "qwen,droid", False), ["gemini", "qwen", "droid"])
        with self.assertRaises(TripsError):
            choose_team(roster, config, "claude", "nobody,codex", False)
        save_team("gemini", ["claude", "codex"])
        saved = load_config()
        self.assertEqual((saved["conductor"], saved["workers"]), ("gemini", ["claude", "codex"]))
        Path(os.environ["TRIPS_CONFIG"]).unlink()

    def test_config_can_set_a_model_or_add_a_tool(self):
        roster = build_roster({"agents": {
            "gemini": {"model": "gemini-pro"},
            "local": {"label": "Local", "command": ["mytool", "--ask", "{prompt_file}"]},
        }})
        try:
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                os.environ["TRIPS_GEMINI"] = "gemini-test"
                os.environ["TRIPS_LOCAL"] = "local-test"
                gemini = command_for("gemini", root, root / "p.txt", root / "l.txt")
                local = command_for("local", root, root / "p.txt", root / "l.txt")
            self.assertEqual(gemini, ["gemini-test", "--model", "gemini-pro", "--output-format", "json"])
            self.assertEqual(local, ["local-test", "--ask", str(root / "p.txt")])
            self.assertFalse(roster["local"].stdin)
            for bad in ({"agents": {"plan": {"command": ["x"]}}}, {"agents": {"new": {"label": "No command"}}}):
                with self.assertRaises(TripsError):
                    build_roster(bad)
        finally:
            os.environ.pop("TRIPS_GEMINI", None)
            os.environ.pop("TRIPS_LOCAL", None)
            build_roster({})

    def test_only_listed_variables_are_hidden_from_the_agents(self):
        os.environ["TRIPS_TEST_KEY"] = "secret"
        os.environ["CLAUDECODE"] = "1"
        try:
            self.assertEqual(child_env().get("TRIPS_TEST_KEY"), "secret")
            self.assertNotIn("TRIPS_TEST_KEY", child_env(("TRIPS_TEST_KEY",)))
            self.assertNotIn("CLAUDECODE", child_env())
        finally:
            os.environ.pop("TRIPS_TEST_KEY", None)
            os.environ.pop("CLAUDECODE", None)

    def test_status_line_ticks_only_the_agents_that_reported(self):
        from trips_tool.display import STATUS, Status
        from trips_tool.style import visible_width
        status = Status(tty=True, team=("gemini", "claude", "codex"))
        self.assertEqual(status.line(0), "")
        status._phase = "work"
        status.feed(STATUS + "done:claude")
        line = status.line(3, 100)
        plain = line.replace("\x1b[0m", "")
        self.assertIn("Claude", plain)
        self.assertEqual(plain.count("✓"), 1)
        self.assertLess(visible_width(status.line(3, 30)), 30)

    def test_a_run_with_a_different_conductor(self):
        from trips_tool.agents import FakeRunner
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            seen = []
            outcome = run_request(
                root, "write a hello note", FakeRunner(), conductor="gemini",
                team=("gemini", "qwen", "claude"), timeout_s=5, progress=seen.append,
            )
            self.assertEqual(outcome.written, ["trips-hello.md"])
            self.assertIn("Gemini drafted it", outcome.answer)
            plan = json.loads((outcome.job_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual([item["agent"] for item in plan["assignments"]], ["gemini", "qwen", "claude"])
            self.assertEqual(sum(1 for item in seen if "done:" in item), 3)
            with self.assertRaises(TripsError):
                run_request(root, "again", FakeRunner(), conductor="codex", team=("gemini", "qwen", "claude"))


class ColourTests(unittest.TestCase):
    CLAUDE = "\x1b[38;2;217;119;87m"
    CODEX = "\x1b[38;2;16;163;127m"
    GEMINI = "\x1b[38;2;66;133;244m"

    def setUp(self):
        self.previous = os.environ.get("COLORTERM")
        os.environ["COLORTERM"] = "truecolor"

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("COLORTERM", None)
        else:
            os.environ["COLORTERM"] = self.previous

    def test_spinner_line_is_the_colour_of_the_agent_it_names(self):
        from trips_tool.display import Status, work_lines
        team = ("gemini", "claude", "codex")
        status = Status(tty=True, team=team)
        status._phase = "plan"
        self.assertTrue(status.line(0, 100).startswith(self.GEMINI))
        status._phase = "write"
        self.assertTrue(status.line(0, 100).startswith(self.GEMINI))
        status._phase = "work"
        owners = {"gemini": self.GEMINI, "claude": self.CLAUDE, "codex": self.CODEX}
        for index, (owner, text) in enumerate(work_lines(team)):
            line = status.line(index * 18, 120)
            self.assertTrue(line.startswith(owners[owner]), text)
            self.assertIn(text, line)

    def test_highlights_take_the_conductor_colour_and_sentences_do_not(self):
        from trips_tool.display import format_answer
        source = "# Result\n\nPlain words, then **the fix** and `run.py`.\n\n- one [docs](https://example.com)\n\n```\ncode line\n```"
        for accent, ink, other in (("claude", self.CLAUDE, self.CODEX), ("codex", self.CODEX, self.CLAUDE)):
            text = format_answer(source, width=70, color=True, accent=accent)
            self.assertNotIn(other, text)
            self.assertIn(f"\x1b[1m{ink}Result", text)
            self.assertIn(f"\x1b[1m{ink}the fix\x1b[39m\x1b[22m", text)
            self.assertIn(f"{ink}run.py\x1b[39m", text)
            self.assertIn(f"{ink}•", text)
            self.assertIn(f"{ink}code line", text)
            self.assertIn(f"{ink}docs (https://example.com)", text)
            sentence = next(line for line in text.splitlines() if "Plain words" in line)
            self.assertTrue(sentence.startswith("Plain words, then "))
        plain = format_answer(source, width=70, color=False, accent="claude")
        self.assertNotIn("\x1b", plain)

    def test_bold_holding_code_stays_one_colour(self):
        from trips_tool.display import format_answer
        text = format_answer("See **the `main` loop** here.", width=70, color=True, accent="claude")
        self.assertIn(f"\x1b[1m{self.CLAUDE}the main loop\x1b[39m\x1b[22m here.", text)


class EnvelopeTests(unittest.TestCase):
    def test_model_text_unwraps_cli_json(self):
        raw = json.dumps({"text": '{"report":"ok","files":[]}', "stopReason": "end_turn"})
        self.assertEqual(extract_object(model_text(raw))["report"], "ok")
        gemini = json.dumps({"response": '{"report":"fine","files":[]}', "stats": {}})
        self.assertEqual(extract_object(model_text(gemini))["report"], "fine")


class RunTests(unittest.TestCase):
    def test_fake_run_writes_only_inside_the_project(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            outside = Path(raw).parent
            before_outside = {item.name for item in outside.iterdir()}
            os.environ["TRIPS_FAKE"] = "1"
            os.environ["TRIPS_ALLOW_OUTSIDE"] = "1"
            try:
                code = main(["--project", str(root), "--here", "write a hello note"])
                code_again = main(["--project", str(root), "--here", "check the note"])
            finally:
                os.environ.pop("TRIPS_FAKE", None)
                os.environ.pop("TRIPS_ALLOW_OUTSIDE", None)
            self.assertEqual(code, 0)
            self.assertEqual(code_again, 0)
            self.assertEqual((root / "trips-hello.md").read_text(encoding="utf-8"), "Hello from Trips.\n")
            jobs = list((root / ".trips" / "jobs").iterdir())
            self.assertEqual(len(jobs), 1)
            self.assertTrue((root / ".trips" / "conversation.jsonl").exists())
            self.assertTrue((jobs[0] / "previous" / "trips-hello.md").is_file())
            added_outside = {item.name for item in outside.iterdir()} - before_outside
            self.assertNotIn(root.name, added_outside)
            removed = clean_jobs(root)
            self.assertEqual(removed, 1)
            self.assertTrue((root / ".trips" / "conversation.jsonl").exists())
            self.assertFalse((root / ".trips" / "jobs").exists() and any((root / ".trips" / "jobs").iterdir()))

    def test_unexpected_edit_blocks_proposed_writes(self):
        class Rogue:
            def stop_all(self):
                return None

            def complete(self, agent, prompt, project, dest, timeout_s):
                if "ROLE: plan" in prompt:
                    payload = {
                        "summary": "Split.",
                        "assignments": [
                            {"agent": "grok", "task": "Draft a.txt.", "files": ["a.txt"]},
                            {"agent": "claude", "task": "Review.", "files": []},
                            {"agent": "codex", "task": "Review.", "files": []},
                        ],
                    }
                elif "ROLE: work" in prompt and agent == "grok":
                    (project / "rogue.txt").write_text("surprise\n", encoding="utf-8")
                    payload = {"report": "Tried.", "files": [{"path": "a.txt", "content": "ok\n"}]}
                elif "ROLE: work" in prompt:
                    payload = {"report": "Reviewed.", "files": []}
                else:
                    payload = {"answer": "Combined.", "accept": [{"agent": "grok", "path": "a.txt"}]}
                return AgentOutput(True, json.dumps(payload))

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            outcome = run_request(root, "do the thing", Rogue(), timeout_s=5)
            self.assertFalse((root / "a.txt").exists())
            self.assertTrue((root / "rogue.txt").exists())
            self.assertIn("rogue.txt", outcome.violations)
            self.assertIn("Combined.", outcome.answer)


if __name__ == "__main__":
    unittest.main()
