"""How each installed CLI is started.

Workers may read the project. They are not given a writable copy of it.
Proposed file text comes back on stdout, and Trips writes the accepted files.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from .plan import DEFAULT_TEAM, Assignment, WorkerResult
from .roster import build_command, label_of, spec_for
from .safety import extract_object, model_text

# A tool started by Trips must not think it is inside another tool's session.
SESSION_ENV = (
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_PID",
)


def child_env(strip: tuple[str, ...] = ()) -> dict[str, str]:
    env = dict(os.environ)
    for key in (*SESSION_ENV, *strip):
        env.pop(key, None)
    return env


def command_for(agent: str, project: Path, prompt_file: Path, last_message: Path) -> list[str]:
    spec = spec_for(agent)
    if spec is None:
        raise FileNotFoundError(f"Unknown agent {agent}")
    return build_command(spec, project, prompt_file, last_message)


@dataclass
class AgentOutput:
    ok: bool
    text: str
    error: str = ""


class LiveRunner:
    """Run one installed agent in the real project folder."""

    def __init__(self, strip_env: tuple[str, ...] = ()) -> None:
        self._strip = tuple(strip_env)
        self._procs: list[subprocess.Popen] = []
        self._guard = threading.Lock()

    def stop_all(self) -> None:
        with self._guard:
            procs = list(self._procs)
        for proc in procs:
            _kill_tree(proc)

    def complete(self, agent: str, prompt: str, project: Path, dest: Path, timeout_s: float) -> AgentOutput:
        dest.mkdir(parents=True, exist_ok=True)
        prompt_file = dest / "prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        last_message = dest / "last_message.txt"
        try:
            cmd = command_for(agent, project, prompt_file, last_message)
        except FileNotFoundError as exc:
            return AgentOutput(False, "", str(exc))
        (dest / "command.txt").write_text(" ".join(cmd), encoding="utf-8")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=project,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env(self._strip),
                creationflags=flags,
            )
        except OSError as exc:
            return AgentOutput(False, "", f"Could not start {agent}: {exc}")
        with self._guard:
            self._procs.append(proc)
        try:
            spec = spec_for(agent)
            payload = prompt.encode("utf-8") if spec is None or spec.stdin else b""
            stdout, stderr = proc.communicate(payload, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            return AgentOutput(False, "", f"{agent} timed out after {int(timeout_s // 60)} minutes.")
        except BaseException:
            _kill_tree(proc)
            raise
        finally:
            with self._guard:
                if proc in self._procs:
                    self._procs.remove(proc)
        out_text = stdout.decode("utf-8", errors="replace")
        err_text = stderr.decode("utf-8", errors="replace")
        (dest / "stdout.txt").write_text(out_text[-500_000:], encoding="utf-8")
        (dest / "stderr.txt").write_text(err_text[-200_000:], encoding="utf-8")
        if last_message.exists():
            message = last_message.read_text(encoding="utf-8", errors="replace").strip()
            if message:
                out_text = message
        text = model_text(out_text)
        if proc.returncode not in (0, None) and not text.strip():
            reason = _failure_reason(err_text)
            return AgentOutput(False, "", reason or f"{label_of(agent)} stopped with code {proc.returncode}.")
        return AgentOutput(True, text, "")


def _failure_reason(err_text: str) -> str:
    """The tool's own error line when it printed one, and not the prompt it echoed back."""
    lines = [line.strip() for line in err_text.splitlines() if line.strip()]
    errors = [line for line in lines if line.lower().startswith(("error", "fatal")) or "error:" in line.lower()]
    picked = errors[-1] if errors else " ".join(lines[-3:])
    return picked[:500]


class FakeRunner:
    """A stand-in that splits a request without calling a model."""

    def stop_all(self) -> None:
        return None

    def complete(self, agent: str, prompt: str, project: Path, dest: Path, timeout_s: float) -> AgentOutput:
        named = re.search(r"^Team: (.+)$", prompt, re.M)
        team = [item.strip() for item in named.group(1).split(",")] if named else list(DEFAULT_TEAM)
        first, second, third = (label_of(name) for name in team)
        if "ROLE: plan" in prompt:
            payload = {
                "summary": "One agent drafts a note. The other two check different parts of it.",
                "assignments": [
                    {"agent": team[0], "task": "Draft trips-hello.md for the user's request.", "files": ["trips-hello.md"]},
                    {"agent": team[1], "task": "Check the wording and report problems. Do not edit a file.", "files": []},
                    {"agent": team[2], "task": "Check that the note belongs in this folder. Do not edit a file.", "files": []},
                ],
            }
        elif "ROLE: work" in prompt:
            if "and no others: trips-hello.md" in prompt:
                payload = {
                    "report": "Drafted the hello note.",
                    "files": [{"path": "trips-hello.md", "content": "Hello from Trips.\n"}],
                }
            else:
                payload = {"report": f"{label_of(agent)} found nothing to add.", "files": []}
        else:
            payload = {
                "answer": f"The note is in trips-hello.md. {first} drafted it. {second} and {third} had nothing to add.",
                "accept": [{"agent": team[0], "path": "trips-hello.md"}],
            }
        return AgentOutput(True, json.dumps(payload))


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    else:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def proposal_from_output(agent: str, output: AgentOutput, project: Path, assignment: Assignment) -> WorkerResult:
    from .plan import parse_worker

    if not output.ok:
        return WorkerResult(agent, f"{agent} did not finish. {output.error}".strip(), notes=["No proposal."])
    data = extract_object(output.text)
    return parse_worker(project, agent, assignment.files, data, output.text)
