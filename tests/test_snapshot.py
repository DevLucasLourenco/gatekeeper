import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from gatekeeper.snapshot import parse_porcelain, take_snapshot


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


class ParsePorcelainTest(unittest.TestCase):
    def test_entries_and_renames(self):
        raw = " M src/a.py\0?? new file.txt\0R  b.py\0old_b.py\0D  gone.py\0"
        self.assertEqual(
            parse_porcelain(raw),
            [(" M", "src/a.py"), ("??", "new file.txt"), ("R ", "b.py"), ("R ", "old_b.py"), ("D ", "gone.py")],
        )

    def test_empty_and_truncated(self):
        self.assertEqual(parse_porcelain(""), [])
        self.assertEqual(parse_porcelain("x"), [])


class TakeSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init", "-q")
        (self.root / "tracked.txt").write_text("v1", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_repo_is_empty(self):
        self.assertEqual(take_snapshot(self.root, ()), {})

    def test_modified_and_untracked(self):
        (self.root / "tracked.txt").write_text("v2", encoding="utf-8")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "new.txt").write_text("x", encoding="utf-8")
        snapshot = take_snapshot(self.root, ())
        self.assertEqual(set(snapshot), {"tracked.txt", "sub/new.txt"})
        self.assertEqual(snapshot["sub/new.txt"][0], "??")

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
        self.assertEqual(set(snapshot), {".gitignore"})

    def test_deleted_file(self):
        (self.root / "tracked.txt").unlink()
        self.assertEqual(take_snapshot(self.root, ())["tracked.txt"][1:], [None, None])

    def test_not_a_repo_returns_none(self):
        with tempfile.TemporaryDirectory() as other:
            self.assertIsNone(take_snapshot(Path(other), ()))


if __name__ == "__main__":
    unittest.main()
