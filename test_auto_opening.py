"""Stage ten: controllable clocks, fictional stores and mock completions only."""
from copy import deepcopy
from datetime import datetime, timedelta
import threading
import unittest
from unittest.mock import Mock, patch

from auto_opening import AutoOpening, Binding, SessionBudget
from character import Cancelled
from core import CompletionResult
from memory_rules import LOCAL_TZ
import test_activity as fixtures


class Clock:
    def __init__(self):
        self.mono = 0.
        self.wall = datetime(2026, 11, 12, 12, 0, tzinfo=LOCAL_TZ)

    def advance(self, seconds):
        self.mono += seconds
        self.wall += timedelta(seconds=seconds)


class AutoOpeningTests(unittest.TestCase):
    setUp = fixtures.ActivityTests.setUp
    open = fixtures.ActivityTests.open
    data = fixtures.ActivityTests.data
    say = fixtures.ActivityTests.say
    topic = fixtures.ActivityTests.topic

    def setup_auto(self, *, api=True, topic=True):
        self.clock = Clock()
        self.core.memory.clock = lambda: self.clock.wall
        if topic:
            self.topic()
        self.policy = AutoOpening(monotonic=lambda: self.clock.mono, wall_clock=lambda: self.clock.wall,
                                  budget=SessionBudget())
        self.binding = Binding(self.core, ("fictional-api" if api else "simulation", False, True, True),
                               "local-owner", "owner", api, True, True)
        self.policy.start(self.binding, self.data())
        return self.policy

    def idle(self, seconds=600):
        # Real periodic checks with a fake clock, never sleep ten minutes.
        ready = False
        for _ in range(int(seconds / 30)):
            self.clock.advance(30)
            ready = self.policy.due()
        return ready

    def generate(self, output=fixtures.DRAFT, action=None):
        run = self.policy.begin(self.core.ticket())

        def complete(_messages):
            with self.core._lock:
                self.policy.guard(run)
                if run.binding.api:
                    self.policy.reserve_attempt(run)
            if action:
                action()
            return output

        self.complete = Mock(side_effect=complete)
        result = self.core.evaluate_candidate("local-owner", "owner", "private", self.complete,
            ticket=run.ticket, guard=lambda: self.policy.guard(run))
        return run, result

    def test_default_off_start_wait_and_read_only_precheck(self):
        self.setup_auto(topic=False)
        before = deepcopy(self.core.memory.state), self.core._epoch
        for _ in range(4):
            ready, _ = self.core.candidate_precheck("local-owner", "owner", "private")
            self.assertFalse(ready)
        self.assertEqual((self.core.memory.state, self.core._epoch), before)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.idle(570))
        self.assertTrue(self.idle(30))
        self.assertEqual(self.policy.budget.attempt_total, 0)
        self.policy.stop()
        self.assertFalse(self.idle())
        self.assertFalse(AutoOpening(budget=SessionBudget()).enabled)

    def test_precheck_all_local_blocks_never_calls_completion_or_writes(self):
        self.setup_auto()
        before = self.path.read_bytes(), self.core._epoch
        for memory, topics in ((False, True), (True, False)):
            self.assertFalse(self.core.candidate_precheck("local-owner", "owner", "private",
                use_memory=memory, use_topics=topics)[0])
        self.core.pause()
        self.assertFalse(self.core.candidate_precheck("local-owner", "owner", "private")[0])
        self.core.resume()
        self.assertEqual(self.path.read_bytes(), before[0])
        self.assertEqual(self.policy.budget.attempt_total, 0)
        for quiet in ("我想安静一点", "这个话题不要再聊了"):
            self.core.reset_scene("local-owner")
            self.topic()
            self.say(quiet)
            self.assertFalse(self.core.candidate_precheck("local-owner", "owner", "private")[0])

    def test_draft_once_exact_receipt_then_wait_for_real_user(self):
        self.setup_auto()
        self.policy.set_adoption(True)
        self.assertTrue(self.idle())
        run, result = self.generate()
        self.assertEqual(result["commit_ticket"], self.core._epoch)
        self.assertFalse(self.core._inflight)
        event = self.policy.adopt(run, result)
        self.policy.finish(run, result)
        self.assertEqual(self.data()["turns"][-1]["id"], event)
        self.assertEqual(self.data()["turns"][-1]["kind"], "local_assistant")
        self.assertEqual(self.policy.budget.adoption_total, 1)
        self.assertEqual(self.policy.budget.attempt_total, 1)
        with self.assertRaises(Cancelled):
            self.policy.adopt(run, result)
        self.policy.stop()
        self.policy.start(self.binding, self.data())
        self.assertFalse(self.idle())
        self.assertIn("等待用户回应", self.policy.note)
        self.policy.activity()  # Editing/copying/feedback is not a response.
        self.assertFalse(self.idle())
        self.say("虚构接话：我想试试这个方向")
        self.policy.user_committed(self.core, "local-owner", "owner")
        self.assertTrue(self.idle())

    def test_draft_only_never_adopts_old_pending_and_read_only_precheck(self):
        self.setup_auto()
        self.idle()
        run, result = self.generate()
        self.assertIsNone(self.policy.adopt(run, result))
        self.policy.finish(run, result)
        self.assertEqual(len(self.data()["turns"]), 1)
        before = self.path.read_bytes(), self.core._epoch
        ready, reason = self.core.candidate_precheck("local-owner", "owner", "private")
        self.assertFalse(ready)
        self.assertIn("待处理", reason)
        self.assertEqual((self.path.read_bytes(), self.core._epoch), before)
        self.policy.set_adoption(True)
        self.policy.stop()
        self.policy.start(self.binding, self.data())
        self.assertEqual(len(self.data()["turns"]), 1)

    def test_silent_success_counts_attempt_and_original_cooldown(self):
        self.setup_auto()
        self.idle()
        run, result = self.generate(fixtures.SILENT)
        self.policy.finish(run, result)
        self.assertEqual(result["decision"], "silent")
        self.assertEqual(self.policy.budget.attempt_total, 1)
        self.assertEqual(self.policy.budget.adoption_total, 0)
        self.assertIn("冷却", self.core.candidate_precheck("local-owner", "owner", "private")[1])
        self.idle(600)
        self.assertTrue(self.core.candidate_precheck("local-owner", "owner", "private")[0])

    def test_hourly_and_session_quotas_not_reset_by_scope_or_switch(self):
        self.setup_auto()
        for index in range(6):
            if index in (2, 4):
                self.idle(3600)
            else:
                self.idle()
            self.policy.start(self.binding, self.data())
            self.idle()
            run, result = self.generate(fixtures.SILENT)
            self.policy.finish(run, result)
            if index == 1:
                self.assertFalse(self.idle(600))
                self.assertIn("上限", self.policy.note)
        self.assertEqual(self.policy.budget.attempt_total, 6)
        self.policy.stop()
        self.policy.start(self.binding, self.data())
        self.assertFalse(self.idle(3600))
        self.assertEqual(len(self.policy.budget.attempts), 6)
        self.assertIn("请求尝试 6/6", self.policy.status())

    def test_atomic_attempt_reservation_and_failed_attempt_not_refunded(self):
        budget = SessionBudget()
        outcomes = []
        def reserve():
            try:
                budget.reserve_attempt(0)
                outcomes.append(True)
            except Cancelled:
                outcomes.append(False)
        threads = [threading.Thread(target=reserve) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(outcomes), 2)
        self.assertEqual(budget.attempt_total, 2)
        self.assertTrue(budget.available(3600))

    def test_long_gap_restarts_idle_and_quiet_hours_checked_at_commit(self):
        self.setup_auto()
        self.idle(570)
        self.clock.advance(36000)
        self.assertFalse(self.policy.due())
        self.assertIn("恢复", self.policy.note)
        self.clock.wall = self.clock.wall.replace(hour=12)
        self.assertTrue(self.idle())
        self.clock.wall = self.clock.wall.replace(hour=22, minute=59, second=59)
        before = self.path.read_bytes()
        with self.assertRaises(Cancelled):
            self.generate(action=lambda: self.clock.advance(2))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.policy.budget.attempt_total, 1)
        self.policy.finish(self.policy.active, cancelled=True)
        self.assertFalse(self.idle())
        self.assertIn("安静时段", self.policy.note)

    def test_ui_activity_suspension_and_scope_cancel_late_commit(self):
        for action in ("input", "panel", "scope", "off", "pause", "topic-edit", "delete", "expiry"):
            with self.subTest(action=action):
                self.core.reset_scene("local-owner")
                self.setup_auto()
                self.idle()
                before = self.path.read_bytes()
                def change():
                    if action == "input":
                        self.policy.activity()
                    elif action == "panel":
                        self.policy.environment(self.binding, True)
                    elif action == "scope":
                        other = Binding(self.core, ("other-mode",), "local-owner", "owner", True, True, True)
                        self.policy.environment(other, False)
                    elif action == "off":
                        self.policy.stop()
                    elif action == "pause":
                        self.core.pause()
                    elif action == "topic-edit":
                        self.core.change_topic("local-owner", self.data()["activity"]["topics"][0]["id"], "pause")
                    elif action == "delete":
                        self.core.reset_scene("local-owner")
                    else:
                        self.clock.wall += timedelta(days=8)
                with self.assertRaises(Cancelled):
                    self.generate(action=change)
                self.assertEqual(self.policy.budget.attempt_total, 1)
                self.assertFalse(self.data().get("activity", {}).get("drafts", []))
                if action in ("input", "panel", "scope", "off", "pause", "expiry"):
                    self.assertEqual(self.path.read_bytes(), before)
                self.core.resume()

    def test_generation_commit_ticket_cannot_be_refreshed_after_maintenance(self):
        self.setup_auto()
        self.policy.set_adoption(True)
        self.idle()
        run, result = self.generate()
        self.core.edit_preference("local-owner", "owner", "length", "简短")
        self.assertNotEqual(result["commit_ticket"], self.core.ticket())
        with self.assertRaises(Cancelled):
            self.policy.adopt(run, result)
        self.assertEqual(self.policy.budget.adoption_total, 0)
        self.assertEqual(len(self.data()["turns"]), 1)

    def test_no_retroactive_adoption_after_toggle_or_input(self):
        for action in ("off-on", "input", "night", "stop"):
            with self.subTest(action=action):
                self.core.reset_scene("local-owner")
                self.setup_auto()
                self.policy.set_adoption(True)
                self.idle()
                if action == "night":
                    self.clock.wall = self.clock.wall.replace(hour=22, minute=59, second=59)
                run, result = self.generate()
                if action == "off-on":
                    self.policy.set_adoption(False)
                    self.policy.set_adoption(True)
                    self.assertIsNone(self.policy.adopt(run, result))
                else:
                    if action == "input":
                        self.policy.activity()
                    elif action == "night":
                        self.clock.wall = self.clock.wall.replace(hour=23, minute=0, second=0)
                    else:
                        self.policy.stop()
                    with self.assertRaises(Cancelled):
                        self.policy.adopt(run, result)
                self.assertEqual(self.policy.budget.adoption_total, 0)
                self.assertEqual(len(self.data()["turns"]), 1)
                self.assertEqual(self.data()["activity"]["drafts"][0]["status"], "pending")

    def test_auto_adoption_hourly_session_limits_and_manual_opening_wait(self):
        self.setup_auto()
        # Manual use has its own count and still blocks an automatic followup.
        draft = self.core.evaluate_candidate("local-owner", "owner", "private", lambda _: fixtures.DRAFT)
        event = self.core.adopt_draft("local-owner", draft["draft_id"], target="owner", purpose="private")
        self.policy.manual_adopted(self.core, "local-owner", "owner", event)
        self.assertEqual(self.policy.budget.adoption_total, 0)
        self.assertFalse(self.idle())
        self.say("虚构新回复")
        self.policy.user_committed(self.core, "local-owner", "owner")
        self.policy.manual_adopted(self.core, "local-owner", "owner", event)  # duplicate receipt
        self.assertFalse(self.policy.budget.waiting[self.binding.key])
        self.policy.set_adoption(True)
        for index in range(6):
            if index in (2, 4):
                self.idle(3600)
            self.idle()
            run, result = self.generate()
            self.policy.adopt(run, result)
            self.policy.finish(run, result)
            self.say("虚构新接话")
            self.policy.user_committed(self.core, "local-owner", "owner")
        self.assertEqual(self.policy.budget.adoption_total, 6)
        self.assertFalse(self.idle(3600))

    def test_model_format_and_storage_failure_count_once_and_stop(self):
        for output in ("", "not-json", CompletionResult("", "length"), fixtures.DRAFT):
            with self.subTest(output=repr(output)):
                self.core.reset_scene("local-owner")
                self.setup_auto()
                self.idle()
                before = self.path.read_bytes()
                def operation():
                    if output == fixtures.DRAFT:
                        with patch("memory_store.atomic_json", side_effect=OSError("fictional")):
                            self.generate(output)
                    else:
                        self.generate(output)
                with self.assertRaises((RuntimeError, ValueError, OSError)):
                    operation()
                self.complete.assert_called_once()
                self.policy.finish(self.policy.active, error="静态诊断")
                self.assertFalse(self.policy.enabled)
                self.assertFalse(self.core._paused)
                self.assertFalse(self.core._inflight)
                self.assertEqual(self.policy.budget.attempt_total, 1)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertFalse(self.idle(3600))

    def test_automatic_manual_chat_and_candidate_share_slot(self):
        self.setup_auto()
        self.idle()
        def other_request():
            for operation in (
                    lambda: self.core.exchange("local-owner", "owner", "虚构输入", Mock()),
                    lambda: self.core.evaluate_candidate("local-owner", "owner", "private", Mock())):
                with self.assertRaisesRegex(Cancelled, "在途"):
                    operation()
        run, result = self.generate(action=other_request)
        self.policy.finish(run, result)
        self.assertEqual(self.policy.budget.attempt_total, 1)

    def test_only_owner_id_and_core_binding_are_eligible(self):
        self.setup_auto()
        for scene_id, person in (("local-lin", "friend-lin"), ("local-group", "owner")):
            other = Binding(self.core, (), scene_id, person, True, True, True)
            with self.assertRaises(ValueError):
                self.policy.start(other, self.core.snapshot(scene_id))

    def test_simulation_exercises_transactions_without_model_attempts(self):
        self.setup_auto(api=False)
        self.policy.set_adoption(True)
        self.idle()
        run, result = self.generate()
        self.policy.adopt(run, result)
        self.policy.finish(run, result)
        self.assertEqual(self.policy.budget.attempt_total, 0)
        self.assertEqual(self.policy.budget.adoption_total, 1)

    def test_expression_quota_is_separate_and_pending_text_survives_denial(self):
        self.setup_auto(api=False)
        self.policy.set_adoption(True)
        self.idle()
        run, result = self.generate()
        self.policy.budget.adoptions.extend((self.clock.mono, self.clock.mono))
        self.policy.budget.adoption_total = 2
        self.assertIsNone(self.policy.adopt(run, result))
        self.assertEqual(self.policy.budget.attempt_total, 0)
        self.assertEqual(len(self.data()["turns"]), 1)
        self.assertEqual(self.data()["activity"]["drafts"][0]["status"], "pending")
        self.policy.finish(run, result)
        self.assertFalse(self.idle(600))
        self.assertIn("上限", self.policy.note)
        self.assertTrue(self.idle(3000))
        self.policy.budget.adoption_total = 6
        self.assertFalse(self.idle(3600))

    def test_exact_draft_id_does_not_adopt_a_different_or_old_draft(self):
        self.setup_auto()
        self.policy.set_adoption(True)
        self.idle()
        run, first = self.generate()
        self.policy.activity()
        self.policy.finish(run, first, cancelled=True)
        self.core.discard_draft("local-owner", first["draft_id"])
        topic_id = self.data()["activity"]["topics"][0]["id"]
        self.core.change_topic("local-owner", topic_id, "activate")
        self.idle()
        run, second = self.generate()
        # A stale result cannot be repaired by refreshing the current UI view.
        self.assertNotEqual(first["draft_id"], second["draft_id"])
        with self.assertRaises(Cancelled):
            self.policy.adopt(run, first)
        self.policy.adopt(run, second)
        self.assertEqual(self.data()["turns"][-1]["draft_id"], second["draft_id"])

    def test_minimize_or_panel_resume_waits_a_fresh_interval(self):
        self.setup_auto()
        self.idle(570)
        self.policy.environment(self.binding, True)
        self.idle(600)
        self.policy.environment(self.binding, False)
        self.assertFalse(self.idle(570))
        self.assertTrue(self.idle(30))

    def test_new_process_policy_detects_preexisting_unanswered_manual_opening(self):
        self.setup_auto()
        draft = self.core.evaluate_candidate("local-owner", "owner", "private", lambda _: fixtures.DRAFT)
        self.core.adopt_draft("local-owner", draft["draft_id"], target="owner", purpose="private")
        self.policy = AutoOpening(monotonic=lambda: self.clock.mono, wall_clock=lambda: self.clock.wall,
                                  budget=SessionBudget())
        self.policy.start(self.binding, self.data())
        self.assertFalse(self.idle())
        self.assertIn("等待用户回应", self.policy.note)

    def test_adoption_storage_failure_has_no_counter_or_extra_assistant_event(self):
        self.setup_auto()
        self.policy.set_adoption(True)
        self.idle()
        run, result = self.generate()
        before = self.path.read_bytes()
        with patch("memory_store.atomic_json", side_effect=OSError("fictional")):
            with self.assertRaises(OSError):
                self.policy.adopt(run, result)
        self.policy.finish(run, error="本地采用写入失败")
        self.assertFalse(self.policy.enabled)
        self.assertEqual(self.policy.budget.adoption_total, 0)
        self.assertEqual(self.policy.budget.attempt_total, 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_expired_or_deleted_generation_source_cannot_be_adopted(self):
        for action in ("delete", "expiry", "revision"):
            with self.subTest(action=action):
                self.core.reset_scene("local-owner")
                self.setup_auto()
                self.policy.set_adoption(True)
                self.idle()
                run, result = self.generate()
                topic_id = self.data()["activity"]["topics"][0]["id"]
                if action == "expiry":
                    self.clock.advance(1801)
                    self.policy.last_check = self.clock.mono
                else:
                    self.core.change_topic("local-owner", topic_id,
                        "delete" if action == "delete" else "edit", title="虚构新标题", direction="新方向")
                with self.assertRaises((Cancelled, ValueError)):
                    self.policy.adopt(run, result)
                self.assertEqual(len(self.data()["turns"]), 1)
                self.assertEqual(self.policy.budget.adoption_total, 0)


if __name__ == "__main__":
    unittest.main()
