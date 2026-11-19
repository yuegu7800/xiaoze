"""ASTRA review: actual manual entry points after worker-start failure, mock only."""
import tkinter as tk
import unittest
from unittest.mock import patch

from smoke_stage10_auto import playground, topic, response
from smoke_local_ui import pump


class ManualStartRecoveryTests(unittest.TestCase):
    def test_prelaunch_paint_failure_restores_controls_even_if_notice_cannot_paint(self):
        for candidate in (False, True):
            with self.subTest(candidate=candidate), playground() as (app, _clock), \
                    patch('requests.post', side_effect=AssertionError('network forbidden')) as post:
                topic(app)
                panel = app.show_activity() if candidate else None
                app.input.insert('1.0', '虚构待发内容')
                before = app.core.memory.path.read_bytes()
                view = panel.message if candidate else app.status
                with patch.object(view, 'set', side_effect=tk.TclError('PRIVATE_PAINT_CANARY')):
                    panel.evaluate() if candidate else app.send()
                self.assertFalse(app.busy)
                self.assertEqual(str(app.send_button.cget('state')), 'normal')
                self.assertEqual(app.input.get('1.0', 'end-1c'), '虚构待发内容')
                self.assertEqual(app.core.memory.path.read_bytes(), before)
                self.assertFalse(app.core._inflight)
                post.assert_not_called()

    def test_manual_chat_start_failure_preserves_input_and_allows_explicit_retry(self):
        with playground() as (app, _clock), patch('requests.post', return_value=response('虚构手动回复')) as post:
            topic(app)
            app.auto_check.invoke()
            before = app.core.memory.path.read_bytes()
            app.input.insert('1.0', '虚构待发送输入')
            app.window.update()
            with patch('local_chat.threading.Thread.start', side_effect=RuntimeError('PRIVATE_START_CANARY')):
                try:
                    app.send()
                except RuntimeError:
                    pass  # Assert the observable original failure, not a new API.
            self.assertFalse(app.busy, 'manual send must not remain stuck busy')
            self.assertFalse(app.auto_policy.enabled)
            self.assertIsNone(app.auto_after)
            self.assertFalse(app.core._inflight)
            self.assertEqual(str(app.send_button.cget('state')), 'normal')
            self.assertEqual(app.input.get('1.0', 'end-1c'), '虚构待发送输入')
            self.assertEqual(app.core.memory.path.read_bytes(), before)
            self.assertNotIn('PRIVATE_START_CANARY', app.status.get())
            self.assertIn('未请求模型', app.status.get())
            post.assert_not_called()
            app.send()  # A deliberate new action after the failure.
            pump(app.window, lambda: not app.busy)
            post.assert_called_once()
            self.assertEqual(len(app.core.snapshot(app.scene.id)['turns']), 2)
            self.assertEqual(app.auto_policy.budget.attempt_total, 0)

    def test_manual_candidate_start_failure_does_not_leave_busy_or_commit(self):
        with playground() as (app, _clock), patch('requests.post', return_value=response()) as post:
            topic(app)
            panel = app.show_activity()
            app.auto_check.invoke()
            before = app.core.memory.path.read_bytes()
            app.input.insert('1.0', '候选失败不动这段输入')
            with patch('activity_panel.threading.Thread.start', side_effect=RuntimeError('PRIVATE_START_CANARY')):
                try:
                    panel.evaluate()
                except RuntimeError:
                    pass
            self.assertFalse(app.busy, 'manual candidate must not leave chat stuck busy')
            self.assertFalse(app.auto_policy.enabled)
            self.assertIsNone(app.auto_after)
            self.assertFalse(app.core._inflight)
            self.assertEqual(app.core.memory.path.read_bytes(), before)
            self.assertEqual(app.input.get('1.0', 'end-1c'), '候选失败不动这段输入')
            self.assertIn('未请求模型', panel.message.get())
            self.assertNotIn('PRIVATE_START_CANARY', panel.message.get())
            post.assert_not_called()
            panel.evaluate()
            pump(app.window, lambda: not app.busy)
            post.assert_called_once()
            self.assertEqual(len(app.core.snapshot(app.scene.id)['activity']['drafts']), 1)
            self.assertEqual(app.auto_policy.budget.attempt_total, 0)


if __name__ == '__main__':
    with patch('requests.post', side_effect=AssertionError('unmocked network forbidden')):
        unittest.main(verbosity=2)
