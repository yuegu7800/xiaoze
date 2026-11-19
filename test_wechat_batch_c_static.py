"""C fictional regressions; execution recorded in final E validation.

Synthetic temporary stores and mocked completion/transport only. Existing
runner/dual/quota and non-integrated assertions remain unchanged.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_wechat_character as fixtures
from activity import audience_quiet, wechat_opening_context
from character_service import Cancelled
from core import OWNER, CompletionResult, PrivateRunner, PrivateScope, ScopedTransport
from groups import DEFAULT_GROUP, SILENCE, GroupScope, GroupPolicy
from memory_store import empty_scene, make_record, expire
from wechat_character import digest, make_model
from test_core import parse, message
from test_sessions import FakeDesktop


class BatchCTests(unittest.TestCase):
    setUp = fixtures.IntegratedTests.setUp
    private = fixtures.IntegratedTests.private
    group = fixtures.IntegratedTests.group
    text = fixtures.IntegratedTests.text
    send = fixtures.IntegratedTests.send
    completion = fixtures.IntegratedTests.completion

    def task(self):
        return json.loads(self.calls[-1][-1]['content'])

    def opening(self, model, contact=DEFAULT_GROUP):
        with patch('wechat_character.complete_chat_result', side_effect=self.completion) as complete:
            reply = model.reply(contact, '', proactive=True)
        self.assertEqual(complete.call_count, 1)
        return reply

    def test_reload_uses_actual_order_not_manifest_or_profile(self):
        model = self.group()
        self.send(model, DEFAULT_GROUP, self.text([('甲', '小说'), ('乙', '音乐'), ('甲', '结尾怎么写')]))
        reloaded = self.group()
        session = reloaded.character_session(DEFAULT_GROUP)
        self.assertEqual(list(session.people.values()), ['甲', '乙'])
        self.assertEqual(session.last_person, 'display:' + digest('甲'))
        self.opening(reloaded)
        task = self.task()
        self.assertEqual(task['last_actual_speaker'], session.last_person)
        self.assertEqual(set(task['retrieval_participants']), set(session.people))
        self.assertEqual(task['reply_target'], 'all')
        self.assertFalse(task['is_user_speech'])
        for forbidden in ('speaker_id', 'text', 'recorded_at'):
            self.assertNotIn(forbidden, task)
        reloaded.discard_pending()

    def test_no_candidate_ui_required_one_call_and_real_lineage(self):
        model = self.private()
        session = self.send(model, OWNER, '小说纸船还没想好结尾')
        before = session.core.snapshot(session.scene_id, compatibility=False)
        self.assertNotIn('activity', before)
        self.opening(model, OWNER)
        request = self.calls[-1]
        self.assertIn('小说纸船还没想好结尾', json.dumps(request, ensure_ascii=False))
        self.assertTrue(any(m['role'] == 'assistant' and m['content'] == '虚构回复' for m in request))
        model.validate_pending()
        model.remember(OWNER, '', '虚构回复', proactive=True)
        after = session.core.snapshot(session.scene_id, compatibility=False)
        event = after['turns'][-1]
        self.assertEqual(event['kind'], 'local_assistant')
        self.assertEqual(event['text'], '')
        self.assertEqual(event['reply_trace']['source_ids'], [before['turns'][-1]['id']])
        self.assertNotIn(event['id'], event['reply_trace']['raw_source_ids'])
        self.assertEqual(after['processed'], before['processed'])
        self.assertEqual(after['memories'], before['memories'])
        self.assertIsNone(session.core.feedback_view(session.scene_id)[-1]['evidence'])
        self.assertIsNone(session.core._prepared_exchange)

    def test_personal_topic_target_and_revision_deletion_expiry_cancel(self):
        for action in ('edit', 'delete', 'expire', 'pause'):
            with self.subTest(action=action):
                # A fresh isolated temporary file per scenario, never live data.
                self.setUp()
                model = self.group()
                session = self.send(model, DEFAULT_GROUP, self.text([('甲', '小说纸船结尾')]))
                self.send(model, DEFAULT_GROUP, self.text([('乙', '天气不错')]))
                core, sid = session.core, session.scene_id
                now = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
                session.memory.clock = lambda: now
                source = core.snapshot(sid)['turns'][0]['id']
                core.enable_activity(sid)
                topic = core.mark_topic(sid, source, '小说纸船', '小说纸船', '讨论结尾方向', confirm=True)
                def completed(*args, **kw):
                    result = self.completion(*args, **kw)
                    nonlocal now
                    if action == 'edit':
                        core.change_topic(sid, topic, 'edit', title='新方向', direction='新结尾')
                    elif action == 'delete':
                        core.change_topic(sid, topic, 'delete')
                    elif action == 'expire':
                        now += timedelta(days=8)
                    else:
                        model.discard_pending()
                    return result
                with patch('wechat_character.complete_chat_result', side_effect=completed) as complete:
                    model.reply(DEFAULT_GROUP, '', proactive=True)
                self.assertEqual(complete.call_count, 1)
                self.assertEqual(self.task()['reply_target'], 'display:' + digest('甲'))
                self.assertIn('讨论结尾方向', self.calls[-1][0]['content'])
                with self.assertRaises(Cancelled):
                    model.validate_pending()
                model.discard_pending()
                self.assertIsNone(core._prepared_exchange)
                self.assertFalse(any(t['kind'] == 'local_assistant' for t in core.snapshot(sid)['turns']))

    def test_topic_not_before_then_due_all_target_and_withdrawal(self):
        model = self.group()
        session = self.send(model, DEFAULT_GROUP, self.text([('甲', '小说结尾'), ('乙', '一起讨论')]))
        core, sid = session.core, session.scene_id
        now = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
        session.memory.clock = lambda: now
        core.enable_activity(sid)
        topic = core.mark_topic(sid, core.snapshot(sid)['turns'][0]['id'], '小说', '纸船', '独有待续方向',
                               target='all', wait_minutes=10, confirm=True)
        self.opening(model)
        self.assertIsNone(self.task()['topic_id'])
        self.assertNotIn('独有待续方向', json.dumps(self.calls[-1], ensure_ascii=False))
        model.discard_pending()
        now += timedelta(minutes=11)
        self.opening(model)
        self.assertEqual(self.task()['topic_id'], topic)
        self.assertEqual(self.task()['reply_target'], 'all')
        model.remember(DEFAULT_GROUP, '', '虚构回复', proactive=True)
        event = core.snapshot(sid)['turns'][-1]
        self.assertEqual(event['target'], 'all')
        self.assertIn(topic, event['reply_trace']['topics'])
        core.change_topic(sid, topic, 'delete')
        self.assertFalse(any(t['reply'] for t in core.snapshot(sid)['turns'] if t['kind'] == 'local_assistant'))
        self.opening(model)
        self.assertIsNone(self.task()['topic_id'])
        self.assertNotIn('独有待续方向', json.dumps(self.calls[-1], ensure_ascii=False))
        model.discard_pending()

    def test_silence_failure_and_cancel_release_without_commit(self):
        model = self.private()
        session = self.send(model, OWNER, '继续写小说')
        before = deepcopy(session.memory.state)
        with patch('wechat_character.complete_chat_result', return_value=CompletionResult(SILENCE, 'stop')) as complete:
            self.assertEqual(model.reply(OWNER, '', proactive=True), SILENCE)
        self.assertEqual(complete.call_count, 1)
        self.assertIsNone(session.core._prepared_exchange)
        self.assertIsNone(model._prepared)
        with self.assertRaises(Cancelled):
            model.validate_pending()
        with patch('wechat_character.complete_chat_result', side_effect=RuntimeError('mock failure')) as complete:
            with self.assertRaises(RuntimeError):
                model.reply(OWNER, '', proactive=True)
        self.assertEqual(complete.call_count, 1)
        self.assertEqual(before, session.memory.state)
        self.assertIsNone(session.core._prepared_exchange)
        self.send(model, OWNER, '今天想安静一点')
        with patch('wechat_character.complete_chat_result') as complete:
            self.assertEqual(model.reply(OWNER, '', proactive=True), SILENCE)
        complete.assert_not_called()

    def test_legacy_timestamp_unknown_and_no_reliable_history_is_zero_call(self):
        model = self.group()
        # Mock archive input is fictional and has unknown original time.
        model.history[DEFAULT_GROUP] = [{'role': 'user', 'content': self.text([('甲', '今天想安静一点')])}]
        session = model.character_session(DEFAULT_GROUP)
        self.assertIsNone(session.last_person)
        with patch('wechat_character.complete_chat_result') as complete:
            self.assertEqual(model.reply(DEFAULT_GROUP, '', proactive=True), SILENCE)
        complete.assert_not_called()
        self.send(model, DEFAULT_GROUP, self.text([('乙', '聊小说')]))
        self.opening(model)
        self.assertEqual(self.task()['last_actual_speaker'], 'display:' + digest('乙'))
        self.assertEqual(self.task()['retrieval_participants'], ['display:' + digest('乙')])
        legacy = [json.loads(m['content']) for m in self.calls[-1] if m['role'] == 'user' and 'recorded_at' in m['content']]
        self.assertTrue(any(m['recorded_at'] is None for m in legacy))
        model.discard_pending()

    def test_bounded_scope_cannot_bypass_quiet_and_expired_temporary(self):
        now = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
        data = empty_scene()
        people = ['a', 'b']
        for i in range(10):
            data['turns'].append({'id': str(i), 'speaker_id': 'b', 'text': '小说',
                                  'kind': 'local_exchange', 'backend': 'wechat-observed', 'created_at': now.isoformat()})
        data['memories'].append(make_record('s', 'a', 'quiet-root', 'temporary', 'quiet', '想安静', '', 0,
                                           now, expires_at=(now + timedelta(minutes=1)).isoformat()))
        self.assertTrue(audience_quiet(data, set(people), now))
        self.assertIsNone(wechat_opening_context(data, people, True, now))
        expire(data, now + timedelta(minutes=2))
        plan = wechat_opening_context(data, people, True, now + timedelta(minutes=2))
        self.assertEqual(plan['participants'], ['b'])
        self.assertEqual(plan['boundary_audience'], people)
        self.assertEqual(len(plan['source_ids']), 8)
        self.assertEqual(plan['target'], 'all')
        data['turns'].append(dict(data['turns'][-1], id='ending', text='不聊了'))
        self.assertIsNone(wechat_opening_context(data, people, True, now))

    def test_single_member_group_and_shared_budget_and_private_isolation(self):
        self.send(self.private(), OWNER, '私聊独有暗号')
        model = self.group()
        session = self.send(model, DEFAULT_GROUP, self.text([('甲', '小说')]))
        self.opening(model)
        self.assertEqual(self.task()['reply_target'], 'display:' + digest('甲'))
        model.discard_pending()
        self.send(model, DEFAULT_GROUP, self.text([('乙', '结尾')]))
        now = session.memory.clock()
        data = session.core.snapshot(session.scene_id, compatibility=False)
        for pid in session.people:
            for i in range(5):
                data['memories'].append(make_record(session.scene_id, pid, pid + str(i), 'boundary',
                    'manual' + str(i), '界' * 300, '', 0, now))
        session.core._save(session.scene_id, data)
        self.opening(model)
        claims = json.JSONDecoder().raw_decode(self.calls[-1][0]['content'].split('【当前有效资料】')[1])[0]['claims']
        self.assertEqual(len(claims), 6)
        self.assertEqual(sum(len(c['content']) for c in claims), 1800)
        self.assertNotIn('私聊独有暗号', json.dumps(self.calls[-1], ensure_ascii=False))
        model.discard_pending()
        other = make_model(self.cfg, GroupScope('虚构另一群'), self.persona,
                           self.root / 'other.json', GroupPolicy())
        other_input = json.dumps({'group': '虚构另一群', 'messages': [
            {'sender_display': '丙', 'text': '另群独有音乐'}]})
        self.send(other, '虚构另一群', other_input)
        self.opening(other, '虚构另一群')
        self.assertNotIn('小说', json.dumps(self.calls[-1], ensure_ascii=False))
        other.discard_pending()

    def test_runner_silent_opening_zero_send_and_quota_unchanged(self):
        desktop, model = FakeDesktop(), self.private()
        runner = PrivateRunner(ScopedTransport(desktop, PrivateScope(), parse), model,
                               self.root / 'preview.jsonl', lambda *_: None, {OWNER: 'owner'})
        runner.initialize_session()
        runner.resume('live', proactive=True)
        desktop.frames[OWNER].append(message('小说结尾'))
        with patch('wechat_character.complete_chat_result', side_effect=self.completion):
            runner.process_once()
        self.assertEqual(len(desktop.sent), 1)
        session = model.character_session(OWNER)
        before = deepcopy(session.memory.state)
        runner.next_proactive = 0
        count = runner.proactive_count
        with patch('core.time.localtime', return_value=SimpleNamespace(tm_hour=12)), patch(
                'wechat_character.complete_chat_result', return_value=CompletionResult(SILENCE, 'stop')) as complete:
            runner.process_once()
        self.assertEqual(complete.call_count, 1)
        self.assertEqual(len(desktop.sent), 1)
        self.assertEqual(runner.proactive_count, count)
        self.assertEqual(before, session.memory.state)
        self.assertIsNone(session.core._prepared_exchange)
        self.assertTrue(runner.enabled.is_set())
