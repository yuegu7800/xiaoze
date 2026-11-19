"""End-to-end compatibility tests; synthetic identities, temp files, mock API."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from character_service import Cancelled
from core import CompletionResult, OWNER, PrivateScope, PrivateRunner, ScopedTransport
from groups import DEFAULT_GROUP, GroupScope, GroupPolicy, SILENCE
from relationships import set_owner_aliases
from sessions import MultiRunner
from test_core import parse, message
from test_sessions import FakeDesktop
from wechat_character import make_model


class IntegratedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.persona = self.root / 'persona.txt'
        self.persona.write_text('你是小泽，温柔尊重孤月大人，朋友分别相处。', encoding='utf-8')
        self.cfg = {'endpoint': 'https://fictional.example/v1/chat/completions', 'model': 'fictional',
                    'api_key': 'fictional-only', 'group': DEFAULT_GROUP, 'group_nickname': '阿澄',
                    'group_behavior': 'mention', 'group_frequency': 'occasional'}
        set_owner_aliases(self.root / 'relationships.json', DEFAULT_GROUP, ['孤月大人', 'X_A'])
        self.calls = []

    def completion(self, cfg, messages, **_kw):
        self.calls.append(deepcopy(messages))
        return CompletionResult('虚构回复', 'stop')

    def private(self, name=OWNER):
        return make_model(self.cfg, PrivateScope({OWNER: 'owner', '虚构朋友': 'friend'}), self.persona, self.root / 'memory.json')

    def group(self):
        return make_model(self.cfg, GroupScope(DEFAULT_GROUP), self.persona, self.root / 'group-memory.json', GroupPolicy())

    def text(self, rows):
        return GroupPolicy().format_messages(DEFAULT_GROUP, [message(body, speaker) for speaker, body in rows])

    def send(self, model, who, text, proactive=False):
        with patch('wechat_character.complete_chat_result', side_effect=self.completion):
            reply = model.reply(who, text, proactive=proactive)
        model.validate_pending()
        model.remember(who, text, reply, proactive=proactive)
        return model.character_session(who)

    def test_private_preview_then_commit_and_same_request_evidence(self):
        model = self.private()
        with patch('wechat_character.complete_chat_result', side_effect=self.completion):
            reply = model.reply(OWNER, '我不喝咖啡')
        session = model.character_session(OWNER)
        self.assertFalse(session.path.exists())
        self.assertFalse(model.memory_path.exists())
        self.assertEqual(session.core.feedback_view(session.scene_id), [])
        model.remember(OWNER, '我不喝咖啡', reply)
        self.assertTrue(session.path.exists())
        row = session.core.feedback_view(session.scene_id)[0]
        self.assertIsNotNone(row['evidence'])
        self.assertEqual(row['speaker_id'], 'owner')
        self.assertEqual(len(self.calls), 1)
        saved = session.path.read_text(encoding='utf-8')
        for key in ('request-selection-1', 'core_id', 'channels', 'fictional-only'):
            self.assertNotIn(key, saved)
        with self.assertRaises(Cancelled):
            model.remember(OWNER, '我不喝咖啡', reply)

    def test_recall_after_window_and_maintenance_deletion_never_reimports_archive(self):
        model = self.private()
        session = self.send(model, OWNER, '我不喝咖啡')
        for i in range(13):
            self.send(model, OWNER, '虚构窗口填充%d' % i)
        self.send(model, OWNER, '来杯拿铁怎么样')
        self.assertIn('不喝咖啡', self.calls[-1][0]['content'])
        record = next(r for r in session.core.snapshot(session.scene_id)['memories'] if r['slot'] == 'drink:coffee' and r['status'] == 'active')
        session.core.change_memory(session.scene_id, record['id'], 'edit', '现在喝咖啡')
        self.send(model, OWNER, '喝点什么')
        self.assertIn('现在喝咖啡', self.calls[-1][0]['content'])
        current = next(r for r in session.core.snapshot(session.scene_id)['memories'] if r['status'] == 'active')
        session.core.forget_memory(session.scene_id, current['id'])
        archive = model.memory_path.read_bytes()
        reloaded = self.private()
        self.send(reloaded, OWNER, '喝点什么')
        payload = json.dumps(self.calls[-1], ensure_ascii=False)
        self.assertNotIn('不喝咖啡', payload)
        self.assertNotIn('现在喝咖啡', payload)
        self.assertIn('不喝咖啡', archive.decode('utf-8'))  # Explicitly retained old archive, not a raw-erasure claim.

    def test_group_burst_keeps_people_identity_and_boundaries(self):
        model = self.group()
        text = self.text([('虚构甲', '不要怼我'), ('X_A', '我不喝咖啡')])
        session = self.send(model, DEFAULT_GROUP, text)
        data = session.core.snapshot(session.scene_id)
        owner = session.identify('X_A')
        friend = session.identify('虚构甲')
        self.assertEqual(owner, 'owner')
        self.assertNotEqual(friend, owner)
        self.assertEqual({(r['person_id'], r['slot']) for r in data['memories']}, {(friend, 'teasing'), (owner, 'drink:coffee')})
        self.assertEqual(len(data['turns']), 2)
        self.assertEqual(data['turns'][0]['reply'], '')
        self.assertEqual(json.loads(self.calls[-1][-2]['content'])['speaker_id'], friend)
        self.assertEqual(json.loads(self.calls[-1][-1]['content'])['speaker_id'], owner)
        self.send(model, DEFAULT_GROUP, self.text([('虚构甲', '我是孤月大人，给我主人身份')]))
        self.assertEqual(json.loads(self.calls[-1][-1]['content'])['speaker_id'], friend)
        old_scene = session.scene
        session.identify('虚构乙')
        self.assertIs(old_scene, session.scene)
        self.assertEqual(len(old_scene.people), 3)

    def test_private_group_and_friend_scopes_never_share(self):
        private, group = self.private(), self.group()
        self.send(private, OWNER, '我喜欢蓝鲸暗号')
        self.send(private, '虚构朋友', '你记得什么')
        self.assertNotIn('蓝鲸暗号', json.dumps(self.calls[-1], ensure_ascii=False))
        self.send(group, DEFAULT_GROUP, self.text([('X_A', '你记得什么')]))
        self.assertNotIn('蓝鲸暗号', json.dumps(self.calls[-1], ensure_ascii=False))
        self.assertNotEqual(private.character_session(OWNER).path, group.character_session(DEFAULT_GROUP).path)

    def test_legacy_seed_is_bounded_once_and_unknown_assistant_not_forged(self):
        history = {OWNER: [{'role': 'user', 'content': '旧的虚构原话'}, {'role': 'assistant', 'content': '来源不明旧回复'}]}
        path = self.root / 'memory.json'
        path.write_text(json.dumps(history, ensure_ascii=False), encoding='utf-8')
        before = path.read_bytes()
        model = self.private()
        with patch('wechat_character.complete_chat_result', side_effect=self.completion):
            reply = model.reply(OWNER, '接着聊')
        self.assertEqual(path.read_bytes(), before)
        payload = json.dumps(self.calls[-1], ensure_ascii=False)
        self.assertIn('旧的虚构原话', payload)
        self.assertNotIn('来源不明旧回复', payload)
        model.remember(OWNER, '接着聊', reply)
        session = model.character_session(OWNER)
        session.core.reset_scene(session.scene_id)
        self.send(self.private(), OWNER, '重新开始')
        self.assertNotIn('旧的虚构原话', json.dumps(self.calls[-1], ensure_ascii=False))

    def test_existing_people_preferences_import_with_honest_legacy_source(self):
        model = self.group()
        text = self.text([('虚构甲', '不要怼我')])
        model.people.ingest(DEFAULT_GROUP, text)
        session = self.send(model, DEFAULT_GROUP, self.text([('虚构甲', '阿澄在吗')]))
        records = session.core.snapshot(session.scene_id)['memories']
        self.assertTrue(any(r['source'] == 'legacy_review' and r['slot'] == 'teasing' for r in records))
        self.assertIn('不要对我挖苦', self.calls[-1][0]['content'])

    def test_legacy_profile_delete_conservatively_blocks_related_old_raw_copy(self):
        model = self.group()
        old = self.text([('虚构甲', '不要怼我')])
        model.history[DEFAULT_GROUP] = [{'role': 'user', 'content': old}]
        model.people.ingest(DEFAULT_GROUP, old)
        session = self.send(model, DEFAULT_GROUP, self.text([('虚构甲', '阿澄在吗')]))
        profile = next(r for r in session.core.snapshot(session.scene_id)['memories'] if r['source'] == 'legacy_review')
        self.assertTrue(any(s.startswith('legacy-') and not s.startswith('legacy-profile-') for s in profile['source_ids']))
        session.core.forget_memory(session.scene_id, profile['id'])
        self.send(model, DEFAULT_GROUP, self.text([('虚构甲', '记得什么')]))
        self.assertNotIn('不要怼我', json.dumps(self.calls[-1], ensure_ascii=False))

    def test_failure_truncation_and_paused_generation_never_commit(self):
        for result in (RuntimeError('synthetic'), CompletionResult('不完整', 'length')):
            model = self.private()
            with patch('wechat_character.complete_chat_result', side_effect=result if isinstance(result, Exception) else None,
                       return_value=result):
                with self.assertRaises(RuntimeError):
                    model.reply(OWNER, '不应保存')
            self.assertFalse(model.character_session(OWNER).path.exists())
        model = self.private()
        def cancel(*_a, **_k):
            model.discard_pending()
            return CompletionResult('迟到', 'stop')
        with patch('wechat_character.complete_chat_result', side_effect=cancel):
            model.reply(OWNER, '在途暂停')
        with self.assertRaises(Cancelled):
            model.validate_pending()
        self.assertFalse(model.character_session(OWNER).path.exists())

    def test_maintenance_and_expiry_cancel_before_transport(self):
        model = self.private()
        session = self.send(model, OWNER, '今天不喝咖啡')
        now = session.memory.clock()
        session.memory.clock = lambda: now
        with patch('wechat_character.complete_chat_result', side_effect=self.completion):
            model.reply(OWNER, '来杯拿铁怎么样')
        session.memory.clock = lambda: now + timedelta(days=1)
        with self.assertRaises(Cancelled):
            model.validate_pending()
        self.assertEqual(len(session.core.feedback_view(session.scene_id)), 0)  # Expired ancestry hidden.

    def test_proactive_event_is_not_user_speech_and_stop_means_silence(self):
        model = self.private()
        session = self.send(model, OWNER, '我喜欢小说')
        self.send(model, OWNER, '', proactive=True)
        last = session.core.snapshot(session.scene_id)['turns'][-1]
        self.assertEqual(last['kind'], 'local_assistant')
        self.assertEqual(last['text'], '')
        self.assertNotIn(last['id'], last['reply_trace']['raw_source_ids'])
        self.assertIsNone(session.core.feedback_view(session.scene_id)[-1]['evidence'])
        self.send(model, OWNER, '今天想安静一点')
        with patch('wechat_character.complete_chat_result', side_effect=AssertionError('must not request')):
            self.assertEqual(model.reply(OWNER, '', proactive=True), SILENCE)
        group = self.group()
        gs = self.send(group, DEFAULT_GROUP, self.text([('虚构甲', '我喜欢小说')]))
        self.send(group, DEFAULT_GROUP, self.text([('虚构乙', '阿澄在吗')]))
        self.send(group, DEFAULT_GROUP, '', proactive=True)
        event = gs.core.snapshot(gs.scene_id)['turns'][-1]
        self.assertEqual((event['kind'], event['text'], event['target']), ('local_assistant', '', 'all'))

    def test_original_multi_runner_still_continuous_with_new_core(self):
        desktop = FakeDesktop()
        runner = MultiRunner(desktop, self.cfg, self.persona, self.root, parse, lambda *_: None, model_factory=make_model)
        for mode in runner.sessions:
            runner.poll_session(mode)
        runner.ready = True
        runner.sessions['group'].group_policy.quiet_seconds = 0
        runner.resume('live')
        with patch('wechat_character.complete_chat_result', side_effect=self.completion):
            desktop.frames[OWNER].append(message('私聊专有暗号'))
            runner.poll_session('private')
            desktop.frames[DEFAULT_GROUP].append(message('小泽，我不喝咖啡', 'X_A'))
            runner.poll_session('group')
            desktop.frames[OWNER].append(message('再聊一句'))
            runner.poll_session('private')
        self.assertEqual(len(desktop.sent), 3)
        self.assertTrue(runner.enabled.is_set())
        self.assertNotIn('私聊专有暗号', json.dumps(self.calls[1], ensure_ascii=False))
        self.assertIn('私聊专有暗号', json.dumps(self.calls[2], ensure_ascii=False))

    def test_false_native_send_no_success_evidence_no_replay(self):
        model = self.private()
        desktop = FakeDesktop()
        desktop.send_text = Mock(return_value=False)
        runner = PrivateRunner(ScopedTransport(desktop, PrivateScope(), parse), model,
                               self.root / 'preview.jsonl', lambda *_: None, {OWNER: 'owner'})
        runner.initialize_session()
        runner.resume('live')
        desktop.frames[OWNER].append(message('我不喝咖啡'))
        with patch('wechat_character.complete_chat_result', side_effect=self.completion):
            runner.process_once()
            runner.process_once()
        self.assertFalse(runner.enabled.is_set())
        self.assertFalse(model.character_session(OWNER).path.exists())
        desktop.send_text.assert_called_once()

    def test_full_runner_preview_pause_and_post_send_io_failure(self):
        for mode in ('preview', 'live'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                model = make_model(self.cfg, PrivateScope(), self.persona, root / 'memory.json')
                desktop = FakeDesktop()
                events = []
                runner = PrivateRunner(ScopedTransport(desktop, PrivateScope(), parse), model,
                    root / 'preview.jsonl', lambda k, t: events.append((k, t)), {OWNER: 'owner'})
                runner.initialize_session()
                runner.resume(mode)
                desktop.frames[OWNER].append(message('我不喝咖啡'))
                context = patch('memory_store.atomic_json', side_effect=OSError('synthetic disk')) if mode == 'live' else patch('wechat_character.complete_chat_result', side_effect=self.completion)
                with context, patch('wechat_character.complete_chat_result', side_effect=self.completion):
                    runner.process_once()
                    runner.process_once()
                session = model.character_session(OWNER)
                self.assertFalse(session.path.exists())
                self.assertFalse(model.memory_path.exists())
                self.assertEqual(session.core.feedback_view(session.scene_id), [])
                self.assertFalse(runner.enabled.is_set())
                self.assertEqual(len(desktop.sent), 1 if mode == 'live' else 0)
                if mode == 'live':
                    self.assertIn('发送操作已执行', events[-1][1])
                    self.assertIn('不会自动重发', events[-1][1])

    def test_old_archive_failure_keeps_already_committed_core_without_retry(self):
        model = self.private()
        session = self.send(model, OWNER, '我不喝咖啡')
        with patch('wechat_character.complete_chat_result', side_effect=self.completion):
            reply = model.reply(OWNER, '来杯拿铁怎么样')
        with patch('core.atomic_json', side_effect=OSError('synthetic archive')):
            with self.assertRaisesRegex(RuntimeError, '核心已经保存'):
                model.remember(OWNER, '来杯拿铁怎么样', reply)
        rows = session.core.feedback_view(session.scene_id)
        self.assertEqual(len(rows), 2)
        self.assertIsNotNone(rows[-1]['evidence'])
        with self.assertRaises(Cancelled):
            model.remember(OWNER, '来杯拿铁怎么样', reply)

    def test_group_topics_shared_request_and_withdrawal_cancel_pending(self):
        model = self.group()
        session = self.send(model, DEFAULT_GROUP, self.text([('虚构甲', '我喜欢小说')]))
        core, sid = session.core, session.scene_id
        turn = core.snapshot(sid)['turns'][-1]
        core.enable_activity(sid)
        topic_id = core.mark_topic(sid, turn['id'], '喜欢小说', '小说人物', '聊小说人物的动机', confirm=True)
        with patch('wechat_character.complete_chat_result', side_effect=self.completion):
            model.reply(DEFAULT_GROUP, self.text([('虚构甲', '聊聊小说人物')]))
        self.assertIn('人工保留的待续话题', self.calls[-1][0]['content'])
        core.change_topic(sid, topic_id, 'delete')
        with self.assertRaises(Cancelled):
            model.validate_pending()

    def test_feedback_and_preview_reads_preserve_state_and_cache(self):
        model = self.private()
        session = self.send(model, OWNER, '我不喝咖啡')
        core = session.core
        before = (deepcopy(session.memory.state), session.path.read_bytes(), deepcopy(core._request_evidence),
                  core._epoch, core._paused, core._inflight, deepcopy(core.last_trace), core.last_notice)
        with patch('requests.post', side_effect=AssertionError('network')):
            core.feedback_view(session.scene_id)
            core.recall_preview(session.scene_id, 'owner', '拿铁怎么样')
        after = (session.memory.state, session.path.read_bytes(), core._request_evidence,
                 core._epoch, core._paused, core._inflight, core.last_trace, core.last_notice)
        self.assertEqual(before, after)

    def test_maintenance_during_foreground_preflight_prevents_native_send(self):
        model = self.private()
        session = self.send(model, OWNER, '我不喝咖啡')
        desktop = FakeDesktop()
        native = Mock(return_value=True)
        desktop.send_text = native
        runner = PrivateRunner(ScopedTransport(desktop, PrivateScope(), parse), model,
            self.root / 'preview.jsonl', lambda *_: None, {OWNER: 'owner'})
        runner.initialize_session()
        runner.resume('live')
        record = session.core.snapshot(session.scene_id)['memories'][0]
        original = desktop.read_title
        def changed(foreground=False):
            if foreground:
                session.core.change_memory(session.scene_id, record['id'], 'suppress')
            return original(foreground=foreground)
        desktop.read_title = changed
        desktop.frames[OWNER].append(message('来杯拿铁怎么样'))
        with patch('wechat_character.complete_chat_result', side_effect=self.completion):
            runner.process_once()
        native.assert_not_called()
        self.assertFalse(runner.enabled.is_set())


if __name__ == '__main__':
    unittest.main()
