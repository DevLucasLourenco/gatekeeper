import unittest

from gatekeeper.turn import UNKNOWN_COMMIT, analyze, is_automatic_prompt, snapshot_hash

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


def tree(head, paths=None, committed=None):
    return {"head": head, "paths": paths or {}, "committed": {} if committed is None else committed}


def at(snapshot):
    """A snapshot event as the hooks log it: only the hash and the HEAD."""
    return {"kind": "snapshot", "hash": snapshot_hash(snapshot), "head": snapshot["head"]}


class CommitTest(unittest.TestCase):
    START = tree("h1")
    DIRTY = tree("h1", {"src/a.py": [" M", 5, 10]})
    COMMITTED = tree("h2", committed={"src/a.py": [5, 10]})

    def test_commit_of_a_clean_start_is_a_change(self):
        facts = analyze([turn(snapshot=self.START)], self.COMMITTED)
        self.assertTrue(facts.changed)
        self.assertEqual(facts.changed_paths, {"src/a.py"})

    def test_commit_of_content_already_seen_does_not_move_the_last_change(self):
        events = [turn(snapshot=self.START), at(self.DIRTY), subagent("qa"), at(self.COMMITTED)]
        facts = analyze(events, self.COMMITTED)
        self.assertEqual(facts.changed_paths, {"src/a.py"})
        self.assertEqual(facts.subagents_after_last_change, {"qa"})

    def test_new_commit_with_new_content_moves_the_last_change(self):
        amended = tree("h3", committed={"src/a.py": [6, 11]})
        events = [turn(snapshot=self.START), at(self.DIRTY), subagent("qa"), at(self.COMMITTED), at(amended)]
        facts = analyze(events, amended)
        self.assertEqual(facts.subagents_after_last_change, set())

    def test_same_head_and_clean_tree_is_no_change(self):
        facts = analyze([turn(snapshot=self.START), at(self.START)], self.START)
        self.assertFalse(facts.changed)

    def test_commit_without_paths_is_no_change(self):
        message_only = tree("h2")
        facts = analyze([turn(snapshot=self.START), at(message_only)], message_only)
        self.assertFalse(facts.changed)

    def test_unknown_commit_paths_still_move_the_last_change(self):
        unknown = tree("h2", committed=None)
        unknown["committed"] = None
        facts = analyze([turn(snapshot=self.START), subagent("qa"), at(unknown)], unknown)
        self.assertEqual(facts.subagents_after_last_change, set())

    def test_unknown_commit_paths_count_as_a_change(self):
        # diff-tree failed (start commit gone after gc, timeout): the commit must not vanish.
        unknown = tree("h2")
        unknown["committed"] = None
        facts = analyze([turn(snapshot=self.START)], unknown)
        self.assertTrue(facts.changed)
        self.assertIn(UNKNOWN_COMMIT, facts.changed_paths)

    def test_unknown_commit_paths_without_a_new_head_are_no_change(self):
        same = tree("h1")
        same["committed"] = None
        self.assertFalse(analyze([turn(snapshot=self.START)], same).changed)

    def test_committing_a_deletion_keeps_the_same_content(self):
        deleted = tree("h1", {"gone.py": [" D", None, None]})
        committed = tree("h2", committed={"gone.py": [None, None]})
        self.assertEqual(snapshot_hash(deleted), snapshot_hash(committed))

    def test_staging_alone_does_not_move_the_last_change(self):
        staged = tree("h1", {"src/a.py": ["M ", 5, 10]})
        self.assertEqual(snapshot_hash(self.DIRTY), snapshot_hash(staged))


def launch(name, agent_id):
    return {"kind": "launch", "name": name, "id": agent_id}


class PendingSubagentTest(unittest.TestCase):
    def test_background_subagent_still_running_is_pending(self):
        facts = analyze([turn(), launch("qa", "a1")], CLEAN)
        self.assertEqual(facts.pending_subagents, {"qa"})

    def test_finished_background_subagent_is_not_pending(self):
        events = [turn(), launch("qa", "a1"), {"kind": "subagent", "name": "qa", "id": "a1"}]
        self.assertEqual(analyze(events, CLEAN).pending_subagents, set())

    def test_another_subagent_of_the_same_type_does_not_finish_it(self):
        events = [turn(), launch("qa", "a1"), {"kind": "subagent", "name": "qa", "id": "a2"}]
        self.assertEqual(analyze(events, CLEAN).pending_subagents, {"qa"})

    def test_without_ids_the_type_is_matched(self):
        events = [turn(), launch("qa", None), subagent("qa")]
        self.assertEqual(analyze(events, CLEAN).pending_subagents, set())


class AutomaticPromptTest(unittest.TestCase):
    def test_harness_envelopes(self):
        automatic = [
            '<agent-message from="a54279b9cae1f90b3">\n[Subagent hand-back] report',
            'Another Claude session sent a message:\n<agent-message from="a1">\nhello',
            "<task-notification>\n<task-id>a1</task-id>\n<status>completed</status>\n</task-notification>",
            "  \n<task-notification>\n",
        ]
        for prompt in automatic:
            with self.subTest(prompt=prompt):
                self.assertTrue(is_automatic_prompt(prompt))

    def test_user_prompts(self):
        for prompt in ("do it", "/review", "see <task-notification> below", "", "<agent-message"):
            with self.subTest(prompt=prompt):
                self.assertFalse(is_automatic_prompt(prompt))


if __name__ == "__main__":
    unittest.main()
