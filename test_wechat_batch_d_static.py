"""D fictional regressions; execution recorded in final E validation."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import test_wechat_character as fixtures
from groups import DEFAULT_GROUP
from core import OWNER
from wechat_character import digest
from wechat_character_panel import WechatCharacterPanel


class BatchDTests(unittest.TestCase):
    setUp = fixtures.IntegratedTests.setUp
    group = fixtures.IntegratedTests.group
    private = fixtures.IntegratedTests.private
    text = fixtures.IntegratedTests.text
    send = fixtures.IntegratedTests.send
    completion = fixtures.IntegratedTests.completion

    def profiles(self, model, names, prefs=None):
        return patch.multiple(model.people,
            people=lambda group, limit=None: names[:limit],
            people_count=lambda group: len(names),
            profile=lambda group, display: {'preferences': prefs or {
                'teasing': '温柔一点', 'tone': '语气简短', 'length': '回答简短'}})

    def test_128_times_three_is_bounded_staged_then_one_atomic_acceptance(self):
        model = self.group()
        names = ['虚构成员%03d' % i for i in range(128)]
        with self.profiles(model, names):
            session = model.character_session(DEFAULT_GROUP)
            before = deepcopy(session.memory.state)
            self.assertEqual(session.people, {})
            self.assertNotIn('wechat_seeded', before)
            self.assertFalse(session.path.exists())
            self.assertEqual(len(session.people_view()), 128)
            self.assertEqual(len(session.core.snapshot(session.scene_id)['memories']), 256)
            self.assertEqual(session._legacy_pending['status']['omitted']['256记录容量（偏好）'], 128)
            self.assertIn('尚未持久化', session.import_status())
            text = self.text([(names[0], '普通新文字')])
            with patch('wechat_character.complete_chat_result', side_effect=self.completion):
                model.reply(DEFAULT_GROUP, text)
            self.assertEqual(before, session.memory.state)
            model.discard_pending()
            self.assertFalse(session.path.exists())
            self.send(model, DEFAULT_GROUP, text)
        saved = json.loads(session.path.read_text(encoding='utf-8'))
        self.assertTrue(saved['wechat_seeded'])
        self.assertEqual(len(saved['wechat_people']), 128)
        self.assertEqual(len(saved['scenes'][session.scene_id]['memories']), 256)
        self.assertIn('wechat_import', saved)
        self.assertIsNone(session._legacy_pending)
        self.assertEqual(len(session.people), 128)
        self.assertIn('已持久化', session.import_status())
        reloaded = self.group()
        with patch.object(reloaded.people, 'people', side_effect=AssertionError('must not reimport')):
            other = reloaded.character_session(DEFAULT_GROUP)
        other.core.reset_scene(other.scene_id)
        again = self.group().character_session(DEFAULT_GROUP)
        self.assertEqual(again.core.snapshot(again.scene_id)['memories'], [])
        self.assertIsNone(again._legacy_pending)

    def test_history_and_profile_union_overflow_counted_without_loading_excess_profiles(self):
        model = self.group()
        model.history[DEFAULT_GROUP] = [{'role': 'user', 'content': self.text([('历史成员', '旧原话')])}]
        names = ['档案%03d' % i for i in range(140)]
        visited = []
        with self.profiles(model, names), patch.object(model.people, 'profile', side_effect=lambda g, d:
                visited.append(d) or {'preferences': {'tone': '温柔'}}):
            session = model.character_session(DEFAULT_GROUP)
        self.assertEqual(len(session.people_view()), 128)
        self.assertEqual(len(visited), 127)
        omitted = session._legacy_pending['status']['omitted']
        self.assertEqual(omitted['人物扫描窗口外（档案）'], 12)
        self.assertEqual(omitted['人物并集容量（档案）'], 1)

    def test_invalid_rows_batches_profile_shapes_and_aliases_are_not_promotions(self):
        model = self.group()
        model.history[DEFAULT_GROUP] = [None, {'role': 'user', 'content': 42},
            {'role': 'user', 'content': 'not JSON'},
            {'role': 'user', 'content': self.text([('半批不得登记', '有效'), ('乙', ' ')])},
            {'role': 'assistant', 'content': '未知来源回复'},
            {'role': 'user', 'content': self.text([('X_A', '旧原话')])}]
        names = ['X_A', '孤月大人', '朋友', '无效结构', '空偏好']
        profiles = {'X_A': {'preferences': {'tone': '温柔'}},
                    '孤月大人': {'preferences': {'tone': '别名重复值', 'teasing': '怼主人'}},
                    '朋友': {'preferences': {'tone': '温柔', 'length': 3, 'unsupported': '不兼容'}},
                    '无效结构': [], '空偏好': {'preferences': []}}
        with self.profiles(model, names), patch.object(model.people, 'profile', side_effect=lambda g, d: profiles[d]):
            session = model.character_session(DEFAULT_GROUP)
        view = session.people_view()
        self.assertEqual(set(view), {'owner', 'display:' + digest('朋友')})
        data = session.core.snapshot(session.scene_id)
        owners = [r for r in data['memories'] if r['person_id'] == 'owner']
        self.assertEqual(len(owners), 1)
        self.assertEqual(owners[0]['content'], '温柔')
        self.assertEqual(owners[0]['source'], 'legacy_review')
        self.assertTrue(any(s.startswith('legacy-') and not s.startswith('legacy-profile-') for s in owners[0]['source_ids']))
        self.assertTrue(all(t['backend'] == 'wechat-legacy' and not t['reply'] for t in data['turns']))
        self.assertIsNone(session.last_person)
        self.assertTrue(session._legacy_pending['status']['omitted'])

    def test_model_and_write_failure_leave_pending_marker_and_manifest_unaccepted(self):
        model = self.private()
        model.history[OWNER] = [{'role': 'user', 'content': '虚构旧原话'}]
        session = model.character_session(OWNER)
        before = deepcopy(session.memory.state)
        with patch('wechat_character.complete_chat_result', side_effect=RuntimeError('mock')):
            with self.assertRaises(RuntimeError):
                model.reply(OWNER, '新文字')
        self.assertEqual(before, session.memory.state)
        with patch('wechat_character.complete_chat_result', side_effect=self.completion):
            reply = model.reply(OWNER, '新文字')
        with patch('memory_store.atomic_json', side_effect=OSError('synthetic write failure')):
            with self.assertRaises(OSError):
                model.remember(OWNER, '新文字', reply)
        self.assertEqual(before, session.memory.state)
        self.assertEqual(session.people, {})
        self.assertIsNotNone(session._legacy_pending)
        self.assertFalse(session.path.exists())
        self.assertIsNone(session.core._prepared_exchange)
        model.discard_pending()
        self.send(model, OWNER, '明确下一轮')
        session.core.reset_scene(session.scene_id)
        reloaded = self.private().character_session(OWNER)
        self.assertEqual(reloaded.core.snapshot(reloaded.scene_id)['turns'], [])
        self.assertIsNone(reloaded._legacy_pending)

    def test_import_interruption_does_not_publish_session_and_old_boolean_blocks_import(self):
        model = self.group()
        with patch.object(model.people, 'people', side_effect=OSError('synthetic interruption')):
            with self.assertRaises(OSError):
                model.character_session(DEFAULT_GROUP)
        self.assertEqual(model.character_sessions, {})
        session = self.send(model, DEFAULT_GROUP, self.text([('甲', '新文字')]))
        state = json.loads(session.path.read_text(encoding='utf-8'))
        state.pop('wechat_import', None)
        session.path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        other = self.group()
        with patch.object(other.people, 'people', side_effect=AssertionError('boolean means processed')):
            reloaded = other.character_session(DEFAULT_GROUP)
        self.assertIsNone(reloaded._legacy_pending)
        self.assertIn('旧版仅有完成标记', reloaded.import_status())

    def test_navigation_selects_only_exact_captured_session_without_constructing(self):
        a = SimpleNamespace(contact='虚构群甲', scene=SimpleNamespace(kind='group'))
        b = SimpleNamespace(contact=OWNER, scene=SimpleNamespace(kind='private'))
        runner = object()
        changed = []
        panel = SimpleNamespace(runner=runner, app=SimpleNamespace(runner=runner),
            available=lambda: {'甲': a, '私聊': b}, chosen=SimpleNamespace(set=changed.append), refresh=lambda: None)
        self.assertFalse(WechatCharacterPanel.select_session(panel, '虚构群乙', 'group'))
        self.assertEqual(changed, [])
        self.assertTrue(WechatCharacterPanel.select_session(panel, '虚构群甲', 'group'))
        self.assertEqual(changed, ['甲'])
