"""Actual temporary Tk review paths; fictional data, no external requests."""
from datetime import datetime
from copy import deepcopy
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from local_chat import LocalChat
from memory_rules import LOCAL_TZ


class ReviewTkTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="xiaoze-review8-")
        self.addCleanup(folder.cleanup)
        self.network = patch("requests.post", side_effect=AssertionError("no HTTP")).start()
        self.addCleanup(patch.stopall)
        self.app = LocalChat(data_dir=folder.name)
        self.addCleanup(self.app.close)
        self.core, self.root = self.app.core, self.app.window
        self.core.memory.clock = lambda: datetime(2026, 11, 12, 12, tzinfo=LOCAL_TZ)
        self.root.after_cancel(self.app.poll_after)
        self.app.poll_after = None

    def say(self, text="虚构填充"):
        self.core.exchange("local-owner", "owner", text, lambda _: "合成回复", use_memory=False)
        return self.core.feedback_view("local-owner")[-1]["id"]

    def stable(self):
        c = self.core
        return (c.memory.path.read_bytes(), deepcopy(c.memory.state), deepcopy(c._request_evidence),
                c._epoch, c._paused, c._inflight, deepcopy(c.last_trace), c.last_notice)

    def open_panel(self, key):
        return self.app.show_feedback(turn_id=key, speaker_id="owner")

    def test_failed_list_repaint_keeps_surviving_id_and_clears_removed_draft(self):
        ids = [self.say() for _ in range(12)]
        panel = self.open_panel(ids[1])
        panel.tags["机械追问"].set(True)
        panel.note.insert("1.0", "属于原第二轮的虚构期望")
        old_label = panel.chosen.get()
        self.say()
        before = self.stable()
        configure = panel.choice.configure

        def fail_list(**kwargs):
            if "values" in kwargs:
                raise tk.TclError("fiction list failure")
            return configure(**kwargs)

        with patch.object(panel.choice, "configure", side_effect=fail_list):
            self.app.poll()
            self.assertIn("刷新失败", panel.summary.get("1.0", "end"))
            self.assertIn("刷新失败", panel.diagnosis.get("1.0", "end"))
            self.assertEqual(str(panel.choice.cget("state")), "disabled")
            # A late native variable event with a stale ordinal must not rebind.
            panel.chosen.set(old_label)
            self.assertEqual(panel.selected_turn_id(), ids[1])
        panel.refresh()
        self.assertEqual(panel.labels[panel.chosen.get()], ids[1])
        self.assertEqual(panel.editing_turn_id, ids[1])
        self.assertEqual(self.stable(), before)
        panel.save_button.invoke()
        self.assertEqual(self.core.snapshot("local-owner")["feedback"][-1]["turn_id"], ids[1])

        # Same-time eviction with failure must clear a removed source's note
        # before the failing dropdown redraw, rather than waiting for recovery.
        panel = self.open_panel(ids[1])
        panel.tags["机械追问"].set(True)
        panel.note.insert("1.0", "DELETE_FICTIONAL_UNSAVED_NOTE")
        self.say()
        configure = panel.choice.configure
        before = self.stable()
        with patch.object(panel.choice, "configure", side_effect=fail_list):
            self.app.poll()
            self.assertFalse(panel.note.get("1.0", "end").strip())
            self.assertFalse(any(v.get() for v in panel.tags.values()))
        panel.refresh()
        self.assertIsNone(panel.selected_turn_id())
        self.assertEqual(panel.chosen.get(), "")
        self.assertEqual(self.stable(), before)
        self.network.assert_not_called()

    def test_submitted_close_failure_never_claims_another_turn_was_saved(self):
        first, second = self.say(), self.say()
        panel = self.open_panel(first)
        panel.tags["自然合适"].set(True)
        panel.note.insert("1.0", "仅针对第一轮的虚构期望")
        other_label = next(label for label, key in panel.labels.items() if key == second)
        with patch.object(panel.window, "destroy", side_effect=tk.TclError("fiction close failure")), \
             patch.object(self.core, "feedback", wraps=self.core.feedback) as commit:
            panel.save()
            self.assertTrue(panel.submitted)
            self.assertEqual(str(panel.choice.cget("state")), "disabled")
            self.assertFalse(panel.select_turn(second))
            panel.chosen.set(other_label)
            self.assertEqual(panel.selected_turn_id(), first)
            self.assertEqual(panel.labels[panel.chosen.get()], first)
            self.assertIs(self.open_panel(second), panel)
            self.assertIn("原反馈已提交", panel.hint.get())
            panel.save()
            self.assertEqual(commit.call_count, 1)
        new = self.open_panel(second)
        self.assertIsNot(new, panel)
        self.assertFalse(new.submitted)
        self.assertEqual(new.selected_turn_id(), second)
        self.assertFalse(new.note.get("1.0", "end").strip())
        self.assertFalse(any(v.get() for v in new.tags.values()))
        new.tags["自然合适"].set(True)
        new.save()
        self.assertEqual([f["turn_id"] for f in self.core.snapshot("local-owner")["feedback"]], [first, second])
        self.network.assert_not_called()

    def test_selection_read_failure_clears_both_tabs_and_recovers_without_writing(self):
        first, second = self.say(), self.say()
        panel = self.open_panel(first)
        label = next(label for label, key in panel.labels.items() if key == second)
        before = self.stable()
        with patch.object(self.core, "feedback_view", side_effect=RuntimeError("fiction read failure")):
            panel.chosen.set(label)
            self.assertIn("刷新失败", panel.summary.get("1.0", "end"))
            self.assertIn("刷新失败", panel.diagnosis.get("1.0", "end"))
            self.assertEqual(str(panel.save_button.cget("state")), "disabled")
        panel.refresh()
        self.assertEqual(panel.selected_turn_id(), second)
        self.assertIn(second, panel.diagnosis.get("1.0", "end"))
        self.assertEqual(self.stable(), before)
        self.network.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
