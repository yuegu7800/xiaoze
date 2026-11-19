"""Isolated source-function regression; never import/initialize the backend."""
from pathlib import Path
import ast
import re
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from visual_adapter import current_chat_backend, visible_blocks, visible_message_reader, message_frame_ocr
from core import OWNER, PrivateScope, ScopedTransport, TitleScopeError, ReadUnavailableError
from groups import GroupScope
from test_core import parse

UPSTREAM = Path(r"C:\Users\ZhuanZ\Documents\ChatGPT\奇思妙想\work\wechat-lab\xiaoli\xiaoli_desktop")
source = ast.parse((UPSTREAM / 'wx_backend' / 'visual_backend.py').read_text(encoding='utf-8'))
names = {'analyze_blocks', '_contains', 'drop_panel_contents', '_anchor_avatar'}
functions = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in names]
backend_class = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'VisualBackend')
reader = next(n for n in backend_class.body if isinstance(n, ast.FunctionDef) and n.name == 'get_messages')
title_reader = next(n for n in backend_class.body if isinstance(n, ast.FunctionDef) and n.name == 'read_title')
# Compile only known function definitions with deferred annotations. Never
# execute module imports, initializers, class decorators or native operations.
isolated = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *functions, reader, title_reader], type_ignores=[])
upstream = ModuleType('fictional_geometry_functions')
def forbidden(*args, **kwargs):
    raise AssertionError('native/OCR function must never execute')
upstream.__dict__.update(Image=Image, find_bubble_boxes=forbidden, find_media_boxes=forbidden,
                         _region_fingerprint=forbidden, ocr_image=forbidden,
                         drop_panel_contents=lambda media, panels: media, _pxr=lambda pixels: pixels)
exec(compile(ast.fix_missing_locations(isolated), '<isolated-backend-functions>', 'exec'), upstream.__dict__)
upstream.VisualBackend = type('IsolatedReader', (), {'get_messages': upstream.get_messages})


class VisibleWindowTests(unittest.TestCase):
    def joint_frame(self, frame_title='虚构测试群(3)'):
        shot = Image.new('RGB', (400, 400), 'white')
        title_region = (.4, .04, .8, .15)
        message_region = (.4, .2, .99, .9)
        def item(text, x=0, y=0):
            return dict(text=text, x=x, y=y, w=100, h=12)
        def ocr(image):
            if image.size == (160, 44):
                return [item(frame_title), item('X', x=140)]
            if image.size == (192, 112):
                # New bounded retry pads this same blank title, not the body.
                return []
            if image.size == (236, 344):
                # Old joint OCR includes toolbar noise / changes short title.
                return [item('虚构测试群按钮(3)'), item('虚构新消息', x=100, y=84)]
            if image.size == (236, 300):
                return [item('虚构新消息', x=100, y=40)]
            raise AssertionError(f'unexpected crop {image.size}')
        return shot, title_region, message_region, Mock(side_effect=ocr)

    def test_joint_crop_failure_reproduced_then_actual_reader_uses_same_frame_title(self):
        for split in (False, True):
            with self.subTest(split=split):
                shot, title_region, message_region, ocr = self.joint_frame()
                class GeometryBase:
                    get_messages = upstream.VisualBackend.get_messages
                    _title_region, _message_region = title_region, message_region
                    _current_chat = None
                    _current_title = None
                    _current_is_group = False
                    enforce_window_geometry = lambda self: None
                    _note_theme = lambda *args: None
                    read_title = lambda self, **kw: '虚构测试群(3)'
                    def _refresh(self, **kw):
                        self.refreshes += 1
                        self._last_shot = shot
                        return shot
                dependencies = dict(ocr_image=ocr, logger=Mock(), parse_title=parse, re=re,
                    WeChatMessage=SimpleNamespace, MessageType=SimpleNamespace(TEXT='text', FILE='file'),
                    detect_bubble_colors=lambda *_: {'bg': 'white'},
                    detect_avatar_tops=lambda img, bg, side: [0] if side == 'left' else [],
                    analyze_blocks=lambda *a, **kw: {'blocks': [dict(avatar_top=0, top=0, bottom=200,
                        kind='text', panel=None, media=None, icon=None)]})
                with patch.dict(upstream.__dict__, dependencies):
                    cls = current_chat_backend(GeometryBase, parse, {'虚构测试群': GroupScope('虚构测试群')})
                    if not split:
                        cls.get_messages = visible_message_reader(GeometryBase.get_messages, parse)
                    backend = cls()
                    backend.refreshes = 0
                    transport = ScopedTransport(backend, GroupScope('虚构测试群'), parse)
                    if split:
                        name, messages = transport.read()
                        self.assertEqual(name, '虚构测试群')
                        self.assertEqual([(m.sender, m.content) for m in messages], [('虚构测试群', '虚构新消息')])
                        self.assertEqual(backend._current_title, '虚构测试群')
                        self.assertEqual([call.args[0].size for call in ocr.call_args_list], [(160, 44), (236, 300)])
                    else:
                        with self.assertRaises(TitleScopeError) as error:
                            transport.read()
                        self.assertEqual(error.exception.phase, 'message_frame')
                    self.assertEqual(backend.refreshes, 1)

    def test_split_frame_preserves_message_coordinates_and_upstream_ocr(self):
        shot, title, message, ocr = self.joint_frame()
        union = shot.crop((160, 16, 396, 360))
        result = message_frame_ocr(ocr, union, shot, title, message)
        self.assertEqual([it['text'] for it in result], ['虚构测试群(3)', '虚构新消息'])
        self.assertEqual(result[1]['y'], 84)  # same original union coordinates
        self.assertIs(upstream.get_messages.__globals__['ocr_image'], forbidden)

    def test_blank_frame_title_stops_without_reusing_cache_or_ocr_of_body(self):
        shot, title, message, ocr = self.joint_frame('')
        with self.assertRaises(ReadUnavailableError) as error:
            message_frame_ocr(ocr, shot.crop((160, 16, 396, 360)), shot, title, message)
        self.assertEqual(error.exception.reason, 'title_blank')
        self.assertEqual([call.args[0].size for call in ocr.call_args_list],
                         [(160, 44), (192, 112)])

    def test_nonjoint_message_ocr_remains_unchanged(self):
        shot, title, message, _ = self.joint_frame()
        body = shot.crop((160, 80, 396, 360))
        result = [dict(text='虚构正文', x=120, y=20, w=90, h=12)]
        ocr = Mock(return_value=result)
        self.assertIs(message_frame_ocr(ocr, body, shot, title, message), result)
        ocr.assert_called_once_with(body)

    def test_split_frame_never_reuses_initial_title_if_frame_changes(self):
        for raw, expected_reason in [('别的虚构群(3)', 'group_name_mismatch'),
                                     ('虚构测试群', 'group_marker_missing')]:
            with self.subTest(raw=raw):
                shot, title, message, ocr = self.joint_frame(raw)
                class GeometryBase:
                    get_messages = upstream.VisualBackend.get_messages
                    _title_region, _message_region = title, message
                    _current_chat = None
                    _current_title = None
                    _current_is_group = False
                    enforce_window_geometry = lambda self: None
                    _note_theme = lambda *args: None
                    read_title = lambda self, **kw: '虚构测试群(3)'
                    def _refresh(self, **kw):
                        self._last_shot = shot
                        return shot
                with patch.dict(upstream.__dict__, dict(ocr_image=ocr, logger=Mock(),
                        detect_bubble_colors=lambda *_: {}, detect_avatar_tops=lambda *_: [],
                        analyze_blocks=lambda *a, **kw: {'blocks': []})):
                    backend = current_chat_backend(GeometryBase, parse, {'虚构测试群': GroupScope('虚构测试群')})()
                    transport = ScopedTransport(backend, GroupScope('虚构测试群'), parse)
                    with self.assertRaises(TitleScopeError) as error:
                        transport.read()
                    self.assertEqual((error.exception.phase, error.exception.reason), ('message_frame', expected_reason))

    def analyze(self, bots, others):
        # Synthetic blank frame: missing bubble boxes must still be bounded
        # by every avatar, including outgoing ones. No desktop access occurs.
        with patch.object(upstream, "find_bubble_boxes", return_value=[]), \
             patch.object(upstream, "find_media_boxes", return_value=[]), \
             patch.object(upstream, "_region_fingerprint", return_value=None):
            return visible_blocks(upstream.analyze_blocks,
                Image.new("RGB", (400, 400), "white"), {}, bots, others)

    def test_old_and_new_incoming_survive_outgoing_boundary(self):
        info = self.analyze([100, 250], [20, 150, 300])
        self.assertEqual(info["other_new_tops"], [20, 150, 300])
        self.assertEqual([b["avatar_top"] for b in info["blocks"]], [20, 150, 300])
        self.assertEqual([b["bottom"] for b in info["blocks"]], [99, 249, 399])
        self.assertEqual(info["all_tops"], [20, 100, 150, 250, 300])

    def test_no_outgoing_bubble_can_become_incoming(self):
        info = self.analyze([100, 250], [])
        self.assertEqual(info["blocks"], [])
        self.assertFalse(info["has_text"])
        self.assertIsNone(info["region_top"])

    def test_ambiguous_same_row_is_excluded(self):
        info = self.analyze([100], [20, 100, 300])
        self.assertEqual(info["other_new_tops"], [20, 300])

    def test_upstream_reader_remains_unmodified(self):
        original = upstream.VisualBackend.get_messages
        copied = visible_message_reader(original)
        self.assertIs(original.__globals__["analyze_blocks"], upstream.analyze_blocks)
        self.assertIsNot(copied.__globals__["analyze_blocks"], upstream.analyze_blocks)
        self.assertIs(copied.__code__, original.__code__)
        self.assertEqual(copied.__defaults__, original.__defaults__)
        self.assertIs(copied.__globals__["ocr_image"], original.__globals__["ocr_image"])

    def test_divider_is_remeasured_when_list_width_changes_without_window_resize(self):
        class FakeBase:
            get_messages = upstream.VisualBackend.get_messages
            def read_title(self, foreground=False):
                self.observed_key = self._boundary_key
                return OWNER
        backend = current_chat_backend(FakeBase, parse, {OWNER: PrivateScope()})()
        backend._boundary_key = (1266, 1432)
        self.assertEqual(backend.check_title(OWNER), OWNER)
        self.assertIsNone(backend.observed_key)


if __name__ == "__main__":
    unittest.main()
