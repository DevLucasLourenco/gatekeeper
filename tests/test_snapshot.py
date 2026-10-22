import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from gatekeeper.snapshot import parse_porcelain, take_snapshot


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def commit_all(root, message="next"):
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message)


class ParsePorcelainTest(unittest.TestCase):
    def test_entries_and_renames(self):
        raw = (
            "# branch.oid abc123\0# branch.head main\0"
            "1 .M N... 100644 100644 100644 aaa aaa src/a.py\0"
            "? new file.txt\0"
            "2 R. N... 100644 100644 100644 bbb bbb R100 b.py\0old_b.py\0"
            "1 D. N... 100644 000000 000000 ccc 000 gone.py\0"
            "u UU N... 100644 100644 100644 100644 d1 d2 d3 both.py\0"
        )
        self.assertEqual(
            parse_porcelain(raw),
            (
                "abc123",
                [
                    (" M", "src/a.py"),
                    ("??", "new file.txt"),
                    ("R ", "b.py"),
                    ("R ", "old_b.py"),
                    ("D ", "gone.py"),
                    ("UU", "both.py"),
                ],
            ),
        )

    def test_repository_without_commits_has_no_head(self):
        self.assertEqual(parse_porcelain("# branch.oid (initial)\0# branch.head main\0? a\0"), (None, [("??", "a")]))

    def test_empty_and_truncated(self):
        self.assertEqual(parse_porcelain(""), (None, []))
        self.assertEqual(parse_porcelain("1 .M x"), (None, []))


class TakeSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init", "-q")
        (self.root / "tracked.txt").write_text("v1", encoding="utf-8")
        commit_all(self.root, "init")

    def tearDown(self):
        self.tmp.cleanup()

    def head(self):
        return subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()

    def test_clean_repo_is_empty(self):
        self.assertEqual(take_snapshot(self.root, ()), {"head": self.head(), "paths": {}, "committed": {}})

    def test_modified_and_untracked(self):
        (self.root / "tracked.txt").write_text("v2", encoding="utf-8")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "new.txt").write_text("x", encoding="utf-8")
        snapshot = take_snapshot(self.root, ())
        self.assertEqual(set(snapshot["paths"]), {"tracked.txt", "sub/new.txt"})
        self.assertEqual(snapshot["paths"]["sub/new.txt"][0], "??")

    def test_content_change_changes_signature(self):
        target = self.root / "tracked.txt"
        target.write_text("v2", encoding="utf-8")
        first = take_snapshot(self.root, ())
        target.write_text("v3 longer", encoding="utf-8")
        os.utime(target, ns=(1, 1))
        self.assertNotEqual(first, take_snapshot(self.root, ()))

    def test_ignore_globs_and_gitignore(self):
        (self.root / ".gitignore").write_text("target/\n", encoding="utf-8")
        (self.root / "target").mkdir()
        (self.root / "target" / "out.bin").write_text("x", encoding="utf-8")
        (self.root / "Cargo.lock").write_text("x", encoding="utf-8")
        snapshot = take_snapshot(self.root, ("*.lock",))
        self.assertEqual(set(snapshot["paths"]), {".gitignore"})

    def test_deleted_file(self):
        (self.root / "tracked.txt").unlink()
        self.assertEqual(take_snapshot(self.root, ())["paths"]["tracked.txt"][1:], [None, None])

    def test_not_a_repo_returns_none(self):
        with tempfile.TemporaryDirectory() as other:
            self.assertIsNone(take_snapshot(Path(other), ()))

    def test_repository_without_commits(self):
        with tempfile.TemporaryDirectory() as other:
            git(other, "init", "-q")
            (Path(other) / "a.txt").write_text("x", encoding="utf-8")
            snapshot = take_snapshot(Path(other), ())
        self.assertIsNone(snapshot["head"])
        self.assertEqual(set(snapshot["paths"]), {"a.txt"})

    def test_commit_since_the_turn_started_lists_its_paths(self):
        start = self.head()
        (self.root / "tracked.txt").write_text("v2", encoding="utf-8")
        (self.root / "new.txt").write_text("n", encoding="utf-8")
        (self.root / "Cargo.lock").write_text("x", encoding="utf-8")
        commit_all(self.root)
        snapshot = take_snapshot(self.root, ("*.lock",), since=start)
        self.assertEqual(snapshot["paths"], {})
        self.assertEqual(set(snapshot["committed"]), {"tracked.txt", "new.txt"})
        self.assertEqual(snapshot["committed"]["new.txt"][1], 1)

    def test_same_head_lists_nothing_committed(self):
        self.assertEqual(take_snapshot(self.root, (), since=self.head())["committed"], {})

    def test_first_commit_of_a_new_repository(self):
        with tempfile.TemporaryDirectory() as other:
            git(other, "init", "-q")
            (Path(other) / "a.txt").write_text("x", encoding="utf-8")
            commit_all(Path(other), "i")
            snapshot = take_snapshot(Path(other), (), since=None)
        self.assertEqual(set(snapshot["committed"]), {"a.txt"})

    def test_unknown_start_commit_is_reported_as_unknown(self):
        self.assertIsNone(take_snapshot(self.root, (), since="0" * 40)["committed"])


if __name__ == "__main__":
    unittest.main()
