"""Instructions sent to the conductor and the three workers."""

from __future__ import annotations

from .plan import DEFAULT_TEAM, Assignment, Plan, WorkerResult


def _history(turns: list[dict]) -> str:
    if not turns:
        return "No earlier turns."
    lines = []
    for turn in turns:
        role = "User" if turn.get("role") == "user" else "Trips"
        lines.append(f"{role}: {turn.get('text', '').strip()}")
    return "\n".join(lines)


def _names(team) -> str:
    return f"{team[0]}, {team[1]}, and {team[2]}"


def plan_prompt(request: str, turns: list[dict], team=DEFAULT_TEAM) -> str:
    shapes = ",\n".join(
        '  {"agent":"%s","task":"what %s alone should do","files":[%s]}'
        % (name, name, '"relative/path"' if index == 0 else "")
        for index, name in enumerate(team)
    )
    return f"""ROLE: plan
Team: {", ".join(team)}
You are the Trips conductor. Split one request across {_names(team)}.
Reply with JSON only. No markdown fence.

Rules:
- Name each of {_names(team)} exactly once.
- Give them different work. Do not ask all three to answer the whole request.
- A file may belong to only one agent. Use project-relative paths with forward slashes.
- If only one file should change, give that file to one agent. The others review a different slice or draft part of the explanation, with an empty files list.
- If no project file should change, leave every files list empty and split the thinking instead.
- Do not tell anyone to copy the project or to write outside it.
- summary is one sentence, 16 words or fewer.
- Each task is one sentence, 18 words or fewer. No markdown, no file lists inside the task.

Recent conversation:
{_history(turns)}

Request:
{request}

JSON shape:
{{"summary":"short plan","assignments":[
{shapes}
]}}
"""


def work_prompt(request: str, assignment: Assignment, summary: str) -> str:
    files = ", ".join(assignment.files) if assignment.files else "(none)"
    return f"""ROLE: work
You are {assignment.agent}, one of three Trips workers. You are working in the real project folder.
Read whatever you need. Do not create a copy of the project. Do not write or edit project files yourself.
Trips will write a file only if you include its full new text in the JSON below, and only if it was assigned to you.

Plan: {summary}
Your task: {assignment.task}
Files you may propose, and no others: {files}

User request:
{request}

Reply with JSON only. No markdown fence.
{{"report":"what you did, in a short report","files":[{{"path":"relative/path","content":"full new file text"}}]}}
Use an empty files list when you were not given any files.
"""


def review_prompt(request: str, plan: Plan, workers: list[WorkerResult], violations: list[str]) -> str:
    sections = [f"Plan: {plan.summary}"]
    for worker in workers:
        proposed = ", ".join(item.path for item in worker.files) or "(no files)"
        notes = "\n".join(worker.notes) or "(none)"
        bodies = []
        for item in worker.files:
            content = item.content
            if len(content) > 20000:
                content = content[:20000] + "\n...[truncated for review]..."
            bodies.append(f"----- {item.path} -----\n{content}")
        file_block = "\n".join(bodies) if bodies else "(no proposed text)"
        sections.append(
            f"## {worker.agent}\nTask: {plan.for_agent(worker.agent).task}\n"
            f"Report:\n{worker.report}\nNotes:\n{notes}\nProposed files: {proposed}\n{file_block}"
        )
    violation_text = "\n".join(violations) if violations else "(none)"
    team = [item.agent for item in plan.assignments]
    return f"""ROLE: review
Team: {", ".join(team)}
You are the Trips conductor. Read the three reports and write the single answer the user should see.
Do not paste three separate answers. Combine what is useful, drop repetition, and say when the workers disagreed.
Reply with JSON only. No markdown fence.

If a file was proposed, include it in accept only when it should be written into the project.
Accept a path only for the agent it was assigned to. When the violation list is not empty, accept nothing.

User request:
{request}

Unexpected project changes during the run:
{violation_text}

{chr(10).join(sections)}

JSON shape:
{{"answer":"the one reply for the user","accept":[{{"agent":"{team[0]}","path":"relative/path"}}]}}
"""
