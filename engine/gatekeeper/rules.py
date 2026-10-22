"""Judges the facts of a turn against the configured gates. Pure: no I/O."""

from __future__ import annotations

import re
from typing import NamedTuple

from .config import Config, Gate
from .matching import normalize, path_matches
from .turn import UNKNOWN_COMMIT, TurnFacts


class Violation(NamedTuple):
    gate_id: str
    missing_subagents: tuple[str, ...] = ()
    missing_skills: tuple[str, ...] = ()
    missing_text: str | None = None
    message: str | None = None


class Verdict(NamedTuple):
    violations: tuple[Violation, ...]
    bypassed: tuple[str, ...]


def evaluate(config: Config, facts: TurnFacts, final_text: str) -> Verdict:
    bypass = parse_bypass(facts.prompt, config.bypass_token)
    violations: list[Violation] = []
    bypassed: list[str] = []
    normalized_text = normalize(final_text)
    for gate in config.gates:
        if not applies(gate, facts):
            continue
        violation = _check(gate, facts, normalized_text)
        if violation is None:
            continue
        if bypass == "all" or (isinstance(bypass, frozenset) and gate.id in bypass):
            bypassed.append(gate.id)
        else:
            violations.append(violation)
    return Verdict(tuple(violations), tuple(bypassed))


def applies(gate: Gate, facts: TurnFacts) -> bool:
    if gate.when == "always":
        return True
    if not facts.changed:
        return False
    if not gate.paths:
        return True
    if UNKNOWN_COMMIT in facts.changed_paths:
        return True  # a commit whose paths git could not list may touch any of them
    return any(path_matches(path, pattern) for path in facts.changed_paths for pattern in gate.paths)


def _check(gate: Gate, facts: TurnFacts, normalized_text: str) -> Violation | None:
    if gate.after_last_change:
        subagents, skills = facts.subagents_after_last_change, facts.skills_after_last_change
    else:
        subagents, skills = facts.subagents_in_turn, facts.skills_in_turn
    missing_subagents = tuple(name for name in gate.subagents if not _seen(name, subagents))
    missing_skills = tuple(name for name in gate.skills if not _seen(name, skills))
    missing_text = gate.final_text if gate.final_text and normalize(gate.final_text) not in normalized_text else None
    if not (missing_subagents or missing_skills or missing_text):
        return None
    return Violation(gate.id, missing_subagents, missing_skills, missing_text, gate.message)


def _seen(required: str, observed: frozenset[str]) -> bool:
    """Exact name, or the same name under a plugin namespace (`plugin:name`)."""
    return any(name == required or name.endswith(":" + required) for name in observed)


def parse_bypass(prompt: str, token: str) -> str | frozenset[str] | None:
    """`token` skips every gate; `token:a,b` skips gates a and b."""
    match = re.search(re.escape(token) + r"(?::([a-z0-9_,-]+))?(?![\w-])", prompt, re.IGNORECASE)
    if match is None:
        return None
    if match.group(1) is None:
        return "all"
    return frozenset(part for part in match.group(1).lower().split(",") if part)

