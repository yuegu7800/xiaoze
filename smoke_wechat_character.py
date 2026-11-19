"""Real temporary Tk controls, synthetic config/data, no WeChat or paid API."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType
from unittest.mock import Mock, patch
from tkinter import ttk

import app
from core import OWNER, CompletionResult
from groups import DEFAULT_GROUP
from memory_panel import MemoryPanel
from sessions import MultiRunner
from test_core import message, parse
from test_sessions import FakeDesktop


def main():
    old_cwd, old_home, old_path = os.getcwd(), os.environ.get('XIAOLI_HOME'), list(sys.path)
    try:
        with tempfile.TemporaryDirectory() as folder, patch.object(app.os, 'chdir'):
            root = Path(folder)
            persona = root / 'persona.txt'
            persona.write_text('你是小泽，温柔待孤月大人，朋友分别相处。', encoding='utf-8')
            cfg = {'endpoint': 'https://fictional.example/v1/chat/completions', 'model': 'fictional-only',
                   'api_key': 'dpapi:fictional', 'contacts': {OWNER: 'owner'}, 'session_mode': 'both',
                   'group': DEFAULT_GROUP, 'group_nickname': '阿澄', 'group_behavior': 'mention', 'group_frequency': 'occasional'}
            (root / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
            modules = {name: ModuleType(name) for name in ('wx_backend.visual_backend', 'wx_backend.visual_regions', 'pyautogui')}
            modules['wx_backend.visual_backend'].VisualBackend = object
            modules['wx_backend.visual_regions'].parse_title = parse
            desktop = FakeDesktop()
            windows = []
            with patch.object(app, 'DATA', root), patch.object(app, 'PERSONA', persona), \
                 patch.object(app, 'decrypt', return_value='fictional-key'), patch.object(app, 'encrypt', return_value='dpapi:fictional'), \
                 patch.object(app.messagebox, 'askyesno', return_value=True), patch.object(app.messagebox, 'showerror') as error, \
                 patch.dict(sys.modules, modules), patch.object(app, 'current_chat_backend', side_effect=lambda *_: lambda: desktop), \
                 patch.object(MultiRunner, 'start'), patch('requests.post', side_effect=AssertionError('network')), \
                 patch('wechat_character.complete_chat_result', return_value=CompletionResult('虚构小泽回复', 'stop')):
                gui = app.App()
                try:
                    gui.connect_btn.invoke()
                    assert gui.runner is not None, error.call_args
                    runner = gui.runner
                    for mode in runner.sessions:
                        runner.poll_session(mode)
                    runner.ready = True
                    runner.sessions['group'].group_policy.quiet_seconds = 0
                    runner.resume('live')
                    desktop.frames[OWNER].append(message('我不喝咖啡'))
                    runner.poll_session('private')
                    desktop.frames[DEFAULT_GROUP].append(message('阿澄，我喜欢小说', '虚构甲'))
                    runner.poll_session('group')
                    assert len(desktop.sent) == 2
                    gui.show_character()
                    gui.window.update()
                    panel = gui.character_panel
                    private = runner.sessions['private'].model.character_session(OWNER)
                    group = runner.sessions['group'].model.character_session(DEFAULT_GROUP)
                    # D navigation must retarget an already-open panel to the
                    # exact captured group, never construct a missing session.
                    panel.chosen.set(private.scene.label)
                    panel.refresh()
                    gui.show_people()
                    legacy = next(w for w in gui.window.winfo_children()
                                  if w.winfo_class() == 'Toplevel' and w.title() == '旧显示名绑定 / 兼容归档')
                    box = next(w for w in legacy.winfo_children() if isinstance(w, ttk.Frame))
                    next(w for w in box.winfo_children() if isinstance(w, ttk.Button)
                         and w.cget('text') == '打开本群实际使用的小泽核心').invoke()
                    assert panel.session() is group
                    with patch.object(app.messagebox, 'showinfo') as notice, patch.object(
                            runner.sessions['group'].model, 'character_session', side_effect=AssertionError('navigation must not construct')):
                        gui.show_character('不存在的虚构群', 'group')
                    notice.assert_called_once()
                    assert panel.session() is group
                    legacy.destroy()
                    panel.chosen.set(private.scene.label)
                    panel.refresh()
                    turn = private.core.feedback_view(private.scene_id)[-1]
                    panel.tree.selection_set(turn['id'])
                    panel.feedback()
                    feedback = panel._feedback[(private.core, private.scene_id)]
                    gui.window.update()
                    assert feedback.selected_turn_id() == turn['id']
                    assert '本进程' in feedback.summary.get('1.0', 'end')
                    feedback.tags['自然合适'].set(True)
                    feedback.note.insert('1.0', '虚构控件验收')
                    feedback.save_button.invoke()
                    assert private.core.snapshot(private.scene_id)['feedback'][-1]['turn_id'] == turn['id']
                    def open_memory(facade):
                        memory = MemoryPanel(facade)
                        windows.append(memory)
                        return memory
                    with patch('wechat_character_panel.MemoryPanel', side_effect=open_memory):
                        panel.memory()
                    gui.window.update()
                    memory = windows[-1]
                    record = next(r for r in private.core.snapshot(private.scene_id)['memories'] if r['status'] == 'active')
                    memory.view.selection_set(record['id'])
                    memory.action('suppress')
                    assert next(r for r in private.core.snapshot(private.scene_id)['memories'] if r['id'] == record['id'])['status'] == 'suppressed'
                    memory.close()
                    panel.chosen.set(group.scene.label)
                    panel.refresh()
                    gid = group.core.snapshot(group.scene_id)['turns'][-1]['id']
                    panel.tree.selection_set(gid)
                    with patch('wechat_character_panel.simpledialog.askstring', side_effect=['喜欢小说', '小说', '下次聊小说人物']):
                        panel.topic()
                    assert group.core.snapshot(group.scene_id)['activity']['topics'][0]['status'] == 'active'
                    topics_window = panel.manage_topics()
                    topics_tree = next(w for w in topics_window.winfo_children() if isinstance(w, ttk.Treeview))
                    topic_id = group.core.snapshot(group.scene_id)['activity']['topics'][0]['id']
                    topics_tree.selection_set(topic_id)
                    controls = next(w for w in topics_window.winfo_children() if isinstance(w, ttk.Frame))
                    next(w for w in controls.winfo_children() if w.cget('text') == '暂停').invoke()
                    assert group.core.snapshot(group.scene_id)['activity']['topics'][0]['status'] == 'paused'
                    next(w for w in controls.winfo_children() if w.cget('text') == '重新启用').invoke()
                    assert group.core.snapshot(group.scene_id)['activity']['topics'][0]['status'] == 'active'
                    topics_window.destroy()
                    # Another group participant arrives while the view is open.
                    stable_scene = group.scene
                    desktop.frames[DEFAULT_GROUP].append(message('阿澄，我不喝咖啡', '虚构乙'))
                    runner.poll_session('group')
                    panel.refresh()
                    assert stable_scene is group.scene and len(group.scene.people) == 2
                    assert len(panel.tree.get_children()) == 2
                    gui.disconnect_btn.invoke()
                    gui.window.update()
                    assert gui.runner is None and not panel.window.winfo_exists()
                    assert private.core._request_evidence == {} and group.core._request_evidence == {}
                    assert error.call_count == 0
                    print('PASS integrated full Tk: both sessions, stable feedback, shared maintenance, active topic, new group member, disconnect cleanup; mock only')
                finally:
                    gui.window.destroy()
    finally:
        os.chdir(old_cwd)
        sys.path[:] = old_path
        if old_home is None:
            os.environ.pop('XIAOLI_HOME', None)
        else:
            os.environ['XIAOLI_HOME'] = old_home


if __name__ == '__main__':
    main()
