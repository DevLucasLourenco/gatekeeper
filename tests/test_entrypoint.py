import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUN = Path(__file__).resolve().parents[1] / "engine" / "run.py"
HOOKS = Path(__file__).resolve().parents[1] / "hooks" / "hooks.json"


class EntrypointTest(unittest.TestCase):
    """Runs the hook the way Claude Code does: a process, JSON on stdin, exit code back."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        (self.root / ".claude").mkdir()
        (self.root / ".claude" / "gatekeeper.toml").write_text(
            'language = "pt-BR"\n[[gates]]\nid = "closing"\ndescription = "Feche com OK."\n'
            '[gates.require]\nfinal_text = "Ação concluída"\n',
            encoding="utf-8",
        )
        self.env = {
            **os.environ,
            "CLAUDE_PROJECT_DIR": str(self.root),
            "CLAUDE_PLUGIN_DATA": str(Path(self.tmp.name) / "data"),
        }

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, event, payload):
        return subprocess.run(
            [sys.executable, str(RUN), "hook", event],
            input=json.dumps(payload).encode("utf-8"),
            capture_output=True,
            env=self.env,
            timeout=60,
        )

    def test_prompt_then_blocked_stop_in_portuguese(self):
        result = self.run_hook("UserPromptSubmit", {"session_id": "s", "prompt": "oi"})
        self.assertEqual(result.returncode, 0)
        self.assertIn("Feche com OK.", json.loads(result.stdout.decode("utf-8"))["hookSpecificOutput"]["additionalContext"])

        result = self.run_hook("Stop", {"session_id": "s", "last_assistant_message": "sem conclusão"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("Ação concluída", result.stderr.decode("utf-8"))

        result = self.run_hook("Stop", {"session_id": "s", "last_assistant_message": "AÇÃO CONCLUÍDA"})
        self.assertEqual(result.returncode, 0)

    def test_garbage_payload_never_blocks(self):
        result = subprocess.run(
            [sys.executable, str(RUN), "hook", "Stop"], input=b"not json", capture_output=True, env=self.env, timeout=60
        )
        self.assertEqual(result.returncode, 0)

    def test_hooks_json_points_every_handler_at_run_py(self):
        config = json.loads(HOOKS.read_text(encoding="utf-8"))["hooks"]
        self.assertEqual(set(config), {"UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "SubagentStop", "Stop"})
        for event, groups in config.items():
            for group in groups:
                for hook in group["hooks"]:
                    self.assertEqual(hook["args"], ["${CLAUDE_PLUGIN_ROOT}/engine/run.py", "hook", event])


if __name__ == "__main__":
    unittest.main()
