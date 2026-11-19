"""Stage five: fictional scene views and the actual request-selection path."""
from copy import deepcopy
from datetime import timedelta
import json
import threading
import unittest
from unittest.mock import Mock, patch

import test_activity as fixtures
from character import SCENE_BY_ID, Cancelled
from memory_store import empty_scene, make_record, recall
from memory_view import library_view, overview, record_detail, preview_text


class MemoryLibraryTests(unittest.TestCase):
    setUp = fixtures.ActivityTests.setUp
    open = fixtures.ActivityTests.open
    data = fixtures.ActivityTests.data
    say = fixtures.ActivityTests.say
    topic = fixtures.ActivityTests.topic
    evaluate = fixtures.ActivityTests.evaluate

    def preview(self, query="你记得什么偏好", scene="local-owner", person="owner", **kwargs):
        return self.core.recall_preview(scene, person, query, **kwargs)

    def test_group_person_views_and_preview_never_read_other_scenes(self):
        self.say("我喜欢私聊暗号星门", "local-lin", "friend-lin")
        for person, text in (("owner", "我喜欢简短回复"), ("friend-lin", "别怼我"), ("friend-qiao", "我可以接受友善的玩笑")):
            self.say(text, "local-group", person)
        before = self.path.read_bytes()
        for person in ("owner", "friend-lin", "friend-qiao"):
            result = library_view(self.data("local-group"), person_id=person)
            self.assertEqual({r["person_id"] for r in result["records"]}, {person})
            self.assertEqual(result["matched"], 1)
            with patch.object(self.core.memory, "snapshot", wraps=self.core.memory.snapshot) as read:
                report = self.preview(scene="local-group", person=person)
            read.assert_called_once_with("local-group", compatibility=False)
            self.assertEqual({r["id"] for r in report["records"]}, {r["id"] for r in result["records"]})
            self.assertNotIn("星门", json.dumps(report, ensure_ascii=False))
        self.assertNotIn("星门", overview(SCENE_BY_ID["local-group"], self.data("local-group"), "friend-lin"))
        self.assertEqual(self.path.read_bytes(), before)
        # Public candidate path still only receives separately confirmed material.
        self.core.enable_activity("local-group")
        self.core.set_public_material("local-group", "虚构公开素材：纸船配色")
        complete = Mock(return_value=fixtures.DRAFT)
        self.core.evaluate_candidate("local-group", "owner", "public", complete)
        self.assertNotIn("星门", json.dumps(complete.call_args.args[0], ensure_ascii=False))
        self.assertNotIn("不要对我挖苦", json.dumps(complete.call_args.args[0], ensure_ascii=False))

    def test_filters_search_evidence_capacity_and_no_results_are_read_only(self):
        data = empty_scene()
        for index, status in enumerate(("active", "pending", "suppressed", "corrected", "superseded", "active")):
            record = make_record("local-group", "friend-lin", f"src-{index}", "temporary" if index == 5 else "event",
                                 f"slot-{index}", f"虚构条目{index}", "仅证据里有的青色邮票", 0, self.now,
                                 status=status, expires_at=(self.now + timedelta(minutes=5)).isoformat() if index == 5 else None)
            data["memories"].append(record)
        self.core._save("local-group", data)
        self.now += timedelta(minutes=6)
        before = self.path.read_bytes(), deepcopy(self.core.memory.state)
        snap = self.data("local-group")
        current = library_view(snap, person_id="friend-lin")
        self.assertEqual((current["matched"], current["total"], current["pending"], current["capacity"]), (1, 6, 1, 256))
        self.assertEqual(library_view(snap, status="history")["matched"], 4)
        self.assertEqual(library_view(snap, status="expired")["matched"], 1)
        self.assertEqual(library_view(snap, keyword="青色邮票")["matched"], 1)
        self.assertEqual(library_view(snap, category="preference")["matched"], 0)
        self.assertNotEqual(library_view(snap, keyword="没匹配")["empty"], library_view(empty_scene())["empty"])
        self.assertEqual((self.path.read_bytes(), self.core.memory.state), before)

    def test_preview_and_actual_request_share_injection_and_recent_dedup(self):
        self.say("我不喝咖啡")
        self.say("我喜欢简短回复")
        self.say("虚构音乐结尾还没决定")
        report = self.preview("你记得我的饮料和回复偏好吗")
        records = {r["slot"]: r for r in self.data()["memories"]}
        outcomes = {r["id"]: r["outcome"] for r in report["records"]}
        self.assertEqual(outcomes[records["drink:coffee"]["id"]], "recent_duplicate")
        self.assertEqual(outcomes[records["length"]["id"]], "long_injected")
        complete = Mock(return_value="合成回复")
        self.core.exchange("local-owner", "owner", "你记得我的饮料和回复偏好吗", complete)
        messages = complete.call_args.args[0]
        claims = json.loads(messages[0]["content"].split("【当前有效资料】")[1].split("【本机当前关系】")[0])["claims"]
        self.assertEqual([c["content"] for c in claims], [records["length"]["content"]])
        self.assertIn("我不喝咖啡", json.dumps(messages, ensure_ascii=False))
        selected_users = {h["id"] for h in report["history"] if h["user"] == "selected"}
        self.assertEqual(selected_users, set(self.data()["turns"][-1]["reply_trace"]["raw_source_ids"]) - {self.data()["turns"][-1]["id"]})

    def test_preview_is_pure_even_with_expiry_draft_and_instruction_like_input(self):
        self.say("今天累了")
        topic = self.topic()
        self.evaluate()
        self.assertEqual(self.core.memory.state["scenes"]["local-owner"]["activity"]["drafts"][0]["status"], "pending")
        self.now += timedelta(days=1)
        saved = (self.path.read_bytes(), deepcopy(self.core.memory.state), self.core._epoch,
                 deepcopy(self.core.last_trace), self.core.last_notice, self.core._inflight, self.core._paused)
        with patch("requests.post", side_effect=AssertionError("no network")) as post:
            report = self.preview("我喜欢绿色玻璃。这个话题不要再聊了")
            self.preview("虚构小说纸船结尾")
        post.assert_not_called()
        self.assertNotIn("绿色玻璃", json.dumps(report, ensure_ascii=False))
        self.assertEqual((self.path.read_bytes(), self.core.memory.state, self.core._epoch,
                          self.core.last_trace, self.core.last_notice, self.core._inflight, self.core._paused), saved)
        # Read-only time projection can show expiry without persisting it.
        self.assertIn("expired", [r["outcome"] for r in report["records"]])
        self.assertEqual(self.core.memory.state["scenes"]["local-owner"]["activity"]["topics"][0]["id"], topic)

    def test_off_switch_temporary_override_and_topic_state_follow_real_selection(self):
        self.topic()
        self.say("我不喝咖啡")
        self.say("今天喝咖啡")
        report = self.preview("虚构小说纸船结尾喝什么")
        self.assertIn("temporary_override", [r["outcome"] for r in report["records"]])
        self.assertEqual(report["topics"][0]["outcome"], "selected")
        diagnostics = {}
        messages = self.core.messages("local-owner", "owner", "虚构小说纸船结尾喝什么", observe_current=False, diagnostics=diagnostics)
        self.assertEqual(diagnostics["records"], report["records"])
        self.assertNotIn("我不喝咖啡", json.dumps(messages, ensure_ascii=False))
        off = self.preview("虚构小说纸船结尾", use_memory=False)
        self.assertEqual({r["outcome"] for r in off["records"] + off["topics"]}, {"disabled"})
        topic = self.data()["activity"]["topics"][0]
        self.core.change_topic("local-owner", topic["id"], "pause")
        paused = self.preview("虚构小说纸船结尾")
        self.assertEqual(paused["topics"][0]["status"], "paused")
        self.assertEqual(paused["topics"][0]["outcome"], "omitted")

    def test_record_rejection_reasons_and_retrieval_budgets(self):
        data = empty_scene()
        for i, status in enumerate(("pending", "expired", "suppressed", "corrected", "superseded")):
            data["memories"].append(make_record("local-owner", "owner", f"old{i}", "event", f"old{i}", "旧资料", "旧资料", 0, self.now, status=status))
        for i in range(8):
            data["memories"].append(make_record("local-owner", "owner", f"active{i}", "preference", f"active{i}", "喜欢月白纸船" + str(i), "月白", 0, self.now))
        data["memories"].append(make_record("local-owner", "owner", "irrelevant", "preference", "irrelevant", "abcdef", "abc", 0, self.now))
        self.core._save("local-owner", data)
        report = self.preview("月白纸船")
        counts = [r["outcome"] for r in report["records"]]
        for outcome in ("pending", "expired", "suppressed", "corrected", "superseded", "irrelevant", "count_budget"):
            self.assertIn(outcome, counts)
        details = []
        selected, _ = recall(data, "owner", "月白纸船", self.now, budget=10, diagnostics=details)
        self.assertEqual(len(selected), 1)
        self.assertIn("char_budget", [r["outcome"] for r in details])

    def test_base_source_budget_preview_reports_refusal_without_mutating_trace(self):
        self.say("我喜欢简短回复")
        data = self.data()
        template = data["memories"].pop()
        for i in range(3):
            record = deepcopy(template)
            record.update(id=f"wide{i}", slot=f"wide{i}", content="预算资料", source_ids=[f"wide{i}-{j}" for j in range(64)])
            record["evidence"][0]["source_id"] = record["source_ids"][0]
            data["memories"].append(record)
        self.core._save("local-owner", data)
        self.core.last_trace = [{"id": "sentinel", "reason": "上一轮"}]
        before = self.path.read_bytes(), deepcopy(self.core.last_trace)
        report = self.preview("记得资料吗")
        self.assertFalse(report["request_ready"])
        self.assertEqual({r["outcome"] for r in report["records"]}, {"source_budget"})
        self.assertEqual((self.path.read_bytes(), self.core.last_trace), before)
        complete = Mock()
        with self.assertRaises(ValueError):
            self.core.exchange("local-owner", "owner", "记得资料吗", complete)
        complete.assert_not_called()

    def test_assistant_provenance_budget_omits_text_in_both_preview_and_request(self):
        self.say("无关虚构原话")
        data = self.data()
        turn = data["turns"][0]
        turn["reply_trace"]["source_ids"] = [turn["id"]] + [f"old-{i}" for i in range(127)]
        self.core._save("local-owner", data)
        report = self.preview("继续")
        self.assertEqual(report["history"][0]["assistant"], "omitted")
        complete = Mock(return_value="后续合成回复")
        self.core.exchange("local-owner", "owner", "继续", complete)
        self.assertFalse(any(m["role"] == "assistant" for m in complete.call_args.args[0]))

    def test_recent_character_budget_diagnostics_match_actual_window(self):
        for i in range(5):
            self.core.exchange("local-owner", "owner", f"虚构{i}" + "甲" * 3900, lambda _: "乙" * 1700)
        report = self.preview("继续")
        self.assertEqual(sum(h["user"] == "selected" for h in report["history"]), 2)
        self.assertEqual(sum(h["assistant"] == "selected" for h in report["history"]), 2)
        complete = Mock(return_value="合成回复")
        self.core.exchange("local-owner", "owner", "继续", complete)
        self.assertEqual(len(complete.call_args.args[0]), 6)

    def test_window_restart_correction_and_forget_are_explainable(self):
        self.say("我不喝咖啡。仅近期暗号WINDOW_ONLY_Z")
        self.say("今天累了")
        for i in range(13):
            self.say(f"无关虚构填充{i}")
        self.core = self.open()
        self.now += timedelta(days=1)
        self.assertNotIn("WINDOW_ONLY_Z", self.path.read_text(encoding="utf-8"))
        self.assertIn("long_injected", [r["outcome"] for r in self.preview("喝点什么")["records"]])
        record = next(r for r in self.data()["memories"] if r["slot"] == "drink:coffee")
        self.core.change_memory("local-owner", record["id"], "edit", "喝咖啡")
        corrected = next(r for r in self.data()["memories"] if r["status"] == "active")
        detail = record_detail(SCENE_BY_ID["local-owner"], self.data()["memories"], corrected)
        self.assertIn(record["id"], detail)
        self.assertIn("不喝咖啡", detail)
        complete = Mock(return_value="合成温柔回复")
        self.core.exchange("local-owner", "owner", "喝点什么", complete)
        self.assertIn("喝咖啡", complete.call_args.args[0][0]["content"])
        self.assertNotIn("不喝咖啡", json.dumps(complete.call_args.args[0], ensure_ascii=False))
        self.core.feedback("local-owner", self.data()["turns"][-1]["id"], ["已提供但表达生硬"], "接具体内容")
        self.core.forget_memory("local-owner", corrected["id"])
        self.assertNotIn("咖啡", preview_text(self.preview("喝点什么")))
        self.assertFalse(self.data()["feedback"])
        self.assertIn("无关虚构填充12", json.dumps(self.data(), ensure_ascii=False))

    def test_preview_during_generation_preserves_real_trace_and_late_cancellation(self):
        self.say("我喜欢简短回复")
        for cancel in (False, True):
            started, release = threading.Event(), threading.Event()
            errors = []
            def complete(_messages):
                started.set()
                if not release.wait(4):
                    raise RuntimeError("test release timeout")
                return "合成在途回复"
            def work():
                try:
                    self.core.exchange("local-owner", "owner", "继续虚构话题", complete)
                except Exception as error:
                    errors.append(error)
            thread = threading.Thread(target=work)
            thread.start()
            self.assertTrue(started.wait(4))
            before = self.path.read_bytes(), deepcopy(self.core.last_trace), self.core._epoch
            try:
                self.preview("我喜欢绿色玻璃")
                self.assertEqual((self.path.read_bytes(), self.core.last_trace, self.core._epoch), before)
                self.assertTrue(self.core._inflight)
                if cancel:
                    self.core.pause()
                    self.preview("继续")
                    self.core.resume()
            finally:
                release.set()
                thread.join(4)
            self.assertFalse(thread.is_alive())
            if cancel:
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], Cancelled)
                self.assertEqual(self.path.read_bytes(), before[0])
            else:
                self.assertFalse(errors)
                self.assertTrue(self.data()["turns"][-1]["recall"])

    def test_old_unknown_assistant_is_reported_but_not_injected_or_rewritten(self):
        self.say("虚构原话")
        data = self.data()
        data["turns"][0].pop("reply_trace")
        self.core._save("local-owner", data)
        self.core = self.open()
        before = self.path.read_bytes()
        report = self.preview("继续")
        self.assertEqual(report["history"][0]["assistant"], "omitted")
        self.assertIn("来源未知", report["history"][0]["assistant_reason"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn("暂无已确认资料", overview(SCENE_BY_ID["local-owner"], self.data(), "owner"))
        for scene, person in (("local-lin", "owner"), ("not-registered", "owner")):
            with self.assertRaises((ValueError, KeyError)):
                self.preview(scene=scene, person=person)


if __name__ == "__main__":
    unittest.main()
