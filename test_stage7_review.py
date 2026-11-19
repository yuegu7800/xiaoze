"""Stage-seven review: feedback ownership/concurrency and legacy uncertainty."""
from copy import deepcopy
import unittest
from unittest.mock import Mock

import test_activity as fixtures
from character import Cancelled
from feedback_panel import FeedbackPanel
from request_evidence import evidence_text


class Stage7ReviewTests(unittest.TestCase):
    setUp = fixtures.ActivityTests.setUp
    open = fixtures.ActivityTests.open
    data = fixtures.ActivityTests.data
    say = fixtures.ActivityTests.say
    topic = fixtures.ActivityTests.topic
    evaluate = fixtures.ActivityTests.evaluate

    def test_feedback_during_exchange_does_not_cancel_or_overwrite(self):
        turn_id = self.say("第一段虚构对话")
        before = self.path.read_bytes()
        def complete(_messages):
            with self.assertRaisesRegex(Cancelled, "生成"):
                self.core.feedback("local-owner", turn_id, ["机械追问"], "稍后保存的虚构期望")
            self.assertEqual(self.path.read_bytes(), before)
            return "第二段合成回复"
        self.core.exchange("local-owner", "owner", "第二段虚构对话", complete)
        self.assertEqual(len(self.data()["turns"]), 2)
        self.assertEqual(self.data()["feedback"], [])
        self.assertIsNotNone(self.core.feedback_view("local-owner")[-1]["evidence"])
        self.core.feedback("local-owner", turn_id, ["机械追问"], "稍后保存的虚构期望")
        self.assertEqual(self.data()["feedback"][0]["turn_id"], turn_id)

    def test_draft_feedback_during_exchange_preserves_request(self):
        self.topic()
        draft_id = self.evaluate()["draft_id"]
        def complete(_messages):
            with self.assertRaisesRegex(Cancelled, "生成"):
                self.core.draft_feedback("local-owner", draft_id, ["打扰"], "虚构反馈")
            return "正常合成回复"
        self.core.exchange("local-owner", "owner", "新的虚构话题", complete)
        self.assertIsNotNone(self.core.feedback_view("local-owner")[-1]["evidence"])

    def test_unsaved_feedback_cannot_be_transferred_by_selection(self):
        first, second = self.say("虚构第一轮"), self.say("虚构第二轮")
        panel = FeedbackPanel.__new__(FeedbackPanel)
        panel.core, panel.scene = self.core, self.core.scenes["local-owner"]
        panel.submitted = False
        panel.editing_turn_id = first
        panel.labels, panel.rows = {"selected": second}, {r["id"]: r for r in self.core.feedback_view("local-owner")}
        panel.chosen = Mock(get=Mock(return_value="selected"))
        panel.tags = {"机械追问": Mock(get=Mock(return_value=True))}
        panel.note = Mock(get=Mock(return_value="针对第一轮的虚构期望"))
        panel.hint, panel.save_button, panel.window = Mock(), Mock(), Mock()
        panel.refresh = Mock()
        before = self.path.read_bytes(), deepcopy(self.core.memory.state)
        panel.save()
        self.assertEqual((self.path.read_bytes(), self.core.memory.state), before)
        self.assertFalse(panel.submitted)
        self.assertIn("另一轮", panel.hint.set.call_args.args[0])

    def test_missing_and_v1_sources_are_not_confirmed_zero_or_raw_counts(self):
        turn_id = self.say()
        row = self.core.feedback_view("local-owner")[-1]
        row["evidence"] = None
        row["trace"] = None
        missing = evidence_text(row)
        self.assertIn("没有可用的累计来源", missing)
        self.assertNotIn("原话祖先 0 个", missing)
        row["trace"] = {"version": 1, "scene_id": "local-owner", "source_ids": [turn_id], "record_ids": [], "topics": {}}
        legacy = evidence_text(row)
        self.assertIn("可能的原话祖先", legacy)
        self.assertIn("旧v1", legacy)


if __name__ == "__main__":
    unittest.main()
