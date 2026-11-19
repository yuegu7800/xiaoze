"""Final batch integration boundaries; fictional temp stores, no native I/O."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_wechat_character as fixtures
from groups import DEFAULT_GROUP, SILENCE
from wechat_character import digest


class BatchEBoundaryTests(unittest.TestCase):
    setUp = fixtures.IntegratedTests.setUp
    group = fixtures.IntegratedTests.group
    text = fixtures.IntegratedTests.text
    completion = fixtures.IntegratedTests.completion

    def test_first_import_pending_cannot_be_used_as_current_proactive_speech(self):
        model = self.group()
        model.history[DEFAULT_GROUP] = [{'role': 'user', 'content': self.text([('旧成员', '今天想安静一点')])}]
        session = model.character_session(DEFAULT_GROUP)
        before = session.memory.state.copy()
        with patch('wechat_character.complete_chat_result') as complete:
            self.assertEqual(model.reply(DEFAULT_GROUP, '', proactive=True), SILENCE)
        complete.assert_not_called()
        self.assertFalse(session.path.exists())
        self.assertNotIn('wechat_seeded', session.memory.state)
        self.assertEqual(session.memory.state, before)
        self.assertEqual(session.people, {})
        self.assertIsNone(session.core._prepared_exchange)
        # A valid live observation accepts the staged import atomically. It is
        # the actual observation, never old profile ordering, that enables C.
        model.observe(DEFAULT_GROUP, self.text([('新成员', '聊小说结尾')]), persist=True)
        self.assertTrue(session.memory.state['wechat_seeded'])
        self.assertIsNone(session._legacy_pending)
        with patch('wechat_character.complete_chat_result', side_effect=self.completion) as complete:
            model.reply(DEFAULT_GROUP, '', proactive=True)
        self.assertEqual(complete.call_count, 1)
        self.assertEqual(session.last_person, 'display:' + digest('新成员'))
        model.discard_pending()
        self.assertIsNone(session.core._prepared_exchange)

    def test_clean_imports_no_runtime_files_windows_backend_or_process_mutation(self):
        source = r'''
import os, sys, importlib.abc
from unittest.mock import patch
class RejectBackend(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('wx_backend'):
            raise AssertionError('backend import forbidden')
sys.meta_path.insert(0, RejectBackend())
before = os.getcwd(), dict(os.environ), list(sys.path)
with patch('pathlib.Path.read_text', side_effect=AssertionError('runtime read forbidden')), \
     patch('pathlib.Path.mkdir', side_effect=AssertionError('mkdir forbidden')), \
     patch('tkinter.Tk', side_effect=AssertionError('window forbidden')):
    import app, wechat_character, wechat_character_panel, wechat_private_bridge
assert before == (os.getcwd(), dict(os.environ), list(sys.path))
assert not any(n.startswith('wx_backend') for n in sys.modules)
print('Clean imports OK: no data read/mkdir/window/backend; cwd/environment/sys.path unchanged')
'''
        result = subprocess.run([sys.executable, '-B', '-c', source], cwd=Path(__file__).parent,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_check_branch_cannot_open_window_or_read_saved_material(self):
        source = r'''
import os, sys
from unittest.mock import patch
import launch
before = os.getcwd(), dict(os.environ), list(sys.path)
sys.argv = ['launch.py', '--check']
with patch('pathlib.Path.read_text', side_effect=AssertionError('runtime read forbidden')), \
     patch('pathlib.Path.mkdir', side_effect=AssertionError('mkdir forbidden')), \
     patch('tkinter.Tk', side_effect=AssertionError('window forbidden')):
    assert launch.main() == 0
assert os.getcwd() == before[0] and dict(os.environ) == before[1]
assert not any(n.startswith('wx_backend') for n in sys.modules)
print('Guarded --check OK')
'''
        result = subprocess.run([sys.executable, '-B', '-c', source], cwd=Path(__file__).parent,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
