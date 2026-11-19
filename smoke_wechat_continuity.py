"""Exercise the reused full Tk entry using synthetic saved config and no desktop IO."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType
from unittest.mock import Mock, patch

import app
from groups import GroupRunner, DEFAULT_GROUP
from core import OWNER, PrivateRunner
from sessions import MultiRunner
from test_core import parse
from test_sessions import FakeDesktop


def main():
    old_cwd, old_home, old_path = os.getcwd(), os.environ.get('XIAOLI_HOME'), list(sys.path)
    try:
        # Avoid changing the process cwd into a directory being cleaned up if
        # Tk initialization fails; runtime chdir itself remains unchanged.
        with tempfile.TemporaryDirectory() as folder, patch.object(app.os, 'chdir'):
            root = Path(folder)
            cfg = {'endpoint': 'https://example.test/chat/completions', 'model': 'unchanged-synthetic-model',
                   'api_key': 'dpapi:synthetic-only', 'contacts': {OWNER: 'owner'},
                   'session_mode': 'both', 'group': DEFAULT_GROUP, 'group_nickname': '阿澄旧显示昵称',
                   'group_behavior': 'social', 'group_frequency': 'occasional'}
            (root / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
            backend_module, region_module = ModuleType('wx_backend.visual_backend'), ModuleType('wx_backend.visual_regions')
            backend_module.VisualBackend = object
            region_module.parse_title = parse
            gui = ModuleType('pyautogui')
            with patch.object(app, 'DATA', root), \
                 patch.object(app, 'decrypt', return_value='synthetic-runtime-key'), \
                 patch.object(app, 'encrypt', return_value='dpapi:synthetic-saved'), \
                 patch.object(app.messagebox, 'askyesno', return_value=True), \
                 patch.object(app.messagebox, 'showerror') as error, \
                 patch.dict(sys.modules, {'wx_backend.visual_backend': backend_module,
                                          'wx_backend.visual_regions': region_module, 'pyautogui': gui}), \
                 patch.object(app, 'current_chat_backend', side_effect=lambda *_: lambda: FakeDesktop()), \
                 patch.object(PrivateRunner, 'start'), patch.object(MultiRunner, 'start'), \
                 patch('requests.post') as network:
                window = app.App()
                try:
                    window.window.update()
                    assert '小泽' in window.window.title()
                    assert window.values['model'].get() == cfg['model']
                    assert window.group_nickname.get() == cfg['group_nickname']
                    assert window.session_mode.get() == '群聊＋孤月私聊'
                    assert window.runner is None
                    # Save and connect each existing mode; no worker thread or backend runs.
                    for label, expected in [('孤月私聊', PrivateRunner), ('指定群聊', GroupRunner),
                                            ('群聊＋孤月私聊', MultiRunner)]:
                        window.session_mode.set(label)
                        window.session_changed()
                        window.connect()
                        assert isinstance(window.runner, expected), label
                        window.runner = None
                        window.lock_settings(False)
                    stored = json.loads((root / 'config.json').read_text(encoding='utf-8'))
                    assert stored['api_key'] == 'dpapi:synthetic-saved'
                    assert stored['model'] == cfg['model']
                    assert stored['group_nickname'] == cfg['group_nickname']
                    # An explicitly edited group must survive save -> both
                    # transports -> diagnostics, without falling back to default.
                    renamed = '虚构改名后的群'
                    window.group_name.set(renamed)
                    window.session_mode.set('群聊＋孤月私聊')
                    window.connect()
                    assert window.runner.sessions['group'].transport.target == renamed
                    assert window.runner.sessions['group'].scope.group == renamed
                    assert window.runner.sessions['group'].transport.parse_title(renamed + '（ 3 ）') == (renamed, True, 3)
                    with patch.object(app.messagebox, 'showinfo') as diagnostic:
                        window.show_diagnostics()
                    report = diagnostic.call_args.args[1]
                    assert renamed in report and 'bound_group' in report
                    assert 'synthetic-runtime-key' not in report and 'api_key' not in report
                    window.runner = None
                    window.show_people()
                    window.show_moments()
                    window.window.update()
                    error.assert_not_called()
                    network.assert_not_called()
                    print('PASS full Tk: saved profile, unchanged model/nickname, private/group/both, people and draft tools; no desktop/API')
                finally:
                    window.window.destroy()
            os.chdir(old_cwd)
    finally:
        os.chdir(old_cwd)
        sys.path[:] = old_path
        if old_home is None:
            os.environ.pop('XIAOLI_HOME', None)
        else:
            os.environ['XIAOLI_HOME'] = old_home


if __name__ == '__main__':
    main()
