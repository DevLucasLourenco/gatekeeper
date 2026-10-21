import unittest

from gatekeeper.config import ConfigError, parse_config

MINIMAL = """
[[gates]]
id = "closing"
description = "End with a summary."
[gates.require]
final_text = "Summary"
"""


class ParseConfigTest(unittest.TestCase):
    def test_defaults_are_applied(self):
        config = parse_config(MINIMAL)
        self.assertEqual(config.language, "en")
        self.assertEqual(config.on_error, "open")
        self.assertEqual(config.max_blocks, 3)
        self.assertEqual(config.bypass_token, "#skip-gates")
        self.assertTrue(config.inject_enabled)
        gate = config.gates[0]
        self.assertEqual(gate.when, "always")
        self.assertTrue(gate.after_last_change)
        self.assertEqual(gate.final_text, "Summary")
        self.assertEqual(gate.subagents, ())

    def test_full_gate(self):
        config = parse_config(
            """
language = "pt-BR"
on_error = "closed"
max_blocks = 5
ignore = ["*.lock"]
[inject]
enabled = false
preamble = "Rules:"
[[gates]]
id = "verify"
description = "Run the verifiers."
when = "changed"
paths = ["src/**"]
message = "Custom."
[gates.require]
subagents = ["reviewer", "tester"]
skills = ["test-first"]
after_last_change = false
"""
        )
        self.assertEqual(config.language, "pt-BR")
        self.assertEqual(config.on_error, "closed")
        self.assertEqual(config.max_blocks, 5)
        self.assertEqual(config.ignore, ("*.lock",))
        self.assertFalse(config.inject_enabled)
        self.assertEqual(config.inject_preamble, "Rules:")
        gate = config.gates[0]
        self.assertEqual(gate.when, "changed")
        self.assertEqual(gate.paths, ("src/**",))
        self.assertEqual(gate.subagents, ("reviewer", "tester"))
        self.assertEqual(gate.skills, ("test-first",))
        self.assertFalse(gate.after_last_change)
        self.assertEqual(gate.message, "Custom.")

    def test_rejects_invalid_toml(self):
        with self.assertRaises(ConfigError):
            parse_config("gates = [")

    def test_rejects_unknown_keys_to_catch_typos(self):
        with self.assertRaisesRegex(ConfigError, "subagent"):
            parse_config(MINIMAL.replace("final_text", "subagent"))
        with self.assertRaisesRegex(ConfigError, "langauge"):
            parse_config('langauge = "en"\n' + MINIMAL)

    def test_rejects_bad_values(self):
        cases = [
            'language = "fr"\n' + MINIMAL,
            'on_error = "maybe"\n' + MINIMAL,
            "max_blocks = 0\n" + MINIMAL,
            MINIMAL.replace('id = "closing"', 'id = "closing"\nwhen = "sometimes"'),
            MINIMAL.replace('id = "closing"', 'id = "Has Space"'),
            MINIMAL.replace('final_text = "Summary"', "final_text = 3"),
            MINIMAL.replace('final_text = "Summary"', 'skills = ["ok", 2]'),
        ]
        for text in cases:
            with self.subTest(text=text), self.assertRaises(ConfigError):
                parse_config(text)

    def test_gate_must_require_something(self):
        with self.assertRaisesRegex(ConfigError, "require"):
            parse_config('[[gates]]\nid = "empty"\ndescription = "x"\n')

    def test_duplicate_gate_ids(self):
        with self.assertRaisesRegex(ConfigError, "closing"):
            parse_config(MINIMAL + MINIMAL)

    def test_needs_at_least_one_gate(self):
        with self.assertRaisesRegex(ConfigError, "gates"):
            parse_config('language = "en"\n')

    def test_paths_imply_changed(self):
        config = parse_config(MINIMAL.replace('id = "closing"', 'id = "closing"\npaths = ["docs/**"]'))
        self.assertEqual(config.gates[0].when, "changed")


if __name__ == "__main__":
    unittest.main()
