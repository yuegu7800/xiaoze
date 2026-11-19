"""Stage four: fictional dialogue provenance and explicit local adoption."""
from copy import deepcopy
from datetime import timedelta
import json
import threading
from unittest.mock import Mock, patch
import unittest

from character import Cancelled
import test_activity as fixtures


class DialogueTests(unittest.TestCase):
    setUp = fixtures.ActivityTests.setUp
    open = fixtures.ActivityTests.open
    data = fixtures.ActivityTests.data
    say = fixtures.ActivityTests.say
    topic = fixtures.ActivityTests.topic
    evaluate = fixtures.ActivityTests.evaluate
    pending = fixtures.ActivityTests.pending

    def test_multihop_forget_cleans_replies_but_keeps_independent_user_text(self):
        self.say("我喜欢银色纸灯")
        record = self.data()["memories"][0]
        self.core.exchange("local-owner", "owner", "那你怎么看", lambda _: "我记得你喜欢银色纸灯")
        self.core.exchange("local-owner", "owner", "另外讨论虚构音乐", lambda _: "银色纸灯适合这个故事")
        preview = self.core.forget_preview("local-owner", record["id"])
        self.assertEqual(preview["assistant_messages"], 3)
        self.core.forget_memory("local-owner", record["id"])
        self.core = self.open()
        self.assertNotIn("银色纸灯", self.path.read_text(encoding="utf-8"))
        self.assertIn("另外讨论虚构音乐", self.path.read_text(encoding="utf-8"))
        self.assertNotIn("银色纸灯", json.dumps(self.core.messages("local-owner", "owner", "继续"), ensure_ascii=False))
        self.say("我喜欢银色纸灯")
        self.assertTrue(self.data()["memories"])

    def test_local_adoption_has_no_fake_user_and_is_idempotent_after_restart(self):
        self.topic()
        draft_id = self.evaluate()["draft_id"]
        before = deepcopy(self.data()["memories"])
        event_id = self.core.adopt_draft("local-owner", draft_id, target="owner", purpose="private")
        event = self.data()["turns"][-1]
        self.assertEqual(event["kind"], "local_assistant")
        self.assertEqual(event["text"], "")
        self.assertEqual(event["id"], event_id)
        self.assertEqual(self.data()["memories"], before)
        self.core = self.open()
        self.assertEqual(self.core.adopt_draft("local-owner", draft_id, target="owner", purpose="private"), event_id)
        self.assertEqual(len(self.data()["turns"]), 2)
        payload = self.core.messages("local-owner", "owner", "我想试试这个方向")
        self.assertEqual(payload[-2]["role"], "assistant")
        self.assertEqual(payload[-2]["content"], event["reply"])

    def test_suppression_correction_expiry_and_long_only_delete_have_distinct_effects(self):
        for action in ("suppress", "edit", "delete", "expiry"):
            with self.subTest(action=action):
                self.core.reset_scene("local-owner")
                self.say("今天累了" if action == "expiry" else "我喜欢银色纸灯")
                record = self.data()["memories"][0]
                self.core.exchange("local-owner", "owner", "接着说", lambda _: "ECHO_OF_OLD_FACT")
                if action == "expiry":
                    self.now += timedelta(days=1)
                elif action == "edit":
                    self.core.change_memory("local-owner", record["id"], action, "喜欢木制小船")
                else:
                    self.core.change_memory("local-owner", record["id"], action)
                payload = json.dumps(self.core.messages("local-owner", "owner", "继续"), ensure_ascii=False)
                self.assertNotIn("ECHO_OF_OLD_FACT", payload)
                disk = self.path.read_text(encoding="utf-8")
                if action == "delete":
                    self.assertNotIn("ECHO_OF_OLD_FACT", disk)
                    self.assertIn("我喜欢银色纸灯", disk)  # Long-only deletion retains raw self-report.
                else:
                    self.assertIn("ECHO_OF_OLD_FACT", disk)  # Suppression isn't deletion.

    def test_propagation_survives_source_window_eviction_and_new_user_evidence_is_independent(self):
        self.say("我喜欢银色纸灯")
        record_id = self.data()["memories"][0]["id"]
        for i in range(14):
            self.core.exchange("local-owner", "owner", f"独立用户文字{i}", lambda _: "银色纸灯的转述")
        self.assertNotIn("我喜欢银色纸灯", json.dumps(self.data()["turns"], ensure_ascii=False))
        self.core.forget_memory("local-owner", record_id)
        self.assertNotIn("银色纸灯", self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(self.data()["turns"]), 12)
        self.say("我喜欢银色纸灯")
        self.assertEqual(len(self.data()["memories"]), 1)

    def test_budget_omits_complete_assistant_context_before_generation_not_provenance(self):
        omitted = False
        for i in range(140):
            def complete(messages):
                nonlocal omitted
                count = sum(m["role"] == "assistant" for m in messages)
                if i > 20 and count < 12:
                    omitted = True
                return f"虚构回复{i}"
            self.core.exchange("local-owner", "owner", f"虚构话语{i}", complete)
            for turn in self.data()["turns"]:
                self.assertLessEqual(len(turn["reply_trace"]["source_ids"]), 128)
        self.assertTrue(omitted)

    def test_unfit_base_memory_budget_refuses_before_api_and_keeps_store(self):
        self.say("我喜欢简短回复")
        data = self.data()
        original = data["memories"][0]
        data["memories"] = []
        for i in range(3):
            record = deepcopy(original)
            record.update(id=f"wide-record-{i}", slot=f"wide-{i}", content="预算测试资料",
                          source_ids=[f"wide-{i}-{j}" for j in range(64)])
            record["evidence"][0]["source_id"] = record["source_ids"][0]
            data["memories"].append(record)
        self.core._save("local-owner", data)
        before = self.path.read_bytes()
        complete = Mock()
        with self.assertRaisesRegex(ValueError, "预算"):
            self.core.exchange("local-owner", "owner", "记得资料吗", complete)
        complete.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_expiry_or_mutation_during_ordinary_generation_cancels_commit(self):
        self.say("今天累了")
        before = self.path.read_bytes()
        def expire(_messages):
            self.now += timedelta(days=1)
            return "迟到的回复"
        with self.assertRaises(Cancelled):
            self.core.exchange("local-owner", "owner", "继续", expire)
        self.assertEqual(self.path.read_bytes(), before)
        self.say("我喜欢银色纸灯")
        record = self.data()["memories"][-1]
        def mutate(_messages):
            self.core.change_memory("local-owner", record["id"], "suppress")
            return "失效的回复"
        with self.assertRaises(Cancelled):
            self.core.exchange("local-owner", "owner", "记得喜好吗", mutate)
        self.assertNotIn("失效的回复", self.path.read_text(encoding="utf-8"))

    def test_v2_read_only_explicit_upgrade_unknown_replies_and_failure(self):
        from core import atomic_json
        self.say("我喜欢银色纸灯")
        old = deepcopy(self.core.memory.state)
        old["version"] = 2
        for turn in old["scenes"]["local-owner"]["turns"]:
            turn.pop("reply_trace")
            turn["reply"] = "LEGACY_ECHO银色纸灯"
        atomic_json(self.path, old)
        before = self.path.read_bytes()
        self.core = self.open()
        self.assertEqual(self.path.read_bytes(), before)
        complete = Mock()
        with self.assertRaisesRegex(RuntimeError, "只读"):
            self.core.exchange("local-owner", "owner", "继续", complete)
        complete.assert_not_called()
        with patch("memory_store.atomic_json", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError):
                self.core.upgrade_v2()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.core.memory.state["version"], 2)
        self.core.upgrade_v2()
        self.assertIn("LEGACY_ECHO", self.path.read_text(encoding="utf-8"))
        self.assertNotIn("LEGACY_ECHO", json.dumps(self.core.messages("local-owner", "owner", "继续")))
        self.core.forget_memory("local-owner", self.data()["memories"][0]["id"])
        self.assertNotIn("LEGACY_ECHO", self.path.read_text(encoding="utf-8"))

    def test_adopted_message_survives_draft_expiry_but_source_delete_scrubs_it(self):
        self.say("我喜欢银色纸灯")
        record = self.data()["memories"][0]
        self.topic()
        draft_id = self.evaluate(lambda _: '{"decision":"draft","text":"银色纸灯和纸船聊哪一个？"}')["draft_id"]
        event_id = self.core.adopt_draft("local-owner", draft_id, target="owner", purpose="private")
        self.core.feedback("local-owner", event_id, ["接得自然"], "银色纸灯")
        self.now += timedelta(minutes=31)
        self.core = self.open()
        self.assertIn("银色纸灯和纸船", json.dumps(self.core.messages("local-owner", "owner", "继续"), ensure_ascii=False))
        self.core.forget_memory("local-owner", record["id"])
        self.assertNotIn("银色纸灯", self.path.read_text(encoding="utf-8"))
        self.assertFalse(self.data()["feedback"])

    def test_adoption_rechecks_pause_ticket_scope_expiry_and_file_failure(self):
        self.topic()
        draft_id = self.evaluate()["draft_id"]
        args = {"target": "owner", "purpose": "private"}
        before = self.path.read_bytes()
        ticket = self.core.ticket()
        self.core.invalidate()
        with self.assertRaises(Cancelled):
            self.core.adopt_draft("local-owner", draft_id, ticket=ticket, **args)
        self.core.pause()
        with self.assertRaises(Cancelled):
            self.core.adopt_draft("local-owner", draft_id, **args)
        self.core.resume()
        with self.assertRaises(ValueError):
            self.core.adopt_draft("local-owner", draft_id, target="owner", purpose="public")
        with patch("memory_store.atomic_json", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError):
                self.core.adopt_draft("local-owner", draft_id, **args)
        self.assertEqual(self.path.read_bytes(), before)
        self.now += timedelta(minutes=31)
        with self.assertRaises(ValueError):
            self.core.adopt_draft("local-owner", draft_id, **args)

    def test_group_adoption_target_and_following_speaker_stay_distinct(self):
        self.topic("local-group", "friend-lin", target="all")
        draft_id = self.evaluate(scene="local-group", target="all", purpose="group")["draft_id"]
        with self.assertRaises(ValueError):
            self.core.adopt_draft("local-group", draft_id, target="friend-lin", purpose="group")
        self.core.adopt_draft("local-group", draft_id, target="all", purpose="group")
        complete = Mock(return_value="合成回复")
        self.core.exchange("local-group", "friend-qiao", "我来接话", complete)
        messages = complete.call_args.args[0]
        self.assertEqual(json.loads(messages[-2]["content"])["target"], "all")
        self.assertEqual(json.loads(messages[-1]["content"])["speaker_id"], "friend-qiao")
        relation = json.loads(messages[0]["content"].split("【本机当前关系】")[1])
        self.assertEqual(relation["relation"], "friend")

    def test_pending_model_blocks_adoption_and_second_request_without_network(self):
        self.topic()
        draft_id = self.evaluate()["draft_id"]
        started, release = threading.Event(), threading.Event()
        def complete(_messages):
            started.set()
            release.wait(3)
            return "合成回复"
        errors = []
        def work():
            try:
                self.core.exchange("local-owner", "owner", "新上下文", complete)
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=work)
        worker.start()
        try:
            self.assertTrue(started.wait(3))
            with self.assertRaises(Cancelled):
                self.core.adopt_draft("local-owner", draft_id, target="owner", purpose="private")
            other = Mock()
            with self.assertRaises(Cancelled):
                self.core.exchange("local-owner", "owner", "第二请求", other)
            other.assert_not_called()
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(errors)
        with self.assertRaises(ValueError):
            self.core.adopt_draft("local-owner", draft_id, target="owner", purpose="private")

    def test_topic_edit_same_clock_tick_invalidates_generated_context(self):
        topic_id = self.topic()
        self.core.exchange("local-owner", "owner", "小说纸船结尾", lambda _: "OLD_TOPIC_GENERATION")
        self.core.change_topic("local-owner", topic_id, "edit", title="小说纸船", direction="新的结尾方向")
        self.assertNotIn("OLD_TOPIC_GENERATION", json.dumps(self.core.messages("local-owner", "owner", "继续")))

    def test_public_draft_never_becomes_local_message_and_unknown_trace_is_preserved_on_error(self):
        from core import atomic_json
        self.core.enable_activity("local-owner")
        self.core.set_public_material("local-owner", "PUBLIC_ONLY_MATERIAL")
        draft_id = self.evaluate(purpose="public")["draft_id"]
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.core.adopt_draft("local-owner", draft_id, target="owner", purpose="public")
        self.assertEqual(self.path.read_bytes(), before)
        self.say("虚构用户发言")
        bad = deepcopy(self.core.memory.state)
        bad["scenes"]["local-owner"]["turns"][0]["reply_trace"]["version"] = 999
        atomic_json(self.path, bad)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "原文件保留"):
            self.open()
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
