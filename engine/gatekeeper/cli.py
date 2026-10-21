"""Command line for people: `init`, `doctor`, `stats` and `inventory` (hooks use `hooks.run_from_stdin`)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

from .config import CONFIG_PATH, ConfigError, load_config
from .hooks import run_from_stdin
from .inventory import definitions, inventory
from .snapshot import take_snapshot
from .state import StateStore, default_data_dir

TEMPLATES = Path(__file__).resolve().parent / "templates"
MIN_PYTHON = (3, 13)


def init(root: Path, preset: str) -> tuple[int, str]:
    template = TEMPLATES / f"{preset}.toml"
    if not template.is_file():
        available = ", ".join(sorted(p.stem for p in TEMPLATES.glob("*.toml")))
        return 1, f"unknown preset '{preset}' (available: {available})"
    target = root / CONFIG_PATH
    if target.exists():
        return 1, f"{target} already exists; edit it instead"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    return 0, f"wrote {target}"


def doctor(root: Path, home: Path, data_dir: Path | None = None) -> tuple[int, str]:
    lines: list[str] = []
    errors = 0

    def report(level: str, message: str) -> None:
        nonlocal errors
        errors += level == "ERROR"
        lines.append(f"{level:5} {message}")

    version = sys.version_info[:2]
    if version < MIN_PYTHON:
        report("ERROR", f"Python {version[0]}.{version[1]} found; gatekeeper needs 3.13 or later")
    else:
        report("OK", f"Python {version[0]}.{version[1]}")
    if shutil.which("git") is None:
        report("ERROR", "git not found on PATH: changes cannot be detected")
    elif take_snapshot(root, ()) is None:
        report("WARN", f"{root} is not a git repository: no change will ever be detected")
    else:
        report("OK", "git repository")

    try:
        config = load_config(root)
    except ConfigError as exc:
        report("ERROR", f"{CONFIG_PATH}: {exc}")
        return 1, "\n".join(lines)
    if config is None:
        report("ERROR", f"{root / CONFIG_PATH} not found (run `init`, or /gatekeeper in Claude Code)")
        return 1, "\n".join(lines)
    report("OK", f"{CONFIG_PATH}: {len(config.gates)} gate(s)")
    if data_dir is not None:
        report("OK", f"state and audit in {data_dir}")

    agents = _agent_names(root, home)
    skills = _skill_names(root, home)
    for gate in config.gates:
        for name in gate.subagents:
            if ":" in name:
                report("OK", f"gate '{gate.id}': subagent '{name}' comes from a plugin (not checked)")
            elif name not in agents:
                report("WARN", f"gate '{gate.id}': subagent '{name}' not found in .claude/agents (project or user)")
        for name in gate.skills:
            if ":" not in name and name not in skills:
                report("WARN", f"gate '{gate.id}': skill '{name}' not found in .claude/skills (project or user)")
    return (1 if errors else 0), "\n".join(lines)


def stats(root: Path, data_dir: Path) -> str:
    records = StateStore(data_dir, root).audit_records()
    outcomes = Counter(str(r.get("outcome")) for r in records)
    gates = Counter(g for r in records if r.get("outcome") == "blocked" for g in r.get("gates", []) if isinstance(g, str))
    lines = [f"audit: {data_dir}", f"responses judged: {len(records)}"]
    lines += [f"  {outcome}: {count}" for outcome, count in outcomes.most_common()]
    if gates:
        lines.append("blocks per gate:")
        lines += [f"  {gate}: {count}" for gate, count in gates.most_common()]
    return "\n".join(lines)


def _agent_names(root: Path, home: Path) -> set[str]:
    return {entry["name"] for entry in definitions(root, home, "agents", "**/*.md")}


def _skill_names(root: Path, home: Path) -> set[str]:
    return {entry["name"] for entry in definitions(root, home, "skills", "*/SKILL.md")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gatekeeper")
    sub = parser.add_subparsers(dest="command", required=True)
    hook = sub.add_parser("hook", help="run a hook handler (reads the payload from stdin)")
    hook.add_argument("event")
    for name in ("init", "doctor", "stats", "inventory"):
        command = sub.add_parser(name)
        command.add_argument("--root", type=Path, default=None, help="project root (default: current directory)")
        command.add_argument("--data-dir", type=Path, default=None, help="plugin data folder (default: detected)")
        if name == "init":
            command.add_argument("--preset", default="verifiers")
    args = parser.parse_args(argv)
    if args.command == "hook":
        return run_from_stdin(args.event)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")

    root = (args.root or Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())).resolve()
    data_dir = args.data_dir or default_data_dir(os.environ, Path.home())
    if args.command == "init":
        code, out = init(root, args.preset)
    elif args.command == "doctor":
        code, out = doctor(root, Path.home(), data_dir)
    elif args.command == "inventory":
        code, out = 0, json.dumps(inventory(root, Path.home()), ensure_ascii=False, indent=2)
    else:
        code, out = 0, stats(root, data_dir)
    print(out)
    return code
