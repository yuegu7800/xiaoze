"""Restore established WeChat capabilities; all data and API responses synthetic."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from core import OWNER, ChatModel, PrivateScope
from groups import DEFAULT_GROUP, GroupChatModel, GroupPolicy, GroupScope
from relationships import set_owner_aliases
from sessions import MultiRunner
from test_core import message, parse
from test_sessions import FakeDesktop


class WechatContinuityTests(unittest.TestCase):
    def test_import_keeps_data_cwd_and_backend_untouched(self):
        source = '''
import os, sys
from unittest.mock import patch
before = os.getcwd(), dict(os.environ), list(sys.path)
with patch('pathlib.Path.mkdir', side_effect=AssertionError('mkdir during import')):
    import app
assert before == (os.getcwd(), dict(os.environ), list(sys.path))
assert not any(n.startswith('wx_backend') for n in sys.modules)
print('safe full entry import')
'''
        result = subprocess.run([sys.executable, '-B', '-c', source], cwd=Path(__file__).parent,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_current_and_legacy_calls_share_one_character(self):
        with tempfile.TemporaryDirectory() as folder:
            persona = Path(folder) / 'persona.txt'
            persona.write_text('你是小泽。自然聊天，不惯性追问。', encoding='utf-8')
            policy = GroupPolicy('阿澄今天值班')
            self.assertTrue(policy.addressed([message('小泽你怎么看', '朋友')]))
            self.assertTrue(policy.addressed([message('阿澄你怎么看', '朋友')]))
            self.assertTrue(policy.addressed([message('@阿澄今天值班', '朋友')]))
            self.assertEqual(policy.nickname, '阿澄今天值班')
            model = GroupChatModel({'api_key': 'synthetic'}, GroupScope(DEFAULT_GROUP), persona,
                                   Path(folder) / 'group-memory.json', policy)
            system = model.system_prompt(DEFAULT_GROUP)
            self.assertIn('角色名字是小泽', system)
            self.assertNotIn('人格名字仍是阿澄', system)

    def test_full_runner_sends_current_persona_and_preserves_split_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            persona = root / 'persona.txt'
            persona.write_text('你是小泽。温柔待孤月大人，围绕具体话题自然聊天，不机械追问。', encoding='utf-8')
            cfg = {'group': DEFAULT_GROUP, 'group_nickname': '阿澄', 'group_behavior': 'mention',
                   'group_frequency': 'occasional', 'api_key': 'synthetic',
                   'endpoint': 'https://example.test/chat/completions', 'model': 'synthetic'}
            set_owner_aliases(root / 'relationships.json', DEFAULT_GROUP, ['孤月大人', 'X_A'])
            desktop = FakeDesktop()
            runner = MultiRunner(desktop, cfg, persona, root, parse, lambda *_: None)
            runner.sessions['group'].group_policy.quiet_seconds = 0
            for name in runner.sessions:
                runner.poll_session(name)
            runner.ready = True
            runner.resume('live')
            response = Mock(status_code=200)
            response.json.return_value = {'choices': [{'message': {'content': '虚构自然回复'}, 'finish_reason': 'stop'}]}
            with patch('requests.post', return_value=response) as post:
                desktop.frames[OWNER].append(message('私聊暗号蓝鲸'))
                runner.poll_session('private')
                private_payload = post.call_args.kwargs['json']['messages']
                desktop.frames[DEFAULT_GROUP].append(message('小泽陪我聊两句', 'X_A'))
                runner.poll_session('group')
                group_payload = post.call_args.kwargs['json']['messages']
                self.assertEqual(post.call_count, 2)
            self.assertIn('你是小泽', private_payload[0]['content'])
            self.assertIn('角色名字是小泽', group_payload[0]['content'])
            self.assertNotIn('私聊暗号蓝鲸', json.dumps(group_payload, ensure_ascii=False))
            self.assertEqual(len(desktop.sent), 2)
            self.assertTrue(runner.enabled.is_set())
            self.assertEqual(json.loads((root / 'memory.json').read_text(encoding='utf-8'))[OWNER][0]['content'], '私聊暗号蓝鲸')
            self.assertNotIn('私聊暗号蓝鲸', (root / 'group-memory.json').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
