"""Run one request: plan, three workers at once, then one combined answer."""

from __future__ import annotations

import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from .agents import AgentOutput, LiveRunner, proposal_from_output
from .jobs import append_turn, new_job, recent_turns, retain_latest_job
from .plan import DEFAULT_TEAM, Plan, Review, WorkerResult, parse_plan, parse_review
from .prompts import plan_prompt, review_prompt, work_prompt
from .roster import label_of
from .display import STATUS, format_plan
from .safety import TripsError, changed_files, ensure_gitignore, extract_object, safe_path, snapshot


@dataclass
class JobOutcome:
    answer: str
    job_dir: Path
    written: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)


def run_request(
    project: Path,
    request: str,
    runner,
    conductor: str = "claude",
    timeout_s: float = 1200,
    history: int = 6,
    progress=None,
    team=DEFAULT_TEAM,
) -> JobOutcome:
    """``team`` is the three agents that work. ``conductor`` is the one of them that plans and answers."""
    team = tuple(team)
    if len(set(team)) != 3 or conductor not in team:
        raise TripsError("Trips needs three different agents, and the conductor must be one of them.")
    project = project.resolve()
    say = progress or (lambda _message: None)
    request = request.strip()
    if not request:
        raise TripsError("Type a request first.")
    ensure_gitignore(project)
    job = new_job(project)
    (job / "request.md").write_text(request + "\n", encoding="utf-8")
    turns = recent_turns(project, history)
    append_turn(project, "user", request, job.name)

    say(STATUS + "plan")
    before_plan = snapshot(project)
    plan = _plan(project, request, turns, runner, conductor, team, job, timeout_s)
    plan_changes = changed_files(before_plan, snapshot(project))
    if plan_changes:
        raise TripsError(
            "The conductor changed project files while planning, so the workers were not started: "
            + ", ".join(plan_changes[:8])
        )
    _print_plan(plan, say)
    (job / "plan.json").write_text(
        json.dumps(
            {
                "summary": plan.summary,
                "assignments": [
                    {"agent": item.agent, "task": item.task, "files": item.files}
                    for item in plan.assignments
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    say(STATUS + "work")
    before = snapshot(project)
    workers = _work(project, request, plan, runner, team, job, timeout_s, say)
    violations = changed_files(before, snapshot(project))
    for worker in workers:
        folder = job / worker.agent
        folder.mkdir(exist_ok=True)
        (folder / "report.md").write_text(worker.report + "\n", encoding="utf-8")

    say(STATUS + "write")
    try:
        review = _review(project, request, plan, workers, violations, runner, conductor, job, timeout_s)
    except TripsError as exc:
        answer = (
            "The conductor could not finish the combined answer. "
            f"{exc} No project files were written. Notes are in .trips/jobs/{job.name}."
        )
        _finish(project, job, answer, [], violations, workers)
        return JobOutcome(answer, job, [], violations)

    written: list[str] = []
    if violations:
        say("A project file changed outside the plan, so no proposed files were written.")
    else:
        written = _apply(project, job, workers, review)
    answer = review.answer
    footer = _footer(written, violations, job)
    if footer:
        answer = answer + "\n\n" + footer
    _finish(project, job, answer, written, violations, workers)
    return JobOutcome(answer, job, written, violations)


def _plan(project, request, turns, runner, conductor, team, job, timeout_s) -> Plan:
    prompt = plan_prompt(request, turns, team)
    last_error = "The plan could not be read."
    for _attempt in range(2):
        output: AgentOutput = runner.complete(conductor, prompt, project, job / "plan", timeout_s)
        if not output.ok:
            raise TripsError(
                f"{label_of(conductor)}, the conductor, could not write a plan. {output.error}".strip()
                + " To use a different conductor, type team, or start with --conductor."
            )
        try:
            data = extract_object(output.text)
            if data is None:
                raise TripsError("The conductor did not return a plan.")
            return parse_plan(project, data, team)
        except TripsError as exc:
            last_error = str(exc)
            prompt = plan_prompt(request, turns, team) +f"\nYour previous reply was rejected: {exc}\nReply with JSON only.\n"
    raise TripsError(last_error)


def _work(project, request, plan: Plan, runner, team, job, timeout_s, say) -> list[WorkerResult]:
    results: dict[str, WorkerResult] = {}

    def once(agent: str) -> WorkerResult:
        assignment = plan.for_agent(agent)
        output = runner.complete(
            agent,
            work_prompt(request, assignment, plan.summary),
            project,
            job / agent,
            timeout_s,
        )
        return proposal_from_output(agent, output, project, assignment)

    pool = ThreadPoolExecutor(max_workers=3)
    futures = {pool.submit(once, agent): agent for agent in team}
    try:
        for future in as_completed(futures):
            agent = futures[future]
            results[agent] = future.result()
            say(STATUS + "done:" + agent)
    except BaseException:
        runner.stop_all()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return [results[agent] for agent in team]


def _review(project, request, plan, workers, violations, runner, conductor, job, timeout_s) -> Review:
    prompt = review_prompt(request, plan, workers, violations)
    output = runner.complete(conductor, prompt, project, job / "review", timeout_s)
    if not output.ok:
        raise TripsError(output.error or "The conductor could not write the answer.")
    data = extract_object(output.text)
    return parse_review(plan, data)


def _apply(project: Path, job: Path, workers: list[WorkerResult], review: Review) -> list[str]:
    by_agent = {worker.agent: {item.path: item.content for item in worker.files} for worker in workers}
    written: list[str] = []
    for agent, relative in review.accept:
        content = by_agent.get(agent, {}).get(relative)
        if content is None:
            continue
        target = safe_path(project, relative)
        previous = job / "previous" / relative
        if target.exists():
            previous.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, previous)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(relative)
    return written


def _footer(written: list[str], violations: list[str], job: Path) -> str:
    lines = []
    if written:
        lines.append("Wrote: " + ", ".join(written))
    if violations:
        shown = ", ".join(violations[:8])
        lines.append("Left these unexpected changes alone: " + shown)
    if lines:
        lines.append(f"Notes: .trips/jobs/{job.name}/")
    return "\n".join(lines)


def _finish(project, job, answer, written, violations, workers: list[WorkerResult]) -> None:
    (job / "answer.md").write_text(answer + "\n", encoding="utf-8")
    manifest = {
        "written": written,
        "violations": violations,
        "reports": {worker.agent: worker.report[:500] for worker in workers},
    }
    (job / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for name in ("stdout.txt", "stderr.txt", "prompt.txt", "last_message.txt", "command.txt"):
        for path in job.rglob(name):
            path.unlink(missing_ok=True)
    append_turn(project, "trips", answer, job.name)
    retain_latest_job(project, job)


def _print_plan(plan: Plan, say) -> None:
    width = max(36, shutil.get_terminal_size((80, 24)).columns - 2)
    rows = [(item.agent, item.task, item.files) for item in plan.assignments]
    say(format_plan(plan.summary, rows, width, color=sys.stderr.isatty()))
