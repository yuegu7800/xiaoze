"""Synthetic integration checks for topic provenance and unsent candidates."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from character import CharacterCore, LocalMemory, Cancelled, ApiCompletion
from core import atomic_json
from memory_rules import LOCAL_TZ

ROOT = Path(__file__).resolve().parent
CFG = {"endpoint": "https://example.test/v1/chat/completions", "model": "synthetic", "api_key": "synthetic-only"}
DRAFT = json.dumps({"decision": "draft", "text": "小说里的纸船后来想怎么安排？"}, ensure_ascii=False)
SILENT = '{"decision":"silent","text":""}'


class ActivityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "state-v2.json"
        self.now = datetime(2026, 11, 11, 23, 50, tzinfo=LOCAL_TZ)
        self.core = self.open()

    def open(self):
        return CharacterCore(ROOT / "persona.txt", LocalMemory(self.path, lambda: self.now))

    def data(self, scene="local-owner"):
        return self.core.snapshot(scene)

    def say(self, text="虚构小说纸船结尾还没想好。无关暗号TOPIC_CANARY", scene="local-owner", person="owner", **kwargs):
        self.core.exchange(scene, person, text, lambda _messages: "合成回复", **kwargs)
        return self.data(scene)["turns"][-1]["id"]

    def topic(self, scene="local-owner", person="owner", target=None, *, activate=True, **kwargs):
        source = self.say(scene=scene, person=person)
        self.core.enable_activity(scene)
        topic_id = self.core.mark_topic(scene, source, "小说纸船结尾还没想好", "小说纸船", "聊聊尚未决定的小说结尾", target=target, **kwargs)
        if activate:
            self.core.change_topic(scene, topic_id, "activate")
        return topic_id

    def evaluate(self, completion=None, scene="local-owner", target="owner", purpose="private", **kwargs):
        return self.core.evaluate_candidate(scene, target, purpose, completion or (lambda _: DRAFT), **kwargs)

    def pending(self, scene="local-owner"):
        return [d for d in self.data(scene)["activity"]["drafts"] if d["status"] == "pending"]

    def test_additive_upgrade_is_explicit_preserves_v2_and_invalid_extension_is_not_overwritten(self):
        self.say()
        before = self.path.read_bytes()
        self.core = self.open()
        self.assertNotIn("activity", self.data())
        self.assertEqual(before, self.path.read_bytes())
        with self.assertRaises(ValueError):
            self.core.mark_topic("local-owner", self.data()["turns"][0]["id"], "小说", "小说", "结尾")
        self.core.enable_activity("local-owner")
        self.assertEqual(self.data()["turns"][0]["id"], json.loads(before)["scenes"]["local-owner"]["turns"][0]["id"])
        state = json.loads(self.path.read_text(encoding="utf-8"))
        state["scenes"]["local-owner"]["activity"]["version"] = 999
        atomic_json(self.path, state)
        damaged = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "原文件保留"):
            self.open()
        self.assertEqual(damaged, self.path.read_bytes())

    def test_minimal_topic_survives_window_restart_without_unrelated_text_and_is_not_user_preference(self):
        self.topic()
        for i in range(13):
            self.say(f"第{i}个无关虚构问题")
        self.core = self.open()
        self.assertNotIn("TOPIC_CANARY", self.path.read_text(encoding="utf-8"))
        self.assertEqual(self.data()["memories"], [])
        messages = self.core.messages("local-owner", "owner", "小说结尾怎么继续")
        self.assertIn("人工保留的待续话题", messages[0]["content"])
        self.assertIn("纸船", messages[0]["content"])
        self.assertNotIn("人工保留的待续话题", self.core.messages("local-owner", "owner", "今天气温如何")[0]["content"])
        self.assertNotIn("人工保留的待续话题", self.core.messages("local-owner", "owner", "小说结尾", use_topics=False)[0]["content"])
        self.assertNotIn("人工保留的待续话题", self.core.messages("local-owner", "owner", "小说结尾", use_memory=False)[0]["content"])

    def test_pending_future_and_closed_topics_do_not_generate_or_extend_their_lifetime(self):
        topic_id = self.topic(activate=False, wait_minutes=10)
        complete = Mock(return_value=DRAFT)
        self.assertEqual(self.evaluate(complete)["decision"], "silent")
        self.core.change_topic("local-owner", topic_id, "activate")
        self.assertEqual(self.evaluate(complete)["decision"], "silent")
        deadline = self.data()["activity"]["topics"][0]["expires_at"]
        self.now += timedelta(minutes=11)
        self.assertEqual(self.evaluate(complete)["decision"], "draft")
        self.assertEqual(deadline, self.data()["activity"]["topics"][0]["expires_at"])
        self.core.change_topic("local-owner", topic_id, "close")
        self.now += timedelta(minutes=11)
        self.assertEqual(self.evaluate(complete)["decision"], "silent")
        self.assertEqual(complete.call_count, 1)

    def test_silence_without_context_or_when_switches_off_does_not_call_completion(self):
        complete = Mock(return_value=DRAFT)
        self.core.enable_activity("local-owner")
        self.assertEqual(self.evaluate(complete)["decision"], "silent")
        self.topic()
        for kwargs in ({"use_memory": False}, {"use_topics": False}):
            self.assertEqual(self.evaluate(complete, **kwargs)["decision"], "silent")
        self.say("今天累了，想安静一点")
        self.assertIn("安静", self.evaluate(complete)["reason"])
        complete.assert_not_called()
        self.now += timedelta(hours=1)
        self.core = self.open()
        self.assertEqual(self.evaluate(complete)["decision"], "draft")
        complete.assert_called_once()

    def test_explicit_stop_closes_topic_but_conditional_and_quoted_stop_do_not(self):
        self.topic()
        self.say('如果我说“这个话题不要再聊了”呢')
        self.assertEqual(self.data()["activity"]["topics"][0]["status"], "active")
        self.say("这个话题不要再聊了")
        self.say("嗯")
        complete = Mock()
        self.assertEqual(self.evaluate(complete)["decision"], "silent")
        self.assertEqual(self.data()["activity"]["topics"][0]["status"], "closed")
        complete.assert_not_called()

    def test_duplicate_pending_cooldown_discard_and_explicit_reopen_survive_restart(self):
        topic_id = self.topic()
        complete = Mock(return_value=DRAFT)
        result = self.evaluate(complete)
        self.assertEqual(self.evaluate(complete)["decision"], "silent")
        self.core.discard_draft("local-owner", result["draft_id"])
        self.core = self.open()
        self.now += timedelta(hours=1)
        self.assertEqual(self.evaluate(complete)["decision"], "silent")
        self.core.change_topic("local-owner", topic_id, "activate")
        self.assertEqual(self.evaluate(complete)["decision"], "draft")
        self.assertEqual(complete.call_count, 2)

    def test_model_silence_records_only_cooldown_and_invalid_protocol_does_not_commit(self):
        self.topic()
        before = deepcopy(self.data())
        invalid = [None, "", "not-json", "[]", '{"decision":"silent","text":"hello"}',
                   '{"decision":"send","text":"hello"}', '{"decision":"draft","text":""}',
                   json.dumps({"decision": "draft", "text": "x" * 801}),
                   '{"decision":"draft","text":"hello","target":"other"}']
        for output in invalid:
            with self.subTest(output=output):
                with self.assertRaises(RuntimeError):
                    self.evaluate(lambda _messages: output)
                self.assertEqual(self.data(), before)
        self.assertEqual(self.evaluate(lambda _: SILENT)["decision"], "silent")
        self.core = self.open()
        complete = Mock(return_value=DRAFT)
        self.assertEqual(self.evaluate(complete)["decision"], "silent")
        complete.assert_not_called()
        self.now = self.now.astimezone(timezone.utc) + timedelta(minutes=10)
        self.assertEqual(self.evaluate(complete)["decision"], "draft")
        self.assertEqual(self.data()["turns"], before["turns"])

    def test_expiry_rechecks_copy_without_touching_real_clipboard(self):
        self.topic()
        draft_id = self.evaluate()["draft_id"]
        clipboard = Mock()
        before_turns, before_memories = self.data()["turns"], self.data()["memories"]
        self.core.copy_draft("local-owner", draft_id, clipboard)
        clipboard.assert_called_once_with("小说里的纸船后来想怎么安排？")
        self.core.draft_feedback("local-owner", draft_id, ["时机合适"], "合成反馈")
        self.assertEqual(before_turns, self.data()["turns"])
        self.assertEqual(before_memories, self.data()["memories"])
        self.assertEqual(self.pending()[0]["status"], "pending")
        self.now += timedelta(minutes=31)
        self.core = self.open()
        with self.assertRaises(ValueError):
            self.core.copy_draft("local-owner", draft_id, clipboard)
        self.assertEqual(clipboard.call_count, 1)
        draft = self.data()["activity"]["drafts"][0]
        self.assertEqual((draft["status"], draft["text"], draft["feedback"]), ("expired", "", None))

    def test_private_group_person_and_public_request_scopes_with_actual_captured_payloads(self):
        self.say("PRIVATE_SENTINEL我喜欢音乐", "local-lin", "friend-lin")
        self.topic("local-group", "friend-lin")
        complete = Mock(return_value=DRAFT)
        self.evaluate(complete, "local-group", "friend-lin", "group")
        messages = complete.call_args.args[0]
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(messages, ensure_ascii=False))
        relation = json.loads(messages[0]["content"].split("【本机当前关系】")[1].split("这是本地主动表达评估")[0])
        self.assertEqual(relation["relation"], "friend")
        self.assertNotIn("默认相信孤月大人", relation["attitude"])
        owner = json.loads(self.core.messages("local-group", "owner", "继续")[0]["content"].split("【本机当前关系】")[1])
        self.assertEqual(owner["relation"], "owner")
        self.core.enable_activity("local-qiao")
        self.core.set_public_material("local-qiao", "一段关于纸风筝的公开小想法")
        complete.reset_mock()
        with patch("memory_store.recall", side_effect=AssertionError("public must not retrieve private preferences")):
            self.evaluate(complete, "local-qiao", "friend-qiao", "public", use_memory=False, use_topics=False)
        public = json.dumps(complete.call_args.args[0], ensure_ascii=False)
        for secret in ("PRIVATE_SENTINEL", "孤月大人", "X_A", "小说纸船", "TOPIC_CANARY"):
            self.assertNotIn(secret, public)
        self.assertIn("公开小想法", public)
        self.assertEqual(self.data("local-qiao")["turns"], [])

    def test_all_group_audience_is_local_and_cannot_be_assigned_private_or_other_member_source(self):
        source = self.say(scene="local-group", person="friend-lin")
        self.core.enable_activity("local-group")
        with self.assertRaises(ValueError):
            self.core.mark_topic("local-group", source, "小说", "小说", "待续", target="friend-qiao")
        topic_id = self.core.mark_topic("local-group", source, "小说", "小说", "待续", target="all")
        self.core.change_topic("local-group", topic_id, "activate")
        complete = Mock(return_value=DRAFT)
        self.evaluate(complete, "local-group", "all", "group")
        self.assertIn('"audience"', complete.call_args.args[0][-1]["content"])
        with self.assertRaises(ValueError):
            self.evaluate(complete, target="all")
        with self.assertRaises(ValueError):
            self.evaluate(complete, purpose="group")

    def test_memory_derived_topic_and_drafts_invalidate_on_correct_suppress_delete_and_expiry(self):
        for action in ("edit", "suppress", "delete", "expire"):
            with self.subTest(action=action):
                self.core.reset_scene("local-owner")
                self.core.enable_activity("local-owner")
                self.say("我喜欢音乐" if action != "expire" else "今天我喝咖啡")
                record = self.data()["memories"][-1]
                topic = self.core.mark_topic("local-owner", record["id"], record["content"], "音乐", "谈谈最近关注的音色", source_kind="memory")
                self.core.change_topic("local-owner", topic, "activate")
                draft_id = self.evaluate()["draft_id"]
                self.core.draft_feedback("local-owner", draft_id, ["打扰"], "DERIVED_CANARY")
                if action == "expire":
                    self.now += timedelta(days=1)
                else:
                    self.core.change_memory("local-owner", record["id"], action, "更正内容" if action == "edit" else None)
                clipboard = Mock()
                if action == "delete":
                    self.assertEqual(self.data()["activity"]["topics"], [])
                    self.assertEqual(self.data()["activity"]["drafts"], [])
                    self.assertNotIn("DERIVED_CANARY", self.path.read_text(encoding="utf-8"))
                else:
                    with self.assertRaises(ValueError):
                        self.core.copy_draft("local-owner", draft_id, clipboard)
                clipboard.assert_not_called()

    def test_forget_related_cleans_topics_drafts_feedback_and_known_v1_copies(self):
        original, backup = self.core.memory.legacy_paths()
        atomic_json(original, {"version": 1, "scenes": {"local-owner": {"turns": [], "preferences": {}}}})
        self.core.import_legacy()
        self.say("我喜欢音乐")
        self.core.enable_activity("local-owner")
        record = self.data()["memories"][0]
        topic = self.core.mark_topic("local-owner", record["id"], "喜欢音乐", "DELETE_CANARY", "继续音色", source_kind="memory")
        self.core.change_topic("local-owner", topic, "activate")
        draft_id = self.evaluate()["draft_id"]
        self.core.draft_feedback("local-owner", draft_id, ["打扰"], "DELETE_CANARY")
        self.assertEqual(self.core.forget_preview("local-owner", record["id"])["topics"], 1)
        self.core.forget_memory("local-owner", record["id"])
        for path in (self.path, original, backup):
            self.assertNotIn("DELETE_CANARY", path.read_text(encoding="utf-8"))
        self.assertEqual(self.open().snapshot("local-owner")["activity"]["topics"], [])

    def test_delete_topic_blocks_same_source_and_edit_invalidates_text(self):
        topic = self.topic()
        source_id = self.data()["turns"][0]["id"]
        draft_id = self.evaluate()["draft_id"]
        self.core.change_topic("local-owner", topic, "edit", title="小说纸船", direction="换个方向")
        with self.assertRaises(ValueError):
            self.core.copy_draft("local-owner", draft_id)
        self.core.change_topic("local-owner", topic, "delete")
        self.core = self.open()
        with self.assertRaises(ValueError):
            self.core.mark_topic("local-owner", source_id, "小说", "小说", "又来一次")
        self.assertEqual(self.data()["activity"]["drafts"], [])

    def test_public_material_replacement_and_rejection_remove_old_derived_content(self):
        self.core.enable_activity("local-owner")
        complete = Mock(return_value=DRAFT)
        self.assertEqual(self.evaluate(complete, purpose="public")["decision"], "silent")
        complete.assert_not_called()
        self.core.set_public_material("local-owner", "PUBLIC_CANARY")
        draft_id = self.evaluate(complete, purpose="public")["draft_id"]
        self.core.draft_feedback("local-owner", draft_id, ["打扰"], "PUBLIC_CANARY")
        self.core.set_public_material("local-owner", "新的公开素材")
        self.assertNotIn("PUBLIC_CANARY", self.path.read_text(encoding="utf-8"))
        self.now += timedelta(minutes=11)
        draft_id = self.evaluate(complete, purpose="public")["draft_id"]
        self.core.discard_draft("local-owner", draft_id)
        self.now += timedelta(minutes=11)
        self.core = self.open()
        self.assertEqual(self.evaluate(complete, purpose="public")["decision"], "silent")

    def test_late_response_cancelled_after_mutations_new_dialogue_or_clock_expiry(self):
        actions = [lambda: (self.core.pause(), self.core.resume()), self.core.invalidate,
                   lambda: self.core.reset_scene("local-owner"), lambda: self.say("新的上下文"),
                   lambda: self.core.change_topic("local-owner", self.data()["activity"]["topics"][0]["id"], "close")]
        for action in actions:
            with self.subTest(action=action):
                self.core.reset_scene("local-owner")
                self.topic()
                def complete(_messages):
                    action()
                    return DRAFT
                with self.assertRaises(Cancelled):
                    self.evaluate(complete)
                self.assertEqual(self.data().get("activity", {}).get("drafts", []), [])
        self.topic()
        def expire(_messages):
            self.now += timedelta(days=8)
            return DRAFT
        with self.assertRaises(Cancelled):
            self.evaluate(expire)

    def test_real_thread_topic_edit_cancels_late_draft_and_write_failure_keeps_state(self):
        topic = self.topic()
        started, release = threading.Event(), threading.Event()
        errors = []
        def complete(_messages):
            started.set()
            release.wait(3)
            return DRAFT
        def worker():
            try:
                self.evaluate(complete)
            except Cancelled:
                errors.append("cancelled")
        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(started.wait(3))
        self.core.change_topic("local-owner", topic, "pause")
        release.set()
        thread.join(3)
        self.assertEqual(errors, ["cancelled"])
        self.core.change_topic("local-owner", topic, "activate")
        before = deepcopy(self.core.memory.state)
        with patch("memory_store.atomic_json", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError):
                self.evaluate()
        self.assertEqual(before, self.core.memory.state)

    def test_actual_gateway_failure_incomplete_and_invalid_json_never_commit_or_retry(self):
        self.topic()
        before = deepcopy(self.core.memory.state)
        for status, content, reason in ((503, DRAFT, "stop"), (200, DRAFT, "length"), (200, "无效JSON", "stop"), (200, "", "stop")):
            response = Mock(status_code=status)
            response.json.return_value = {"choices": [{"message": {"content": content}, "finish_reason": reason}]}
            with self.subTest(status=status, reason=reason), patch("requests.post", return_value=response) as post:
                with self.assertRaises(RuntimeError):
                    self.evaluate(ApiCompletion(CFG))
                post.assert_called_once()
                self.assertEqual(before, self.core.memory.state)

    def test_versions_include_relation_policy_and_recall_off_still_saves_explicit_preferences(self):
        self.say("我不喝咖啡", use_memory=False)
        self.assertTrue(self.data()["memories"])
        self.assertNotIn("不喝咖啡", self.core.messages("local-owner", "owner", "饮料", use_memory=False)[0]["content"])
        turn = self.data()["turns"][-1]
        self.core.feedback("local-owner", turn["id"], ["自然合适"], "合成反馈")
        self.assertIn("relation_version", self.data()["feedback"][0])
        self.assertIn("policy_version", self.data()["feedback"][0])
        before = self.core.versions()
        with patch("character_service.OWNER_STYLE", "changed local relation"):
            self.assertNotEqual(before["relation_version"], self.core.versions()["relation_version"])


if __name__ == "__main__":
    unittest.main()
