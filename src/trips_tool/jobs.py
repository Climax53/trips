"""Job folders, the conversation log, and the one-at-a-time lock.

Everything here is created under ``<project>/.trips``. Nothing is written to a
temporary folder elsewhere on the disk.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .safety import TripsError

CONVERSATION = "conversation.jsonl"
LOCK = "trips.lock"


def trips_root(project: Path) -> Path:
    folder = project.resolve() / ".trips"
    folder.mkdir(exist_ok=True)
    return folder


def new_job(project: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    job = trips_root(project) / "jobs" / stamp
    if job.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        job = trips_root(project) / "jobs" / stamp
    job.mkdir(parents=True)
    return job


def assert_inside_trips(project: Path, path: Path) -> None:
    root = (project.resolve() / ".trips").resolve()
    full = path.resolve()
    if full != root and root not in full.parents:
        raise TripsError(f"Refusing to write notes outside {root}")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def acquire_lock(project: Path) -> Path:
    folder = trips_root(project)
    lock = folder / LOCK
    if lock.exists():
        try:
            pid = int(lock.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            pid = 0
        if _pid_alive(pid) and pid != os.getpid():
            raise TripsError(
                "Trips is already running in this folder. Close that window before starting another."
            )
        lock.unlink()
    lock.write_text(str(os.getpid()), encoding="utf-8")
    return lock


def release_lock(lock: Path | None) -> None:
    if lock is None:
        return
    try:
        if lock.exists() and lock.read_text(encoding="utf-8").strip() == str(os.getpid()):
            lock.unlink()
    except OSError:
        return


def append_turn(project: Path, role: str, text: str, job: str | None = None) -> None:
    folder = trips_root(project)
    record = {
        "role": role,
        "text": text,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if job:
        record["job"] = job
    with (folder / CONVERSATION).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def recent_turns(project: Path, limit: int) -> list[dict]:
    path = project.resolve() / ".trips" / CONVERSATION
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            rows.append(item)
    return rows[-limit:]


def retain_latest_job(project: Path, keep: Path) -> None:
    """Drop older job folders so notes do not pile up inside the project."""
    jobs = trips_root(project) / "jobs"
    if not jobs.exists():
        return
    keep_resolved = keep.resolve()
    for child in jobs.iterdir():
        if child.is_dir() and child.resolve() != keep_resolved:
            shutil.rmtree(child, ignore_errors=True)


def clean_jobs(project: Path) -> int:
    jobs = project.resolve() / ".trips" / "jobs"
    if not jobs.exists():
        return 0
    removed = 0
    for child in jobs.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed
