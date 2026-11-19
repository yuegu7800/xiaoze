"""ASTRA review regressions: fictional data and no external operations."""
from copy import deepcopy
import json
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import test_activity as fixtures
from memory_panel import MemoryPanel
from memory_store import ingest_turn, make_record, CapacityError


class Stage5ReviewTests(unittest.TestCase):
    setUp = fixtures.ActivityTests.setUp
    open = fixtures.ActivityTests.open
    say = fixtures.ActivityTests.say
    data = fixtures.ActivityTests.data

    def shared(self):
        self.say("我不喝咖啡，我喜欢简短回复")
        records = {r["slot"]: r for r in self.data()["memories"]}
        return records["drink:coffee"], records["length"]

    def test_suppressed_shared_source_does_not_swallow_other_record_correction(self):
        coffee, length = self.shared()
        self.core.change_memory("local-owner", coffee["id"], "suppress")
        before = self.path.read_bytes(), deepcopy(self.core.memory.state)
        with patch("memory_store.atomic_json", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError):
                self.core.change_memory("local-owner", length["id"], "edit", "回答可以详细展开")
        self.assertEqual((self.path.read_bytes(), self.core.memory.state), before)
        self.core.change_memory("local-owner", length["id"], "edit", "回答可以详细展开")
        active = [r for r in self.data()["memories"] if r["status"] == "active"]
        self.assertEqual([r["content"] for r in active], ["回答可以详细展开"])
        self.assertEqual(active[0]["source_ids"], length["source_ids"])
        complete = Mock(return_value="合成回复")
        self.core.exchange("local-owner", "owner", "继续聊", complete)
        self.assertIn("回答可以详细展开", complete.call_args.args[0][0]["content"])
        self.assertNotIn("我不喝咖啡", json.dumps(complete.call_args.args[0], ensure_ascii=False))
        self.core.forget_memory("local-owner", active[0]["id"])
        self.assertNotIn("回答可以详细展开", self.path.read_text(encoding="utf-8"))

    def test_explicit_accept_of_existing_candidate_preserves_source_block(self):
        coffee, _ = self.shared()
        source = self.data()["turns"][0]
        candidate = self.core.mark_turn("local-owner", source["id"], "喜欢简短回复", "倾向简短表达", "preference")
        self.core.change_memory("local-owner", coffee["id"], "suppress")
        self.core.change_memory("local-owner", candidate, "accept")
        self.assertTrue(any(r["id"] == candidate and r["status"] == "active" for r in self.data()["memories"]))
        self.assertIn(source["id"], self.data()["blocked"])
        data = self.data()
        before = deepcopy(data)
        ingest_turn(data, "local-owner", source, "owner", self.now)
        self.assertEqual(data, before)
        with self.assertRaises(ValueError):
            self.core.mark_turn("local-owner", source["id"], "不喝咖啡", "不重新提取")

    def test_confirmation_over_source_budget_preserves_pending_candidate(self):
        self.say("我喜欢简短回复")
        data = self.data()
        existing = data["memories"][0]
        existing["source_ids"].extend(f"synthetic-history-{i}" for i in range(63))
        pending = make_record("local-owner", "owner", "legacy-only-root", "preference", "length",
                              existing["content"], "", 0, self.now, source="legacy_review", status="pending")
        data["memories"].append(pending)
        self.core._save("local-owner", data)
        before = self.path.read_bytes(), deepcopy(self.core.memory.state), self.core._epoch
        with self.assertRaises(CapacityError):
            self.core.change_memory("local-owner", pending["id"], "accept")
        self.assertEqual((self.path.read_bytes(), self.core.memory.state, self.core._epoch), before)

    def test_topic_can_use_valid_record_with_blocked_raw_ancestor(self):
        coffee, length = self.shared()
        self.core.change_memory("local-owner", coffee["id"], "suppress")
        self.core.enable_activity("local-owner")
        topic = self.core.mark_topic("local-owner", length["id"], "回复简短", "表达长度", "探讨简洁表达", source_kind="memory", confirm=True)
        self.assertEqual(self.data()["activity"]["topics"][0]["status"], "active")
        with self.assertRaises(ValueError):
            self.core.mark_topic("local-owner", length["source_ids"][0], "我不喝咖啡", "咖啡", "继续", confirm=True)
        complete = Mock(return_value=fixtures.DRAFT)
        draft = self.core.evaluate_candidate("local-owner", "owner", "private", complete)
        self.assertTrue(draft["draft_id"])
        self.assertNotIn("我不喝咖啡", json.dumps(complete.call_args.args[0], ensure_ascii=False))
        self.core.change_topic("local-owner", topic, "delete")
        with self.assertRaises(ValueError):
            self.core.mark_topic("local-owner", length["id"], "回复简短", "表达长度", "继续", source_kind="memory", confirm=True)

    def test_duplicate_mark_reports_no_new_candidate_without_saving(self):
        source = self.say("虚构小说纸船结尾")
        self.core.mark_turn("local-owner", source, "小说纸船", "小说话题")
        before = self.path.read_bytes(), self.core._epoch
        with self.assertRaisesRegex(ValueError, "已有"):
            self.core.mark_turn("local-owner", source, "小说纸船", "改写却没有真正保存的内容")
        self.assertEqual((self.path.read_bytes(), self.core._epoch), before)

    def test_accept_paint_failure_reports_committed_without_second_refresh(self):
        source = self.say("虚构小说纸船结尾")
        record_id = self.core.mark_turn("local-owner", source, "小说纸船", "小说话题")
        panel = MemoryPanel.__new__(MemoryPanel)
        panel.core, panel.scene = self.core, self.core.scenes["local-owner"]
        panel.chosen = Mock(return_value=record_id)
        panel.message, panel.status_filter, panel.view, panel.app = Mock(), Mock(), Mock(), Mock()
        panel.refresh = Mock(side_effect=tk.TclError("synthetic paint failure"))
        panel.accept()
        self.assertEqual(next(r for r in self.data()["memories"] if r["id"] == record_id)["status"], "active")
        panel.refresh.assert_called_once()
        self.assertIn("提交已完成", panel.message.set.call_args.args[0])

    def test_mark_paint_failure_commits_once_and_preserves_failure_notice(self):
        source = self.say("虚构小说纸船结尾")
        panel = MemoryPanel.__new__(MemoryPanel)
        panel.core, panel.scene = self.core, self.core.scenes["local-owner"]
        panel.turn = Mock(get=Mock(return_value="selected"))
        panel.turn_labels = {"selected": source}
        # D requires explicit stable source binding; keep the original
        # commit-once/paint-failure assertions below intact.
        panel.turn_id = panel.mark_bound_id = source
        panel.people = {panel.scene.person('owner').display: 'owner'}
        panel.turns = {source: self.data()["turns"][0]}
        panel.quote = Mock(get=Mock(return_value="小说纸船"))
        panel.content = Mock(get=Mock(return_value="虚构小说话题"))
        panel.category = Mock(get=Mock(return_value="经历（本地聊天中提到）"))
        for attr in ("message", "person_filter", "status_filter", "category_filter", "keyword", "tabs", "library_tab", "app"):
            setattr(panel, attr, Mock())
        panel.refresh = Mock(side_effect=tk.TclError("synthetic paint failure"))
        panel.mark()
        self.assertEqual(len(self.data()["memories"]), 1)
        panel.refresh.assert_called_once()
        self.assertIn("提交已完成", panel.message.set.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
