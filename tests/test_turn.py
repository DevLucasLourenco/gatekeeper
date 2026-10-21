import unittest

from gatekeeper.turn import analyze

CLEAN: dict = {}
EDITED = {"src/a.py": [" M", 1, 10]}
EDITED_AGAIN = {"src/a.py": [" M", 2, 12]}


def turn(prompt="do it", snapshot=CLEAN):
    return {"kind": "turn", "prompt": prompt, "snapshot": snapshot}


def snap(snapshot):
    return {"kind": "snapshot", "snapshot": snapshot}


def subagent(name):
    return {"kind": "subagent", "name": name}


class AnalyzeTest(unittest.TestCase):
    def test_no_change(self):
        facts = analyze([turn(), snap(CLEAN), subagent("qa")], CLEAN)
        self.assertFalse(facts.changed)
        self.assertEqual(facts.changed_paths, frozenset())
        self.assertEqual(facts.subagents_after_last_change, {"qa"})

    def test_change_then_verifier(self):
        facts = analyze([turn(), snap(EDITED), subagent("qa")], EDITED)
        self.assertTrue(facts.changed)
        self.assertEqual(facts.changed_paths, {"src/a.py"})
        self.assertEqual(facts.subagents_after_last_change, {"qa"})

    def test_verifier_before_last_change_does_not_count(self):
        facts = analyze([turn(), snap(EDITED), subagent("qa"), snap(EDITED_AGAIN)], EDITED_AGAIN)
        self.assertEqual(facts.subagents_after_last_change, set())
        self.assertEqual(facts.subagents_in_turn, {"qa"})

    def test_change_seen_only_by_final_snapshot(self):
        facts = analyze([turn(), subagent("qa")], EDITED)
        self.assertTrue(facts.changed)
        self.assertEqual(facts.subagents_after_last_change, set())

    def test_unchanged_snapshots_do_not_move_last_change(self):
        facts = analyze([turn(), snap(EDITED), subagent("qa"), snap(EDITED)], EDITED)
        self.assertEqual(facts.subagents_after_last_change, {"qa"})

    def test_reverted_edit_is_not_a_net_change(self):
        facts = analyze([turn(), snap(EDITED), snap(CLEAN)], CLEAN)
        self.assertFalse(facts.changed)

    def test_deleted_and_new_paths_count(self):
        before = {"a.txt": ["??", 1, 1]}
        after = {"b.txt": ["??", 1, 1]}
        facts = analyze([turn(snapshot=before)], after)
        self.assertEqual(facts.changed_paths, {"a.txt", "b.txt"})

    def test_missing_snapshots_are_ignored(self):
        facts = analyze([turn(snapshot=None), snap(None)], None)
        self.assertFalse(facts.changed)

    def test_without_turn_event_first_snapshot_is_baseline(self):
        facts = analyze([snap(EDITED), subagent("qa")], EDITED)
        self.assertFalse(facts.changed)
        self.assertEqual(facts.prompt, "")

    def test_skills_blocks_and_prompt(self):
        events = [
            turn(prompt="/test-first now"),
            {"kind": "skill", "name": "review"},
            {"kind": "block"},
            {"kind": "block"},
        ]
        facts = analyze(events, CLEAN)
        self.assertEqual(facts.prompt, "/test-first now")
        self.assertEqual(facts.skills_in_turn, {"test-first", "review"})
        self.assertEqual(facts.blocks, 2)

    def test_unknown_or_malformed_events_are_skipped(self):
        facts = analyze([turn(), {"kind": "mystery"}, {"no": "kind"}, "junk", subagent(3)], CLEAN)
        self.assertEqual(facts.subagents_in_turn, set())


if __name__ == "__main__":
    unittest.main()
