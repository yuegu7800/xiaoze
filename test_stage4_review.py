"""Review regressions; temporary fictional records and mocked completions only."""
from copy import deepcopy
from datetime import timedelta
import json
import unittest
from unittest.mock import Mock
import test_activity as fixtures


class Stage4ReviewTests(unittest.TestCase):
    setUp = fixtures.ActivityTests.setUp
    open = fixtures.ActivityTests.open
    data = fixtures.ActivityTests.data
    say = fixtures.ActivityTests.say
    topic = fixtures.ActivityTests.topic
    evaluate = fixtures.ActivityTests.evaluate

    def test_manual_correction_can_be_used_in_real_exchange_and_new_topic(self):
        self.say("我喜欢简短回复")
        original = self.data()["memories"][0]
        self.core.change_memory("local-owner", original["id"], "edit", "回答可以详细展开")
        corrected = next(r for r in self.data()["memories"] if r["status"] == "active")
        complete = Mock(return_value="按更正后的资料继续聊")
        self.core.exchange("local-owner", "owner", "你记得我的偏好吗", complete)
        request = json.dumps(complete.call_args.args[0], ensure_ascii=False)
        self.assertIn("回答可以详细展开", request)
        self.assertNotIn("我喜欢简短回复", request)
        self.core.enable_activity("local-owner")
        topic = self.core.mark_topic("local-owner", corrected["id"], "详细展开", "回答方式", "更细致地聊", source_kind="memory", confirm=True)
        self.assertEqual(next(t for t in self.data()["activity"]["topics"] if t["id"] == topic)["status"], "active")
        draft_id = self.evaluate()["draft_id"]
        self.assertTrue(draft_id)
        self.core.forget_memory("local-owner", corrected["id"])
        self.assertNotIn("按更正后的资料继续聊", self.path.read_text(encoding="utf-8"))

    def test_group_other_members_temporary_exception_does_not_cancel_exchange(self):
        self.say("我不喝咖啡", "local-group", "friend-lin")
        self.say("今天喝咖啡", "local-group", "friend-lin")
        complete = Mock(return_value="小乔的合成回复")
        self.core.exchange("local-group", "friend-qiao", "聊聊小说", complete)
        request = json.dumps(complete.call_args.args[0], ensure_ascii=False)
        self.assertNotIn("我不喝咖啡", request)
        self.assertIn("今天喝咖啡", request)

    def test_deleting_derived_topic_removes_other_topic_candidate_and_feedback(self):
        first = self.topic()
        self.core.exchange("local-owner", "owner", "小说纸船结尾", lambda _: "DERIVED_TOPIC_SENTINEL")
        second_source = self.say("虚构音乐配色还没决定")
        second = self.core.mark_topic("local-owner", second_source, "音乐配色", "音乐配色", "讨论配色", confirm=True)
        self.now += timedelta(seconds=1)
        self.core.change_topic("local-owner", second, "activate")
        draft = self.evaluate(lambda _: '{"decision":"draft","text":"DERIVED_TOPIC_SENTINEL 和音乐"}')["draft_id"]
        self.core.draft_feedback("local-owner", draft, ["话题硬转"], "DERIVED_TOPIC_SENTINEL")
        self.core.change_topic("local-owner", first, "delete")
        self.assertNotIn("DERIVED_TOPIC_SENTINEL", self.path.read_text(encoding="utf-8"))

    def test_topic_reopen_same_clock_does_not_resurrect_old_reply(self):
        topic = self.topic()
        self.core.exchange("local-owner", "owner", "小说纸船结尾", lambda _: "OLD_TOPIC_REPLY")
        self.core.change_topic("local-owner", topic, "pause")
        self.core.change_topic("local-owner", topic, "activate")
        self.assertNotIn("OLD_TOPIC_REPLY", json.dumps(self.core.messages("local-owner", "owner", "继续")))

    def test_v1_trace_loads_without_rewrite_and_propagates_conservatively(self):
        from core import atomic_json
        self.say("虚构用户文字")
        saved = deepcopy(self.core.memory.state)
        trace = saved["scenes"]["local-owner"]["turns"][0]["reply_trace"]
        trace["version"] = 1
        trace.pop("raw_source_ids")
        atomic_json(self.path, saved)
        before = self.path.read_bytes()
        self.core = self.open()
        self.assertEqual(self.path.read_bytes(), before)
        self.say("后来独立的虚构发言")
        new_trace = self.data()["turns"][-1]["reply_trace"]
        self.assertEqual(new_trace["version"], 2)
        self.assertTrue(set(trace["source_ids"]).issubset(new_trace["raw_source_ids"]))

    def test_display_failure_does_not_stop_completion_poll_or_confuse_saved_reply(self):
        import queue
        from local_chat import LocalChat
        self.say("已经提交的虚构发言")
        app = LocalChat.__new__(LocalChat)
        app.events = queue.Queue()
        app.request_number, app.draft_version = 1, 2
        app.closed, app.busy = False, True
        app.window, app.status, app.input = Mock(), Mock(), Mock()
        app.draft_changed, app.update_controls = Mock(), Mock()
        app.render = Mock(side_effect=RuntimeError("synthetic display failure"))
        app.events.put((1, "done", "已提交回复", self.core, 1, "旧草稿"))
        app.poll()
        self.assertFalse(app.busy)
        app.window.after.assert_called_once()
        app.update_controls.assert_called_once()
        self.assertIn("已保存", app.status.set.call_args.args[0])
        app.input.delete.assert_not_called()
        app.render = Mock()
        app.events.put((1, "error", "合成失败", self.core, 0, ""))
        app.poll()
        self.assertEqual(app.last_error, "error")
        self.assertEqual(len(self.data()["turns"]), 1)
