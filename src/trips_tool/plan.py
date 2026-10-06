"""The conductor's plan, and the checks that keep assignments apart."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .safety import TripsError, safe_path

# The team used when nobody has chosen one. The conductor is listed first.
DEFAULT_TEAM = ("claude", "codex", "grok")


@dataclass
class Assignment:
    agent: str
    task: str
    files: list[str]


@dataclass
class Plan:
    summary: str
    assignments: list[Assignment]

    def for_agent(self, agent: str) -> Assignment:
        for item in self.assignments:
            if item.agent == agent:
                return item
        raise TripsError(f"The plan has no work for {agent}.")


@dataclass
class ProposedFile:
    path: str
    content: str


@dataclass
class WorkerResult:
    agent: str
    report: str
    files: list[ProposedFile] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Review:
    answer: str
    accept: list[tuple[str, str]]


def _agent_name(value: object, team) -> str:
    if not isinstance(value, str):
        raise TripsError("An agent name was missing from the plan.")
    name = value.strip().lower()
    if name not in team:
        raise TripsError(f"Unknown agent in the plan: {value}")
    return name


def parse_plan(root: Path, data: dict, team=DEFAULT_TEAM) -> Plan:
    if not isinstance(data, dict):
        raise TripsError("The plan was not a JSON object.")
    summary = data.get("summary")
    rows = data.get("assignments")
    if not isinstance(summary, str) or not summary.strip():
        raise TripsError("The plan needs a summary.")
    if not isinstance(rows, list):
        raise TripsError("The plan needs an assignments list.")
    assignments: list[Assignment] = []
    seen_agents: set[str] = set()
    seen_files: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TripsError("Each assignment needs to be an object.")
        agent = _agent_name(row.get("agent"), team)
        if agent in seen_agents:
            raise TripsError(f"The plan gives {agent} more than one assignment.")
        seen_agents.add(agent)
        task = row.get("task")
        if not isinstance(task, str) or not task.strip():
            raise TripsError(f"The plan has no task for {agent}.")
        raw_files = row.get("files", [])
        if raw_files is None:
            raw_files = []
        if not isinstance(raw_files, list):
            raise TripsError(f"The file list for {agent} is not a list.")
        files: list[str] = []
        for item in raw_files:
            if not isinstance(item, str):
                raise TripsError(f"A file for {agent} was not text.")
            full = safe_path(root, item)
            relative = full.relative_to(root.resolve()).as_posix()
            owner = seen_files.get(relative)
            if owner:
                raise TripsError(f"{relative} is assigned to both {owner} and {agent}.")
            seen_files[relative] = agent
            files.append(relative)
        assignments.append(Assignment(agent, task.strip(), files))
    missing = [name for name in team if name not in seen_agents]
    if missing:
        raise TripsError(f"The plan must name {', '.join(team)}. Missing: " + ", ".join(missing))
    order = {name: index for index, name in enumerate(team)}
    assignments.sort(key=lambda item: order[item.agent])
    return Plan(summary.strip(), assignments)


def parse_worker(root: Path, agent: str, allowed: list[str], data: dict | None, fallback: str) -> WorkerResult:
    if not isinstance(data, dict):
        report = fallback.strip() or f"{agent} returned no report."
        return WorkerResult(agent, report[:4000], notes=["No file proposal was returned."])
    report = data.get("report")
    if not isinstance(report, str) or not report.strip():
        report = fallback.strip() or f"{agent} returned no report."
    notes: list[str] = []
    files: list[ProposedFile] = []
    raw_files = data.get("files", [])
    if raw_files is None:
        raw_files = []
    if not isinstance(raw_files, list):
        notes.append("The file list could not be read.")
        raw_files = []
    allowed_set = set(allowed)
    for item in raw_files:
        if not isinstance(item, dict):
            notes.append("Skipped a file entry that was not an object.")
            continue
        path = item.get("path")
        content = item.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            notes.append("Skipped a file entry that was missing a path or text.")
            continue
        try:
            full = safe_path(root, path)
        except TripsError as exc:
            notes.append(str(exc))
            continue
        relative = full.relative_to(root.resolve()).as_posix()
        if relative not in allowed_set:
            notes.append(f"Ignored {relative} because it was not assigned to {agent}.")
            continue
        encoded = content.encode("utf-8")
        if len(encoded) > 1_000_000:
            notes.append(f"Ignored {relative} because it is larger than 1 MB.")
            continue
        if "\0" in content:
            notes.append(f"Ignored {relative} because it is not a text file.")
            continue
        files.append(ProposedFile(relative, content))
    return WorkerResult(agent, report.strip()[:8000], files, notes)


def parse_review(plan: Plan, data: dict | None) -> Review:
    if not isinstance(data, dict):
        raise TripsError("The conductor's final reply was not JSON.")
    answer = data.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise TripsError("The conductor returned an empty answer.")
    allowed = {item.agent: set(item.files) for item in plan.assignments}
    accept: list[tuple[str, str]] = []
    raw = data.get("accept", [])
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise TripsError("The accept list could not be read.")
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            agent = _agent_name(item.get("agent"), allowed)
        except TripsError:
            continue
        path = item.get("path")
        if not isinstance(path, str):
            continue
        normal = path.replace("\\", "/").strip().lstrip("/")
        if normal in allowed.get(agent, set()):
            accept.append((agent, normal))
    return Review(answer.strip(), accept)
