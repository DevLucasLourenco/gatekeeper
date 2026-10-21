import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from gatekeeper.inventory import frontmatter, inventory


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


class InventoryTest(unittest.TestCase):
    """`inventory` gives the /gatekeeper skill the facts it suggests gates from."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "project"
        self.home = base / "home"
        self.root.mkdir()
        self.home.mkdir()
        git(self.root, "init", "-q")
        files = {
            "src/app.py": "x",
            "src/util.py": "x",
            "tests/test_app.py": "x",
            "web/main.ts": "x",
            "web/main.spec.ts": "x",
            "README.md": "x",
            ".github/workflows/ci.yml": "x",
            ".gitignore": "build/\n",
            "build/out.bin": "ignored",
            ".claude/agents/reviewer.md": "---\nname: reviewer\ndescription: Reviews changes for bugs.\n---\nbody",
            ".claude/skills/test-first/SKILL.md": "---\nname: test-first\ndescription: 'Write the test before the code.'\n---\n",
            ".claude/settings.json": json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python check.py"}]}]}}),
        }
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        git(self.root, "add", ".")
        user_agent = self.home / ".claude" / "agents" / "tester.md"
        user_agent.parent.mkdir(parents=True)
        user_agent.write_text("---\nname: tester\ndescription: >\n  Runs the test suites.\n---\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_reports_agents_skills_hooks_and_layout(self):
        data = inventory(self.root, self.home)
        agents = {a["name"]: a for a in data["agents"]}
        self.assertEqual(agents["reviewer"]["scope"], "project")
        self.assertEqual(agents["reviewer"]["description"], "Reviews changes for bugs.")
        self.assertEqual(agents["tester"]["scope"], "user")
        self.assertEqual(agents["tester"]["description"], "Runs the test suites.")
        self.assertEqual(data["skills"][0]["name"], "test-first")
        self.assertEqual(data["skills"][0]["description"], "Write the test before the code.")
        self.assertEqual(data["existing_hooks"], [{"file": ".claude/settings.json", "event": "Stop", "command": "python check.py"}])
        self.assertIn("src", data["top_level_dirs"])
        self.assertNotIn("build", data["top_level_dirs"])
        self.assertEqual(data["extensions"]["py"], 3)
        self.assertEqual(data["extensions"]["ts"], 2)
        self.assertEqual(set(data["test_paths"]), {"tests/test_app.py", "web/main.spec.ts"})
        self.assertEqual(data["ci_files"], [".github/workflows/ci.yml"])
        self.assertFalse(data["config_exists"])
        self.assertTrue(data["git"])

    def test_outside_git_still_reports_claude_folder(self):
        with tempfile.TemporaryDirectory() as other:
            data = inventory(Path(other), self.home)
        self.assertFalse(data["git"])
        self.assertEqual(data["extensions"], {})
        self.assertEqual([a["name"] for a in data["agents"]], ["tester"])

    def test_broken_settings_json_is_reported_not_fatal(self):
        (self.root / ".claude" / "settings.json").write_text("{nope", encoding="utf-8")
        data = inventory(self.root, self.home)
        self.assertEqual(data["existing_hooks"], [])
        self.assertTrue(any("settings.json" in w for w in data["warnings"]))

    def test_long_lists_are_trimmed_and_disabled_skills_skipped(self):
        skills = self.home / ".claude" / "skills"
        for index in range(60):
            folder = skills / f"skill-{index:02d}"
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text("---\ndescription: " + "word " * 100 + "\n---\n", encoding="utf-8")
        (self.home / ".claude" / "settings.json").write_text(
            json.dumps({"skillOverrides": {"skill-00": "off", "skill-01": "on"}}), encoding="utf-8"
        )
        data = inventory(self.root, self.home)
        names = [s["name"] for s in data["skills"]]
        self.assertEqual(names[0], "test-first")
        self.assertNotIn("skill-00", names)
        self.assertIn("skill-01", names)
        self.assertLessEqual(len(names), 40)
        self.assertEqual(data["skills_omitted"], 59 - (len(names) - 1))
        self.assertTrue(all(len(s["description"]) <= 160 for s in data["skills"]))


if __name__ == "__main__":
    unittest.main()


class FrontmatterTest(unittest.TestCase):
    def test_scalars_and_blocks(self):
        cases = [
            ("---\nname: a\ndescription: plain value\n---\n", "plain value"),
            ("---\ndescription: 'single: quoted # x'\n---\n", "single: quoted # x"),
            ('---\ndescription: "double"\n---\n', "double"),
            ("---\ndescription: >\n  folded\n\n  text\n---\n", "folded text"),
            ("---\ndescription: |-\n  literal\n---\n", "literal"),
            ("---\ndescription: |+\n  kept\n---\n", "kept"),
            ("---\ndescription: >2\n  indented\n---\n", "indented"),
            ("---\ndescription: first line\n  continues here\n---\n", "first line continues here"),
            ("\ufeff---\ndescription: with bom\n---\n", "with bom"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(frontmatter(text).get("description"), expected)

    def test_missing_or_unclosed(self):
        for text in ("", "no frontmatter", "---\ndescription: x\n", "---\n---\n"):
            with self.subTest(text=text):
                self.assertNotIn("description", frontmatter(text))

    def test_nested_keys_are_ignored(self):
        self.assertEqual(frontmatter("---\nmetadata:\n  name: inner\nname: outer\n---\n").get("name"), "outer")


class InventoryEdgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "repo"
        self.home = base / "home"
        (self.root / ".claude").mkdir(parents=True)
        self.home.mkdir()
        git(self.root, "init", "-q")

    def tearDown(self):
        self.tmp.cleanup()

    def settings(self, data, where=None):
        target = where or self.root / ".claude" / "settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data), encoding="utf-8")

    def test_malformed_hooks_never_raise(self):
        for hooks in ([], {"Stop": "x"}, {"Stop": [None, {"hooks": None}, {"hooks": 3}, {"hooks": [None, {}]}]}, 7):
            with self.subTest(hooks=hooks):
                self.settings({"hooks": hooks})
                found = inventory(self.root, self.home)["existing_hooks"]
                self.assertTrue(all(h["command"] == "" for h in found))

    def test_user_hooks_are_listed_with_their_file(self):
        self.settings({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "u"}]}]}}, self.home / ".claude" / "settings.json")
        self.assertEqual(
            inventory(self.root, self.home)["existing_hooks"],
            [{"file": "~/.claude/settings.json", "event": "Stop", "command": "u"}],
        )

    def test_disabled_skills_from_each_settings_file(self):
        skills = self.home / ".claude" / "skills"
        for name in ("a", "b", "c", "d"):
            (skills / name).mkdir(parents=True)
            (skills / name / "SKILL.md").write_text("x", encoding="utf-8")
        self.settings({"skillOverrides": {"a": "off", "d": "on"}})
        self.settings({"skillOverrides": {"b": "off"}}, self.root / ".claude" / "settings.local.json")
        self.settings({"skillOverrides": ["c"]}, self.home / ".claude" / "settings.json")
        names = [s["name"] for s in inventory(self.root, self.home)["skills"]]
        self.assertEqual(names, ["c", "d"])

    def test_trim_boundaries_and_project_first(self):
        agents = self.home / ".claude" / "agents"
        agents.mkdir(parents=True)
        for index in range(40):
            (agents / f"u{index:02d}.md").write_text("---\ndescription: " + "x" * 160 + "\n---\n", encoding="utf-8")
        project = self.root / ".claude" / "agents"
        project.mkdir(parents=True)
        (project / "p.md").write_text("---\ndescription: " + "y" * 161 + "\n---\n", encoding="utf-8")
        data = inventory(self.root, self.home)
        self.assertEqual(data["agents"][0]["name"], "p")
        self.assertEqual(len(data["agents"][0]["description"]), 160)
        self.assertTrue(data["agents"][0]["description"].endswith("…"))
        self.assertEqual(data["agents"][1]["description"], "x" * 160)
        self.assertEqual((len(data["agents"]), data["agents_omitted"]), (40, 1))

    def test_subfolder_root_reports_paths_relative_to_it(self):
        (self.root / "app" / "src").mkdir(parents=True)
        (self.root / "app" / "src" / "a.py").write_text("x", encoding="utf-8")
        (self.root / "other.py").write_text("x", encoding="utf-8")
        data = inventory(self.root / "app", self.home)
        self.assertEqual(data["top_level_dirs"], ["src"])
        self.assertEqual(data["extensions"], {"py": 1})


class NestedAgentsTest(unittest.TestCase):
    def test_agents_in_subfolders_are_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, home = Path(tmp) / "repo", Path(tmp) / "home"
            nested = root / ".claude" / "agents" / "review"
            nested.mkdir(parents=True)
            home.mkdir()
            (nested / "deep.md").write_text("---\nname: deep-reviewer\n---\n", encoding="utf-8")
            self.assertEqual([a["name"] for a in inventory(root, home)["agents"]], ["deep-reviewer"])


class RelevanceTest(unittest.TestCase):
    """Before the cut, entries useful for gates come first, so they survive it."""

    def test_relevant_entries_survive_the_cut(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, home = Path(tmp) / "repo", Path(tmp) / "home"
            root.mkdir()
            skills = home / ".claude" / "skills"
            for index in range(60):
                folder = skills / f"a-marketing-{index:02d}"
                folder.mkdir(parents=True)
                (folder / "SKILL.md").write_text("---\ndescription: Write ad copy.\n---\n", encoding="utf-8")
            for name, text in (("zz-tests", "Write the test first (TDD)."), ("zz-revisao", "Revisão de segurança do código.")):
                (skills / name).mkdir()
                (skills / name / "SKILL.md").write_text(f"---\ndescription: {text}\n---\n", encoding="utf-8")
            project = root / ".claude" / "skills" / "deploy"
            project.mkdir(parents=True)
            (project / "SKILL.md").write_text("---\ndescription: Deploy the app.\n---\n", encoding="utf-8")
            names = [s["name"] for s in inventory(root, home)["skills"]]
        self.assertEqual(names[0], "deploy")
        self.assertEqual(set(names[1:3]), {"zz-tests", "zz-revisao"})
        self.assertEqual(names[3], "a-marketing-00")

    def test_unrelated_words_do_not_count(self):
        from gatekeeper.inventory import relevance

        self.assertEqual(relevance({"name": "contest-entry", "description": "Attest the latest protest."}), 0)
        self.assertEqual(relevance({"name": "perfect-loop", "description": "A seamless loop."}), 0)
        self.assertEqual(relevance({"name": "perf", "description": ""}), 3)
        self.assertGreater(relevance({"name": "perf-auditor", "description": "x"}), relevance({"name": "x", "description": "perf"}))
        self.assertGreater(relevance({"name": "qa-reviewer", "description": "Reviews code."}), 0)
        self.assertGreater(relevance({"name": "x", "description": "Roda os testes e a auditoria de performance."}), 0)
