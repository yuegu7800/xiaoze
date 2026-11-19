"""Real Tk candidate button, fictional stores and mocked HTTP only."""
import tempfile
from unittest.mock import Mock, patch

from activity_panel import PURPOSE_LABELS
from character import SCENES
from local_chat import LocalChat, MODE_API
from smoke_local_ui import pump
from test_activity import CFG, DRAFT
from test_candidate_empty import DEEPSEEK


def run_case(scene_id, public=False):
    with tempfile.TemporaryDirectory(prefix='xiaoze-json-smoke-') as folder:
        app = LocalChat(data_dir=folder)
        try:
            app.mode.set(MODE_API)
            app.scene_label.set(next(s.label for s in SCENES if s.id == scene_id))
            app.switch_mode()
            for key, value in CFG.items():
                app.api_values[key].set(value)
            core, scene = app.core, app.scene
            person = scene.people[0].id
            core.exchange(scene.id, person, '虚构小说纸船结尾没想好', lambda _: '虚构回复')
            core.enable_activity(scene.id)
            if public:
                core.set_public_material(scene.id, '虚构公开素材：喜欢纸船故事的开放结尾')
            else:
                source = core.snapshot(scene.id)['turns'][-1]['id']
                core.mark_topic(scene.id, source, '纸船结尾没想好', '纸船故事', '接着讨论结尾',
                                target='all' if scene.kind == 'group' else person, confirm=True)
            original_turns = core.snapshot(scene.id)['turns']
            app.input.insert('1.0', '未发送的输入须保留')
            panel = app.show_activity()
            if public:
                panel.purpose.set(PURPOSE_LABELS['public'])
            elif scene.kind == 'group':
                panel.target.set('全群')

            def provider(*args, **kwargs):
                payload = kwargs['json']
                content = DRAFT if payload.get('response_format') == {'type': 'json_object'} else '普通聊天正文'
                response = Mock(status_code=200)
                response.json.return_value = {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]}
                return response

            with patch('requests.post', side_effect=provider) as post:
                panel.evaluate_button.invoke()
                pump(app.window, lambda: not app.busy)
                post.assert_called_once()
                assert app.last_error is None, app.status.get()
                drafts = [d for d in panel.drafts.values() if d['status'] == 'pending']
                assert len(drafts) == 1
                assert drafts[0]['purpose'] == ('public' if public else scene.kind)
                assert core.snapshot(scene.id)['turns'] == original_turns
                assert app.input.get('1.0', 'end-1c') == '未发送的输入须保留'
                panel.draft_view.selection_set(drafts[0]['id'])
                panel.select_draft()
                assert '小说里的纸船' in panel.draft_details.get('1.0', 'end')
                panel.close()
                # An ordinary subsequent chat must still request free text.
                app.send()
                pump(app.window, lambda: not app.busy)
                assert post.call_count == 2
                assert 'response_format' not in post.call_args.kwargs['json']
                assert app.last_error is None, app.status.get()
        finally:
            app.close()


def run_empty_recovery():
    with tempfile.TemporaryDirectory(prefix='xiaoze-blank-smoke-') as folder:
        app = LocalChat(data_dir=folder)
        try:
            app.mode.set(MODE_API)
            app.switch_mode()
            for key, value in DEEPSEEK.items():
                app.api_values[key].set(value)
            core, scene = app.core, app.scene
            core.exchange(scene.id, 'owner', '虚构纸船结尾没想好', lambda _: '虚构回复')
            core.enable_activity(scene.id)
            source = core.snapshot(scene.id)['turns'][-1]['id']
            core.mark_topic(scene.id, source, '纸船结尾没想好', '虚构纸船', '讨论结尾', confirm=True)
            app.input.insert('1.0', '失败后也保留的输入')
            panel = app.show_activity()
            before = core.memory.path.read_bytes()
            blank = Mock(status_code=200)
            blank.json.return_value = {'choices': [{'message': {'content': '    '}, 'finish_reason': 'stop'}]}
            valid = Mock(status_code=200)
            valid.json.return_value = {'choices': [{'message': {'content': DRAFT}, 'finish_reason': 'stop'}]}
            with patch('requests.post', side_effect=[blank, valid]) as post:
                panel.evaluate_button.invoke()
                pump(app.window, lambda: not app.busy)
                assert post.call_count == 1  # No automatic repair/fallback call.
                assert not app.paused and not core._paused
                assert not core._inflight
                assert '仅空白' in panel.message.get()
                assert '不是小泽选择沉默' in panel.message.get()
                assert '已暂停' not in panel.message.get()
                assert core.memory.path.read_bytes() == before
                assert not panel.drafts
                assert app.input.get('1.0', 'end-1c') == '失败后也保留的输入'
                # An explicit second click, not a programmatic automatic retry.
                panel.evaluate_button.invoke()
                pump(app.window, lambda: not app.busy)
                assert post.call_count == 2
                assert app.last_error is None, app.status.get()
                assert len([d for d in panel.drafts.values() if d['status'] == 'pending']) == 1
                for call in post.call_args_list:
                    payload = call.kwargs['json']
                    assert payload['model'] == 'deepseek-chat'
                    assert payload['thinking'] == {'type': 'disabled'}
                    assert payload['max_tokens'] == 2048
        finally:
            app.close()


if __name__ == '__main__':
    for args in [('local-owner', False), ('local-group', False), ('local-owner', True)]:
        run_case(*args)
    run_empty_recovery()
    print('PASS: private/group/public candidate button uses JSON, local draft visible, normal chat stays text; mocked HTTP only')
    print('PASS: blank JSON has accurate status, no auto-retry/commit/pause; explicit second click can create draft')
