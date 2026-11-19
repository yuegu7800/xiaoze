"""Fault injection against isolated original reader; no native desktop/API."""
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from core import OWNER, ReadUnavailableError, ScopedTransport, TitleScopeError
from groups import GroupScope
from test_core import parse
from test_visual_adapter import upstream
from visual_adapter import current_chat_backend, message_frame_ocr


TITLE = (.4, .04, .8, .15)
MESSAGE = (.4, .2, .99, .9)
GROUP = '虚构测试群'


def item(text, x=0, y=0, w=20, h=12):
    return dict(text=text, x=x, y=y, w=w, h=h)


class GeometryBase:
    get_messages = upstream.get_messages
    read_title = lambda self, **kw: GROUP + '(3)'
    _title_region, _message_region = TITLE, MESSAGE
    _current_chat = OWNER  # deliberately stale after a manual switch
    _current_title, _current_is_group = GROUP, True
    enforce_window_geometry = lambda self: None
    _note_theme = lambda *a: None

    def _refresh(self, **kw):
        self._last_shot = next(self.frames)
        return self._last_shot


def dependencies(ocr):
    return dict(ocr_image=ocr, logger=Mock(), parse_title=parse, re=re,
        WeChatMessage=SimpleNamespace, MessageType=SimpleNamespace(TEXT='text', FILE='file'),
        detect_bubble_colors=lambda *_: {'bg': 'white'},
        detect_avatar_tops=lambda img, bg, side: [0] if side == 'left' else [],
        analyze_blocks=lambda *a, **kw: {'blocks': [dict(avatar_top=0, top=0, bottom=200,
            kind='text', panel=None, media=None, icon=None)]})


class VisualFrameReviewTests(unittest.TestCase):
    def empty_or_message_ocr(self, body_text=''):
        def ocr(image):
            if image.size == (160, 44):
                return [item(GROUP + '(3)', w=120)]
            if image.size == (236, 300):
                return [item(body_text, x=100, y=40, w=110)] if body_text else []
            raise AssertionError(image.size)
        return Mock(side_effect=ocr)

    def test_second_capture_failure_is_unavailable_and_next_poll_recovers(self):
        ocr = self.empty_or_message_ocr()
        with patch.dict(upstream.__dict__, dependencies(ocr)):
            backend = current_chat_backend(GeometryBase, parse, {GROUP: GroupScope(GROUP)})()
            backend.frames = iter([Image.new('RGB', (400, 400), 'white'), None])
            transport = ScopedTransport(backend, GroupScope(GROUP), parse)
            with self.assertRaises(ReadUnavailableError):
                transport.read()
            self.assertIsNone(backend._current_title)
            # A good later poll remains usable; no permanent pause/cache poison.
            ocr.side_effect = self.empty_or_message_ocr('恢复后的虚构消息').side_effect
            backend.frames = iter([Image.new('RGB', (400, 400), 'white')])
            contact, messages = transport.read()
            self.assertEqual(contact, GROUP)
            self.assertEqual([m.content for m in messages], ['恢复后的虚构消息'])

    def test_two_verified_empty_frames_are_valid_without_clicking_or_false_error(self):
        ocr = self.empty_or_message_ocr()
        with patch.dict(upstream.__dict__, dependencies(ocr)):
            backend = current_chat_backend(GeometryBase, parse, {GROUP: GroupScope(GROUP)})()
            backend.frames = iter([Image.new('RGB', (400, 400), 'white') for _ in range(2)])
            self.assertEqual(ScopedTransport(backend, GroupScope(GROUP), parse).read(), (GROUP, []))
            self.assertEqual(backend._current_title, GROUP)
            self.assertEqual(sum(call.args[0].size == (160, 44) for call in ocr.call_args_list), 2)

    def test_title_changes_during_empty_retry_rejected_before_second_capture(self):
        class SwitchingBase(GeometryBase):
            def read_title(self, **kw):
                return next(self.titles)
        with patch.dict(upstream.__dict__, dependencies(self.empty_or_message_ocr())):
            backend = current_chat_backend(SwitchingBase, parse, {GROUP: GroupScope(GROUP)})()
            backend.titles = iter([GROUP + '(3)', '别的群(3)'])
            backend.frames = iter([Image.new('RGB', (400, 400), 'white')])
            with self.assertRaises(TitleScopeError):
                ScopedTransport(backend, GroupScope(GROUP), parse).read()

    def test_fragmented_chinese_and_count_survive_both_original_title_paths(self):
        fragments = [item(t, x=n * 18) for n, t in enumerate(('虚', '构', '测', '试', '群', '(', '3', ')'))]
        class NativeTitleBase(GeometryBase):
            read_title = upstream.read_title
        shot = Image.new('RGB', (400, 400), 'white')
        ocr = Mock(return_value=fragments)
        with patch.dict(upstream.__dict__, dependencies(ocr)):
            backend = current_chat_backend(NativeTitleBase, parse, {GROUP: GroupScope(GROUP)})()
            backend.frames = iter([shot])
            self.assertEqual(backend.check_title(GROUP), GROUP)
            result = message_frame_ocr(ocr, shot.crop((160, 16, 396, 360)), shot, TITLE, MESSAGE)
            header = [it['text'] for it in result if it['y'] + it['h'] // 2 < 44]
            self.assertEqual(''.join(header), GROUP + '(3)')
        # The extracted original method still rejects fragments by itself.
        original = NativeTitleBase()
        original.frames = iter([shot])
        with patch.dict(upstream.__dict__, dependencies(ocr)):
            self.assertIsNone(original.read_title())

    def test_empty_first_frame_retry_revalidates_second_frame_instead_of_cached_group(self):
        seen = []
        def ocr(image):
            shade = image.getpixel((0, 0))[0]
            seen.append((image.size, shade))
            if image.size == (160, 44):
                return [item(GROUP + '(3)' if shade == 255 else '别的虚构群(3)', w=120)]
            if image.size == (236, 300):
                return [] if shade == 255 else [item('不属于目标群的正文', x=100, y=40, w=110)]
            if image.size == (236, 280):
                return [item('不属于目标群的正文', x=100, y=20, w=110)]
            raise AssertionError(image.size)
        with patch.dict(upstream.__dict__, dependencies(ocr)):
            backend = current_chat_backend(GeometryBase, parse, {GROUP: GroupScope(GROUP)})()
            backend.frames = iter([Image.new('RGB', (400, 400), color) for color in ('white', 'gray')])
            with self.assertRaises(TitleScopeError) as error:
                ScopedTransport(backend, GroupScope(GROUP), parse).read()
            self.assertEqual(error.exception.phase, 'message_frame')
        self.assertEqual(sum(size == (160, 44) for size, _ in seen), 2)
        self.assertNotIn((236, 280), [size for size, _ in seen])

    def test_failed_capture_cannot_return_success_using_previous_title_cache(self):
        with patch.dict(upstream.__dict__, dependencies(Mock())):
            backend = current_chat_backend(GeometryBase, parse, {GROUP: GroupScope(GROUP)})()
            backend.frames = iter([None])
            with self.assertRaises(ReadUnavailableError):
                ScopedTransport(backend, GroupScope(GROUP), parse).read()

    def test_resized_frame_keeps_title_and_body_in_original_union_coordinates(self):
        shot = Image.new('RGB', (500, 600), 'white')
        title, message = (.45, .05, .8, .15), (.4, .2, .99, .9)
        # Union=(200,30,495,540), title=(225,30,400,90).
        ocr = Mock(side_effect=[[item(GROUP + '(3)', x=2)], [item('正文', x=100, y=40)]])
        result = message_frame_ocr(ocr, shot.crop((200, 30, 495, 540)), shot, title, message)
        self.assertEqual((result[0]['x'], result[1]['x'], result[1]['y']), (27, 100, 100))


if __name__ == '__main__':
    unittest.main()
