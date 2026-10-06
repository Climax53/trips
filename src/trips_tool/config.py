"""Personal settings. They live in the home folder, not in the Trips source."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .safety import TripsError

DEFAULTS: dict = {
    "conductor": "claude",
    "workers": ["codex", "grok"],
    "worker_timeout_minutes": 20,
    "history_turns": 6,
    # Variables removed before a tool is started, for people who want a tool to
    # use its own sign-in and not an API key that happens to be set.
    "strip_env": [],
    # When this list is not empty, Trips asks before working outside these folders.
    "trusted_folders": [],
    # The opening picture: off, small, medium, or large.
    "logo": "medium",
    "agents": {},
}


def config_path() -> Path:
    override = os.environ.get("TRIPS_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".trips" / "config.json"


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TripsError(f"The config file could not be read: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise TripsError(f"The config file must hold a JSON object: {path}")
    return data


def load_config() -> dict:
    data = dict(DEFAULTS)
    data.update(_read(config_path()))
    data["conductor"] = str(data.get("conductor") or DEFAULTS["conductor"]).strip().lower()
    workers = data.get("workers")
    if not isinstance(workers, list):
        workers = list(DEFAULTS["workers"])
    data["workers"] = [str(item).strip().lower() for item in workers]
    for key in ("strip_env", "trusted_folders"):
        if not isinstance(data.get(key), list):
            data[key] = []
    return data


def save_team(conductor: str, workers: list[str]) -> None:
    """Remember the last team so the next start can offer it. Failing to save is not an error."""
    path = config_path()
    try:
        data = _read(path)
        data["conductor"] = conductor
        data["workers"] = list(workers)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except (OSError, TripsError):
        return
