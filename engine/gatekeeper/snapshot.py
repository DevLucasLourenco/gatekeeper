"""Fingerprint of the project's working tree, taken from git.

Comparing snapshots replaces guessing from command text: a write anywhere
outside the repository (or in a git-ignored path) is not a change, and a write
made by any tool, shell or subagent is. The HEAD comes from the same `git
status` call, so a commit made during the turn shows even when the tree ends
clean, at the cost of one more git call only when HEAD moved.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .matching import path_matches
from .turn import Snapshot

GIT_TIMEOUT_SECONDS = 10
# Beyond this many stat calls only the git status of each dirty path is kept:
# new, deleted and staged paths still show, edits to an already dirty file do not.
MAX_STAT_PATHS = 2000
# `since` default: the snapshot opens a turn, so nothing has been committed yet.
CURRENT_HEAD = object()


def take_snapshot(root: Path, ignore: tuple[str, ...], since: str | None | object = CURRENT_HEAD) -> Snapshot | None:
    """HEAD and dirty paths of the repository containing `root`, or None outside git.

    `since` is the HEAD the turn started at (None: no commit yet). Paths that
    differ between it and the current HEAD go under "committed" with their
    signature, so a commit made during the turn still shows; "committed" is
    None when git cannot tell which paths those are.
    """
    toplevel = find_toplevel(root)
    if toplevel is None:
        return None
    raw = run_git(
        toplevel,
        "status",
        "--porcelain=v2",
        "-z",
        "--branch",
        "--no-ahead-behind",
        "--untracked-files=all",
        "--no-renames",
    )
    if raw is None:
        return None
    head, entries = parse_porcelain(raw)
    budget = _StatBudget(toplevel)
    paths: dict[str, list] = {}
    for status, path in entries:
        if ignore and any(path_matches(path, pattern) for pattern in ignore):
            continue
        paths[path] = [status, *(budget.signature(path) or (None, None))]
    committed: dict[str, list | None] | None = {}
    if since is not CURRENT_HEAD and since != head:
        names = committed_paths(toplevel, since, head)
        if names is None:
            committed = None
        else:
            for path in names:
                if ignore and any(path_matches(path, pattern) for pattern in ignore):
                    continue
                dirty = paths.get(path)
                committed[path] = dirty[1:] if dirty and dirty[1] is not None else budget.signature(path)
    return {"head": head, "paths": paths, "committed": committed}


def committed_paths(toplevel: Path, since: str | None, head: str | None) -> list[str] | None:
    """Paths that differ between two commits; a missing side is the empty tree."""
    if since is None or head is None:
        if since is None and head is None:
            return []
        raw = run_git(toplevel, "ls-tree", "-r", "--name-only", "-z", head or since)
    else:
        raw = run_git(toplevel, "diff-tree", "-r", "--name-only", "-z", "--no-renames", since, head)
    if raw is None:
        return None
    return [path for path in raw.split("\0") if path]


def find_toplevel(root: Path) -> Path | None:
    """Closest ancestor holding `.git` (a folder, or a file in worktrees), without spawning git."""
    current = root.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


# Fields before the path in each `git status --porcelain=v2` entry type.
_V2_FIELDS = {"1": 8, "2": 9, "u": 10}


def parse_porcelain(raw: str) -> tuple[str | None, list[tuple[str, str]]]:
    """HEAD and entries of `git status --porcelain=v2 -z --branch`; a rename yields both paths.

    Statuses keep the v1 spelling (" M", "??") so they read the same in the log.
    """
    head: str | None = None
    entries: list[tuple[str, str]] = []
    parts = raw.split("\0")
    index = 0
    while index < len(parts):
        part = parts[index]
        index += 1
        if part.startswith("# branch.oid "):
            oid = part[len("# branch.oid ") :]
            head = None if oid == "(initial)" else oid
        elif part.startswith("? "):
            entries.append(("??", part[2:]))
        elif part[:1] in _V2_FIELDS and part[1:2] == " ":
            fields = part.split(" ", _V2_FIELDS[part[0]])
            if len(fields) <= _V2_FIELDS[part[0]]:
                continue
            status = fields[1].replace(".", " ")
            entries.append((status, fields[-1]))
            if part[0] == "2" and index < len(parts) and parts[index]:
                entries.append((status, parts[index]))
                index += 1
    return head, entries


class _StatBudget:
    """Stats files until MAX_STAT_PATHS calls, then returns None."""

    def __init__(self, toplevel: Path):
        self.toplevel = toplevel
        self.used = 0

    def signature(self, path: str) -> list | None:
        if self.used >= MAX_STAT_PATHS:
            return None
        self.used += 1
        return _signature(self.toplevel / path)


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
