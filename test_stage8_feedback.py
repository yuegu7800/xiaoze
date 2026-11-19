"""Stage-eight selection/summary behavior; fictional stores, no model requests."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_activity as fixtures
from character import SCENE_BY_ID
from local_chat import LocalChat
from request_evidence import evidence_summary, evidence_text


class Stage8FeedbackTests(unittest.TestCase):
    setUp = fixtures.ActivityTests.setUp
    open = fixtures.ActivityTests.open
    data = fixtures.ActivityTests.data
    say = fixtures.ActivityTests.say
    topic = fixtures.ActivityTests.topic
    evaluate = fixtures.ActivityTests.evaluate

    def stable(self):
        c = self.core
        return (self.path.read_bytes(), deepcopy(c.memory.state), deepcopy(c._request_evidence),
                c._epoch, c._paused, c._inflight, deepcopy(c.last_trace), c.last_notice)

    def test_summary_is_same_turn_counts_not_recall_or_model_understanding(self):
        self.say("我不喝咖啡")
        self.say("拿铁怎么样")
        row = self.core.feedback_view("local-owner")[-1]
        before = self.stable()
        with patch("character_service.recall", side_effect=AssertionError("no recall")), \
             patch("requests.post", side_effect=AssertionError("no HTTP")):
            summary = evidence_summary(row)
            detail = evidence_text(row)
        self.assertEqual(self.stable(), before)
        self.assertIn("直接长期资料：0条", summary)
        self.assertIn("直接近期用户原话：1条", summary)
        self.assertIn("纳入助手历史：1条", summary)
        self.assertIn("近期同源", summary)
        self.assertIn("不能证明", summary)
        for forbidden in (row["id"], "不喝咖啡", "合成回复", "long_injected", "recent_duplicate"):
            self.assertNotIn(forbidden, summary)
        self.assertIn(row["id"], detail)
        self.assertIn("recent_duplicate", detail)

    def test_summary_distinguishes_direct_topics_from_inherited_dependencies(self):
        self.topic()
        self.say("小说纸船结尾")
        row = self.core.feedback_view("local-owner")[-1]
        ev = row["evidence"]
        self.assertEqual(len(ev["channels"]["topics"]), 1)
        summary = evidence_summary(row)
        self.assertIn("直接人工话题：1个版本", summary)
        # The source distinction comes from the assembly, not assistant wording.
        self.say("换成工作话题")
        row = self.core.feedback_view("local-owner")[-1]
        self.assertEqual(row["evidence"]["channels"]["topics"], {})
        self.assertEqual(len(row["evidence"]["channels"]["assistant_only_dependencies"]["topics"]), 1)
        self.assertIn("话题版本1个", evidence_summary(row))

    def test_missing_legacy_and_adoption_have_honest_empty_states(self):
        self.say()
        row = self.core.feedback_view("local-owner")[-1]
        row.update(evidence=None, trace=None)
        missing = evidence_summary(row)
        self.assertIn("没有可用的累计来源", missing)
        self.assertIn("不足以判断", missing)
        self.assertNotIn("原话祖先0", missing)
        row["trace"] = {"version": 1, "source_ids": ["fiction-root"], "record_ids": [], "topics": {}}
        self.assertIn("可能的原话祖先", evidence_summary(row))
        row["kind"] = "local_assistant"
        self.assertIn("本地采用", evidence_summary(row))
        self.assertIn("没有本次直接取数清单", evidence_summary(row))
        self.assertIn("所选轮次已淘汰", evidence_summary(None))

    def test_summary_explains_static_outcomes_without_private_words(self):
        self.say()
        row = self.core.feedback_view("local-owner")[-1]
        outcomes = ["long_injected", "recent_duplicate", "temporary_override", "count_budget",
                    "char_budget", "disabled", "irrelevant", "pending", "expired", "suppressed",
                    "corrected", "superseded", "source_budget"]
        row["evidence"]["records"] = [dict(id="fiction-id-" + str(i), status="active", outcome=o,
            basis="boundary" if i == 0 else "state_or_insufficient") for i, o in enumerate(outcomes)]
        summary = evidence_summary(row)
        for word in ("临时", "数量预算", "字符预算", "开关关闭", "有限规则依据不足", "待确认", "更正", "固定边界"):
            self.assertIn(word, summary)
        for code in outcomes:
            self.assertNotIn(code, summary)
        self.assertNotIn("fiction-id", summary)
        self.assertLess(len(summary), 2200)

    def app_stub(self, turn_id, person="owner", scene="local-owner"):
        binding = dict(core=self.core, scene=SCENE_BY_ID[scene], turn_id=turn_id,
                       speaker_id=person, mode_label="虚构流程模拟")
        return SimpleNamespace(core=self.core, scene=SCENE_BY_ID[scene],
            _feedback_actions={"action": binding}, status=Mock(), show_feedback=Mock())

    def test_action_uses_original_speaker_and_rejects_removed_or_wrong_scope(self):
        self.say("群内第一条", "local-group", "friend-lin")
        self.say("群内第二条", "local-group", "friend-qiao")
        first = self.core.feedback_view("local-group")[0]["id"]
        app = self.app_stub(first, "friend-lin", "local-group")
        before = self.stable()
        LocalChat._open_reply_feedback(app, "action")
        self.assertEqual(app.show_feedback.call_args.kwargs["speaker_id"], "friend-lin")
        self.assertEqual(app.show_feedback.call_args.kwargs["turn_id"], first)
        self.assertEqual(self.stable(), before)
        app.show_feedback.reset_mock()
        app.scene = SCENE_BY_ID["local-lin"]
        LocalChat._open_reply_feedback(app, "action")
        app.show_feedback.assert_not_called()
        app.scene = SCENE_BY_ID["local-group"]
        # A different Core can contain the same stored IDs after reopening;
        # that does not authorize reusing the old UI binding.
        app.core = self.open()
        LocalChat._open_reply_feedback(app, "action")
        app.show_feedback.assert_not_called()
        app.core = self.core
        app._feedback_actions["action"]["speaker_id"] = "owner"
        LocalChat._open_reply_feedback(app, "action")
        app.show_feedback.assert_not_called()
        app._feedback_actions["action"]["speaker_id"] = "friend-lin"
        self.core.reset_scene("local-group")
        LocalChat._open_reply_feedback(app, "action")
        app.show_feedback.assert_not_called()
        app._feedback_actions.clear()
        LocalChat._open_reply_feedback(app, "action")
        app.show_feedback.assert_not_called()

    def test_existing_panel_with_unsaved_feedback_cannot_change_core_or_scene(self):
        self.say()
        panel = SimpleNamespace(core=self.core, scene=SCENE_BY_ID["local-owner"],
            window=Mock(), hint=Mock(), has_unsaved=Mock(return_value=True))
        app = SimpleNamespace(core=self.core, scene=SCENE_BY_ID["local-group"],
            feedback_panel=panel, status=Mock(), feedback_mode_label=Mock(return_value="虚构模式"))
        result = LocalChat.show_feedback(app)
        self.assertIs(result, panel)
        panel.window.destroy.assert_not_called()
        self.assertIn("未保存", panel.hint.set.call_args.args[0])
        self.assertEqual(panel.scene.id, "local-owner")

    def test_read_summary_while_generating_does_not_cancel_or_write(self):
        self.say("先前的虚构对话")
        before = self.stable()
        def complete(_messages):
            inside = self.stable()
            row = self.core.feedback_view("local-owner")[-1]
            with patch("character_service.recall", side_effect=AssertionError("no second recall")):
                evidence_summary(row)
                evidence_text(row)
            self.assertEqual(self.stable(), inside)
            self.assertTrue(self.core._inflight)
            self.assertEqual(self.path.read_bytes(), before[0])
            return "虚构在途回复"
        self.core.exchange("local-owner", "owner", "新的虚构问题", complete)
        self.assertEqual(len(self.core.feedback_view("local-owner")), 2)

    def test_action_read_failure_is_noncommitting_and_does_not_switch(self):
        key = self.say()
        app = self.app_stub(key)
        before = self.stable()
        with patch.object(self.core, "feedback_view", side_effect=RuntimeError("fiction read failure")):
            LocalChat._open_reply_feedback(app, "action")
        app.show_feedback.assert_not_called()
        self.assertIn("暂时无法核对", app.status.set.call_args.args[0])
        self.assertEqual(self.stable(), before)


if __name__ == "__main__":
    unittest.main()
