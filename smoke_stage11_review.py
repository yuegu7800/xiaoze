"""Stage-eleven regression: actual Tk failure paths, fictional stores/mock HTTP."""
import tkinter as tk
import unittest
from unittest.mock import patch

from smoke_stage10_auto import playground, topic, tick, idle, response
from smoke_local_ui import pump


class AutoLifecycleTkTests(unittest.TestCase):
    def test_worker_start_failure_releases_ui_without_request_or_retry(self):
        with playground() as (app, clock), patch("requests.post", return_value=response()) as post:
            topic(app)
            before = app.core.memory.path.read_bytes()
            app.auto_check.invoke()
            with patch("local_chat.threading.Thread.start", side_effect=RuntimeError("synthetic start failure")):
                idle(app, clock)
            self.assertFalse(app.busy, "failed worker must not leave send permanently disabled")
            self.assertIsNone(app.auto_policy.active)
            self.assertFalse(app.auto_policy.enabled)
            self.assertIsNone(app.auto_after)
            self.assertEqual(str(app.send_button.cget("state")), "normal")
            self.assertEqual(app.core.memory.path.read_bytes(), before)
            self.assertEqual(app.auto_policy.budget.attempt_total, 0)
            post.assert_not_called()
            # A local worker-start error must not pause or poison ordinary chat.
            post.return_value = response("虚构手动恢复回复")
            app.input.insert("1.0", "虚构手动恢复输入")
            app.send()
            pump(app.window, lambda: not app.busy)
            self.assertEqual(len(app.core.snapshot(app.scene.id)["turns"]), 2)
            self.assertEqual(app.auto_policy.budget.attempt_total, 0)
            self.assertFalse(app.auto_policy.enabled)
            post.assert_called_once()

    def test_tick_status_failure_stops_instead_of_losing_timer_silently(self):
        with playground() as (app, clock), patch("requests.post", return_value=response()) as post:
            app.auto_check.invoke()
            with patch.object(app.auto_status, "set", side_effect=tk.TclError("synthetic status failure")):
                tick(app, clock)
            self.assertFalse(app.auto_policy.enabled)
            self.assertIsNone(app.auto_after)
            self.assertFalse(app.busy)
            app.refresh_auto_controls()
            self.assertIn("已停止", app.auto_status.get())
            post.assert_not_called()

    def test_completion_environment_failure_does_not_strand_active_run(self):
        with playground() as (app, clock), patch("requests.post", return_value=response()) as post:
            topic(app)
            app.auto_check.invoke()
            app.auto_adopt_check.invoke()
            # Start from a valid idle window, then collect the queue event
            # without entering Tk's poll (the provider is a synchronous mock).
            for _ in range(19):
                tick(app, clock)
            clock.advance(30)
            app.auto_policy.due()
            app.start_auto_request(app.auto_policy.binding)
            request_id, kind, payload, core, version, sent = app.events.get(timeout=4)
            self.assertEqual(kind, "auto_done")
            self.assertEqual(len(core.snapshot(app.scene.id)["activity"]["drafts"]), 1)
            app.events.put((request_id, kind, payload, core, version, sent))
            original = app.sync_auto_environment
            failed = []
            def fail_once():
                if not failed:
                    failed.append(True)
                    raise tk.TclError("synthetic binding read failure")
                return original()
            with patch.object(app, "sync_auto_environment", side_effect=fail_once):
                app.window.after_cancel(app.poll_after)
                app.poll()
            self.assertFalse(app.busy)
            self.assertFalse(app.auto_policy.enabled)
            self.assertIsNone(app.auto_policy.active)
            self.assertIsNone(app.auto_after)
            self.assertEqual(len(core.snapshot(app.scene.id)["turns"]), 1)
            self.assertEqual(app.auto_policy.budget.adoption_total, 0)
            self.assertEqual(app.auto_policy.budget.attempt_total, 1)
            app.finish_auto_request(kind, payload)  # duplicate cannot revive it
            self.assertEqual(len(core.snapshot(app.scene.id)["turns"]), 1)
            post.assert_called_once()

    def test_close_survives_auto_status_paint_failure_and_clears_callbacks(self):
        with playground() as (app, clock), patch("requests.post", return_value=response()) as post:
            topic(app)
            core = app.core
            app.auto_check.invoke()
            afters = app.poll_after, app.auto_after
            with patch.object(app.auto_status, "set", side_effect=tk.TclError("synthetic paint failure")):
                app.close()
            self.assertTrue(app.closed)
            self.assertTrue(core._paused)
            self.assertFalse(app.auto_policy.enabled)
            remaining = app.window.tk.call("after", "info")
            self.assertTrue(all(item not in remaining for item in afters if item))
            post.assert_not_called()

    def test_timer_registration_failure_does_not_leave_enabled_without_timer(self):
        with playground() as (app, clock), patch("requests.post", return_value=response()) as post:
            app.auto_enabled.set(True)
            with patch.object(app.window, "after", side_effect=tk.TclError("synthetic timer failure")):
                app.toggle_auto()
            self.assertFalse(app.auto_policy.enabled)
            self.assertFalse(app.auto_enabled.get())
            self.assertIsNone(app.auto_after)
            self.assertFalse(app.busy)
            post.assert_not_called()

    def test_stop_still_cancels_when_both_status_views_fail(self):
        with playground() as (app, clock), patch("requests.post", return_value=response()) as post:
            app.auto_check.invoke()
            callback = app.auto_after
            with patch.object(app.auto_status, "set", side_effect=tk.TclError("synthetic auto label failure")), \
                    patch.object(app.status, "set", side_effect=tk.TclError("synthetic main label failure")):
                app.stop_auto()
            self.assertFalse(app.auto_policy.enabled)
            self.assertIsNone(app.auto_after)
            self.assertNotIn(callback, app.window.tk.call("after", "info"))
            self.assertFalse(app.busy)
            self.assertFalse(app.core._paused)
            post.assert_not_called()


if __name__ == "__main__":
    with patch("requests.post", side_effect=AssertionError("unmocked network forbidden")):
        unittest.main(verbosity=2)
