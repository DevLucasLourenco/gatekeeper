"""Regression tests for the findings of the first verification round."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gatekeeper import hooks as hooks_module
from gatekeeper import snapshot as snapshot_module
from gatekeeper import state as state_module
from gatekeeper.cli import doctor, init, stats
from gatekeeper.config import ConfigError, parse_config
from gatekeeper.hooks import handle
from gatekeeper.matching import normalize, path_matches
from gatekeeper.rules import parse_bypass
from gatekeeper.snapshot import take_snapshot
from gatekeeper.state import StateStore, default_data_dir


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def make_repo(root: Path, config: str | None) -> None:
    root.mkdir(parents=True)
    git(root, "init", "-q")
    if config is not None:
        (root / ".claude").mkdir()
        (root / ".claude" / "gatekeeper.toml").write_text(config, encoding="utf-8")
    (root / "seed.txt").write_text("seed", encoding="utf-8")
    git(root, "add", ".")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i")


class GlobTest(unittest.TestCase):
    def test_gitignore_style_patterns(self):
        cases = [
            ("Cargo.lock", "*.lock", True),
            ("a/b/Cargo.lock", "*.lock", True),
            ("src/a.py", "src/**", True),
            ("src/deep/a.py", "src/**", True),
            ("dist/x.js", "dist/", True),
            ("pkg/dist/x.js", "dist/", True),
            ("docs/a/b.md", "docs/*.md", False),
            ("docs/b.md", "docs/*.md", True),
            ("lib/core/main.rs", "lib/**/*.rs", True),
            ("other/src/a.py", "src/**", False),
            ("/abs.txt", "abs.txt", True),
        ]
        for path, pattern, expected in cases:
            with self.subTest(path=path, pattern=pattern):
                self.assertEqual(path_matches(path, pattern), expected)


class NormalizeTest(unittest.TestCase):
    def test_keeps_non_ascii_letters_and_symbols(self):
        self.assertEqual(normalize("Ação  ✅"), "acao ✅")
        self.assertEqual(normalize("Закрытие"), "закрытие")

    def test_final_text_that_normalizes_to_empty_is_rejected(self):
        with self.assertRaises(ConfigError):
            parse_config('[[gates]]\nid = "x"\ndescription = "d"\n[gates.require]\nfinal_text = "́"\n')


class BypassTokenTest(unittest.TestCase):
    def test_tokens_with_regex_characters_are_literal(self):
        for token in ("[skip.me]", "a+b", "(x)", "$$gk"):
            with self.subTest(token=token):
                self.assertEqual(parse_bypass(f"go {token} now", token), "all")
                self.assertEqual(parse_bypass(f"go {token}:verify now", token), frozenset({"verify"}))
        self.assertIsNone(parse_bypass("aab", "a+b"))
        self.assertIsNone(parse_bypass("[skipxme]", "[skip.me]"))


class SnapshotRealGitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        make_repo(self.root, None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_paths_with_spaces_and_accents(self):
        (self.root / "relatório final.txt").write_text("x", encoding="utf-8")
        (self.root / "dir com espaço").mkdir()
        (self.root / "dir com espaço" / "ação.py").write_text("x", encoding="utf-8")
        self.assertEqual(set(take_snapshot(self.root, ())), {"relatório final.txt", "dir com espaço/ação.py"})

    def test_git_mv_reports_both_paths(self):
        git(self.root, "mv", "seed.txt", "moved.txt")
        self.assertEqual(set(take_snapshot(self.root, ())), {"seed.txt", "moved.txt"})

    def test_path_cap_skips_stat_but_keeps_every_path(self):
        for name in ("a.txt", "b.txt", "c.txt"):
            (self.root / name).write_text("1", encoding="utf-8")
        with mock.patch.object(snapshot_module, "MAX_STAT_PATHS", 2):
            first = take_snapshot(self.root, ())
            (self.root / "d.txt").write_text("new", encoding="utf-8")
            second = take_snapshot(self.root, ())
        self.assertEqual(set(first), {"a.txt", "b.txt", "c.txt"})
        self.assertEqual(first["c.txt"], ["??", None, None])
        self.assertIn("d.txt", second)


GATES = """
language = "en"
ignore = ["docs/**"]
bypass_token = "[skip.me]"
[[gates]]
id = "verify"
description = "Run reviewer after changes."
when = "changed"
[gates.require]
subagents = ["reviewer"]
[[gates]]
id = "docs"
description = "Docs need the editor."
paths = ["docs/**"]
[gates.require]
subagents = ["editor"]
[[gates]]
id = "test-first"
description = "src needs the test-first skill after the change."
paths = ["src/**"]
[gates.require]
skills = ["test-first"]
"""


class HookFindingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "project"
        self.data = base / "data"
        make_repo(self.root, GATES)
        self.env = {"CLAUDE_PROJECT_DIR": str(self.root), "CLAUDE_PLUGIN_DATA": str(self.data)}

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, event, session="s1", **payload):
        return handle(event, {"session_id": session, **payload}, self.env)

    def write(self, rel, content="x"):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def audit(self):
        return StateStore.from_env(self.env, self.root).audit_records()

    def test_ignore_wins_over_paths(self):
        self.run_hook("UserPromptSubmit", prompt="go")
        self.write("docs/x.md")
        self.assertEqual(self.run_hook("Stop", last_assistant_message="").exit_code, 0)

    def test_custom_bypass_token_end_to_end(self):
        self.run_hook("UserPromptSubmit", prompt="hotfix [skip.me]:verify")
        self.write("a.txt")
        self.assertEqual(self.run_hook("Stop", last_assistant_message="").exit_code, 0)
        self.assertEqual(self.audit()[-1]["outcome"], "bypassed")

    def test_blocked_record_lists_bypassed_gates(self):
        self.run_hook("UserPromptSubmit", prompt="[skip.me]:verify")
        self.write("src/a.py")
        self.assertEqual(self.run_hook("Stop", last_assistant_message="").exit_code, 2)
        record = self.audit()[-1]
        self.assertEqual((record["outcome"], record["gates"], record["bypassed"]), ("blocked", ["test-first"], ["verify"]))

    def test_skill_that_changes_files_counts_after_its_own_change(self):
        self.run_hook("UserPromptSubmit", prompt="go")
        self.run_hook("SubagentStop", agent_type="reviewer")
        self.write("src/a.py")
        self.run_hook("PostToolUse", tool_name="Skill", tool_input={"skill": "test-first"})
        self.run_hook("SubagentStop", agent_type="reviewer")
        self.assertEqual(self.run_hook("Stop", last_assistant_message="").exit_code, 0)

    def test_block_counter_resets_on_new_prompt(self):
        for _ in range(2):
            self.run_hook("UserPromptSubmit", prompt="go")
            self.write("a.txt", content=str(_))
            codes = [self.run_hook("Stop", last_assistant_message="").exit_code for _ in range(4)]
            self.assertEqual(codes, [2, 2, 2, 0])

    def test_sessions_in_the_same_project_are_isolated(self):
        self.run_hook("UserPromptSubmit", session="A", prompt="go")
        self.run_hook("UserPromptSubmit", session="B", prompt="go")
        self.write("a.txt")
        self.run_hook("PostToolUse", session="B", tool_name="Write", tool_input={})
        self.run_hook("SubagentStop", session="B", agent_type="reviewer")
        self.assertEqual(self.run_hook("Stop", session="A", last_assistant_message="").exit_code, 2)
        self.assertEqual(self.run_hook("Stop", session="B", last_assistant_message="").exit_code, 0)

    def test_sanitized_session_ids_do_not_collide(self):
        store = StateStore.from_env(self.env, self.root)
        self.assertNotEqual(store.session_file("a/b"), store.session_file("a:b"))

    def test_log_stores_hashes_not_full_snapshots(self):
        self.run_hook("UserPromptSubmit", prompt="go")
        self.write("a.txt")
        self.run_hook("PostToolUse", tool_name="Write", tool_input={})
        lines = StateStore.from_env(self.env, self.root).session_file("s1").read_text(encoding="utf-8").splitlines()
        event = json.loads(lines[-1])
        self.assertEqual(event["kind"], "snapshot")
        self.assertNotIn("snapshot", event)
        self.assertIn("hash", event)

    def test_handler_exception_respects_on_error(self):
        boom = mock.patch.object(hooks_module, "analyze", side_effect=RuntimeError("boom"))
        with boom:
            result = self.run_hook("Stop", last_assistant_message="")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("boom", result.stderr)

        closed = GATES.replace('language = "en"', 'language = "en"\non_error = "closed"')
        (self.root / ".claude" / "gatekeeper.toml").write_text(closed, encoding="utf-8")
        with mock.patch.object(hooks_module, "take_snapshot", side_effect=RuntimeError("boom")):
            self.assertEqual(self.run_hook("Stop", last_assistant_message="").exit_code, 2)
            self.assertEqual(self.run_hook("UserPromptSubmit", prompt="go").exit_code, 0)
            self.assertEqual(self.run_hook("PostToolUse", tool_name="Write", tool_input={}).exit_code, 0)

    def test_invalid_config_is_shown_to_the_user_on_prompt(self):
        (self.root / ".claude" / "gatekeeper.toml").write_text("gates = [", encoding="utf-8")
        result = self.run_hook("UserPromptSubmit", prompt="go")
        self.assertEqual(result.exit_code, 0)
        output = json.loads(result.stdout)
        self.assertIn("gatekeeper.toml", output["systemMessage"])
        self.assertIn("gatekeeper.toml", output["hookSpecificOutput"]["additionalContext"])

    def test_invalid_config_closed_gives_up_after_default_max_blocks(self):
        (self.root / ".claude" / "gatekeeper.toml").write_text('on_error = "closed"\nbad = 1\n', encoding="utf-8")
        codes = [self.run_hook("Stop", last_assistant_message="").exit_code for _ in range(4)]
        self.assertEqual(codes, [2, 2, 2, 0])

    def test_audit_rotates_when_large(self):
        store = StateStore.from_env(self.env, self.root)
        with mock.patch.object(state_module, "AUDIT_MAX_BYTES", 200):
            for _ in range(10):
                store.audit({"outcome": "passed", "pad": "x" * 50})
        self.assertTrue(store.audit_file.with_suffix(".jsonl.1").exists())
        self.assertLess(store.audit_file.stat().st_size, 400)


class CliFindingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "project"
        self.home = base / "home"
        make_repo(self.root, None)
        self.home.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_data_dir_prefers_env_then_installed_plugin(self):
        self.assertEqual(default_data_dir({"CLAUDE_PLUGIN_DATA": "X"}, self.home), Path("X"))
        installed = self.home / ".claude" / "plugins" / "data" / "gatekeeper-gatekeeper"
        installed.mkdir(parents=True)
        self.assertEqual(default_data_dir({}, self.home), installed)

    def test_stats_reads_the_given_data_dir(self):
        data = Path(self.tmp.name) / "plugin-data"
        StateStore(data, self.root).audit({"outcome": "blocked", "gates": ["verify"]})
        self.assertIn("blocked: 1", stats(self.root, data))

    def test_doctor_names_missing_skill_and_finds_present_one(self):
        init(self.root, "verifiers")
        config = self.root / ".claude" / "gatekeeper.toml"
        config.write_text(
            config.read_text(encoding="utf-8").replace('final_text = "Summary"', 'final_text = "Summary"\nskills = ["test-first"]'),
            encoding="utf-8",
        )
        _, out = doctor(self.root, self.home)
        self.assertIn("skill 'test-first'", out)
        skill = self.root / ".claude" / "skills" / "test-first"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("x", encoding="utf-8")
        _, out = doctor(self.root, self.home)
        self.assertNotIn("skill 'test-first'", out)


if __name__ == "__main__":
    unittest.main()


class SecondRoundTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "project"
        make_repo(self.root, GATES)
        self.env = {"CLAUDE_PROJECT_DIR": str(self.root), "CLAUDE_PLUGIN_DATA": str(base / "data")}

    def tearDown(self):
        self.tmp.cleanup()

    def test_folder_patterns_without_trailing_slash_cover_contents(self):
        self.assertTrue(path_matches("node_modules/x/y.js", "node_modules"))
        self.assertTrue(path_matches("a/node_modules/x.js", "node_modules"))
        self.assertTrue(path_matches("build/a.o", "/build"))
        self.assertFalse(path_matches("sub/build/a.o", "/build"))
        self.assertFalse(path_matches("node_modules_old/x.js", "node_modules"))

    def test_config_in_another_encoding_is_a_config_error(self):
        (self.root / ".claude" / "gatekeeper.toml").write_text(GATES, encoding="utf-16")
        result = handle("Stop", {"session_id": "s", "last_assistant_message": ""}, self.env)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("gatekeeper.toml", result.stderr)

    def test_audit_rotation_failure_does_not_lose_the_record(self):
        store = StateStore.from_env(self.env, self.root)
        store.audit({"outcome": "passed"})
        with mock.patch.object(state_module, "AUDIT_MAX_BYTES", 1), mock.patch.object(
            state_module.os, "replace", side_effect=PermissionError("locked")
        ):
            store.audit({"outcome": "blocked"})
        self.assertEqual([r["outcome"] for r in store.audit_records()], ["passed", "blocked"])
