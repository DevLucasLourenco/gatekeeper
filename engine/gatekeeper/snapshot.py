"""Fingerprint of the project's working tree, taken from git.

Comparing snapshots replaces guessing from command text: a write anywhere
outside the repository (or in a git-ignored path) is not a change, and a write
made by any tool, shell or subagent is.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .matching import path_matches
from .turn import Snapshot

GIT_TIMEOUT_SECONDS = 10
# Beyond this many dirty paths only the git status of each is kept (no stat):
# new, deleted and staged paths still show, edits to an already dirty file do not.
MAX_STAT_PATHS = 2000


def take_snapshot(root: Path, ignore: tuple[str, ...]) -> Snapshot | None:
    """Dirty paths of the repository containing `root`, or None outside git."""
    toplevel = find_toplevel(root)
    if toplevel is None:
        return None
    raw = run_git(toplevel, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    if raw is None:
        return None
    snapshot: Snapshot = {}
    for status, path in parse_porcelain(raw):
        if ignore and any(path_matches(path, pattern) for pattern in ignore):
            continue
        snapshot[path] = [status, *(_signature(toplevel / path) if len(snapshot) < MAX_STAT_PATHS else (None, None))]
    return snapshot


def find_toplevel(root: Path) -> Path | None:
    """Closest ancestor holding `.git` (a folder, or a file in worktrees), without spawning git."""
    current = root.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def parse_porcelain(raw: str) -> list[tuple[str, str]]:
    """Entries of `git status --porcelain=v1 -z`; a rename yields both paths."""
    entries: list[tuple[str, str]] = []
    parts = raw.split("\0")
    index = 0
    while index < len(parts):
        part = parts[index]
        index += 1
        if len(part) < 4:
            continue
        status, path = part[:2], part[3:]
        entries.append((status, path))
        if status[0] in "RC" and index < len(parts) and parts[index]:
            entries.append((status, parts[index]))
            index += 1
    return entries


def _signature(path: Path) -> list:
    try:
        info = path.stat()
    except OSError:
        return [None, None]
    return [info.st_mtime_ns, info.st_size]


def run_git(cwd: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", "replace")
