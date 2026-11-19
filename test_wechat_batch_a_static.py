"""Batch A fictional regressions; execution recorded in final E validation."""
from copy import deepcopy
import json
import threading
import unittest
from unittest.mock import Mock, patch
from character_service import Cancelled
from core import OWNER, PrivateRunner, PrivateScope, ScopedTransport, ScopeError, CompletionResult
from groups import DEFAULT_GROUP, SILENCE
from sessions import MultiRunner
from test_core import message, parse
from test_sessions import FakeDesktop
import test_wechat_character as fixtures
from wechat_character import Conversation, make_model, MAX_PEOPLE, digest


class BatchATests(unittest.TestCase):
    setUp = fixtures.IntegratedTests.setUp
    private = fixtures.IntegratedTests.private
    group = fixtures.IntegratedTests.group
    text = fixtures.IntegratedTests.text
    send = fixtures.IntegratedTests.send
    completion = fixtures.IntegratedTests.completion

    def registries(self, s):
        return deepcopy((s.people, s.identities, s.memory.state, s.last_person))

    def test_entire_invalid_batch_and_capacity_leave_no_registration(self):
        s = self.group().character_session(DEFAULT_GROUP)
        before = self.registries(s)
        invalid = [self.text([('新成员', '有效'), ('乙', ' ')]),
                   self.text([('新成员', '有效'), ('', '有效')]),
                   json.dumps({'group': DEFAULT_GROUP, 'messages': [{'text': '缺显示名'}]}),
                   self.text([(str(i), '有效') for i in range(9)]),
                   self.text([('新成员', '字' * 4001)])]
        for text in invalid:
            with self.assertRaises(ValueError):
                s.incoming(text)
            self.assertEqual(before, self.registries(s))
        s.people = {'display:' + digest(str(i)): str(i) for i in range(MAX_PEOPLE)}
        s._refresh_scene()
        before = self.registries(s)
        with self.assertRaises(RuntimeError):
            s.incoming(self.text([('超额新成员', '有效')]))
        self.assertEqual(before, self.registries(s))

    def test_preview_model_failure_discard_and_silence_people(self):
        m = self.group()
        s = m.character_session(DEFAULT_GROUP)
        before = self.registries(s)
        text = self.text([('新成员', '不要怼我')])
        with patch('wechat_character.complete_chat_result', side_effect=RuntimeError('mock')):
            with self.assertRaises(RuntimeError):
                m.reply(DEFAULT_GROUP, text)
        self.assertEqual(before, self.registries(s))
        with patch('wechat_character.complete_chat_result', side_effect=self.completion):
            m.reply(DEFAULT_GROUP, text)
        self.assertEqual(before, self.registries(s))
        m.discard_pending()
        m.discard_pending()
        self.assertIsNone(s.core._prepared_exchange)
        self.assertEqual(before, self.registries(s))
        with self.assertRaises(Cancelled):
            m.validate_pending()
        with patch('wechat_character.complete_chat_result', return_value=CompletionResult(SILENCE, 'stop')):
            self.assertEqual(m.reply(DEFAULT_GROUP, text), SILENCE)
        self.assertEqual(before, self.registries(s))
        m.observe(DEFAULT_GROUP, text)
        self.assertTrue(s.people)
        self.assertIsNone(s.core._prepared_exchange)
        self.assertTrue(all(not t['reply'] for t in s.core.snapshot(s.scene_id)['turns']))

    def test_prepare_feedback_lease_maintenance_and_idempotent_release(self):
        m = self.private()
        s = self.send(m, OWNER, '第一条')
        core, sid = s.core, s.scene_id
        tid = core.snapshot(sid)['turns'][-1]['id']
        with patch('wechat_character.complete_chat_result', side_effect=self.completion):
            reply = m.reply(OWNER, '第二条')
        epoch = core._epoch
        with self.assertRaises(Cancelled):
            core.feedback(sid, tid, ['自然合适'], '')
        self.assertEqual(epoch, core._epoch)
        m.validate_pending()
        m.remember(OWNER, '第二条', reply)
        core.feedback(sid, tid, ['自然合适'], '')
        _, old = core.prepare_exchange(sid, 'owner', '甲', lambda _: '回复')
        old.discard()
        _, current = core.prepare_exchange(sid, 'owner', '乙', lambda _: '回复')
        old.discard()
        self.assertIs(core._prepared_exchange, current)
        current.check_current()
        core.reset_scene(sid)
        with self.assertRaises(Cancelled):
            current.check_current()
        current.discard()
        core.exchange(sid, 'owner', '兼容exchange', lambda _: '回复')
        self.assertIsNone(core._prepared_exchange)

    def runner(self):
        m, desktop = self.private(), FakeDesktop()
        r = PrivateRunner(ScopedTransport(desktop, PrivateScope(), parse), m,
                          self.root / 'preview.jsonl', lambda *_: None, {OWNER: 'owner'})
        r.initialize_session()
        r.resume('live')
        desktop.frames[OWNER].append(message('新文字'))
        return r, m, desktop

    def test_received_busy_cancel_resume_does_not_create_session(self):
        for boundary in ('received', 'busy'):
            r, m, desktop = self.runner()
            def notify(kind, _):
                if kind == boundary:
                    r.pause()
                    r.resume('live')
            r.notify = notify
            with patch('wechat_character.complete_chat_result') as complete:
                r.process_once()
            complete.assert_not_called()
            self.assertEqual(m.character_sessions, {})
            self.assertEqual(desktop.sent, [])

    def test_event_cancel_during_first_constructor_no_fresh_epoch(self):
        r, m, desktop = self.runner()
        entered, release = threading.Event(), threading.Event()
        def construct(*args):
            entered.set()
            if not release.wait(3):
                raise AssertionError('unreleased constructor')
            return Conversation(*args)
        with patch('wechat_character.Conversation', side_effect=construct), patch('wechat_character.complete_chat_result') as complete:
            worker = threading.Thread(target=r.process_once)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                r.pause()
                r.resume('live')
                r.process_once()
            finally:
                release.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
            complete.assert_not_called()
        self.assertEqual(m.character_sessions, {})
        self.assertEqual(desktop.sent, [])

    def test_observe_cancelled_permission_no_registration(self):
        m = self.group()
        s = m.character_session(DEFAULT_GROUP)
        before = self.registries(s)
        with self.assertRaises(ScopeError):
            m.observe(DEFAULT_GROUP, self.text([('新成员', '旁听文字')]),
                      round_permission=Mock(side_effect=ScopeError('cancelled')))
        self.assertEqual(before, self.registries(s))
        self.assertFalse(m.memory_path.exists())

    def test_completion_callback_rechecks_permission_after_assembly(self):
        r, m, desktop = self.runner()
        s = m.character_session(OWNER)
        original = s.core.messages
        def assemble(*args, **kwargs):
            result = original(*args, **kwargs)
            r.pause()
            r.resume('live')
            return result
        with patch.object(s.core, 'messages', side_effect=assemble), patch('wechat_character.complete_chat_result') as complete:
            r.process_once()
        complete.assert_not_called()
        self.assertEqual(desktop.sent, [])
        self.assertIsNone(s.core._prepared_exchange)

    def test_routed_foreground_cancel_resume_never_enters_native_send(self):
        desktop = FakeDesktop()
        r = MultiRunner(desktop, self.cfg, self.persona, self.root, parse,
                        lambda *_: None, model_factory=make_model)
        for name in r.sessions:
            r.poll_session(name)
        r.ready = True
        r.resume('live')
        original = desktop.read_title
        def title(foreground=False):
            if foreground:
                r.pause()
                r.resume('live')
            return original(foreground=foreground)
        desktop.read_title = title
        desktop.frames[OWNER].append(message('新文字'))
        with patch('wechat_character.complete_chat_result', side_effect=self.completion):
            r.poll_session('private')
        self.assertEqual(desktop.sent, [])
        model = r.sessions['private'].model
        self.assertIsNone(model._prepared)
        self.assertIsNone(model.character_session(OWNER).core._prepared_exchange)

    def test_continuous_dual_and_feedback_inside_native_send(self):
        desktop = FakeDesktop()
        r = MultiRunner(desktop, self.cfg, self.persona, self.root, parse,
                        lambda *_: None, model_factory=make_model)
        for name in r.sessions:
            r.poll_session(name)
        r.ready = True
        r.resume('live')
        r.sessions['group'].group_policy.quiet_seconds = 0
        with patch('wechat_character.complete_chat_result', side_effect=self.completion):
            desktop.frames[OWNER].append(message('第一条'))
            r.poll_session('private')
            s = r.sessions['private'].model.character_session(OWNER)
            tid = s.core.snapshot(s.scene_id)['turns'][-1]['id']
            original = desktop.send_text
            def send(target, body):
                with self.assertRaises(Cancelled):
                    s.core.feedback(s.scene_id, tid, ['自然合适'], '')
                return original(target, body)
            desktop.send_text = send
            desktop.frames[OWNER].append(message('第二条'))
            r.poll_session('private')
            desktop.send_text = original
            desktop.frames[DEFAULT_GROUP].append(message('小泽在吗', 'X_A'))
            r.poll_session('group')
        self.assertEqual(len(desktop.sent), 3)
        self.assertTrue(r.enabled.is_set())
        for child in r.sessions.values():
            self.assertIsNone(child.model._prepared)
            for s in child.model.character_sessions.values():
                self.assertIsNone(s.core._prepared_exchange)
