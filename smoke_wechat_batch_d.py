"""Temporary Tk regression; execution recorded in final E validation.

Run explicitly at final validation, using fictional files/mock completion only.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import tkinter as tk

from core import CompletionResult
from groups import DEFAULT_GROUP, GroupScope, GroupPolicy
from memory_panel import MemoryPanel
from wechat_character import make_model, digest
from test_core import message


def main():
    with TemporaryDirectory() as folder, patch('requests.post', side_effect=AssertionError('network forbidden')):
        root = tk.Tk()
        root.withdraw()
        panel = None
        try:
            path = Path(folder)
            persona = path / 'persona.txt'
            persona.write_text('你是小泽，温柔尊重孤月大人。', encoding='utf-8')
            model = make_model({'model': 'fictional', 'group_nickname': '小泽'},
                GroupScope(DEFAULT_GROUP), persona, path / 'group.json', GroupPolicy())
            def say(name, body):
                text = GroupPolicy().format_messages(DEFAULT_GROUP, [message(body, name)])
                with patch('wechat_character.complete_chat_result', return_value=CompletionResult('虚构回复', 'stop')):
                    reply = model.reply(DEFAULT_GROUP, text)
                model.remember(DEFAULT_GROUP, text, reply)
            say('甲', '我不喝咖啡')
            session = model.character_session(DEFAULT_GROUP)
            active = [True]
            facade = SimpleNamespace(window=root, core=session.core, scene=session.scene,
                mode=tk.StringVar(root, '虚构微信'), speaker=tk.StringVar(root, '甲'),
                practice_isolated=tk.BooleanVar(root, False), use_memory=tk.BooleanVar(root, True),
                use_topics=tk.BooleanVar(root, True), memory_active=lambda: active[0],
                memory_status=session.import_status, render=lambda: None)
            panel = MemoryPanel(facade)
            root.update()
            source_id = session.core.snapshot(session.scene_id)['turns'][-1]['id']
            panel.turn.set(next(k for k, v in panel.turn_labels.items() if v == source_id))
            panel.quote.set('我不喝咖啡')
            panel.content.set('虚构未保存内容')
            rid = next(iter(panel.records))
            panel.view.selection_set(rid)
            panel.edit('correction')
            panel.edit_value.set('保留的编辑输入')
            panel.query.set('咖啡')
            say('乙', '小说结尾')
            panel.refresh()
            assert 'display:' + digest('乙') in panel.people.values()
            assert panel.turn_id == source_id and panel.mark_bound_id == source_id
            assert panel.quote.get() == '我不喝咖啡' and panel.edit_id == rid
            assert panel.edit_value.get() == '保留的编辑输入'
            panel.preview_person.set(next(k for k, v in panel.people.items() if v == 'display:' + digest('乙')))
            panel.preview()
            assert panel.preview_report['person_id'] == 'display:' + digest('乙')
            session.core.forget_memory(session.scene_id, rid)
            panel.refresh()
            assert panel.turn.get() == '' and panel.turn_id == source_id
            before = session.core.snapshot(session.scene_id, compatibility=False)
            panel.mark()
            panel.save_edit()
            assert before == session.core.snapshot(session.scene_id, compatibility=False)
            assert panel.content.get() == '虚构未保存内容'
            assert panel.edit_value.get() == '保留的编辑输入'
            # Roll a newly selected source out: reused ordinals never rebind ID.
            newer = session.core.snapshot(session.scene_id)['turns'][-1]['id']
            with patch('memory_panel.messagebox.askyesno', return_value=True):
                panel.turn.set(next(k for k, v in panel.turn_labels.items() if v == newer))
            panel.quote.set('小说')
            panel.content.set('滚动后仍保留的输入')
            for i in range(13):
                say('乙', '虚构后续%d' % i)
            panel.refresh()
            assert panel.turn_id == newer and panel.turn.get() == ''
            before = session.core.snapshot(session.scene_id, compatibility=False)
            panel.mark()
            assert before == session.core.snapshot(session.scene_id, compatibility=False)
            assert panel.content.get() == '滚动后仍保留的输入'
            # Deliberately collide display labels while stable IDs remain distinct.
            session.people['display:' + digest('丙')] = '甲'
            session._refresh_scene()
            panel.refresh()
            assert len([pid for pid in panel.people.values() if pid is not None]) == 3
            assert len(panel.people) == len(set(panel.people))
            active[0] = False
            panel.poll()
            root.update()
            assert not panel.window.winfo_exists()
            assert panel._poll_id is None
            print('PASS D temporary Tk: new people, stable IDs, preview, deletion/rolling, edits, duplicate displays, disconnect cleanup; mock only')
        finally:
            if panel is not None and panel.window.winfo_exists():
                panel.window.destroy()
            root.destroy()


if __name__ == '__main__':
    main()
