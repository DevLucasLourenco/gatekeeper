import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from gatekeeper.hooks import handle

CONFIG = """
language = "en"
max_blocks = 2
[[gates]]
id = "verify"
description = "Run reviewer after changing files."
when = "changed"
[gates.require]
subagents = ["reviewer"]

[[gates]]
id = "closing"
description = "End with DONE."
[gates.require]
final_text = "DONE"
"""


class HooksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "project"
        self.data = base / "data"
        self.root.mkdir()
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        (self.root / ".claude").mkdir()
        (self.root / ".claude" / "gatekeeper.toml").write_text(CONFIG, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"],
            check=True,
        )
        self.env = {"CLAUDE_PROJECT_DIR": str(self.root), "CLAUDE_PLUGIN_DATA": str(self.data)}

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, event, **payload):
        payload.setdefault("session_id", "s1")
        payload.setdefault("cwd", str(self.root))
        return handle(event, payload, self.env)

    def prompt(self, text="do it"):
        return self.run_hook("UserPromptSubmit", prompt=text)

    def stop(self, text="DONE"):
        return self.run_hook("Stop", last_assistant_message=text, stop_hook_active=False)

    def edit(self, name="file.txt", content="x"):
        (self.root / name).write_text(content, encoding="utf-8")
        return self.run_hook("PostToolUse", tool_name="Write", tool_input={"file_path": name})

    def test_injects_gate_summary(self):
        result = self.prompt()
        self.assertEqual(result.exit_code, 0)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("verify", context)
        self.assertIn("End with DONE.", context)

    def test_turn_without_changes_passes_with_closing(self):
        self.prompt()
        self.assertEqual(self.stop().exit_code, 0)

    def test_missing_closing_blocks(self):
        self.prompt()
        result = self.stop("no marker")
        self.assertEqual(result.exit_code, 2)
        self.assertIn("closing", result.stderr)

    def test_change_requires_verifier_after_it(self):
        self.prompt()
        self.edit()
        result = self.stop()
        self.assertEqual(result.exit_code, 2)
        self.assertIn("reviewer", result.stderr)
        self.run_hook("SubagentStop", agent_type="reviewer", agent_id="a1")
        self.assertEqual(self.stop().exit_code, 0)

    def test_change_after_verifier_blocks_again(self):
        self.prompt()
        self.edit()
        self.run_hook("SubagentStop", agent_type="reviewer", agent_id="a1")
        self.edit(content="y2")
        self.assertEqual(self.stop().exit_code, 2)

    def test_writes_outside_the_project_do_not_count(self):
        self.prompt()
        outside = Path(self.tmp.name) / "scratch.json"
        outside.write_text("{}", encoding="utf-8")
        self.run_hook("PostToolUse", tool_name="Bash", tool_input={"command": f"cat > {outside}"})
        self.assertEqual(self.stop().exit_code, 0)

    def test_shell_change_not_reported_by_any_tool_is_caught_at_stop(self):
        self.prompt()
        (self.root / "sneaky.txt").write_text("x", encoding="utf-8")
        self.assertEqual(self.stop().exit_code, 2)

    def test_new_prompt_resets_the_turn(self):
        self.prompt()
        self.edit()
        self.prompt("next")
        self.assertEqual(self.stop().exit_code, 0)

    def test_gives_up_after_max_blocks(self):
        self.prompt()
        self.assertEqual(self.stop("x").exit_code, 2)
        self.assertEqual(self.stop("x").exit_code, 2)
        self.assertEqual(self.stop("x").exit_code, 0)

    def test_bypass_is_audited(self):
        self.prompt("hotfix #skip-gates")
        self.edit()
        self.assertEqual(self.stop("x").exit_code, 0)
        audit = list((self.data / "audit").glob("*.jsonl"))
        self.assertEqual(len(audit), 1)
        records = [json.loads(line) for line in audit[0].read_text(encoding="utf-8").splitlines()]
        self.assertEqual(records[-1]["outcome"], "bypassed")

    def test_skill_tool_counts_as_evidence(self):
        self.prompt()
        self.run_hook("PostToolUse", tool_name="Skill", tool_input={"skill": "test-first"})
        self.assertEqual(self.stop().exit_code, 0)

    def test_project_without_config_is_ignored(self):
        (self.root / ".claude" / "gatekeeper.toml").unlink()
        self.assertEqual(self.prompt().stdout, "")
        self.assertEqual(self.stop("x").exit_code, 0)

    def test_invalid_config_fails_open_by_default(self):
        (self.root / ".claude" / "gatekeeper.toml").write_text("gates = [", encoding="utf-8")
        result = self.stop("x")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("gatekeeper", result.stderr)

    def test_invalid_config_fails_closed_when_asked(self):
        (self.root / ".claude" / "gatekeeper.toml").write_text(
            'on_error = "closed"\n[[gates]]\nid = "x"\n', encoding="utf-8"
        )
        self.assertEqual(self.stop("x").exit_code, 2)

    def test_subagent_events_from_nested_tools_share_the_session(self):
        self.prompt()
        self.run_hook("PostToolUse", tool_name="Write", agent_id="a9", tool_input={})
        self.edit()
        self.run_hook("SubagentStop", agent_type="other", agent_id="a2")
        self.assertEqual(self.stop().exit_code, 2)

    def test_unsafe_session_id_is_sanitized(self):
        result = self.run_hook("UserPromptSubmit", prompt="x", session_id="../../evil")
        self.assertEqual(result.exit_code, 0)
        self.assertFalse((self.data.parent / "evil.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
