"""Turns the event log of one turn into facts the rules can judge.

Pure: no I/O. Events are appended by the hooks in the order Claude Code fires
them. The turn event carries the full baseline snapshot; later snapshot events
carry only a hash, which is enough to place the last change in the sequence.
The Stop hook passes the final full snapshot, compared with the baseline to
know which paths changed.

The hash covers what is on disk, not how git describes it: staging a file or
committing content that was already there leaves it unchanged, so a commit
made after the verifiers does not void their evidence.
"""

from __future__ import annotations

import hashlib
import json
import re

# {"head": str | None, "paths": {path: [status, mtime, size]},
#  "committed": {path: [mtime, size] | None} | None}; see snapshot.take_snapshot.
Snapshot = dict

# Stands for the paths of a commit made in the turn when git could not list them
# (start commit gone after gc, timeout). A path-scoped gate treats it as a match.
UNKNOWN_COMMIT = "<commit with unknown paths>"

_SLASH_COMMAND = re.compile(r"^/([A-Za-z0-9_.:-]+)")
# Prompts Claude Code submits by itself: a background task finished, or a
# subagent (or another session) handed back its report.
_AUTOMATIC_PROMPT = re.compile(
    r"^\s*(?:Another Claude session sent a message:\s*)?(?:<task-notification>|<agent-message from=)"
)


class TurnFacts:
    __slots__ = (
        "prompt",
        "changed_paths",
        "subagents_in_turn",
        "subagents_after_last_change",
        "skills_in_turn",
        "skills_after_last_change",
        "pending_subagents",
        "blocks",
    )

    def __init__(
        self,
        prompt: str = "",
        changed_paths: frozenset[str] = frozenset(),
        subagents_in_turn: frozenset[str] = frozenset(),
        subagents_after_last_change: frozenset[str] = frozenset(),
        skills_in_turn: frozenset[str] = frozenset(),
        skills_after_last_change: frozenset[str] = frozenset(),
        pending_subagents: frozenset[str] = frozenset(),
        blocks: int = 0,
    ):
        self.prompt = prompt
        self.changed_paths = changed_paths
        self.subagents_in_turn = subagents_in_turn
        self.subagents_after_last_change = subagents_after_last_change
        self.skills_in_turn = skills_in_turn
        self.skills_after_last_change = skills_after_last_change
        self.pending_subagents = pending_subagents
        self.blocks = blocks

    @property
    def changed(self) -> bool:
        return bool(self.changed_paths)


def is_automatic_prompt(prompt: str) -> bool:
    return bool(_AUTOMATIC_PROMPT.match(prompt))


def snapshot_hash(snapshot: Snapshot | None) -> str | None:
    if snapshot is None:
        return None
    head, paths, committed = _parts(snapshot)
    content: dict = dict(committed or {})
    content.update((path, _content(entry)) for path, entry in paths.items())
    key: dict = {"content": content}
    if committed is None or None in committed.values():
        # Committed paths unknown or not stat-ed: only the HEAD tells a new commit apart.
        key["head"] = head
    encoded = json.dumps(key, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def _parts(snapshot: Snapshot) -> tuple[str | None, dict, dict | None]:
    paths = snapshot.get("paths")
    if not isinstance(paths, dict):
        # Flat {path: entry} from before HEAD was tracked (a log written by an older version).
        return None, snapshot, {}
    committed = snapshot.get("committed")
    return snapshot.get("head"), paths, committed if isinstance(committed, dict) else None


def _content(entry) -> list:
    """What a dirty entry says about the file on disk."""
    if not isinstance(entry, list) or len(entry) < 3:
        return entry
    signature = entry[1:3]
    if signature == [None, None] and "D" not in str(entry[0]):
        return entry[:1]  # not stat-ed (over the cap): only the status is known
    return signature


def analyze(events: list, final_snapshot: Snapshot | None) -> TurnFacts:
    prompt = ""
    baseline: Snapshot | None = None
    first_hash: str | None = None
    previous_hash: str | None = None
    # Evidence is kept with its position so it can be compared to the last change.
    evidence: list[tuple[int, str, str]] = []
    launches: list[tuple[int, str, str | None]] = []
    finished: list[tuple[int, str, str | None]] = []
    last_change = -1
    blocks = 0

    def observe(event: dict, position: int) -> None:
        nonlocal baseline, first_hash, previous_hash, last_change
        full = event.get("snapshot")
        digest = event.get("hash")
        if not isinstance(digest, str):
            digest = snapshot_hash(full) if isinstance(full, dict) else None
        if digest is None:
            return
        if first_hash is None:
            first_hash = digest
            baseline = full if isinstance(full, dict) else None
        elif digest != previous_hash:
            last_change = position
        previous_hash = digest

    for position, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        kind = event.get("kind")
        if kind == "turn":
            prompt = event.get("prompt") if isinstance(event.get("prompt"), str) else ""
            match = _SLASH_COMMAND.match(prompt.strip())
            if match:
                evidence.append((position, "skill", match.group(1)))
            observe(event, position)
        elif kind == "snapshot":
            observe(event, position)
        elif kind in ("subagent", "skill", "launch"):
            name = event.get("name")
            if not (isinstance(name, str) and name):
                continue
            agent_id = event.get("id") if isinstance(event.get("id"), str) else None
            if kind == "launch":
                launches.append((position, name, agent_id))
                continue
            evidence.append((position, kind, name))
            if kind == "subagent":
                finished.append((position, name, agent_id))
        elif kind == "block":
            blocks += 1

    if final_snapshot is not None:
        observe({"snapshot": final_snapshot}, len(events))

    changed: frozenset[str] = frozenset()
    if final_snapshot is not None and first_hash is not None:
        final_head, final_paths, committed = _parts(final_snapshot)
        committed_paths = frozenset(committed or ())
        if baseline is None:
            # No full baseline (log started mid-turn): every dirty path is suspect.
            if previous_hash != first_hash:
                changed = frozenset(final_paths) | committed_paths
                if committed is None:
                    changed |= {UNKNOWN_COMMIT}
        else:
            base_head, base_paths, _ = _parts(baseline)
            dirty = (path for path in set(base_paths) | set(final_paths) if base_paths.get(path) != final_paths.get(path))
            changed = frozenset(dirty) | committed_paths
            if committed is None and final_head != base_head:
                changed |= {UNKNOWN_COMMIT}

    def names(kind: str, after: int) -> frozenset[str]:
        return frozenset(name for position, k, name in evidence if k == kind and position > after)

    def ended(launch: tuple[int, str, str | None]) -> bool:
        start, name, agent_id = launch
        return any(
            position > start and (other_id == agent_id if agent_id and other_id else other == name)
            for position, other, other_id in finished
        )

    return TurnFacts(
        prompt=prompt,
        changed_paths=changed,
        subagents_in_turn=names("subagent", -1),
        subagents_after_last_change=names("subagent", last_change),
        skills_in_turn=names("skill", -1),
        skills_after_last_change=names("skill", last_change),
        pending_subagents=frozenset(launch[1] for launch in launches if not ended(launch)),
        blocks=blocks,
    )
