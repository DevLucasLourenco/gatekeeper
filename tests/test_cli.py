import subprocess
import tempfile
import unittest
from pathlib import Path

from gatekeeper.cli import doctor, init, stats
from gatekeeper.config import load_config
from gatekeeper.state import StateStore


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "project"
        self.home = base / "home"
        self.root.mkdir()
        self.home.mkdir()
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        self.env = {"CLAUDE_PLUGIN_DATA": str(base / "data")}

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_writes_each_preset_and_never_overwrites(self):
        for preset in ("minimal", "verifiers"):
            with self.subTest(preset=preset):
                target = self.root / ".claude" / "gatekeeper.toml"
                target.unlink(missing_ok=True)
                code, _ = init(self.root, preset)
                self.assertEqual(code, 0)
                self.assertIsNotNone(load_config(self.root))
        code, out = init(self.root, "minimal")
        self.assertEqual(code, 1)
        self.assertIn("exists", out)

    def test_init_rejects_unknown_preset(self):
        code, _ = init(self.root, "nope")
        self.assertEqual(code, 1)

    def test_doctor_reports_missing_config(self):
        code, out = doctor(self.root, self.home)
        self.assertEqual(code, 1)
        self.assertIn("gatekeeper.toml", out)

    def test_doctor_flags_missing_agents_and_skills(self):
        init(self.root, "verifiers")
        code, out = doctor(self.root, self.home)
        self.assertEqual(code, 0)
        self.assertIn("WARN", out)
        self.assertIn("reviewer", out)

    def test_doctor_finds_agents_by_frontmatter_name_and_skills(self):
        init(self.root, "verifiers")
        agents = self.root / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "qa.md").write_text("---\nname: reviewer\n---\nbody", encoding="utf-8")
        (self.home / ".claude" / "agents").mkdir(parents=True)
        (self.home / ".claude" / "agents" / "tester.md").write_text("x", encoding="utf-8")
        _, out = doctor(self.root, self.home)
        self.assertNotIn("WARN", out)

    def test_doctor_reports_invalid_config(self):
        (self.root / ".claude").mkdir()
        (self.root / ".claude" / "gatekeeper.toml").write_text("x = [", encoding="utf-8")
        code, out = doctor(self.root, self.home)
        self.assertEqual(code, 1)
        self.assertIn("ERROR", out)

    def test_stats_summarizes_audit(self):
        store = StateStore.from_env(self.env, self.root)
        store.audit({"outcome": "blocked", "gates": ["verify", "closing"]})
        store.audit({"outcome": "blocked", "gates": ["verify"]})
        store.audit({"outcome": "passed"})
        out = stats(self.root, Path(self.env["CLAUDE_PLUGIN_DATA"]))
        self.assertIn("blocked: 2", out)
        self.assertIn("passed: 1", out)
        self.assertIn("verify: 2", out)

    def test_stats_without_records(self):
        self.assertIn("0", stats(self.root, Path(self.env["CLAUDE_PLUGIN_DATA"])))


if __name__ == "__main__":
    unittest.main()
