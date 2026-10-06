"""Path rules and project checks.

Trips never copies a project. Scratch files live under ``<project>/.trips``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Directories we do not walk file-by-file. Their own timestamp is watched,
# so a new file inside one still shows up without copying or scanning it.
HEAVY_DIRS = {
    "node_modules",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".cache",
    "dist",
    "build",
    ".next",
    "coverage",
    "Data",
    "data",
    "experiments",
    "props_cache",
}

class TripsError(Exception):
    """A problem we can explain to the person at the prompt."""


def safe_path(root: Path, relative: str) -> Path:
    """Resolve a project-relative path, or raise if it escapes the project."""
    if not isinstance(relative, str) or not relative.strip():
        raise TripsError("A file path was empty.")
    raw = relative.replace("\\", "/").strip()
    if raw.startswith("/") or raw.startswith("~"):
        raise TripsError(f"Path must stay inside the project: {relative}")
    parts = Path(raw).parts
    if not parts or any(part in ("..", "") for part in parts):
        raise TripsError(f"Path must stay inside the project: {relative}")
    if parts[0].endswith(":"):
        raise TripsError(f"Path must stay inside the project: {relative}")
    if ".trips" in parts:
        raise TripsError(f"Path is reserved for Trips notes: {relative}")
    root_resolved = root.resolve()
    full = (root_resolved / Path(*parts)).resolve()
    if full != root_resolved and root_resolved not in full.parents:
        raise TripsError(f"Path must stay inside the project: {relative}")
    return full


def _heavy_signature(directory: Path) -> tuple[int, int, int]:
    """Count, total size, and mtime sum for a folder we do not want to copy."""
    count = 0
    total = 0
    mtime_sum = 0
    for dirpath, dirnames, filenames in os.walk(directory, followlinks=False):
        dirnames[:] = [name for name in dirnames if name != ".trips"]
        for name in filenames:
            try:
                stat = (Path(dirpath) / name).stat()
            except OSError:
                continue
            count += 1
            total += stat.st_size
            mtime_sum += stat.st_mtime_ns
    return (count, total, mtime_sum)


def snapshot(root: Path) -> dict[str, tuple[int, ...]]:
    """Record size and time for project files, without reading their contents.

    Heavy folders contribute one marker (``name/``) instead of every child.
    ``.trips`` is ignored because Trips itself writes there during a job.
    """
    found: dict[str, tuple[int, ...]] = {}
    root_resolved = root.resolve()

    def mark(path: Path, key: str) -> None:
        try:
            stat = path.stat()
        except OSError:
            return
        found[key] = (stat.st_size, stat.st_mtime_ns)

    for dirpath, dirnames, filenames in os.walk(root_resolved, followlinks=False):
        current = Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            child = current / name
            if name == ".trips":
                continue
            if name in HEAVY_DIRS:
                relative = child.relative_to(root_resolved).as_posix() + "/"
                found[relative] = _heavy_signature(child)
                continue
            kept.append(name)
        dirnames[:] = kept
        if current != root_resolved and ".trips" in current.relative_to(root_resolved).parts:
            continue
        for name in filenames:
            path = current / name
            relative = path.relative_to(root_resolved).as_posix()
            mark(path, relative)
    return found


def changed_files(before: dict[str, tuple[int, ...]], after: dict[str, tuple[int, ...]]) -> list[str]:
    keys = set(before) | set(after)
    return sorted(key for key in keys if before.get(key) != after.get(key))


def ensure_gitignore(root: Path) -> None:
    """Keep ``.trips`` out of git when this folder is already a repository."""
    if not (root / ".git").exists():
        return
    ignore = root / ".gitignore"
    line = ".trips/"
    if ignore.exists():
        text = ignore.read_text(encoding="utf-8", errors="replace")
        existing = {item.strip().rstrip("/") for item in text.splitlines()}
        if ".trips" in existing:
            return
        suffix = "" if text.endswith("\n") or text == "" else "\n"
        ignore.write_text(text + suffix + line + "\n", encoding="utf-8")
        return
    ignore.write_text(line + "\n", encoding="utf-8")


def extract_object(text: str) -> dict | None:
    """Return the JSON object that holds a plan, a report, or a final answer."""
    if not text:
        return None
    decoder = json.JSONDecoder()
    found: list[dict] = []
    index = 0
    while True:
        start = text.find("{", index)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(obj, dict):
            found.append(obj)
        index = start + max(end, 1)
    for key in ("assignments", "answer", "report"):
        for obj in reversed(found):
            if key in obj:
                return obj
    return found[-1] if found else None


def model_text(raw: str) -> str:
    """Unwrap a CLI envelope (``text``, ``result``, or ``response``) when one is present."""
    stripped = raw.strip()
    if not stripped:
        return ""
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        for key in ("text", "result", "response"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
    if isinstance(data, list):
        for item in reversed(data):
            if isinstance(item, dict):
                for key in ("result", "text"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        return value
    return stripped
