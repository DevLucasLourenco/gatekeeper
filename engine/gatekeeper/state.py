"""Per-session event log and per-project audit log, kept outside the project.

Hooks fire concurrently (parallel tool calls, several subagents), so the log is
append-only JSONL: each event is one small `write` on a file opened for append,
and the reader skips any line it cannot parse.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Mapping
from pathlib import Path

PLUGIN_DATA_ID = "gatekeeper-gatekeeper"
SESSION_RETENTION_SECONDS = 14 * 24 * 3600
AUDIT_MAX_BYTES = 5 * 1024 * 1024
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def default_data_dir(env: Mapping, home: Path) -> Path:
    """Where the hooks keep state.

    Hooks receive `CLAUDE_PLUGIN_DATA`; commands run from a shell do not, so
    they look for the installed plugin's data folder, then the temp folder.
    """
    configured = env.get("CLAUDE_PLUGIN_DATA")
    if configured:
        return Path(configured)
    plugins = home / ".claude" / "plugins" / "data"
    preferred = plugins / PLUGIN_DATA_ID
    if preferred.is_dir():
        return preferred
    others = sorted(plugins.glob("gatekeeper-*")) if plugins.is_dir() else []
    if len(others) == 1:
        return others[0]
    temp = env.get("TEMP") or env.get("TMP") or os.environ.get("TEMP") or "."
    return Path(temp) / "gatekeeper"


class StateStore:
    def __init__(self, data_dir: Path, root: Path):
        self.data_dir = data_dir
        self.sessions = data_dir / "sessions"
        self.root = root
        self.key = project_key(root)
        self.audit_file = data_dir / "audit" / f"{self.key}.jsonl"

    @classmethod
    def from_env(cls, env: Mapping, root: Path) -> StateStore:
        return cls(default_data_dir(env, Path.home()), root)

    def session_file(self, session_id: str) -> Path:
        # Sanitizing alone could map two ids to one name; the digest keeps them apart.
        safe = _UNSAFE.sub("-", session_id)[:40] or "unknown"
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:10]
        return self.sessions / f"{self.key}-{safe}-{digest}.jsonl"

    def start_turn(self, session_id: str, event: dict) -> None:
        """Replaces the session log: a new prompt starts a new turn."""
        path = self.session_file(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_line(event), encoding="utf-8")
        self._prune()

    def append(self, session_id: str, event: dict) -> None:
        path = self.session_file(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(_line(event))

    def events(self, session_id: str) -> list:
        return _read_jsonl(self.session_file(session_id))

    def first_event(self, session_id: str) -> dict | None:
        """The event that opened the turn, reading only the first line."""
        try:
            with open(self.session_file(session_id), encoding="utf-8") as handle:
                event = json.loads(handle.readline())
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        return event if isinstance(event, dict) else None

    def audit(self, record: dict) -> None:
        self.audit_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self.audit_file.stat().st_size > AUDIT_MAX_BYTES:
                os.replace(self.audit_file, self.audit_file.with_suffix(".jsonl.1"))
        except OSError:
            pass  # missing, or held open by a concurrent hook: rotate next time
        stamped = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **record}
        with open(self.audit_file, "a", encoding="utf-8") as handle:
            handle.write(_line(stamped))

    def audit_records(self) -> list[dict]:
        return [r for r in _read_jsonl(self.audit_file) if isinstance(r, dict)]

    def _prune(self) -> None:
        limit = time.time() - SESSION_RETENTION_SECONDS
        try:
            for path in self.sessions.glob("*.jsonl"):
                if path.stat().st_mtime < limit:
                    path.unlink(missing_ok=True)
        except OSError:
            pass


def project_key(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).casefold().encode("utf-8")).hexdigest()[:12]


def _read_jsonl(path: Path) -> list:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    items = []
    for line in lines:
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def _line(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
