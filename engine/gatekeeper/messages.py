"""User-facing text. Block reasons go back to Claude as instructions, so they stay fixed."""

from __future__ import annotations

from .config import Config
from .rules import Violation

CATALOG = {
    "en": {
        "header": "gatekeeper blocked the end of this response:",
        "subagents": "run the subagents {names} (Agent tool, exact subagent_type){after}",
        "skills": "use the skills {names}{after}",
        "text": 'end the response with "{text}"',
        "after": " after the last change to the project",
        "footer": "Then finish the response again.",
        "inject_title": "Gates enforced by gatekeeper in this response (the Stop hook blocks a response that skips one):",
        "error": "gatekeeper: internal error ({error}).",
        "config_error": "gatekeeper: .claude/gatekeeper.toml is invalid, so its gates are not enforced until it is fixed ({error}). Run the doctor command for details.",
        "closed": "gatekeeper is set to fail closed: fix .claude/gatekeeper.toml or the error above.",
    },
    "pt-BR": {
        "header": "O gatekeeper bloqueou o fim desta resposta:",
        "subagents": "rode os subagentes {names} (ferramenta Agent, subagent_type exato){after}",
        "skills": "use as skills {names}{after}",
        "text": 'termine a resposta com "{text}"',
        "after": " depois da última mudança no projeto",
        "footer": "Depois, encerre a resposta de novo.",
        "inject_title": "Gates impostos pelo gatekeeper nesta resposta (o hook Stop bloqueia a resposta que pular um):",
        "error": "gatekeeper: erro interno ({error}).",
        "config_error": "gatekeeper: o .claude/gatekeeper.toml é inválido, e os gates ficam desligados até a correção ({error}). Rode o comando doctor para detalhes.",
        "closed": "O gatekeeper está configurado para falhar fechado: corrija o .claude/gatekeeper.toml ou o erro acima.",
    },
}


def text(language: str, key: str, **values: str) -> str:
    return CATALOG.get(language, CATALOG["en"])[key].format(**values)


def block_reason(config: Config, violations: tuple[Violation, ...]) -> str:
    language = config.language
    after_by_gate = {gate.id: gate.after_last_change for gate in config.gates}
    lines = [text(language, "header")]
    for violation in violations:
        if violation.message:
            lines.append(f"- {violation.gate_id}: {violation.message}")
            continue
        after = text(language, "after") if after_by_gate.get(violation.gate_id, True) else ""
        parts = []
        if violation.missing_subagents:
            parts.append(text(language, "subagents", names=", ".join(violation.missing_subagents), after=after))
        if violation.missing_skills:
            parts.append(text(language, "skills", names=", ".join(violation.missing_skills), after=after))
        if violation.missing_text:
            parts.append(text(language, "text", text=violation.missing_text))
        lines.append(f"- {violation.gate_id}: " + "; ".join(parts) + ".")
    lines.append(text(language, "footer"))
    return "\n".join(lines)


def injection(config: Config) -> str:
    lines = [config.inject_preamble] if config.inject_preamble else []
    lines.append(text(config.language, "inject_title"))
    lines += [f"- {gate.id}: {gate.description}" for gate in config.gates]
    return "\n".join(lines)
