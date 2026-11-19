"""Stage twelve actual Tk controls, fresh fictional stores and mock HTTP only."""
from copy import deepcopy
from datetime import timedelta
import threading
import unittest
from unittest.mock import patch

from local_chat import MODE_SIMULATION
from smoke_local_ui import pump
from smoke_stage10_auto import playground, topic, tick, idle, response
from test_activity import SILENT


class CurrentStatusTkTests(unittest.TestCase):
    def test_countdown_and_missing_topic_do_not_generate_or_advance_scheduler(self):
        with playground() as (app, clock), patch('requests.post') as post:
            app.auto_check.invoke()
            for _ in range(11):
                tick(app, clock)
            policy = app.auto_policy
            original = (policy.last_check, policy.last_activity, policy.activity_version, policy.note,
                        app.core._epoch, deepcopy(app.core.memory.state))
            with patch.object(app.core.memory, 'snapshot', wraps=app.core.memory.snapshot) as snapshot:
                for _ in range(50):
                    app.refresh_auto_controls()
                snapshot.assert_not_called()
            self.assertIn('4分30秒', app.auto_status.get())
            self.assertEqual(original, (policy.last_check, policy.last_activity, policy.activity_version,
                policy.note, app.core._epoch, app.core.memory.state))
            for _ in range(9):
                tick(app, clock)
            self.assertIn('话题 / 主动草稿', app.auto_status.get())
            self.assertFalse(app.core.memory.path.exists())
            self.assertEqual(policy.budget.attempt_total, 0)
            post.assert_not_called()

    def test_metadata_cache_invalidates_at_data_time_and_rewind_boundaries(self):
        with playground() as (app, clock):
            topic(app)
            app.auto_check.invoke()
            # Reach ready without running the UI's generation path.
            for _ in range(20):
                clock.advance(30)
                app.auto_policy.due()
            app.refresh_auto_controls()
            core, scene = app.core, app.scene.id
            data = core.snapshot(scene)
            topic_id = data['activity']['topics'][0]['id']
            with patch.object(core.memory, 'snapshot', wraps=core.memory.snapshot) as snapshot:
                for _ in range(50):
                    app.refresh_auto_controls()
                self.assertEqual(snapshot.call_count, 0)
                clock.advance(1)
                app.refresh_auto_controls(force=False)
                self.assertEqual(snapshot.call_count, 0)
                core.change_topic(scene, topic_id, 'edit', title='虚构修正', direction='讨论另一个结尾')
                snapshot.reset_mock()
                app.refresh_auto_controls()
                self.assertEqual(snapshot.call_count, 1)
                self.assertNotIn('虚构修正', repr(app._auto_candidate_cache[1]))
                clock.wall -= timedelta(seconds=1)
                app.refresh_auto_controls()
                self.assertEqual(snapshot.call_count, 2)
                # Wall-clock expiry invalidates metadata without persisting it.
                before = core.memory.path.read_bytes()
                clock.wall += timedelta(days=8)
                app.refresh_auto_controls()
                self.assertEqual(snapshot.call_count, 3)
                self.assertIn('没有适合', app.auto_status.get())
                self.assertEqual(core.memory.path.read_bytes(), before)
                app.refresh_auto_controls()
                self.assertEqual(snapshot.call_count, 3)
                core.change_topic(scene, topic_id, 'delete')
                snapshot.reset_mock()
                app.refresh_auto_controls()
                self.assertEqual(snapshot.call_count, 1)
                self.assertEqual(app._auto_candidate_cache[1].state.code, 'topic_missing')

    def test_future_topic_becomes_ready_at_boundary_without_display_request(self):
        with playground() as (app, clock), patch('requests.post') as post:
            core, scene = app.core, app.scene.id
            core.exchange(scene, 'owner', '虚构故事还没有结尾', lambda _: '虚构回答')
            core.enable_activity(scene)
            source = core.snapshot(scene)['turns'][0]['id']
            core.mark_topic(scene, source, '故事还没有结尾', '虚构待续', '想结尾', wait_minutes=60, confirm=True)
            app.auto_check.invoke()
            for _ in range(20):
                clock.advance(30)
                app.auto_policy.due()
            app.refresh_auto_controls()
            self.assertIn('50分', app.auto_status.get())
            with patch.object(core.memory, 'snapshot', wraps=core.memory.snapshot) as snapshot:
                clock.wall += timedelta(minutes=50)
                app.refresh_auto_controls()
                self.assertEqual(snapshot.call_count, 1)
                self.assertTrue(app._auto_candidate_cache[1].state.ready)
            self.assertEqual(app.auto_policy.budget.attempt_total, 0)
            post.assert_not_called()

    def test_environment_and_scope_display_clear_metadata_without_restarting(self):
        for change in ('mode', 'person', 'isolated', 'practice', 'topic_switch'):
            with self.subTest(change=change), playground() as (app, clock):
                topic(app)
                app.auto_check.invoke()
                for _ in range(20):
                    clock.advance(30)
                    app.auto_policy.due()
                app.refresh_auto_controls()
                self.assertIsNotNone(app._auto_candidate_cache)
                app.input.insert('1.0', '虚构未发送内容')
                app.window.update()
                app.refresh_auto_controls()
                self.assertIn('输入框', app.auto_status.get())
                app.input.delete('1.0', 'end')
                app.window.update()
                panel = app.show_activity()
                app.refresh_auto_controls()
                self.assertIn('子窗口', app.auto_status.get())
                panel.close()
                with patch.object(app.window, 'state', return_value='iconic'):
                    app.refresh_auto_controls()
                    self.assertIn('最小化', app.auto_status.get())
                if change == 'mode':
                    app.mode.set(MODE_SIMULATION)
                elif change == 'person':
                    app.speaker.set('虚构另一人物')
                elif change == 'isolated':
                    app.practice_isolated.set(True)
                elif change == 'practice':
                    app.practice.set('虚构另一体验组合')
                else:
                    app.use_topics.set(False)
                self.assertFalse(app.auto_policy.enabled)
                self.assertFalse(app.auto_adopt.get())
                self.assertIsNone(app._auto_candidate_cache)
                self.assertIsNone(app.auto_after)
                self.assertIn('关闭', app.auto_status.get())

    def test_inflight_reading_keeps_commit_eligible_and_completion_fact_separate(self):
        with playground() as (app, clock):
            topic(app)
            entered, release = threading.Event(), threading.Event()
            def provider(*_args, **_kwargs):
                entered.set()
                assert release.wait(4)
                return response()
            with patch('requests.post', side_effect=provider) as post:
                app.auto_check.invoke()
                idle(app, clock)
                pump(app.window, entered.is_set)
                core = app.core
                before = (core._epoch, core.memory.path.read_bytes(), deepcopy(core.memory.state),
                          app.auto_policy.activity_version, app.auto_policy.generation)
                try:
                    for _ in range(30):
                        app.refresh_auto_controls()
                        self.assertIn('正在评估', app.auto_status.get())
                    self.assertEqual(before, (core._epoch, core.memory.path.read_bytes(), core.memory.state,
                        app.auto_policy.activity_version, app.auto_policy.generation))
                finally:
                    release.set()
                pump(app.window, lambda: not app.busy)
                self.assertIn('留下待处理草稿', app.status.get())
                self.assertIn('待处理草稿', app.auto_status.get())
                self.assertEqual(len(core.snapshot(app.scene.id)['activity']['drafts']), 1)
                self.assertEqual(app.auto_policy.budget.attempt_total, 1)
                post.assert_called_once()

    def test_silent_cooldown_cache_and_minimum_layout_close(self):
        with playground() as (app, clock), patch('requests.post', return_value=response(SILENT)) as post:
            topic(app)
            app.auto_check.invoke()
            idle(app, clock)
            pump(app.window, lambda: not app.busy)
            self.assertIn('模型选择沉默', app.auto_status.get())
            original_fact = app.status.get()
            with patch.object(app.core.memory, 'snapshot', wraps=app.core.memory.snapshot) as snapshot:
                clock.advance(1)
                app.refresh_auto_controls(force=False)
                self.assertIn('9分59秒', app.auto_status.get())
                self.assertEqual(app.status.get(), original_fact)
                for _ in range(30):
                    app.refresh_auto_controls(force=False)
                snapshot.assert_not_called()
            app.window.geometry('820x680')
            app.window.update()
            self.assertGreaterEqual(app.input.winfo_height(), 40)
            self.assertGreaterEqual(app.transcript.winfo_height(), 75)
            self.assertTrue(app.auto_stop_button.winfo_ismapped())
            self.assertLessEqual(app.send_button.winfo_rooty() + app.send_button.winfo_height(),
                                 app.window.winfo_rooty() + app.window.winfo_height())
            callbacks = (app.poll_after, app.auto_after)
            app.close()
            remaining = app.window.tk.call('after', 'info')
            self.assertTrue(all(c not in remaining for c in callbacks if c))
            self.assertIsNone(app._auto_candidate_cache)
            post.assert_called_once()


if __name__ == '__main__':
    with patch('requests.post', side_effect=AssertionError('unmocked network forbidden')):
        unittest.main(verbosity=2)
