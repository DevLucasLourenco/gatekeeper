"""Facts about a project that the /gatekeeper skill suggests gates from.

Read-only and cheap: one `git ls-files`, a few small files under `.claude/`.
The skill decides what to suggest; this module only reports what exists.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path, PurePosixPath

from .config import CONFIG_PATH
from .matching import normalize
from .snapshot import find_toplevel, run_git

MAX_LISTED = 20
# Users may have hundreds of skills; the /gatekeeper skill only needs a readable sample.
MAX_DEFINITIONS = 40
MAX_DESCRIPTION = 160
RELEVANCE_WINDOW = 120
_TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs"}
_TEST_FILE = re.compile(r"(^test_.+|.+_test\.[^.]+|.+\.(test|spec)\.[^.]+)$")
_CI_FILE = re.compile(r"^(\.github/workflows/[^/]+\.ya?ml|\.gitlab-ci\.yml|azure-pipelines\.yml|Jenkinsfile)$")
_SETTINGS = (Path(".claude") / "settings.json", Path(".claude") / "settings.local.json")
# Words (English and Portuguese, accents removed) that mark an entry as useful for a gate.
# Prefixes match at the start of a word only, so "contest" or "latest" do not count as tests.
_RELEVANT = tuple(
    re.compile(r"\b" + word)
    for word in (
        "test", r"tdd\b", r"qa\b", "review", "revis", "lint", "secur", "segur", "audit",
        "verif", "quality", "qualid", r"perf\b", "performance", "desempenho", "bug", "coverage", "cobertura", "regress",
    )
)
_BLOCK_INDICATOR = re.compile(r"^[>|][+-]?\d*[+-]?$")


def inventory(root: Path, home: Path) -> dict:
    warnings: list[str] = []
    files = _project_files(root)
    disabled = _disabled_skills(root, home)
    agents, agents_omitted = _trim(definitions(root, home, "agents", "**/*.md"))
    skills, skills_omitted = _trim([s for s in definitions(root, home, "skills", "*/SKILL.md") if s["name"] not in disabled])
    data = {
        "git": files is not None,
        "config_exists": (root / CONFIG_PATH).is_file(),
        "agents": agents,
        "agents_omitted": agents_omitted,
        "skills": skills,
        "skills_omitted": skills_omitted,
        "existing_hooks": _hooks(root, home, warnings),
        "top_level_dirs": [],
        "extensions": {},
        "test_paths": [],
        "ci_files": [],
        "warnings": warnings,
    }
    if not files:
        return data
    paths = [PurePosixPath(f) for f in files]
    data["top_level_dirs"] = sorted({p.parts[0] for p in paths if len(p.parts) > 1 and not p.parts[0].startswith(".")})
    extensions = Counter(p.suffix[1:].lower() for p in paths if p.suffix)
    data["extensions"] = dict(extensions.most_common(10))
    tests = [str(p) for p in paths if _TEST_DIRS & set(p.parts[:-1]) or _TEST_FILE.match(p.name)]
    data["test_paths"] = tests[:MAX_LISTED]
    data["ci_files"] = [str(p) for p in paths if _CI_FILE.match(str(p))][:MAX_LISTED]
    return data


def relevance(entry: dict) -> int:
    """Review, test, quality or security words in the name (weight 3) and the description's opening.

    Long descriptions mention "testing" or "performance" in passing; the name and
    the first sentence say what the entry is for.
    """
    name = normalize(entry["name"].replace("-", " ").replace("_", " ").replace(":", " "))
    opening = normalize(entry["description"][:RELEVANCE_WINDOW])
    return sum(3 * bool(p.search(name)) + bool(p.search(opening)) for p in _RELEVANT)


def _trim(entries: list[dict]) -> tuple[list[dict], int]:
    """Project entries first, then the most relevant ones, so the cut keeps what can back a gate."""
    ranked = sorted(entries, key=lambda e: (e["scope"] != "project", -relevance(e)))
    kept = ranked[:MAX_DEFINITIONS]
    for entry in kept:
        text = entry["description"]
        if len(text) > MAX_DESCRIPTION:
            entry["description"] = text[: MAX_DESCRIPTION - 1].rstrip() + "…"
    return kept, len(entries) - len(kept)


def _disabled_skills(root: Path, home: Path) -> set[str]:
    """Skills set to "off" in `skillOverrides` of the project, local or user settings."""
    disabled: set[str] = set()
    for path in (root / _SETTINGS[0], root / _SETTINGS[1], home / ".claude" / "settings.json"):
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        overrides = settings.get("skillOverrides") if isinstance(settings, dict) else None
        if isinstance(overrides, dict):
            disabled |= {name for name, value in overrides.items() if value == "off"}
    return disabled


def frontmatter(text: str) -> dict[str, str]:
    """`name` and `description` of a Markdown file's YAML frontmatter.

    Covers what skill and agent files use: plain scalars (also continued on
    indented lines), quoted scalars and block scalars (`>`, `|`, with chomping
    or indentation indicators). Not a YAML parser: escapes are not decoded.
    """
    text = text.removeprefix("﻿")
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fields: dict[str, str] = {}
    lines = text[3:end].splitlines()
    index = 0
    while index < len(lines):
        match = re.match(r"^(name|description):\s*(.*)$", lines[index])
        index += 1
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        block = []
        while index < len(lines) and (lines[index].startswith((" ", "\t")) or not lines[index].strip()):
            block.append(lines[index].strip())
            index += 1
        if _BLOCK_INDICATOR.match(value):
            value = ""
        value = " ".join(part for part in (value, *block) if part)
        fields[key] = value.strip("'\"")
    return fields


def definitions(root: Path, home: Path, kind: str, pattern: str) -> list[dict]:
    found = []
    for scope, folder in (("project", root / ".claude" / kind), ("user", home / ".claude" / kind)):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob(pattern)):
            try:
                meta = frontmatter(path.read_text(encoding="utf-8", errors="replace")[:4000])
            except OSError:
                continue
            default = path.parent.name if kind == "skills" else path.stem
            found.append({"name": meta.get("name") or default, "description": meta.get("description", ""), "scope": scope})
    return found


def _hooks(root: Path, home: Path, warnings: list[str]) -> list[dict]:
    """Hooks declared in the project, local and user settings (plugin hooks are not visible here)."""
    sources = [(root / relative, relative.as_posix()) for relative in _SETTINGS]
    sources.append((home / ".claude" / "settings.json", "~/.claude/settings.json"))
    hooks = []
    for path, label in sources:
        if not path.is_file():
            continue
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            warnings.append(f"{label}: unreadable ({exc})")
            continue
        events = settings.get("hooks") if isinstance(settings, dict) else None
        for event, groups in events.items() if isinstance(events, dict) else ():
            for group in groups if isinstance(groups, list) else ():
                inner = group.get("hooks") if isinstance(group, dict) else None
                for hook in inner if isinstance(inner, list) else ():
                    if isinstance(hook, dict):
                        hooks.append({"file": label, "event": event, "command": str(hook.get("command", ""))})
    return hooks


def _project_files(root: Path) -> list[str] | None:
    if find_toplevel(root) is None:
        return None
    # Run from `root` itself: ls-files then lists only paths under it, relative to it.
    raw = run_git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if raw is None:
        return None
    return [f for f in raw.split("\0") if f]
