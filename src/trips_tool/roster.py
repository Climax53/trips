"""The AI command-line tools Trips can put on a team.

Each entry says how to start one tool so that it reads the project and replies
on stdout without editing anything. People can change an entry, or add their
own tool, under ``agents`` in the Trips config file.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from .safety import TripsError

# Sent in place of the prompt to tools that take a short instruction, not stdin.
PROMPT_REF = (
    "Read the file {prompt_file} and follow the instructions in it exactly. "
    "Reply only in the form it asks for."
)
RESERVED = {"plan", "review", "previous"}
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")


@dataclass(frozen=True)
class AgentSpec:
    name: str
    label: str
    maker: str
    cli: str
    # Placeholders: {project} {prompt_file} {last_message} {prompt_ref} {model}
    # and the single item "{model_args}", which expands only when a model is set.
    args: tuple[str, ...]
    stdin: bool = True
    code: str = "37"
    rgb: tuple[int, int, int] = (200, 200, 200)
    install: str = ""
    model: str = ""
    model_args: tuple[str, ...] = ()
    note: str = ""


BUILTIN: tuple[AgentSpec, ...] = (
    AgentSpec(
        "claude", "Claude", "Anthropic · Claude Code", "claude",
        (
            "-p",
            "{model_args}",
            "--output-format", "json",
            "--permission-mode", "dontAsk",
            "--permission-prompts", "none",
            "--disallowedTools", "Edit,Write,NotebookEdit,Bash,PowerShell,WebSearch,WebFetch",
        ),
        code="35", rgb=(217, 119, 87),
        install="npm install -g @anthropic-ai/claude-code",
        model_args=("--model", "{model}"),
    ),
    AgentSpec(
        "codex", "Codex", "OpenAI · Codex CLI", "codex",
        (
            "exec",
            "{model_args}",
            "--sandbox", "read-only",
            "--skip-git-repo-check",
            "--color", "never",
            "--ephemeral",
            "--cd", "{project}",
            "--output-last-message", "{last_message}",
            "-",
        ),
        code="33", rgb=(16, 163, 127),
        install="npm install -g @openai/codex",
        model_args=("--model", "{model}"),
    ),
    AgentSpec(
        "grok", "Grok", "xAI · Grok CLI", "grok",
        (
            "--prompt-file", "{prompt_file}",
            "--cwd", "{project}",
            "{model_args}",
            "--output-format", "json",
            "--tools", "read_file,grep,list_dir",
            "--disallowed-tools", "run_terminal_cmd,search_replace,write,Agent,web_search,web_fetch",
            "--no-subagents",
            "--disable-web-search",
            "--permission-mode", "dontAsk",
            "--max-turns", "15",
            "--no-auto-update",
        ),
        stdin=False, code="36", rgb=(110, 196, 222),
        install="see x.ai for the Grok CLI",
        model_args=("--model", "{model}"),
    ),
    AgentSpec(
        "gemini", "Gemini", "Google · Gemini CLI", "gemini",
        ("{model_args}", "--output-format", "json"),
        code="94", rgb=(66, 133, 244),
        install="npm install -g @google/gemini-cli",
        model_args=("--model", "{model}"),
    ),
    AgentSpec(
        "qwen", "Qwen", "Alibaba · Qwen Code", "qwen",
        ("{model_args}", "--output-format", "json"),
        code="95", rgb=(138, 99, 246),
        install="npm install -g @qwen-code/qwen-code",
        model_args=("--model", "{model}"),
    ),
    AgentSpec(
        "opencode", "OpenCode", "SST · OpenCode", "opencode",
        ("run", "--agent", "plan", "{model_args}", "{prompt_ref}"),
        stdin=False, code="92", rgb=(250, 178, 131),
        install="npm install -g opencode-ai",
        model_args=("--model", "{model}"),
    ),
    AgentSpec(
        "copilot", "Copilot", "GitHub · Copilot CLI", "copilot",
        ("-p", "{prompt_ref}", "-s", "--allow-all-tools", "--deny-tool", "write", "--deny-tool", "shell", "{model_args}"),
        stdin=False, code="96", rgb=(130, 170, 255),
        install="npm install -g @github/copilot",
        model_args=("--model", "{model}"),
    ),
    AgentSpec(
        "cursor", "Cursor", "Anysphere · Cursor CLI", "cursor-agent",
        ("-p", "{prompt_ref}", "--output-format", "json", "{model_args}"),
        stdin=False, code="91", rgb=(255, 59, 48),
        install="see cursor.com/cli",
        model_args=("--model", "{model}"),
    ),
    AgentSpec(
        "droid", "Droid", "Factory · Droid", "droid",
        ("exec", "--output-format", "json", "--cwd", "{project}", "{model_args}", "--file", "{prompt_file}"),
        stdin=False, code="93", rgb=(238, 96, 24),
        install="see factory.ai for the Droid CLI",
        model_args=("--model", "{model}"),
    ),
    AgentSpec(
        "ollama", "Ollama", "local model · no file access", "ollama",
        ("run", "{model}"),
        code="37", rgb=(170, 170, 170),
        install="see ollama.com",
        note="Set agents.ollama.model in the config to a model you have pulled.",
    ),
)

_ACTIVE: dict[str, AgentSpec] = {spec.name: spec for spec in BUILTIN}


def build_roster(config: dict | None = None) -> dict[str, AgentSpec]:
    """The built-in tools, changed or added to by the ``agents`` config section."""
    roster = {spec.name: spec for spec in BUILTIN}
    custom = (config or {}).get("agents") or {}
    if not isinstance(custom, dict):
        raise TripsError("The agents section of the config must be an object.")
    for raw_name, entry in custom.items():
        name = str(raw_name).strip().lower()
        if not _NAME.match(name) or name in RESERVED:
            raise TripsError(f"'{raw_name}' cannot be used as an agent name in the config.")
        if not isinstance(entry, dict):
            raise TripsError(f"The config entry for {name} must be an object.")
        roster[name] = _merge(name, roster.get(name), entry)
    _ACTIVE.clear()
    _ACTIVE.update(roster)
    return roster


def _merge(name: str, base: AgentSpec | None, entry: dict) -> AgentSpec:
    changes: dict = {}
    command = entry.get("command")
    if command is not None:
        if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
            raise TripsError(f"The command for {name} must be a list of text items.")
        changes["cli"] = command[0]
        changes["args"] = tuple(command[1:])
    if isinstance(entry.get("args"), list):
        changes["args"] = tuple(str(item) for item in entry["args"])
    for key in ("label", "maker", "cli", "model", "install"):
        if isinstance(entry.get(key), str):
            changes[key] = entry[key]
    if isinstance(entry.get("stdin"), bool):
        changes["stdin"] = entry["stdin"]
    if base is not None:
        return replace(base, **changes)
    if "cli" not in changes:
        raise TripsError(f"The config entry for {name} needs a command.")
    changes.setdefault("label", name.title())
    changes.setdefault("maker", "from your config")
    changes.setdefault("args", ())
    if "stdin" not in changes:
        joined = " ".join(changes["args"])
        changes["stdin"] = "{prompt_file}" not in joined and "{prompt_ref}" not in joined
    return AgentSpec(name=name, **changes)


def spec_for(name: str) -> AgentSpec | None:
    return _ACTIVE.get(name)


def label_of(name: str) -> str:
    spec = _ACTIVE.get(name)
    return spec.label if spec else name.title()


def command_from_npm_shim(cmd_file: Path) -> list[str] | None:
    """Turn an npm ``name.cmd`` shim into the real exe or node script."""
    text = cmd_file.read_text(encoding="utf-8", errors="replace")
    targets = re.findall(r'"%dp0%\\([^"]+\.(?:exe|js))"', text, re.I)
    scripts = [item for item in targets if item.lower().endswith(".js")]
    exes = [item for item in targets if item.lower().endswith(".exe") and not item.lower().endswith("node.exe")]
    if exes:
        return [str(cmd_file.parent / exes[-1])]
    if scripts:
        node = cmd_file.parent / "node.exe"
        node_cmd = str(node) if node.exists() else (shutil.which("node") or "node")
        return [node_cmd, str(cmd_file.parent / scripts[-1])]
    return None


def resolve_cli(name: str, cli: str | None = None) -> list[str]:
    """Find an installed CLI, including the npm .cmd shims on Windows."""
    override = os.environ.get(f"TRIPS_{name.upper()}")
    if override:
        return [override]
    cli = cli or name
    found = shutil.which(cli)
    if not found:
        raise FileNotFoundError(f"The {cli} command is not installed.")
    if os.name != "nt":
        return [found]
    cmd_file = Path(found).with_suffix(".cmd")
    if not cmd_file.exists():
        sibling = Path(found).parent / f"{cli}.cmd"
        cmd_file = sibling if sibling.exists() else cmd_file
    if cmd_file.exists():
        resolved = command_from_npm_shim(cmd_file)
        if resolved:
            return resolved
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(cmd_file)]
    if Path(found).suffix.lower() == ".exe":
        return [str(Path(found))]
    raise FileNotFoundError(f"The {cli} command is not installed.")


def readiness(spec: AgentSpec) -> tuple[bool, str]:
    """Whether this tool can be started here, and a short reason when it cannot."""
    try:
        resolve_cli(spec.name, spec.cli)
    except FileNotFoundError:
        return False, "not installed"
    if not spec.model and any("{model}" in arg for arg in spec.args):
        return False, "needs a model"
    return True, ""


def build_command(spec: AgentSpec, project: Path, prompt_file: Path, last_message: Path) -> list[str]:
    values = {
        "{prompt_ref}": PROMPT_REF,
        "{project}": str(project),
        "{prompt_file}": str(prompt_file),
        "{last_message}": str(last_message),
        "{model}": spec.model,
    }
    command = resolve_cli(spec.name, spec.cli)
    for arg in spec.args:
        if arg == "{model_args}":
            if spec.model:
                command.extend(item.replace("{model}", spec.model) for item in spec.model_args)
            continue
        for mark, value in values.items():
            arg = arg.replace(mark, value)
        command.append(arg)
    return command


def fill_team(roster: dict[str, AgentSpec], conductor: str, wanted: list[str]) -> list[str]:
    """The conductor plus two different workers, topping up from tools that are ready."""
    if conductor not in roster:
        raise TripsError(f"Unknown conductor '{conductor}'. Choose from: {', '.join(roster)}")
    team = [conductor]
    for name in wanted:
        name = name.strip().lower()
        if not name:
            continue
        if name not in roster:
            raise TripsError(f"Unknown agent '{name}'. Choose from: {', '.join(roster)}")
        if name not in team:
            team.append(name)
    if len(team) < 3:
        spare = [name for name in roster if name not in team]
        spare.sort(key=lambda name: not readiness(roster[name])[0])
        team.extend(spare[: 3 - len(team)])
    if len(team) < 3:
        raise TripsError("Trips needs three different agents.")
    return team[:3]
