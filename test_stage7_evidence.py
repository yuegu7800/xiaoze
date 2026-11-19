"""Actual exchange input evidence, fictional state, mock completions only."""
from copy import deepcopy
from datetime import timedelta
import json
import threading
import unittest
from unittest.mock import Mock, patch

import test_activity as fixtures
from character import Cancelled
from core import CompletionResult
from dialogue import empty_trace, topic_signature
from memory_store import make_record, recall
from request_evidence import evidence_text
from test_stage6_recall import claims


class Stage7EvidenceTests(unittest.TestCase):
    setUp = fixtures.ActivityTests.setUp
    open = fixtures.ActivityTests.open
    data = fixtures.ActivityTests.data
    say = fixtures.ActivityTests.say
    topic = fixtures.ActivityTests.topic
    evaluate = fixtures.ActivityTests.evaluate

    def view(self, scene="local-owner"):
        return self.core.feedback_view(scene)

    def stable(self):
        return (self.path.read_bytes(), deepcopy(self.core.memory.state),
                deepcopy(self.core._request_evidence), self.core._epoch, self.core._paused,
                self.core._inflight, deepcopy(self.core.last_trace), self.core.last_notice)

    def record(self, content="不喝咖啡", slot="coffee"):
        data = self.data()
        record = make_record("local-owner", "owner", "fiction-old-root", "preference", slot,
                             content, content[:160], 0, self.now, source="local_maintainer")
        data["memories"].append(record)
        self.core._save("local-owner", data)
        return record["id"]

    def test_same_assembly_exact_channels_and_single_recall(self):
        topic = self.topic()
        record = self.record()
        reports, requests = [], []
        original = self.core.messages
        def assemble(*args, **kwargs):
            result = original(*args, **kwargs)
            reports.append(deepcopy(kwargs["diagnostics"]))
            requests.append(deepcopy(result))
            return result
        complete = Mock(return_value="虚构回复，不代表模型注意到了咖啡")
        with patch.object(self.core, "messages", side_effect=assemble), patch("character_service.recall", wraps=recall) as called:
            self.core.exchange("local-owner", "owner", "来杯拿铁，再聊小说纸船结尾", complete)
        self.assertEqual(called.call_count, 1)
        self.assertEqual(len(reports), 1)
        row = self.view()[-1]
        ev = row["evidence"]
        self.assertEqual(ev["channels"], reports[0]["channels"])
        self.assertEqual(ev["dependencies"], row["trace"])
        self.assertEqual(ev["turn_id"], row["id"])
        self.assertEqual(ev["channels"]["long_memory"], [record])
        self.assertEqual(ev["channels"]["topics"], {topic: topic_signature(self.data()["activity"]["topics"][0])})
        self.assertEqual(claims(requests[0])[0]["content"], "不喝咖啡")
        self.assertIn("人工保留的待续话题", requests[0][0]["content"])
        before_turns = {t["id"]: t for t in self.data()["turns"] if t["id"] != row["id"]}
        users = [json.loads(m["content"])["text"] for m in requests[0][1:-1] if m["role"] == "user"]
        self.assertEqual(users, [before_turns[i]["text"] for i in ev["channels"]["recent_users"]])
        assistants = [m["content"] for m in requests[0] if m["role"] == "assistant"]
        self.assertEqual(assistants, [before_turns[i]["reply"] for i in ev["channels"]["assistants"]])
        self.assertEqual(requests[0], complete.call_args.args[0])

    def test_inherited_dependencies_do_not_claim_semantic_repetition(self):
        record = self.record()
        self.say("latte?")
        self.say("今天有点工作烦恼")
        ev = self.view()[-1]["evidence"]
        self.assertEqual(ev["channels"]["long_memory"], [])
        self.assertEqual(ev["channels"]["assistant_only_dependencies"]["record_ids"], [record])
        text = evidence_text(self.view()[-1])
        self.assertIn("不能证明", text)
        self.assertNotIn("不喝咖啡", text)

    def test_payload_free_nonpersistent_bounded_codes_and_deepcopy(self):
        secret = "fiction_private_canary_" + "x" * 260
        self.record(secret)
        self.say(secret)
        row = self.view()[-1]
        encoded = json.dumps(row["evidence"], ensure_ascii=False)
        for forbidden in (secret, secret[:24], "合成回复", "persona.txt", "quote", "content", "api_key"):
            self.assertNotIn(forbidden, encoded)
        self.assertTrue(all(len(r["basis"]) <= 24 for r in row["evidence"]["records"]))
        row["evidence"]["channels"]["recent_users"].append("mutating-a-view")
        self.assertNotIn("mutating-a-view", json.dumps(self.core._request_evidence))
        turn_id = row["id"]
        self.core.feedback("local-owner", turn_id, ["已提供但表达生硬"], "虚构期望：接具体内容")
        self.assertIsNotNone(self.view()[-1]["evidence"])
        for marker in ("request-selection-1", "assistant_only_dependencies", "recall_version", self.core._evidence_core_id):
            self.assertNotIn(marker, self.path.read_text(encoding="utf-8"))
        self.assertIsNone(self.open().feedback_view("local-owner")[-1]["evidence"])

    def test_scope_turn_speaker_core_and_switches_are_bound(self):
        for scene, person in (("local-owner", "owner"), ("local-lin", "friend-lin"),
                              ("local-group", "friend-lin"), ("local-group", "friend-qiao"), ("local-group", "owner")):
            self.say("虚构场景问题", scene, person, use_memory=False, use_topics=False)
        for scene in ("local-owner", "local-lin", "local-group"):
            for row in self.view(scene):
                ev = row["evidence"]
                self.assertEqual((ev["scene_id"], ev["person_id"], ev["turn_id"]), (scene, row["speaker_id"], row["id"]))
                self.assertFalse(ev["memory_enabled"])
                self.assertFalse(ev["topics_enabled"])
        self.assertNotEqual(self.open()._evidence_core_id, self.core._evidence_core_id)
        with self.assertRaises(KeyError):
            self.view("unregistered-scene")

    def test_failure_format_io_and_pause_never_publish_or_replace(self):
        self.say("之前的虚构成功")
        old = deepcopy(self.core._request_evidence)
        for complete in (Mock(side_effect=RuntimeError("offline failure")), lambda _: "", lambda _: "x" * 1801,
                         lambda _: CompletionResult("不完整", "length")):
            with self.subTest(complete=complete), self.assertRaises((RuntimeError, ValueError)):
                self.core.exchange("local-owner", "owner", "失败的虚构问题", complete)
            self.assertEqual(old, self.core._request_evidence)
        with patch.object(self.core.memory, "save_scene", side_effect=OSError("synthetic IO")), self.assertRaises(OSError):
            self.say("虚构IO失败")
        self.assertEqual(old, self.core._request_evidence)
        def pause(_messages):
            self.core.pause()
            return "迟到的合成回复"
        with self.assertRaises(Cancelled):
            self.core.exchange("local-owner", "owner", "虚构暂停", pause)
        self.assertEqual(old, self.core._request_evidence)

    def test_read_is_pure_and_does_not_cancel_inflight(self):
        self.say("之前的虚构成功")
        entered, release = threading.Event(), threading.Event()
        errors = []
        def complete(_messages):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test release timeout")
            return "合成完成"
        def work():
            try:
                self.core.exchange("local-owner", "owner", "在途虚构问题", complete)
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=work)
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            before = self.stable()
            with patch("character_service.recall", side_effect=AssertionError("no recall")), patch("requests.post", side_effect=AssertionError("no network")):
                evidence_text(self.view()[-1])
            self.assertEqual(before, self.stable())
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(self.view()), 2)
        self.assertIsNotNone(self.view()[-1]["evidence"])

    def test_maintenance_delete_and_topic_edit_clear_without_cross_scene_loss(self):
        rid = self.record()
        self.say("来杯拿铁")
        self.say("另一位的虚构问题", "local-lin", "friend-lin")
        self.core.change_memory("local-owner", rid, "edit", "喜欢奶茶")
        self.assertFalse(any(row["evidence"] for row in self.view()))
        self.assertIsNotNone(self.view("local-lin")[-1]["evidence"])
        self.say("奶茶怎么样")
        edited = next(r for r in self.data()["memories"] if r["status"] == "active")
        self.core.forget_memory("local-owner", edited["id"])
        self.assertEqual(self.core._request_evidence["local-owner"], {})
        self.assertNotIn("喜欢奶茶", json.dumps(self.view(), ensure_ascii=False))
        topic = self.topic()
        self.say("小说纸船结尾")
        self.core.change_topic("local-owner", topic, "pause")
        self.assertFalse(any(row["evidence"] for row in self.view()))
        self.core.reset_scene("local-owner")
        self.assertEqual(self.view(), [])

    def test_expiry_read_only_hides_then_write_prunes_and_late_work_cancels(self):
        self.say("今天想安静一点")
        self.say("虚构日常问候")
        self.now += timedelta(days=1)
        before = self.stable()
        self.assertFalse(any(row["evidence"] for row in self.view()))
        self.assertEqual(before, self.stable())
        self.say("跨日虚构问题")
        self.assertEqual(len(self.core._request_evidence["local-owner"]), 1)
        self.say("今天想安静一点")
        old = deepcopy(self.core._request_evidence)
        def expire(_messages):
            self.now += timedelta(days=1)
            return "到期后合成回复"
        with self.assertRaises(Cancelled):
            self.core.exchange("local-owner", "owner", "虚构问题", expire)
        self.assertEqual(old, self.core._request_evidence)

    def test_pending_work_is_not_published_after_edit_or_delete(self):
        for action in ("edit", "delete", "suppress"):
            with self.subTest(action=action):
                self.core.reset_scene("local-owner")
                rid = self.record()
                self.say("来杯拿铁")
                def maintain(_messages):
                    self.core.change_memory("local-owner", rid, action, "喜欢奶茶" if action == "edit" else None)
                    return "维护后的迟到合成回复"
                with self.assertRaises(Cancelled):
                    self.core.exchange("local-owner", "owner", "喝点什么", maintain)
                self.assertEqual(self.core._request_evidence["local-owner"], {})

    def test_recent_bound_close_and_legacy_adoption_fallback(self):
        for i in range(15):
            self.say(f"虚构填窗{i}")
        self.assertEqual(len(self.core._request_evidence["local-owner"]), 12)
        self.assertEqual(set(self.core._request_evidence["local-owner"]), {r["id"] for r in self.view()})
        self.core.clear_request_evidence()
        self.assertFalse(any(r["evidence"] for r in self.view()))
        self.assertIn("不足以判断各通道", evidence_text(self.view()[-1]))
        topic = self.topic()
        result = self.evaluate()
        event = self.core.adopt_draft("local-owner", result["draft_id"], target="owner", purpose="private")
        row = next(r for r in self.view() if r["id"] == event)
        self.assertIsNone(row["evidence"])
        self.assertIn("本地采用/候选没有本次直接取数清单", evidence_text(row))

    def test_source_budget_refusal_and_whole_assistant_omission(self):
        self.say("虚构源问题")
        data = self.data()
        turn = data["turns"][0]
        turn["reply_trace"]["source_ids"] = [turn["id"]] + [f"fiction-{i}" for i in range(127)]
        self.core._save("local-owner", data)
        complete = Mock(return_value="合成回复")
        self.core.exchange("local-owner", "owner", "虚构新问题", complete)
        row = self.view()[-1]
        self.assertEqual(row["evidence"]["channels"]["assistants"], [])
        self.assertFalse(any(m["role"] == "assistant" for m in complete.call_args.args[0]))
        self.assertEqual(len(row["trace"]["source_ids"]), 2)
        self.core.reset_scene("local-owner")
        data = self.data()
        for i in range(3):
            r = make_record("local-owner", "owner", f"wide-{i}-0", "preference", f"wide-{i}",
                            "喜欢咖啡", "咖啡", 0, self.now)
            r["source_ids"] = [f"wide-{i}-{j}" for j in range(64)]
            data["memories"].append(r)
        self.core._save("local-owner", data)
        complete.reset_mock()
        with self.assertRaises(ValueError):
            self.core.exchange("local-owner", "owner", "latte?", complete)
        complete.assert_not_called()
        self.assertEqual(self.view(), [])


if __name__ == "__main__":
    unittest.main()
