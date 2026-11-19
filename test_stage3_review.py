"""Regression cases discovered in the stage-three review; synthetic data only."""
import json
import unittest
from datetime import timedelta
from unittest.mock import Mock

import test_activity as fixtures
DRAFT = fixtures.DRAFT


class Stage3ReviewTests(unittest.TestCase):
    setUp = fixtures.ActivityTests.setUp
    open = fixtures.ActivityTests.open
    data = fixtures.ActivityTests.data
    say = fixtures.ActivityTests.say
    topic = fixtures.ActivityTests.topic
    evaluate = fixtures.ActivityTests.evaluate
    pending = fixtures.ActivityTests.pending
    def test_draft_derived_from_extra_history_is_removed_when_that_source_is_forgotten(self):
        self.topic()
        self.say("我喜欢银色纸灯")
        memory = self.data()["memories"][0]
        complete = Mock(return_value=json.dumps({"decision": "draft", "text": "聊聊银色纸灯和纸船？"}, ensure_ascii=False))
        self.evaluate(complete)
        self.assertIn("银色纸灯", json.dumps(complete.call_args.args[0], ensure_ascii=False))
        self.core.forget_memory("local-owner", memory["id"])
        self.assertNotIn("银色纸灯", self.path.read_text(encoding="utf-8"))
        self.assertEqual(self.data()["activity"]["drafts"], [])

    def test_new_quiet_message_invalidates_existing_draft_before_copy(self):
        self.topic()
        draft_id = self.evaluate()["draft_id"]
        self.say("今天想安静一点")
        clipboard = Mock()
        with self.assertRaises(ValueError):
            self.core.copy_draft("local-owner", draft_id, clipboard)
        clipboard.assert_not_called()

    def test_hypothetical_negated_or_quoted_endings_do_not_silence_candidate(self):
        self.topic()
        for text in ("我不是不想聊小说", '如果我说“晚安”呢', '小说里出现“先不聊”这句话'):
            with self.subTest(text=text):
                self.say(text)
                complete = Mock(return_value='{"decision":"silent","text":""}')
                self.evaluate(complete)
                complete.assert_called_once()
                self.now += timedelta(minutes=11)

    def test_group_all_checks_each_person_latest_boundary(self):
        self.topic("local-group", "friend-lin", target="all")
        self.say("先不聊了", "local-group", "friend-lin")
        self.say("小说的配色挺好", "local-group", "friend-qiao")
        complete = Mock(return_value=DRAFT)
        result = self.evaluate(complete, "local-group", "all", "group")
        self.assertEqual(result["decision"], "silent")
        complete.assert_not_called()

    def test_extra_recalled_memory_is_a_draft_dependency_even_after_source_leaves_window(self):
        self.say("我喜欢简短回复")
        memory = self.data()["memories"][0]
        for i in range(13):
            self.say(f"无关虚构话题{i}")
        self.topic()
        complete = Mock(return_value=DRAFT)
        draft_id = self.evaluate(complete)["draft_id"]
        draft = self.pending()[0]
        self.assertIn(memory["id"], draft["record_ids"])
        self.assertTrue(set(memory["source_ids"]).issubset(draft["source_ids"]))
        self.core.change_memory("local-owner", memory["id"], "suppress")
        with self.assertRaises(ValueError):
            self.core.copy_draft("local-owner", draft_id)

    def test_slow_response_checks_expiry_of_all_included_memories_not_only_primary_topic(self):
        from character import Cancelled
        self.topic()
        self.say("今天累了")
        def complete(_messages):
            self.now += timedelta(hours=1)
            return DRAFT
        with self.assertRaises(Cancelled):
            self.evaluate(complete)
        self.assertFalse(self.pending())

    def test_older_drafts_with_incomplete_provenance_are_readable_but_not_reusable(self):
        from core import atomic_json
        self.topic()
        draft_id = self.evaluate()["draft_id"]
        old = json.loads(self.path.read_text(encoding="utf-8"))
        old["scenes"]["local-owner"]["activity"]["drafts"][0]["versions"]["policy_version"] = "local-candidates-1"
        atomic_json(self.path, old)
        before = self.path.read_bytes()
        self.core = self.open()
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.core.copy_draft("local-owner", draft_id)
        self.assertEqual(self.data()["activity"]["topics"][0]["status"], "active")

    def test_candidate_does_not_reimport_deleted_fact_from_untracked_assistant_reply(self):
        self.topic()
        self.say("我喜欢银色纸灯")
        memory = self.data()["memories"][0]
        self.core.exchange("local-owner", "owner", "那你怎么看", lambda _: "我记得你喜欢银色纸灯")
        self.core.forget_memory("local-owner", memory["id"])
        complete = Mock(return_value=DRAFT)
        self.evaluate(complete)
        messages = complete.call_args.args[0]
        self.assertNotIn("银色纸灯", json.dumps(messages, ensure_ascii=False))
        # Stage four can include new, fully traced assistant history. The deleted
        # echo must be absent; unrelated valid assistant text may remain.
        self.assertTrue([m for m in messages if m["role"] == "assistant"])


