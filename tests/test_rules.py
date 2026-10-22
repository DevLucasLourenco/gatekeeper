import unittest

from gatekeeper.config import parse_config
from gatekeeper.rules import evaluate, parse_bypass
from gatekeeper.turn import analyze

CONFIG = parse_config(
    """
[[gates]]
id = "verify"
description = "Run the verifiers after changing files."
when = "changed"
[gates.require]
subagents = ["reviewer", "tester"]

[[gates]]
id = "lib"
description = "Rust changes need the perf auditor."
paths = ["lib/**/*.rs"]
[gates.require]
subagents = ["auditor"]

[[gates]]
id = "closing"
description = "Always end with a summary."
[gates.require]
final_text = "Conclusão da tarefa"

[[gates]]
id = "skill"
description = "Use the test-first skill when touching src."
paths = ["src/**"]
[gates.require]
skills = ["test-first"]
after_last_change = false
"""
)

CLOSING = "... \n## Conclusão da Tarefa\n..."


def facts_for(changed=None, subagents=(), skills=(), prompt="go"):
    before: dict = {}
    after = {path: [" M", 1, 1] for path in (changed or [])}
    events = [{"kind": "turn", "prompt": prompt, "snapshot": before}]
    events.append({"kind": "snapshot", "snapshot": after})
    events += [{"kind": "subagent", "name": name} for name in subagents]
    events += [{"kind": "skill", "name": name} for name in skills]
    return analyze(events, after)


def failing(verdict):
    return {v.gate_id for v in verdict.violations}


class EvaluateTest(unittest.TestCase):
    def test_no_change_only_always_gates_apply(self):
        self.assertEqual(failing(evaluate(CONFIG, facts_for(), "no closing")), {"closing"})
        self.assertEqual(failing(evaluate(CONFIG, facts_for(), CLOSING)), set())

    def test_final_text_match_ignores_case_and_accents(self):
        verdict = evaluate(CONFIG, facts_for(), "CONCLUSAO   DA TAREFA")
        self.assertEqual(failing(verdict), set())

    def test_change_requires_verifiers(self):
        verdict = evaluate(CONFIG, facts_for(changed=["docs/a.md"]), CLOSING)
        self.assertEqual(failing(verdict), {"verify"})
        violation = verdict.violations[0]
        self.assertEqual(violation.missing_subagents, ("reviewer", "tester"))

    def test_partial_verifiers_reports_only_missing(self):
        verdict = evaluate(CONFIG, facts_for(changed=["docs/a.md"], subagents=["reviewer"]), CLOSING)
        self.assertEqual(verdict.violations[0].missing_subagents, ("tester",))

    def test_plugin_namespaced_agent_type_matches(self):
        facts = facts_for(changed=["docs/a.md"], subagents=["myplugin:reviewer", "tester"])
        self.assertEqual(failing(evaluate(CONFIG, facts, CLOSING)), set())

    def test_path_scoped_gate(self):
        facts = facts_for(changed=["lib/core/main.rs"], subagents=["reviewer", "tester"])
        self.assertEqual(failing(evaluate(CONFIG, facts, CLOSING)), {"lib"})
        facts = facts_for(changed=["lib/core/build.toml"], subagents=["reviewer", "tester"])
        self.assertEqual(failing(evaluate(CONFIG, facts, CLOSING)), set())

    def test_skill_gate_accepts_typed_slash_command(self):
        verified = ["reviewer", "tester"]
        facts = facts_for(changed=["src/x.py"], subagents=verified)
        self.assertEqual(failing(evaluate(CONFIG, facts, CLOSING)), {"skill"})
        facts = facts_for(changed=["src/x.py"], subagents=verified, prompt="/test-first add it")
        self.assertEqual(failing(evaluate(CONFIG, facts, CLOSING)), set())

    def test_bypass_all_and_specific(self):
        facts = facts_for(changed=["src/x.py"], prompt="quick fix #skip-gates")
        verdict = evaluate(CONFIG, facts, "")
        self.assertEqual(failing(verdict), set())
        self.assertEqual(set(verdict.bypassed), {"verify", "closing", "skill"})

        facts = facts_for(changed=["src/x.py"], prompt="#skip-gates:verify,skill please")
        self.assertEqual(failing(evaluate(CONFIG, facts, "")), {"closing"})

    def test_bypass_only_names_gates_that_applied(self):
        verdict = evaluate(CONFIG, facts_for(prompt="#skip-gates"), CLOSING)
        self.assertEqual(verdict.bypassed, ())


class ParseBypassTest(unittest.TestCase):
    def test_forms(self):
        self.assertIsNone(parse_bypass("nothing here", "#skip-gates"))
        self.assertEqual(parse_bypass("ok #skip-gates", "#skip-gates"), "all")
        self.assertEqual(parse_bypass("#SKIP-GATES:a, b", "#skip-gates"), frozenset({"a"}))
        self.assertEqual(parse_bypass("#skip-gates:a,b x", "#skip-gates"), frozenset({"a", "b"}))
        self.assertIsNone(parse_bypass("#skip-gatesxyz", "#skip-gates"))


class UnknownCommitTest(unittest.TestCase):
    def test_path_scoped_gates_apply_when_commit_paths_are_unknown(self):
        from gatekeeper.rules import applies
        from gatekeeper.turn import UNKNOWN_COMMIT, TurnFacts

        facts = TurnFacts(changed_paths=frozenset({UNKNOWN_COMMIT}))
        lib_gate = next(g for g in CONFIG.gates if g.id == "lib")
        self.assertTrue(applies(lib_gate, facts))


if __name__ == "__main__":
    unittest.main()
