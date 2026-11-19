"""Read-only automatic status, fictional stores and controlled clocks only."""
from copy import deepcopy
from datetime import timedelta
import threading
import unittest
from unittest.mock import Mock, patch

import test_auto_opening as fixtures
from character import Cancelled
from opening_state import duration


class OpeningStatusTests(unittest.TestCase):
    setUp = fixtures.AutoOpeningTests.setUp
    open = fixtures.AutoOpeningTests.open
    data = fixtures.AutoOpeningTests.data
    say = fixtures.AutoOpeningTests.say
    topic = fixtures.AutoOpeningTests.topic
    setup_auto = fixtures.AutoOpeningTests.setup_auto
    idle = fixtures.AutoOpeningTests.idle
    generate = fixtures.AutoOpeningTests.generate

    def view(self):
        return self.core.candidate_state("local-owner", "owner", "private")

    def state(self):
        return self.policy.projection(self.view())

    def protected_state(self):
        # Every mutable policy datum, including result metadata and gates.
        policy = {key: value for key, value in vars(self.policy).items()
                  if key not in ("lock", "budget", "binding", "active", "monotonic", "wall_clock")}
        budget = self.policy.budget
        return (deepcopy(self.core.memory.state), self.path.read_bytes() if self.path.exists() else None,
                self.core._epoch, self.core._paused, self.core._inflight,
                deepcopy(self.core.last_trace), self.core.last_notice, deepcopy(self.core._request_evidence),
                deepcopy(policy), self.policy.binding, self.policy.active,
                tuple(budget.attempts), tuple(budget.adoptions), budget.attempt_total, budget.adoption_total,
                tuple(budget.manual_receipts),
                [(id(core), deepcopy(gates)) for core, gates in budget.waiting.cores.items()])

    def test_idle_status_reports_remaining_time_without_running_check(self):
        self.setup_auto()
        self.idle(330)
        self.assertIn("4分30秒", self.policy.status())

    def test_hourly_and_session_limits_have_different_current_explanations(self):
        self.setup_auto()
        self.idle()
        self.policy.budget.reserve_attempt(self.clock.mono)
        self.policy.budget.reserve_attempt(self.clock.mono)
        self.policy.due()
        hourly = self.policy.status()
        self.policy.budget.attempt_total = 6
        self.policy.due()
        session = self.policy.status()
        self.assertNotEqual(hourly.split(" · ")[0], session.split(" · ")[0])
        self.assertNotIn("重启", session)

    def test_repeated_status_is_fully_read_only_and_does_not_tick_or_reserve(self):
        self.setup_auto()
        self.idle()
        before = self.protected_state()
        with patch.object(self.policy, "due", side_effect=AssertionError("view ticked")), \
                patch.object(self.policy, "activity", side_effect=AssertionError("view reset idle")), \
                patch.object(self.core, "request_slot", side_effect=AssertionError("view claimed slot")):
            for _ in range(50):
                view = self.view()
                self.assertEqual(self.policy.projection(view).code, "ready")
                self.policy.status(view)
        self.assertEqual(self.protected_state(), before)
        self.assertNotIn("纸船", repr(view))
        self.assertNotIn("TOPIC_CANARY", repr(view))

    def test_shared_gate_matches_actual_evaluation_and_call_boundary(self):
        for condition in ("disabled", "missing", "unconfirmed", "future", "quiet", "pending", "cooldown", "ready"):
            with self.subTest(condition=condition):
                self.core.reset_scene("local-owner")
                self.setup_auto(topic=False)
                if condition != "disabled":
                    self.core.enable_activity("local-owner")
                if condition not in ("disabled", "missing"):
                    self.topic(activate=condition != "unconfirmed", wait_minutes=60 if condition == "future" else 0)
                if condition == "quiet":
                    self.say("我想安静一点")
                elif condition in ("pending", "cooldown"):
                    self.core.evaluate_candidate("local-owner", "owner", "private",
                        lambda _: fixtures.fixtures.DRAFT if condition == "pending" else fixtures.fixtures.SILENT)
                view = self.view()
                expected = {"disabled": "activity_disabled", "missing": "topic_missing", "unconfirmed": "topic_unconfirmed",
                            "future": "topic_wait", "quiet": "explicit_quiet", "pending": "pending_draft",
                            "cooldown": "cooldown", "ready": "ready"}[condition]
                self.assertEqual(view.state.code, expected)
                before = self.path.read_bytes() if self.path.exists() else None
                complete = Mock(return_value=fixtures.fixtures.SILENT)
                if condition == "disabled":
                    # Evaluation's original explicit-enable API rejects this
                    # case; the read-only view explains it without enabling.
                    with self.assertRaises(ValueError):
                        self.core.evaluate_candidate("local-owner", "owner", "private", complete)
                    result = None
                else:
                    result = self.core.evaluate_candidate("local-owner", "owner", "private", complete)
                self.assertEqual(complete.call_count, int(condition == "ready"))
                if condition != "ready":
                    if result is not None:
                        self.assertEqual(result["reason"], view.state.message)
                    self.assertEqual(self.path.read_bytes() if self.path.exists() else None, before)

    def test_idle_600_and_30_second_checks_are_not_advanced_by_display(self):
        self.setup_auto()
        self.idle(570)
        self.clock.advance(29)
        self.assertEqual(self.state().remaining_seconds, 1)
        original_check = self.policy.last_check
        self.clock.advance(1)
        self.assertTrue(self.state().ready)
        self.assertEqual(self.policy.last_check, original_check)
        self.assertEqual(self.policy.budget.attempt_total, 0)
        self.assertTrue(self.policy.due())

    def test_resume_and_quiet_hours_use_correct_clocks_and_do_not_write(self):
        self.setup_auto()
        self.idle(600)
        self.clock.advance(91)
        before = self.protected_state()
        self.assertEqual(self.state().code, "resume")
        self.assertEqual(self.protected_state(), before)
        self.assertFalse(self.policy.due())
        self.assertEqual(self.state().code, "idle")
        self.idle()
        self.clock.wall = self.clock.wall.replace(hour=23, minute=0, second=0)
        self.assertEqual(self.state().code, "quiet_hours")
        self.assertEqual(self.state().remaining_seconds, 9 * 3600)
        # Cross midnight without advancing monotonic idle/rolling clocks.
        self.clock.wall = (self.clock.wall + timedelta(days=1)).replace(hour=0)
        self.assertEqual(self.state().remaining_seconds, 8 * 3600)
        self.clock.wall = self.clock.wall.replace(hour=8)
        self.assertTrue(self.state().ready)
        self.clock.wall = self.clock.wall.replace(hour=7, minute=59, second=59)
        self.assertEqual(self.state().remaining_seconds, 1)

    def test_hour_budget_recovers_session_budget_never_gets_a_timer(self):
        self.setup_auto()
        self.idle()
        budget = self.policy.budget
        budget.reserve_attempt(self.clock.mono)
        budget.reserve_attempt(self.clock.mono + 10)
        self.assertEqual(self.state().code, "request_hour_limit")
        self.assertEqual(self.state().remaining_seconds, 3600)
        self.idle(3570)
        self.clock.advance(29)
        self.assertEqual(self.state().remaining_seconds, 1)
        self.clock.advance(1)
        self.assertTrue(self.state().ready)
        budget.attempt_total = 6
        self.assertEqual(self.state().code, "request_session_limit")
        self.assertIsNone(self.state().remaining_seconds)
        self.idle(3600)
        self.assertEqual(self.state().code, "request_session_limit")
        self.assertNotIn("重启", self.policy.status())

    def test_adoption_limits_are_separate_and_simulation_ignores_request_quota(self):
        self.setup_auto(api=False)
        self.idle()
        budget = self.policy.budget
        budget.attempt_total = 6
        self.assertTrue(self.state().ready)
        self.policy.set_adoption(True)
        budget.adoptions.extend((self.clock.mono, self.clock.mono))
        budget.adoption_total = 2
        self.assertEqual(self.state().code, "adoption_hour_limit")
        budget.adoption_total = 6
        self.assertEqual(self.state().code, "adoption_session_limit")
        self.assertIsNone(self.state().remaining_seconds)

    def test_silent_receipt_is_not_inferred_from_cooldown_and_expires_on_activity(self):
        self.setup_auto()
        self.idle()
        run, result = self.generate(fixtures.fixtures.SILENT)
        self.policy.finish(run, result)
        self.assertEqual(self.state().code, "cooldown")
        self.assertIn("模型选择沉默", self.state().message)
        stale = self.view()
        self.clock.advance(30)
        self.assertEqual(self.policy.projection(stale).remaining_seconds, 570)
        before = self.protected_state()
        self.policy.status(stale)
        self.assertEqual(self.protected_state(), before)
        # A new Core transaction invalidates attribution, even if cooldown is
        # still present. Today's state is never used to reconstruct history.
        self.core.enable_activity("local-lin")
        self.assertNotIn("模型选择沉默", self.state().message)
        self.policy.activity()
        self.assertIsNone(self.policy.silent_receipt)
        self.policy.stop("明确停止")
        self.assertEqual(self.policy.projection().code, "off")
        self.policy.stop("错误后已停止", error=True)
        self.assertEqual(self.policy.projection().code, "error_stopped")

    def test_status_views_do_not_cancel_inflight_but_maintenance_still_does(self):
        for maintain in (False, True):
            with self.subTest(maintain=maintain):
                self.core.reset_scene("local-owner")
                self.setup_auto()
                self.idle()
                entered, release = threading.Event(), threading.Event()
                outcome = []
                run = self.policy.begin(self.core.ticket())
                def complete(_):
                    self.policy.reserve_attempt(run)
                    entered.set()
                    self.assertTrue(release.wait(4))
                    return fixtures.fixtures.DRAFT
                def work():
                    try:
                        outcome.append(self.core.evaluate_candidate("local-owner", "owner", "private", complete,
                            ticket=run.ticket, guard=lambda: self.policy.guard(run)))
                    except Cancelled:
                        outcome.append("cancelled")
                worker = threading.Thread(target=work)
                worker.start()
                self.assertTrue(entered.wait(4))
                try:
                    before = self.protected_state()
                    for _ in range(30):
                        self.assertEqual(self.state().code, "evaluating")
                    self.assertEqual(self.protected_state(), before)
                    if maintain:
                        topic = self.data()["activity"]["topics"][0]
                        self.core.change_topic("local-owner", topic["id"], "delete")
                finally:
                    release.set()
                    worker.join(4)
                self.assertFalse(worker.is_alive())
                if maintain:
                    self.assertEqual(outcome, ["cancelled"])
                else:
                    self.assertEqual(outcome[0]["decision"], "draft")

    def test_core_time_boundaries_and_switches_are_metadata_only(self):
        self.setup_auto(topic=False)
        self.topic(wait_minutes=60)
        self.idle()
        view = self.view()
        topic = self.data()["activity"]["topics"][0]
        self.assertEqual(view.state.code, "topic_wait")
        self.assertEqual(view.valid_until.isoformat(), topic["not_before"])
        self.clock.wall += timedelta(hours=1)
        self.assertEqual(self.view().state.code, "ready")
        self.clock.wall += timedelta(days=8)
        original = self.path.read_bytes()
        self.assertEqual(self.view().state.code, "topic_missing")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.core.candidate_state("local-owner", "owner", "private", use_topics=False).state.code, "switches_off")

    def test_deferred_reasons_and_priority_do_not_mutate_environment(self):
        self.setup_auto()
        before = self.protected_state()
        for reason in ("input", "panel", "minimized", "paused", "busy"):
            state = self.policy.projection(environment_reason=reason)
            self.assertEqual(state.code, "deferred_" + reason)
            self.assertIn("重新等待", state.action)
        self.assertEqual(self.protected_state(), before)
        self.clock.wall = self.clock.wall.replace(hour=23)
        self.assertEqual(self.policy.projection().code, "quiet_hours")
        self.assertEqual(self.policy.projection(environment_reason="input").code, "deferred_input")

    def test_display_previews_environment_recovery_without_resetting_idle(self):
        self.setup_auto()
        self.idle()
        self.policy.environment(self.binding, True, reason="input")
        before = self.protected_state()
        self.assertEqual(self.policy.projection(environment_reason="").code, "environment_resume")
        self.assertEqual(self.protected_state(), before)
        self.policy.environment(self.binding, False, reason="")
        self.assertFalse(self.policy.due())
        self.assertEqual(self.policy.projection(environment_reason="").code, "idle")

    def test_reading_expired_projection_keeps_evidence_and_no_raw_text_in_result(self):
        self.setup_auto()
        self.core.mark_turn("local-owner", self.data()["turns"][0]["id"], "小说纸船", "PRIVATE_STATUS_CANARY")
        record = self.data()["memories"][0]
        self.core.change_memory("local-owner", record["id"], "suppress")
        before = self.protected_state()
        view = self.view()
        self.assertEqual(view.state.code, "topic_missing")
        self.assertNotIn("PRIVATE_STATUS_CANARY", repr(view))
        self.assertNotIn(record["id"], repr(view))
        self.assertEqual(self.protected_state(), before)

    def test_duration_never_goes_negative_or_promises_opening(self):
        self.assertEqual(duration(-1), "0秒")
        self.assertEqual(duration(.1), "1秒")
        self.assertEqual(duration(270), "4分30秒")
        self.setup_auto()
        self.idle()
        self.assertIn("不保证", self.state().action)


if __name__ == "__main__":
    unittest.main()
