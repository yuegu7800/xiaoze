"""Review regressions: fictional stores and UI failure boundaries, no HTTP."""
from copy import deepcopy
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import test_activity as fixtures
from feedback_panel import FeedbackPanel
from local_chat import LocalChat
from request_evidence import evidence_summary


class Value:
    def __init__(self, callback):
        self.value, self.callback = "", callback

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        self.callback()


class Stage8ReviewTests(unittest.TestCase):
    setUp = fixtures.ActivityTests.setUp
    open = fixtures.ActivityTests.open
    data = fixtures.ActivityTests.data
    say = fixtures.ActivityTests.say

    def panel(self):
        panel = FeedbackPanel.__new__(FeedbackPanel)
        panel.core, panel.scene = self.core, self.core.scenes["local-owner"]
        panel.window, panel.choice, panel.hint, panel.save_button = Mock(), Mock(), Mock(), Mock()
        panel.submitted = panel._refreshing = panel._editing_guard = False
        panel.editing_turn_id = None
        panel.rows, panel.labels, panel._last_view = {}, {}, None
        panel.chosen = Value(panel._selection_changed)
        panel.note = Mock(get=Mock(return_value=""))
        panel.tags = {}
        panel._render_evidence = Mock()
        panel.refresh(initial=True)
        return panel

    def test_partial_list_paint_does_not_rebind_same_time_labels_after_eviction(self):
        ids = [self.say("虚构同秒填充" + str(i), use_memory=False) for i in range(12)]
        panel = self.panel()
        panel.select_turn(ids[1])
        self.say("虚构新轮次", use_memory=False)
        before = self.path.read_bytes(), deepcopy(self.core.memory.state), self.core._epoch
        with patch.object(panel.choice, "configure", side_effect=tk.TclError("fiction list paint failure")):
            with self.assertRaises(tk.TclError):
                panel.refresh()
        panel.refresh()
        self.assertEqual(panel.labels.get(panel.chosen.get()), ids[1])
        self.assertEqual((self.path.read_bytes(), self.core.memory.state, self.core._epoch), before)

    def test_submitted_panel_is_not_reused_for_a_different_feedback(self):
        first, second = self.say("虚构第一轮"), self.say("虚构第二轮")
        panel = SimpleNamespace(core=self.core, scene=self.core.scenes["local-owner"],
            submitted=True, window=Mock(), select_turn=Mock(), has_unsaved=Mock(return_value=False), hint=Mock())
        app = SimpleNamespace(core=self.core, scene=panel.scene, feedback_panel=panel,
            status=Mock(), feedback_mode_label=Mock(return_value="虚构流程模拟"))
        with patch("feedback_panel.FeedbackPanel") as constructor:
            result = LocalChat.show_feedback(app, turn_id=second, speaker_id="owner")
        panel.select_turn.assert_not_called()
        panel.window.destroy.assert_called_once()
        self.assertIs(result, constructor.return_value)
        self.assertEqual(constructor.call_args.kwargs["turn_id"], second)

    def test_same_turn_self_report_dedup_is_not_claimed_as_prior_history(self):
        self.say("我不喝咖啡")
        row = self.core.feedback_view("local-owner")[-1]
        self.assertEqual(row["evidence"]["channels"]["recent_users"], [])
        self.assertTrue(any(r["outcome"] == "recent_duplicate" for r in row["evidence"]["records"]))
        summary = evidence_summary(row)
        self.assertIn("直接近期用户原话：0条", summary)
        self.assertIn("近期或本轮输入", summary)
        self.assertNotIn("原话已从近期通道提供", summary)


if __name__ == "__main__":
    unittest.main()
