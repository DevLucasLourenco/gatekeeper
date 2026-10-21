"""Loads and validates `.claude/gatekeeper.toml`.

Validation is strict on purpose: an unknown key is almost always a typo, and a
typo in a gate silently disables it.
"""

from __future__ import annotations

import re
from typing import NamedTuple
from pathlib import Path

from .matching import normalize

CONFIG_PATH = Path(".claude") / "gatekeeper.toml"
LANGUAGES = ("en", "pt-BR")
ON_ERROR = ("open", "closed")
WHEN = ("always", "changed")
_GATE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

_TOP_KEYS = {"language", "on_error", "max_blocks", "bypass_token", "ignore", "inject", "gates"}
_INJECT_KEYS = {"enabled", "preamble"}
_GATE_KEYS = {"id", "description", "when", "paths", "message", "require"}
_REQUIRE_KEYS = {"subagents", "skills", "final_text", "after_last_change"}


class ConfigError(Exception):
    """The configuration file is missing required data or has invalid values."""


class Gate(NamedTuple):
    id: str
    description: str
    when: str = "always"
    paths: tuple[str, ...] = ()
    subagents: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    final_text: str | None = None
    after_last_change: bool = True
    message: str | None = None


class Config(NamedTuple):
    gates: tuple[Gate, ...]
    language: str = "en"
    on_error: str = "open"
    max_blocks: int = 3
    bypass_token: str = "#skip-gates"
    ignore: tuple[str, ...] = ()
    inject_enabled: bool = True
    inject_preamble: str | None = None


def load_config(root: Path) -> Config | None:
    """Config of the project at `root`, or None when the project has no config file."""
    path = root / CONFIG_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read {path} as UTF-8: {exc}") from exc
    return parse_config(text)


def raw_on_error(root: Path) -> str:
    """Best-effort `on_error` of a config that failed validation."""
    import tomllib

    try:
        data = tomllib.loads((root / CONFIG_PATH).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return "open"
    value = data.get("on_error")
    return value if value in ON_ERROR else "open"


def parse_config(text: str) -> Config:
    import tomllib  # imported here: projects without a config never pay for it

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML: {exc}") from exc
    _check_keys(data, _TOP_KEYS, "top level")

    inject = data.get("inject", {})
    if not isinstance(inject, dict):
        raise ConfigError("'inject' must be a table")
    _check_keys(inject, _INJECT_KEYS, "[inject]")

    raw_gates = data.get("gates")
    if not isinstance(raw_gates, list) or not raw_gates:
        raise ConfigError("define at least one [[gates]] entry")
    gates = tuple(_parse_gate(raw, index) for index, raw in enumerate(raw_gates))
    seen: set[str] = set()
    for gate in gates:
        if gate.id in seen:
            raise ConfigError(f"duplicate gate id '{gate.id}'")
        seen.add(gate.id)

    max_blocks = data.get("max_blocks", 3)
    if isinstance(max_blocks, bool) or not isinstance(max_blocks, int) or not 1 <= max_blocks <= 8:
        raise ConfigError("'max_blocks' must be an integer from 1 to 8")

    return Config(
        gates=gates,
        language=_choice(data, "language", LANGUAGES, "en"),
        on_error=_choice(data, "on_error", ON_ERROR, "open"),
        max_blocks=max_blocks,
        bypass_token=_string(data, "bypass_token", "top level") or "#skip-gates",
        ignore=_strings(data, "ignore", "top level"),
        inject_enabled=_bool(inject, "enabled", True, "[inject]"),
        inject_preamble=_string(inject, "preamble", "[inject]"),
    )


def _parse_gate(raw: object, index: int) -> Gate:
    where = f"gates[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be a table")
    _check_keys(raw, _GATE_KEYS, where)
    gate_id = raw.get("id")
    if not isinstance(gate_id, str) or not _GATE_ID.match(gate_id):
        raise ConfigError(f"{where}: 'id' must be lowercase letters, digits, '-' or '_' (max 40)")
    where = f"gate '{gate_id}'"
    description = _string(raw, "description", where)
    if not description:
        raise ConfigError(f"{where}: 'description' is required")

    require = raw.get("require", {})
    if not isinstance(require, dict):
        raise ConfigError(f"{where}: 'require' must be a table")
    _check_keys(require, _REQUIRE_KEYS, f"{where} [require]")
    subagents = _strings(require, "subagents", where)
    skills = _strings(require, "skills", where)
    final_text = _string(require, "final_text", where)
    if final_text is not None and not normalize(final_text):
        raise ConfigError(f"{where}: 'final_text' is empty once case, accents and spaces are ignored")
    if not (subagents or skills or final_text):
        raise ConfigError(f"{where}: 'require' needs subagents, skills or final_text")

    paths = _strings(raw, "paths", where)
    when = _choice(raw, "when", WHEN, "changed" if paths else "always", where)
    return Gate(
        id=gate_id,
        description=description,
        when="changed" if paths else when,
        paths=paths,
        subagents=subagents,
        skills=skills,
        final_text=final_text,
        after_last_change=_bool(require, "after_last_change", True, where),
        message=_string(raw, "message", where),
    )


def _check_keys(table: dict, allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(unknown)}")


def _choice(table: dict, key: str, options: tuple[str, ...], default: str, where: str = "top level") -> str:
    value = table.get(key, default)
    if value not in options:
        raise ConfigError(f"{where}: '{key}' must be one of {', '.join(options)}")
    return value


def _string(table: dict, key: str, where: str) -> str | None:
    value = table.get(key)
    if value is not None and not isinstance(value, str):
        raise ConfigError(f"{where}: '{key}' must be a string")
    return value or None


def _strings(table: dict, key: str, where: str) -> tuple[str, ...]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ConfigError(f"{where}: '{key}' must be a list of non-empty strings")
    return tuple(value)


def _bool(table: dict, key: str, default: bool, where: str) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{where}: '{key}' must be true or false")
    return value
