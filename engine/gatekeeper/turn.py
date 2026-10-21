"""Turns the event log of one turn into facts the rules can judge.

Pure: no I/O. Events are appended by the hooks in the order Claude Code fires
them. The turn event carries the full baseline snapshot; later snapshot events
carry only a hash, which is enough to place the last change in the sequence.
The Stop hook passes the final full snapshot, compared with the baseline to
know which paths changed.
"""

from __future__ import annotations

import hashlib
import json
import re

Snapshot = dict[str, list]

_SLASH_COMMAND = re.compile(r"^/([A-Za-z0-9_.:-]+)")


class TurnFacts:
    __slots__ = (
        "prompt",
        "changed_paths",
        "subagents_in_turn",
        "subagents_after_last_change",
        "skills_in_turn",
        "skills_after_last_change",
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
        blocks: int = 0,
    ):
        self.prompt = prompt
        self.changed_paths = changed_paths
        self.subagents_in_turn = subagents_in_turn
        self.subagents_after_last_change = subagents_after_last_change
        self.skills_in_turn = skills_in_turn
        self.skills_after_last_change = skills_after_last_change
        self.blocks = blocks

    @property
    def changed(self) -> bool:
        return bool(self.changed_paths)


def snapshot_hash(snapshot: Snapshot | None) -> str | None:
    if snapshot is None:
        return None
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def analyze(events: list, final_snapshot: Snapshot | None) -> TurnFacts:
    prompt = ""
    baseline: Snapshot | None = None
    first_hash: str | None = None
    previous_hash: str | None = None
    # Evidence is kept with its position so it can be compared to the last change.
    evidence: list[tuple[int, str, str]] = []
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
        elif kind in ("subagent", "skill"):
            name = event.get("name")
            if isinstance(name, str) and name:
                evidence.append((position, kind, name))
        elif kind == "block":
            blocks += 1

    if final_snapshot is not None:
        observe({"snapshot": final_snapshot}, len(events))

    changed: frozenset[str] = frozenset()
    if final_snapshot is not None and first_hash is not None and previous_hash != first_hash:
        if baseline is None:
            # No full baseline (log started mid-turn): every dirty path is suspect.
            changed = frozenset(final_snapshot)
        else:
            changed = frozenset(
                path for path in set(baseline) | set(final_snapshot) if baseline.get(path) != final_snapshot.get(path)
            )

    def names(kind: str, after: int) -> frozenset[str]:
        return frozenset(name for position, k, name in evidence if k == kind and position > after)

    return TurnFacts(
        prompt=prompt,
        changed_paths=changed,
        subagents_in_turn=names("subagent", -1),
        subagents_after_last_change=names("subagent", last_change),
        skills_in_turn=names("skill", -1),
        skills_after_last_change=names("skill", last_change),
        blocks=blocks,
    )
