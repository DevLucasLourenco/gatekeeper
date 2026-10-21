"""Hook handlers: the only module that turns payloads into git calls and state.

Every handler except Stop always exits 0, so a gatekeeper failure never blocks
a tool call or a prompt. Stop fails open unless the config says otherwise, and
even a closed failure gives up after a bounded number of blocks.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from .config import Config, ConfigError, load_config, raw_on_error
from .snapshot import take_snapshot
from .state import StateStore
from .turn import analyze, snapshot_hash

DEFAULT_MAX_BLOCKS = 3


class HookResult:
    __slots__ = ("exit_code", "stdout", "stderr")

    def __init__(self, exit_code: int = 0, stdout: str = "", stderr: str = ""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


def handle(event: str, payload: Mapping, env: Mapping) -> HookResult:
    root = Path(env.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or ".")
    store = StateStore.from_env(env, root)
    session = str(payload.get("session_id") or "unknown")
    try:
        config = load_config(root)
    except ConfigError as exc:
        return _failure(event, store, session, f"{exc}", raw_on_error(root), "en", DEFAULT_MAX_BLOCKS, config_error=True)
    if config is None:
        return HookResult()
    try:
        handler = _HANDLERS.get(event)
        return handler(config, store, root, session, payload) if handler else HookResult()
    except Exception as exc:  # fail per config: see module docstring
        error = f"{type(exc).__name__}: {exc}"
        return _failure(event, store, session, error, config.on_error, config.language, config.max_blocks)


def _on_prompt(config: Config, store: StateStore, root: Path, session: str, payload: Mapping) -> HookResult:
    prompt = payload.get("prompt")
    snapshot = take_snapshot(root, config.ignore)
    store.start_turn(
        session,
        {
            "kind": "turn",
            "prompt": prompt if isinstance(prompt, str) else "",
            "snapshot": snapshot,
            "hash": snapshot_hash(snapshot),
        },
    )
    if not config.inject_enabled:
        return HookResult()
    from . import messages

    return HookResult(stdout=_prompt_output(messages.injection(config)))


def _on_tool(config: Config, store: StateStore, root: Path, session: str, payload: Mapping) -> HookResult:
    # Snapshot first: a skill that changes files must count as evidence after its own change.
    store.append(session, {"kind": "snapshot", "hash": snapshot_hash(take_snapshot(root, config.ignore))})
    tool_input = payload.get("tool_input")
    if payload.get("tool_name") == "Skill" and isinstance(tool_input, Mapping):
        skill = tool_input.get("skill")
        if isinstance(skill, str) and skill:
            store.append(session, {"kind": "skill", "name": skill})
    return HookResult()


def _on_subagent_stop(config: Config, store: StateStore, root: Path, session: str, payload: Mapping) -> HookResult:
    agent_type = payload.get("agent_type")
    if isinstance(agent_type, str) and agent_type:
        store.append(session, {"kind": "subagent", "name": agent_type})
    return HookResult()


def _on_stop(config: Config, store: StateStore, root: Path, session: str, payload: Mapping) -> HookResult:
    from . import messages
    from .rules import evaluate

    facts = analyze(store.events(session), take_snapshot(root, config.ignore))
    final_text = payload.get("last_assistant_message")
    verdict = evaluate(config, facts, final_text if isinstance(final_text, str) else "")
    record = {"session": session, "bypassed": list(verdict.bypassed), "changed": sorted(facts.changed_paths)[:50]}
    if not verdict.violations:
        store.audit({**record, "outcome": "bypassed" if verdict.bypassed else "passed"})
        return HookResult()
    failed = [v.gate_id for v in verdict.violations]
    if facts.blocks >= config.max_blocks:
        store.audit({**record, "outcome": "gave_up", "gates": failed})
        return HookResult()
    store.append(session, {"kind": "block", "gates": failed})
    store.audit({**record, "outcome": "blocked", "gates": failed})
    return HookResult(exit_code=2, stderr=messages.block_reason(config, verdict.violations))


_HANDLERS = {
    "UserPromptSubmit": _on_prompt,
    "PostToolUse": _on_tool,
    "PostToolUseFailure": _on_tool,
    "SubagentStop": _on_subagent_stop,
    "Stop": _on_stop,
}


def _failure(
    event: str,
    store: StateStore,
    session: str,
    error: str,
    on_error: str,
    language: str,
    max_blocks: int,
    config_error: bool = False,
) -> HookResult:
    from . import messages

    note = messages.text(language, "config_error" if config_error else "error", error=error)
    if event == "UserPromptSubmit" and config_error:
        try:
            store.start_turn(session, {"kind": "turn", "prompt": "", "hash": None})  # resets the block count
        except OSError:
            pass
        # Do not rely on the stderr of a passing hook: tell the user and Claude the gates are off.
        return HookResult(stdout=_prompt_output(note, system_message=note), stderr=note)
    if event != "Stop" or on_error != "closed":
        return HookResult(stderr=note)
    try:
        blocks = sum(1 for e in store.events(session) if isinstance(e, dict) and e.get("kind") == "block")
        if blocks >= max_blocks:
            store.audit({"session": session, "outcome": "gave_up", "error": error})
            return HookResult(stderr=note)
        store.append(session, {"kind": "block", "error": error})
    except OSError:
        pass
    return HookResult(exit_code=2, stderr=note + "\n" + messages.text(language, "closed"))


def _prompt_output(context: str, system_message: str | None = None) -> str:
    output: dict = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}
    if system_message:
        output["systemMessage"] = system_message
    return json.dumps(output, ensure_ascii=False)


def run_from_stdin(event: str) -> int:
    """Process entry point: payload JSON on stdin, context on stdout, reason on stderr."""
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except (ValueError, OSError) as exc:
        print(f"gatekeeper: unreadable hook payload ({exc})", file=sys.stderr)
        return 0
    if not isinstance(payload, dict):
        return 0
    result = handle(event, payload, os.environ)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.exit_code
